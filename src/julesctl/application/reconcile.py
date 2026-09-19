from __future__ import annotations

import json
from datetime import UTC, datetime

from ..api.client import JulesApiClient
from ..domain.errors import ApiError
from ..domain.fingerprints import sha256_text
from ..domain.models import ActivityWire
from ..store import StateStore
from ..timestamps import GoogleTimestamp


def _rfc3339_from_nanoseconds(value: int) -> str:
    seconds, nanoseconds = divmod(max(value, 0), 1_000_000_000)
    base = datetime.fromtimestamp(seconds, UTC).strftime("%Y-%m-%dT%H:%M:%S")
    return f"{base}.{nanoseconds:09d}Z"


def _timestamp_nanoseconds(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return GoogleTimestamp.parse(value).unix_nanoseconds
    except ValueError:
        return None


def _activity_cursor(cursor: str, *, overlap_seconds: int) -> str:
    cursor_ns = GoogleTimestamp.parse(cursor).unix_nanoseconds
    overlap_ns = max(cursor_ns - overlap_seconds * 1_000_000_000, 0)
    return _rfc3339_from_nanoseconds(overlap_ns)


def _artifact_summary(activity: ActivityWire) -> list[str]:
    kinds: list[str] = []
    for artifact in activity.artifacts:
        if artifact.change_set is not None:
            kinds.append("change_set")
        if artifact.bash_output is not None:
            kinds.append("bash_output")
        if artifact.media is not None:
            kinds.append("media")
        if artifact.model_extra:
            kinds.extend(sorted(str(key) for key in artifact.model_extra))
    return sorted(set(kinds))


def _event(activity: ActivityWire, *, session_id: str, origin: str) -> dict[str, object]:
    value: dict[str, object] = {
        "type": activity.event_type(),
        "session_id": session_id,
        "activity_id": activity.id,
        "activity_name": activity.name,
        "create_time": activity.create_time,
        "origin": origin,
    }
    if activity.description is not None:
        value["description"] = activity.description
    if activity.user_messaged is not None:
        value["message"] = activity.user_messaged.get("userMessage")
    if activity.agent_messaged is not None:
        value["message"] = activity.agent_messaged.get("agentMessage")
    if activity.progress_updated is not None:
        value["progress"] = activity.progress_updated
    if activity.plan_generated is not None:
        value["plan"] = activity.plan_generated
    if activity.session_failed is not None:
        value["failure"] = activity.session_failed
    artifacts = _artifact_summary(activity)
    if artifacts:
        value["artifact_kinds"] = artifacts
    return value


def reconcile_session_activities(
    api: JulesApiClient,
    store: StateStore,
    session_id: str,
    *,
    origin: str,
    overlap_seconds: int = 5,
) -> list[dict[str, object]]:
    """Fetch and commit only activities not already present in the local ledger."""

    cursor = store.activity_cursor(session_id)
    create_time = (
        _activity_cursor(cursor, overlap_seconds=overlap_seconds) if cursor is not None else None
    )
    fallback_used = False
    try:
        activities = list(api.iter_activities(session_id, create_time=create_time))
    except ApiError as exc:
        if create_time is None or exc.http_status != 400:
            raise
        fallback_used = True
        activities = list(api.iter_activities(session_id))

    def sort_key(activity: ActivityWire) -> tuple[int, str]:
        observed = _timestamp_nanoseconds(activity.create_time)
        return (observed if observed is not None else -1, activity.name)

    activities.sort(key=sort_key)
    receipts: list[dict[str, str | None]] = []
    highest_cursor = cursor
    highest_ns = _timestamp_nanoseconds(cursor)
    for activity in activities:
        payload = json.dumps(
            activity.model_dump(by_alias=True, exclude_none=True),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            default=str,
        )
        receipts.append(
            {
                "activity_name": activity.name,
                "activity_id": activity.id,
                "create_time": activity.create_time,
                "event_type": activity.event_type(),
                "payload_sha256": sha256_text(payload),
            }
        )
        observed_ns = _timestamp_nanoseconds(activity.create_time)
        if observed_ns is not None and (highest_ns is None or observed_ns > highest_ns):
            highest_ns = observed_ns
            highest_cursor = activity.create_time

    new_names = store.commit_activity_batch(
        session_id=session_id,
        receipts=receipts,
        highest_create_time=highest_cursor,
    )
    events = [
        _event(activity, session_id=session_id, origin=origin)
        for activity in activities
        if activity.name in new_names
    ]
    if fallback_used and events:
        events[0]["activity_filter_fallback"] = True
    return events
