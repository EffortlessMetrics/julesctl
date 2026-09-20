from __future__ import annotations

import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime

from ..api.client import JulesApiClient
from ..application.sessions import filter_sessions
from ..domain.errors import ApiError, InputError
from ..redaction import redact_text
from ..store import StateStore

_DELETE_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


def select_prune_targets(
    sessions: list[dict[str, object]],
    *,
    states: list[str] | None = None,
    source_name: str | None = None,
    older_than: str | None = None,
    nonterminal: bool = False,
    all_sessions: bool = False,
    include_unknown: bool = False,
    now: datetime | None = None,
) -> list[dict[str, object]]:
    """Apply a destructive selector locally while keeping unknown states opt-in."""

    modes = int(bool(states)) + int(nonterminal) + int(all_sessions)
    if modes != 1:
        raise InputError("choose exactly one of --state, --nonterminal, or --all")
    selected = filter_sessions(
        sessions,
        states=states,
        source_name=source_name,
        older_than=older_than,
        nonterminal=nonterminal,
        now=now or datetime.now(UTC),
    )
    if all_sessions:
        selected = filter_sessions(
            sessions,
            source_name=source_name,
            older_than=older_than,
            now=now or datetime.now(UTC),
        )
    if not include_unknown:
        selected = [item for item in selected if item.get("lifecycle") != "unknown"]
    return selected


def snapshot_targets(sessions: list[dict[str, object]]) -> list[dict[str, object]]:
    targets: list[dict[str, object]] = []
    for session in sessions:
        targets.append(
            {
                "session_id": str(session["id"]),
                "snapshot_state": session.get("raw_state"),
                "snapshot_lifecycle": session.get("lifecycle"),
                "snapshot_archived": session.get("archived"),
                "source_name": session.get("source_name"),
                "title": session.get("title"),
                "create_time": session.get("create_time"),
            }
        )
    return targets


def delete_targets(
    api: JulesApiClient,
    store: StateStore,
    targets: list[dict[str, object]],
    *,
    max_workers: int = 4,
) -> list[dict[str, object]]:
    if not 1 <= max_workers <= 32:
        raise InputError("delete concurrency must be between 1 and 32")

    def remove(target: dict[str, object]) -> tuple[str, bool | None, ApiError | None]:
        session_id = str(target["session_id"])
        try:
            return session_id, api.delete_session(session_id), None
        except ApiError as exc:
            return session_id, None, exc

    ordered: list[dict[str, object] | None] = [None] * len(targets)
    with ThreadPoolExecutor(max_workers=min(max_workers, max(len(targets), 1))) as pool:
        futures = {pool.submit(remove, target): index for index, target in enumerate(targets)}
        for future in as_completed(futures):
            index = futures[future]
            session_id, removed, error = future.result()
            if error is not None:
                ordered[index] = {
                    "session_id": session_id,
                    "outcome": "failed",
                    "http_status": error.http_status,
                    "api_status": error.api_status,
                    "error": redact_text(str(error)),
                }
                continue
            store.mark_deleted(session_id)
            ordered[index] = {
                "session_id": session_id,
                "outcome": "deleted" if removed else "already_absent",
            }
    return [item for item in ordered if item is not None]


def _selector_states(selector: dict[str, object]) -> list[str] | None:
    raw = selector.get("states")
    if raw is None:
        return None
    if not isinstance(raw, list) or not all(isinstance(item, str) for item in raw):
        raise InputError("stored deletion selector has invalid states")
    return list(raw) or None


def _baseline_session_ids(
    selector: dict[str, object],
    fallback_target_ids: list[str],
) -> set[str]:
    raw = selector.get("baseline_session_ids")
    if raw is None:
        return set(fallback_target_ids)
    if not isinstance(raw, list) or not all(isinstance(item, str) for item in raw):
        raise InputError("stored deletion selector has invalid baseline_session_ids")
    return set(raw)


def _prefer_result(
    previous: dict[str, object] | None,
    current: dict[str, object],
) -> dict[str, object]:
    """Retain the strongest known disposition for one immutable target."""

    if previous is None:
        return current
    rank = {"failed": 0, "already_absent": 1, "deleted": 2}
    previous_rank = rank.get(str(previous.get("outcome")), -1)
    current_rank = rank.get(str(current.get("outcome")), -1)
    return current if current_rank > previous_rank else previous


def _reconciled_absence(
    session_id: str,
    previous: dict[str, object],
) -> dict[str, object]:
    return {
        "session_id": session_id,
        "outcome": "already_absent",
        "reconciled_after_failure": True,
        "prior_error": {
            key: previous.get(key)
            for key in ("http_status", "api_status", "error")
            if previous.get(key) is not None
        },
    }


def _retryable_delete_failure(result: dict[str, object]) -> bool:
    if result.get("outcome") != "failed":
        return False
    status = result.get("http_status")
    return status is None or status in _DELETE_RETRYABLE_STATUS_CODES


def _verification_error(exc: Exception) -> dict[str, object]:
    result: dict[str, object] = {
        "kind": "settle_verification_failed",
        "message": redact_text(str(exc)),
        "error_type": exc.__class__.__name__,
    }
    if isinstance(exc, ApiError):
        result["http_status"] = exc.http_status
        result["api_status"] = exc.api_status
    return result


