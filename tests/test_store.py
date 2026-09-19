from pathlib import Path

import pytest

from julesctl.domain.errors import AdmissionError, InputError
from julesctl.store import StateStore
from julesctl.timestamps import GoogleTimestamp


def _attempt() -> dict[str, object]:
    return {
        "title": "x",
        "prompt_sha256": "sha256:x",
        "automation_mode": "AUTO_CREATE_PR",
        "require_plan_approval": False,
        "environment_variables_enabled": None,
    }


def _reserve(store: StateStore, *, key: str, attempt_id: str, fingerprint: str = "a") -> None:
    store.reserve_work(
        dispatch_key=key,
        fingerprint=fingerprint,
        attempt_id=attempt_id,
        request_fingerprint=f"request:{fingerprint}",
        attempt=_attempt(),
        baseline_session_ids=[],
    )


def test_dispatch_key_conflict_fails_closed(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "s.db")
    try:
        _reserve(store, key="k", attempt_id="1", fingerprint="a")
        with pytest.raises(InputError, match="different fingerprint"):
            _reserve(store, key="k", attempt_id="2", fingerprint="b")
    finally:
        store.close()


def test_freeze_invalidates_reserved_attempt_before_send(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "s.db")
    try:
        _reserve(store, key="k", attempt_id="1")
        store.set_frozen(True)
        with pytest.raises((AdmissionError, InputError)):
            store.begin_send("1", send_started_at=GoogleTimestamp.now().raw)
        row = store.get_attempt("1")
        assert row is not None
        assert row["state"] == "CANCELLED_LOCAL"
        assert store.starts_last_24h() == 0
    finally:
        store.close()


def test_begin_send_counts_start_and_cannot_repeat(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "s.db")
    try:
        _reserve(store, key="k", attempt_id="1")
        store.begin_send("1", send_started_at=GoogleTimestamp.now().raw)
        assert store.starts_last_24h() == 1
        with pytest.raises(InputError, match="cannot begin send"):
            store.begin_send("1", send_started_at=GoogleTimestamp.now().raw)
    finally:
        store.close()


def test_one_session_cannot_bind_to_two_attempts(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "s.db")
    try:
        _reserve(store, key="one", attempt_id="1")
        store.begin_send("1", send_started_at=GoogleTimestamp.now().raw)
        store.bind_session("1", "123", reconciled=False)
        _reserve(store, key="two", attempt_id="2")
        store.begin_send("2", send_started_at=GoogleTimestamp.now().raw)
        with pytest.raises(InputError, match="already bound"):
            store.bind_session("2", "123", reconciled=True)
    finally:
        store.close()
