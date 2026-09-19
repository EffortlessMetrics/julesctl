from __future__ import annotations

import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime

from ..api.client import JulesApiClient
from ..application.sessions import filter_sessions
from ..domain.errors import ApiError, InputError
from ..store import StateStore


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
                    "error": str(error),
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
    if passes < 1 or passes > 20:
        raise InputError("passes must be between 1 and 20")
    if settle_seconds < 0:
        raise InputError("settle_seconds must not be negative")

    results: list[dict[str, object]] = []
    pass_receipts: list[dict[str, object]] = []
    seen_ids = {str(target["session_id"]) for target in initial_targets}
    targets = initial_targets
    appeared_after_snapshot = 0
    stable_after_pass: int | None = None

    for pass_number in range(1, passes + 1):
        current_results = delete_targets(
            api,
            store,
            targets,
            max_workers=max_workers,
        )
        results.extend(current_results)
        pass_receipts.append(
            {
                "pass": pass_number,
                "target_ids": [str(target["session_id"]) for target in targets],
                "results": current_results,
            }
        )
        if pass_number == passes:
            break
        if settle_seconds:
            sleep(settle_seconds)
        current = select_prune_targets(
            list_sessions(),
            states=_selector_states(selector),
            source_name=(
                str(selector["source_name"]) if selector.get("source_name") is not None else None
            ),
            older_than=(
                str(selector["older_than"]) if selector.get("older_than") is not None else None
            ),
            nonterminal=bool(selector.get("nonterminal")),
            all_sessions=bool(selector.get("all_sessions")),
            include_unknown=bool(selector.get("include_unknown")),
        )
        new_sessions = [item for item in current if str(item["id"]) not in seen_ids]
        if not new_sessions:
            stable_after_pass = pass_number
            break
        targets = snapshot_targets(new_sessions)
        appeared_after_snapshot += len(targets)
        seen_ids.update(str(target["session_id"]) for target in targets)

    failed = [item for item in results if item.get("outcome") == "failed"]
    return {
        "plan_id": plan_id,
        "outcome": "partial" if failed else "completed",
        "deleted": sum(item.get("outcome") == "deleted" for item in results),
        "already_absent": sum(item.get("outcome") == "already_absent" for item in results),
        "failed": failed,
        "appeared_after_snapshot": appeared_after_snapshot,
        "stable_after_pass": stable_after_pass,
        "passes": pass_receipts,
    }