def apply_plan_with_settle(
    *,
    api: JulesApiClient,
    store: StateStore,
    plan_id: str,
    list_sessions: Callable[[], list[dict[str, object]]],
    selector: dict[str, object],
    initial_targets: list[dict[str, object]],
    max_workers: int = 4,
    settle_seconds: float = 0.0,
    passes: int = 1,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, object]:
    """Apply one immutable plan and separate retries from stability evidence.

    Every DELETE is limited to the stored target set. A target is retried only when its
    previous DELETE failed transiently and complete fleet enumeration still shows that
    exact ID. Sessions absent from the reviewed snapshot are evidence only and are never
    deleted.
    """

    if passes < 1 or passes > 20:
        raise InputError("passes must be between 1 and 20")
    if settle_seconds < 0:
        raise InputError("settle_seconds must not be negative")

    target_order = [str(target["session_id"]) for target in initial_targets]
    targets_by_id = {str(target["session_id"]): target for target in initial_targets}
    if len(targets_by_id) != len(initial_targets):
        raise InputError("deletion plan contains duplicate session IDs")
    baseline_session_ids = _baseline_session_ids(selector, target_order)

    pending_ids = list(target_order)
    strongest_results: dict[str, dict[str, object]] = {}
    pass_receipts: list[dict[str, object]] = []
    appeared_ids: set[str] = set()
    remaining_planned_ids: list[str] = list(target_order)
    stable_after_pass: int | None = None
    verification_error: dict[str, object] | None = None

    for pass_number in range(1, passes + 1):
        targets = [targets_by_id[session_id] for session_id in pending_ids]
        current_results = (
            delete_targets(api, store, targets, max_workers=max_workers) if targets else []
        )
        for result in current_results:
            session_id = str(result["session_id"])
            strongest_results[session_id] = _prefer_result(
                strongest_results.get(session_id),
                result,
            )

        receipt: dict[str, object] = {
            "pass": pass_number,
            "target_ids": list(pending_ids),
            "results": current_results,
        }
        pass_receipts.append(receipt)

        if settle_seconds:
            sleep(settle_seconds)

        try:
            fleet = list_sessions()
            matching = select_prune_targets(
                fleet,
                states=_selector_states(selector),
                source_name=(
                    str(selector["source_name"])
                    if selector.get("source_name") is not None
                    else None
                ),
                older_than=(
                    str(selector["older_than"]) if selector.get("older_than") is not None else None
                ),
                nonterminal=bool(selector.get("nonterminal")),
                all_sessions=bool(selector.get("all_sessions")),
                include_unknown=bool(selector.get("include_unknown")),
            )
        except Exception as exc:  # Preserve destructive receipts for all normalization failures.
            verification_error = _verification_error(exc)
            receipt["verification_error"] = verification_error
            break

        fleet_ids = {str(item["id"]) for item in fleet}
        matching_ids = {str(item["id"]) for item in matching}
        remaining_planned_ids = [
            session_id for session_id in target_order if session_id in fleet_ids
        ]
        newly_appeared = sorted(
            session_id
            for session_id in matching_ids
            if session_id not in baseline_session_ids and session_id not in appeared_ids
        )
        appeared_ids.update(newly_appeared)

        reconciled_absent: list[str] = []
        for session_id in target_order:
            previous = strongest_results.get(session_id)
            if (
                previous is not None
                and previous.get("outcome") == "failed"
                and session_id not in fleet_ids
            ):
                strongest_results[session_id] = _prefer_result(
                    previous,
                    _reconciled_absence(session_id, previous),
                )
                store.mark_deleted(session_id)
                reconciled_absent.append(session_id)

        receipt["remaining_planned_target_ids"] = remaining_planned_ids
        receipt["appeared_after_snapshot_ids"] = newly_appeared
        receipt["reconciled_absent_target_ids"] = reconciled_absent

        pending_ids = [
            session_id
            for session_id in remaining_planned_ids
            if _retryable_delete_failure(strongest_results.get(session_id, {}))
        ]
        post_snapshot_matches = matching_ids.difference(baseline_session_ids)
        if not remaining_planned_ids and not post_snapshot_matches:
            stable_after_pass = pass_number
            break
        if pass_number == passes:
            break

    final_results = [
        strongest_results[session_id]
        for session_id in target_order
        if session_id in strongest_results
    ]
    failed = [item for item in final_results if item.get("outcome") == "failed"]
    unresolved_targets = [
        session_id for session_id in target_order if session_id not in strongest_results
    ]
    if unresolved_targets:
        failed.extend(
            {
                "session_id": session_id,
                "outcome": "failed",
                "error": "planned target was not attempted",
            }
            for session_id in unresolved_targets
        )

    verification_incomplete = bool(remaining_planned_ids) and verification_error is None
    return {
        "plan_id": plan_id,
        "outcome": (
            "partial" if failed or verification_error or verification_incomplete else "completed"
        ),
        "deleted": sum(item.get("outcome") == "deleted" for item in final_results),
        "already_absent": sum(item.get("outcome") == "already_absent" for item in final_results),
        "failed": failed,
        "verification_error": verification_error,
        "verification_incomplete": verification_incomplete,
        "remaining_planned_target_ids": remaining_planned_ids,
        "appeared_after_snapshot": len(appeared_ids),
        "appeared_after_snapshot_ids": sorted(appeared_ids),
        "stable_after_pass": stable_after_pass,
        "passes": pass_receipts,
    }
