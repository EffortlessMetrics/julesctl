from __future__ import annotations

from pathlib import Path

import pytest

from julesctl.application.prune import apply_plan_with_settle, delete_targets
from julesctl.domain.errors import ApiError
from julesctl.store import StateStore


class SequencedFailureApi:
    def __init__(self, failures: list[ApiError]) -> None:
        self.failures = iter(failures)
        self.calls: list[str] = []

    def delete_session(self, session_id: str) -> bool:
        self.calls.append(session_id)
        raise next(self.failures)


class SuccessfulDeleteApi:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def delete_session(self, session_id: str) -> bool:
        self.calls.append(session_id)
        return True


def test_later_definitive_failure_replaces_transient_failure_and_stops_retries(
    tmp_path: Path,
) -> None:
    store = StateStore(tmp_path / "state.db")
    api = SequencedFailureApi(
        [
            ApiError("temporary", http_status=503, api_status="UNAVAILABLE"),
            ApiError("denied", http_status=403, api_status="PERMISSION_DENIED"),
        ]
    )
    visible = {
        "id": "1",
        "raw_state": "IN_PROGRESS",
        "lifecycle": "executing",
    }
    try:
        result = apply_plan_with_settle(  # type: ignore[arg-type]
            api=api,
            store=store,
            plan_id="plan",
            list_sessions=lambda: [visible],
            selector={"nonterminal": True, "baseline_session_ids": ["1"]},
            initial_targets=[{"session_id": "1"}],
            passes=5,
        )
    finally:
        store.close()

    assert api.calls == ["1", "1"]
    assert result["outcome"] == "partial"
    failed = result["failed"]
    assert isinstance(failed, list)
    assert failed == [
        {
            "session_id": "1",
            "outcome": "failed",
            "http_status": 403,
            "api_status": "PERMISSION_DENIED",
            "error": "denied",
        }
    ]


class SecretStatusApi:
    @staticmethod
    def delete_session(_session_id: str) -> bool:
        raise ApiError(
            "delete failed",
            http_status=403,
            api_status="JULES_API_KEY=super-secret-key",
        )


def test_api_status_is_redacted_in_per_target_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JULES_API_KEY", "super-secret-key")
    store = StateStore(tmp_path / "state.db")
    try:
        result = delete_targets(  # type: ignore[arg-type]
            SecretStatusApi(),
            store,
            [{"session_id": "1"}],
        )
    finally:
        store.close()

    assert "super-secret-key" not in str(result)
    assert result[0]["api_status"] == "JULES_API_KEY=[REDACTED]"


def test_api_status_is_redacted_in_settle_verification_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JULES_API_KEY", "super-secret-key")
    store = StateStore(tmp_path / "state.db")
    api = SuccessfulDeleteApi()

    def failed_scan() -> list[dict[str, object]]:
        raise ApiError(
            "read failed",
            http_status=503,
            api_status="JULES_API_KEY=super-secret-key",
        )

    try:
        result = apply_plan_with_settle(  # type: ignore[arg-type]
            api=api,
            store=store,
            plan_id="plan",
            list_sessions=failed_scan,
            selector={"all_sessions": True, "baseline_session_ids": ["1"]},
            initial_targets=[{"session_id": "1"}],
        )
    finally:
        store.close()

    assert "super-secret-key" not in str(result)
    error = result["verification_error"]
    assert isinstance(error, dict)
    assert error["api_status"] == "JULES_API_KEY=[REDACTED]"


def test_legacy_plan_does_not_claim_post_snapshot_ingress_accounting(
    tmp_path: Path,
) -> None:
    store = StateStore(tmp_path / "state.db")
    api = SuccessfulDeleteApi()
    later_matching_session = {
        "id": "2",
        "raw_state": "IN_PROGRESS",
        "lifecycle": "executing",
    }
    try:
        result = apply_plan_with_settle(  # type: ignore[arg-type]
            api=api,
            store=store,
            plan_id="legacy",
            list_sessions=lambda: [later_matching_session],
            selector={"nonterminal": True},
            initial_targets=[{"session_id": "1"}],
            passes=3,
        )
    finally:
        store.close()

    assert api.calls == ["1"]
    assert result["outcome"] == "completed"
    assert result["ingress_accounting_complete"] is False
    assert result["appeared_after_snapshot"] == 0
    assert result["appeared_after_snapshot_ids"] == []
    assert result["stable_after_pass"] is None
    receipts = result["passes"]
    assert isinstance(receipts, list)
    assert len(receipts) == 1
    assert receipts[0]["ingress_accounting_complete"] is False
