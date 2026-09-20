from __future__ import annotations

from pathlib import Path

from julesctl.application.prune import apply_plan_with_settle
from julesctl.domain.errors import ApiError
from julesctl.store import StateStore


class LostDeleteResponseApi:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def delete_session(self, session_id: str) -> bool:
        self.calls.append(session_id)
        raise ApiError(
            "delete response was lost",
            http_status=503,
            api_status="UNAVAILABLE",
        )


def test_absent_target_reconciles_without_leaving_incomplete_plan(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.db")
    store.upsert_session(
        {
            "session_id": "1",
            "session_name": "sessions/1",
            "origin": "managed",
            "raw_state": "IN_PROGRESS",
            "lifecycle": "executing",
            "archived": False,
            "repo": None,
            "source_name": None,
            "starting_branch": None,
            "working_branch": None,
            "title": "lost response",
            "prompt_sha256": None,
            "pr_url": None,
        }
    )
    api = LostDeleteResponseApi()
    try:
        result = apply_plan_with_settle(  # type: ignore[arg-type]
            api=api,
            store=store,
            plan_id="plan",
            list_sessions=lambda: [],
            selector={"all_sessions": True, "baseline_session_ids": ["1"]},
            initial_targets=[{"session_id": "1"}],
            passes=1,
        )
        assert store.active_rows() == []
    finally:
        store.close()

    assert api.calls == ["1"]
    assert result["outcome"] == "completed"
    assert result["remaining_planned_target_ids"] == []
    assert result["verification_incomplete"] is False
    assert result["already_absent"] == 1
    assert result["failed"] == []

    receipts = result["passes"]
    assert isinstance(receipts, list)
    assert receipts[0]["reconciled_absent_target_ids"] == ["1"]
