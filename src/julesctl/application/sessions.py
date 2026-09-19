from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

from ..domain.errors import InputError
from ..timestamps import GoogleTimestamp

_DURATION = re.compile(r"^(?P<value>\d+)(?P<unit>s|m|h|d|w)$", re.IGNORECASE)


def parse_duration(value: str) -> timedelta:
    match = _DURATION.fullmatch(value.strip())
    if not match:
        raise InputError("duration must use an integer plus s, m, h, d, or w")
    amount = int(match.group("value"))
    unit = match.group("unit").casefold()
    seconds = amount * {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}[unit]
    return timedelta(seconds=seconds)


def _timestamp(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        nanoseconds = GoogleTimestamp.parse(value).unix_nanoseconds
    except ValueError:
        return None
    seconds, remainder = divmod(nanoseconds, 1_000_000_000)
    return datetime.fromtimestamp(seconds, UTC).replace(microsecond=remainder // 1000)


def filter_sessions(
    sessions: list[dict[str, object]],
    *,
    states: list[str] | None = None,
    source_name: str | None = None,
    since: str | None = None,
    older_than: str | None = None,
    active: bool = False,
    nonterminal: bool = False,
    now: datetime | None = None,
) -> list[dict[str, object]]:
    if active and nonterminal:
        raise InputError("--active and --nonterminal are mutually exclusive")
    if since and older_than:
        raise InputError("--since and --older-than are mutually exclusive")
    normalized_states = {value.upper() for value in states or []}
    observed_now = (now or datetime.now(UTC)).astimezone(UTC)
    since_cutoff = observed_now - parse_duration(since) if since else None
    older_cutoff = observed_now - parse_duration(older_than) if older_than else None

    result: list[dict[str, object]] = []
    for session in sessions:
        raw_state = str(session.get("raw_state") or "STATE_UNSPECIFIED")
        lifecycle = str(session.get("lifecycle") or "unknown")
        if normalized_states and raw_state.upper() not in normalized_states:
            continue
        if source_name is not None and session.get("source_name") != source_name:
            continue
        if active and lifecycle not in {"executing", "actionable"}:
            continue
        if nonterminal and lifecycle == "terminal":
            continue
        created = _timestamp(session.get("create_time"))
        if since_cutoff is not None and (created is None or created < since_cutoff):
            continue
        if older_cutoff is not None and (created is None or created >= older_cutoff):
            continue
        result.append(session)
    return result
