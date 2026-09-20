from __future__ import annotations

from pathlib import Path

from julesctl.application.prune import apply_plan_with_settle
from julesctl.domain.errors import ApiError
from julesctl.store import StateStore


class DefinitiveDeleteFailureApi:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def delete_session(self, session_id: str) -> bool:
        self.calls.append(session_id)
        raise ApiError(
            "permission denied",
            http_status=403,
            api_status="PERMISSION_DENIED",
        )


class SuccessfulDeleteApi:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def delete_session(self, session_id: str) -> bool:
        self.calls.append(session_id)
        return True


def test_definitive_delete_failure_is_not_retried_across_settle_passes(
    tmp_path: Path,
) -> None:
    store = StateStore(tmp_path / "state.db")
    api = DefinitiveDeleteFailureApi()
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
            selector={"nonterminal": True},
            initial_targets=[{"session_id": "1"}],
            passes=5,
        )
    finally:
        store.close()

    assert api.calls == ["1"]
    assert result["outcome"] == "partial"
    assert result["remaining_planned_target_ids"] == ["1"]
    assert result["verification_incomplete"] is True
    failed = result["failed"]
    assert isinstance(failed, list)
    assert failed[0]["http_status"] == 403


def test_non_api_verification_failure_preserves_successful_delete_receipt(
    tmp_path: Path,
) -> None:
    store = StateStore(tmp_path / "state.db")
    api = SuccessfulDeleteApi()

    def malformed_fleet() -> list[dict[str, object]]:
        raise ValueError("malformed session payload")

    try:
        result = apply_plan_with_settle(  # type: ignore[arg-type]
            api=api,
            store=store,
            plan_id="plan",
            list_sessions=malformed_fleet,
            selector={"all_sessions": True},
            initial_targets=[{"session_id": "1"}],
            passes=2,
        )
    finally:
        store.close()

    assert api.calls == ["1"]
    assert result["outcome"] == "partial"
    assert result["deleted"] == 1
    assert result["failed"] == []
    assert result["verification_error"] == {
        "kind": "settle_verification_failed",
        "message": "malformed session payload",
        "error_type": "ValueError",
    }
