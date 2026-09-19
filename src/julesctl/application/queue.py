from __future__ import annotations

import json
import uuid
from pathlib import Path

from ..config import Settings
from ..controller import JulesController
from ..domain.errors import AdmissionError, IndeterminateError, InputError, JulesCtlError
from ..domain.models import DispatchSpec
from ..store import StateStore
from ..timestamps import GoogleTimestamp

MAX_CANDIDATE_BYTES = 256 * 1024


def load_candidate(path: Path) -> DispatchSpec:
    """Load one bounded, regular UTF-8 candidate file without following symlinks."""

    if path.is_symlink():
        raise InputError("candidate spec must not be a symlink")
    if not path.is_file():
        raise InputError("candidate spec must be a regular file")
    size = path.stat().st_size
    if size > MAX_CANDIDATE_BYTES:
        raise InputError(
            f"candidate spec is {size} bytes; maximum is {MAX_CANDIDATE_BYTES} bytes"
        )
    try:
        payload = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise InputError("candidate spec must be UTF-8") from exc
    try:
        spec = DispatchSpec.model_validate_json(payload)
    except ValueError as exc:
        raise InputError(f"candidate spec is invalid: {exc}") from exc
    if spec.expires_at is not None:
        try:
            expiry = GoogleTimestamp.parse(spec.expires_at)
        except ValueError as exc:
            raise InputError("candidate expires_at must be an RFC3339 timestamp") from exc
        if expiry.unix_nanoseconds <= GoogleTimestamp.now().unix_nanoseconds:
            raise InputError("candidate is already expired")
    return spec


def canonical_spec(spec: DispatchSpec) -> str:
    return json.dumps(
        spec.model_dump(by_alias=True, exclude_none=True),
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )


def enqueue_candidate(store: StateStore, spec: DispatchSpec) -> dict[str, object]:
    candidate_id = str(uuid.uuid4())
    return store.enqueue_candidate(
        candidate_id=candidate_id,
        dispatch_key=spec.dispatch_key,
        spec_json=canonical_spec(spec),
    )


def queue_status(store: StateStore, *, limit: int = 100) -> dict[str, object]:
    return {
        "counts": store.candidate_counts(),
        "items": store.list_candidates(limit=limit),
    }


def _error_record(exc: Exception) -> dict[str, object]:
    return {
        "kind": exc.__class__.__name__,
        "message": str(exc),
    }


def _candidate_allowed(
    spec: DispatchSpec,
    *,
    allowed_repos: set[str],
    allow_repoless: bool,
) -> bool:
    if spec.repo is None:
        return allow_repoless
    return spec.repo.casefold() in allowed_repos


def run_worker_once(
    settings: Settings,
    *,
    max_items: int,
    allow_repos: list[str],
    allow_repoless: bool,
    stale_claim_seconds: int = 900,
) -> list[dict[str, object]]:
    """Claim and process a bounded candidate batch under the credentialed controller."""

    if max_items < 1 or max_items > 100:
        raise InputError("max_items must be between 1 and 100")
    allowed = {repo.casefold() for repo in allow_repos}
    worker_id = str(uuid.uuid4())
    outcomes: list[dict[str, object]] = []
    with JulesController.from_settings(settings) as controller:
        store = controller.ctx.store
        store.requeue_stale_candidates(older_than_seconds=stale_claim_seconds)
        claimed = store.claim_candidates(limit=max_items, worker_id=worker_id)
        for row in claimed:
            candidate_id = str(row["candidate_id"])
            try:
                spec = DispatchSpec.model_validate_json(str(row["spec_json"]))
            except ValueError as exc:
                error = {"kind": "invalid_candidate", "message": str(exc)}
                store.finish_candidate(candidate_id, state="FAILED", error=error)
                outcomes.append(
                    {
                        "candidate_id": candidate_id,
                        "dispatch_key": row["dispatch_key"],
                        "outcome": "failed",
                        "error": error,
                    }
                )
                continue

            if spec.expires_at is not None:
                try:
                    expired = (
                        GoogleTimestamp.parse(spec.expires_at).unix_nanoseconds
                        <= GoogleTimestamp.now().unix_nanoseconds
                    )
                except ValueError:
                    expired = True
                if expired:
                    store.finish_candidate(candidate_id, state="EXPIRED")
                    outcomes.append(
                        {
                            "candidate_id": candidate_id,
                            "dispatch_key": spec.dispatch_key,
                            "outcome": "expired",
                        }
                    )
                    continue

            if not _candidate_allowed(
                spec,
                allowed_repos=allowed,
                allow_repoless=allow_repoless,
            ):
                error = {
                    "kind": "repository_not_allowed",
                    "message": f"candidate repository is not allowed: {spec.repo!r}",
                }
                store.finish_candidate(candidate_id, state="REJECTED_POLICY", error=error)
                outcomes.append(
                    {
                        "candidate_id": candidate_id,
                        "dispatch_key": spec.dispatch_key,
                        "outcome": "rejected_policy",
                        "error": error,
                    }
                )
                continue

            try:
                result = controller.dispatch(spec)
            except AdmissionError as exc:
                store.release_candidate(candidate_id)
                outcomes.append(
                    {
                        "candidate_id": candidate_id,
                        "dispatch_key": spec.dispatch_key,
                        "outcome": "deferred",
                        "error": _error_record(exc),
                    }
                )
                break
            except IndeterminateError as exc:
                error = _error_record(exc)
                store.finish_candidate(candidate_id, state="INDETERMINATE", error=error)
                outcomes.append(
                    {
                        "candidate_id": candidate_id,
                        "dispatch_key": spec.dispatch_key,
                        "outcome": "indeterminate",
                        "error": error,
                    }
                )
            except JulesCtlError as exc:
                error = _error_record(exc)
                store.finish_candidate(candidate_id, state="FAILED", error=error)
                outcomes.append(
                    {
                        "candidate_id": candidate_id,
                        "dispatch_key": spec.dispatch_key,
                        "outcome": "failed",
                        "error": error,
                    }
                )
            else:
                store.finish_candidate(candidate_id, state="COMPLETED", outcome=result)
                outcomes.append(
                    {
                        "candidate_id": candidate_id,
                        "dispatch_key": spec.dispatch_key,
                        "outcome": result.get("outcome", "completed"),
                        "dispatch": result,
                    }
                )
    return outcomes
