from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from julesctl.cli.output import event
from julesctl.config import state_root
from julesctl.domain.errors import AdmissionError
from julesctl.store import StateStore


def _attempt() -> dict[str, object]:
    return {
        "title": "x",
        "prompt_sha256": "sha256:x",
        "automation_mode": "AUTO_CREATE_PR",
        "require_plan_approval": False,
        "environment_variables_enabled": None,
    }


def _reserve(
    store: StateStore,
    *,
    key: str,
    attempt_id: str,
    max_starts_24h: int | None = None,
) -> None:
    store.reserve_work(
        dispatch_key=key,
        fingerprint=f"fingerprint:{key}",
        attempt_id=attempt_id,
        request_fingerprint=f"request:{key}",
        attempt=_attempt(),
        baseline_session_ids=[],
        max_starts_24h=max_starts_24h,
    )


def _timestamp(hours_ago: int) -> str:
    value = datetime.now(UTC) - timedelta(hours=hours_ago)
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")


def test_event_schema_cannot_be_overridden() -> None:
    value = event(
        {
            "schema": "attacker-controlled",
            "type": "completed",
            "session_id": "123",
        }
    )
    assert value["schema"] == "julesctl.event.v1"


def test_empty_xdg_state_home_uses_home_default(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("JULESCTL_HOME", raising=False)
    monkeypatch.setenv("XDG_STATE_HOME", "")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    assert state_root() == tmp_path / ".local" / "state" / "julesctl"


def test_rolling_start_window_uses_rfc3339_cutoff(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.db")
    try:
        _reserve(store, key="old", attempt_id="old")
        store.begin_send("old", send_started_at=_timestamp(25))
        assert store.starts_last_24h() == 0

        _reserve(store, key="recent", attempt_id="recent", max_starts_24h=1)
        store.begin_send("recent", send_started_at=_timestamp(23))
        assert store.starts_last_24h() == 1

        with pytest.raises(AdmissionError, match="rolling start budget"):
            _reserve(
                store,
                key="blocked",
                attempt_id="blocked",
                max_starts_24h=1,
            )
    finally:
        store.close()
