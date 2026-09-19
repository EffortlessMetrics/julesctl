from datetime import UTC, datetime

import pytest

from julesctl.application.sessions import filter_sessions, parse_duration
from julesctl.domain.errors import InputError

SESSIONS = [
    {
        "id": "1",
        "raw_state": "IN_PROGRESS",
        "lifecycle": "executing",
        "source_name": "sources/github/acme/repo",
        "create_time": "2026-09-19T10:00:00Z",
    },
    {
        "id": "2",
        "raw_state": "AWAITING_USER_FEEDBACK",
        "lifecycle": "actionable",
        "source_name": "sources/github/acme/repo",
        "create_time": "2026-09-18T10:00:00Z",
    },
    {
        "id": "3",
        "raw_state": "COMPLETED",
        "lifecycle": "terminal",
        "source_name": "sources/github/other/repo",
        "create_time": "2026-09-10T10:00:00Z",
    },
    {
        "id": "4",
        "raw_state": "FUTURE_STATE",
        "lifecycle": "unknown",
        "source_name": None,
        "create_time": None,
    },
]


def test_parse_duration() -> None:
    assert parse_duration("2h").total_seconds() == 7200
    assert parse_duration("3d").days == 3
    with pytest.raises(InputError):
        parse_duration("1.5h")


def test_filter_by_state_and_source() -> None:
    result = filter_sessions(
        SESSIONS,
        states=["in_progress"],
        source_name="sources/github/acme/repo",
    )
    assert [item["id"] for item in result] == ["1"]


def test_active_and_nonterminal_have_distinct_semantics() -> None:
    active = filter_sessions(SESSIONS, active=True)
    nonterminal = filter_sessions(SESSIONS, nonterminal=True)
    assert [item["id"] for item in active] == ["1", "2"]
    assert [item["id"] for item in nonterminal] == ["1", "2", "4"]


def test_since_and_older_than() -> None:
    now = datetime(2026, 9, 19, 12, 0, 0, tzinfo=UTC)
    recent = filter_sessions(SESSIONS, since="6h", now=now)
    old = filter_sessions(SESSIONS, older_than="2d", now=now)
    assert [item["id"] for item in recent] == ["1"]
    assert [item["id"] for item in old] == ["3"]


def test_conflicting_filters_fail() -> None:
    with pytest.raises(InputError, match="active"):
        filter_sessions(SESSIONS, active=True, nonterminal=True)
    with pytest.raises(InputError, match="since"):
        filter_sessions(SESSIONS, since="1h", older_than="1d")
