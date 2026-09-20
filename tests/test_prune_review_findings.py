from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from julesctl.application.prune import apply_plan_with_settle, delete_targets
from julesctl.client import JulesClient
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


def test_verification_error_redacts_environment_and_header_credentials(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JULES_API_KEY", "super-secret-key")
    store = StateStore(tmp_path / "state.db")
    api = SuccessfulDeleteApi()

    def failed_scan() -> list[dict[str, object]]:
        raise ValueError(
            "JULES_API_KEY=super-secret-key x-goog-api-key: super-secret-key"
        )

    try:
        result = apply_plan_with_settle(  # type: ignore[arg-type]
            api=api,
            store=store,
            plan_id="plan",
            list_sessions=failed_scan,
            selector={"all_sessions": True},
            initial_targets=[{"session_id": "1"}],
        )
    finally:
        store.close()

    assert "super-secret-key" not in str(result)
    error = result["verification_error"]
    assert isinstance(error, dict)
    assert "[REDACTED]" in str(error["message"])


class SecretDeleteFailureApi:
    @staticmethod
    def delete_session(_session_id: str) -> bool:
        raise ApiError(
            "x-goog-api-key: super-secret-key",
            http_status=403,
            api_status="PERMISSION_DENIED",
        )


def test_per_target_delete_error_is_redacted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JULES_API_KEY", "super-secret-key")
    store = StateStore(tmp_path / "state.db")
    try:
        result = delete_targets(  # type: ignore[arg-type]
            SecretDeleteFailureApi(),
            store,
            [{"session_id": "1"}],
        )
    finally:
        store.close()

    assert result[0]["outcome"] == "failed"
    assert result[0]["error"] == "x-goog-api-key: [REDACTED]"


def test_plan_time_session_that_later_matches_is_not_new_ingress(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.db")
    api = SuccessfulDeleteApi()
    preexisting_but_excluded = {
        "id": "2",
        "raw_state": "IN_PROGRESS",
        "lifecycle": "executing",
    }
    try:
        result = apply_plan_with_settle(  # type: ignore[arg-type]
            api=api,
            store=store,
            plan_id="plan",
            list_sessions=lambda: [preexisting_but_excluded],
            selector={
                "nonterminal": True,
                "baseline_session_ids": ["1", "2"],
            },
            initial_targets=[{"session_id": "1"}],
        )
    finally:
        store.close()

    assert result["outcome"] == "completed"
    assert result["appeared_after_snapshot"] == 0
    assert result["appeared_after_snapshot_ids"] == []
    assert result["stable_after_pass"] == 1


class CapturePlanStore:
    def __init__(self) -> None:
        self.selector: dict[str, object] | None = None
        self.targets: list[dict[str, object]] | None = None

    def create_deletion_plan(
        self,
        _plan_id: str,
        selector: dict[str, object],
        targets: list[dict[str, object]],
    ) -> None:
        self.selector = selector
        self.targets = targets


class PlanController:
    def __init__(self) -> None:
        self.store = CapturePlanStore()
        self.ctx = SimpleNamespace(store=self.store)

    @staticmethod
    def list_sessions(*, all_history: bool = False) -> list[dict[str, object]]:
        assert all_history is True
        return [
            {
                "id": "2",
                "raw_state": "COMPLETED",
                "lifecycle": "terminal",
            },
            {
                "id": "1",
                "raw_state": "IN_PROGRESS",
                "lifecycle": "executing",
            },
        ]


def test_prune_plan_persists_complete_plan_time_fleet_identity() -> None:
    controller = PlanController()
    client = JulesClient(controller)  # type: ignore[arg-type]

    result = client.plan_prune(nonterminal=True)

    selector = result["selector"]
    assert isinstance(selector, dict)
    assert selector["baseline_session_ids"] == ["1", "2"]
    assert controller.store.selector == selector
    targets = result["targets"]
    assert isinstance(targets, list)
    assert [target["session_id"] for target in targets] == ["1"]
