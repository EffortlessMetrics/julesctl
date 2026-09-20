from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from julesctl.application.prune import (
    apply_plan_with_settle,
    delete_targets,
    select_prune_targets,
)
from julesctl.client import JulesClient
from julesctl.domain.errors import ApiError, InputError
from julesctl.domain.models import DispatchSpec, SessionWire, SourceWire
from julesctl.store import StateStore


def _sessions() -> list[dict[str, object]]:
    return [
        {
            "id": "1",
            "raw_state": "FAILED",
            "lifecycle": "terminal",
            "source_name": "sources/one",
            "create_time": "2026-09-01T00:00:00Z",
        },
        {
            "id": "2",
            "raw_state": "IN_PROGRESS",
            "lifecycle": "executing",
            "source_name": "sources/one",
            "create_time": "2026-09-18T00:00:00Z",
        },
        {
            "id": "3",
            "raw_state": "FUTURE_STATE",
            "lifecycle": "unknown",
            "source_name": "sources/one",
            "create_time": "2026-09-18T00:00:00Z",
        },
    ]


def test_destructive_selection_requires_one_mode_and_unknown_is_opt_in() -> None:
    with pytest.raises(InputError, match="exactly one"):
        select_prune_targets(_sessions())
    with pytest.raises(InputError, match="exactly one"):
        select_prune_targets(_sessions(), states=["FAILED"], nonterminal=True)

    nonterminal = select_prune_targets(_sessions(), nonterminal=True)
    assert [item["id"] for item in nonterminal] == ["2"]

    including_unknown = select_prune_targets(
        _sessions(),
        nonterminal=True,
        include_unknown=True,
    )
    assert [item["id"] for item in including_unknown] == ["2", "3"]

    all_visible = select_prune_targets(_sessions(), all_sessions=True)
    assert [item["id"] for item in all_visible] == ["1", "2"]


class FakeDeleteApi:
    def __init__(self, outcomes: dict[str, bool | Exception]) -> None:
        self.outcomes = outcomes
        self.calls: list[str] = []

    def delete_session(self, session_id: str) -> bool:
        self.calls.append(session_id)
        outcome = self.outcomes[session_id]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def test_delete_targets_preserves_deleted_absent_and_failed(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.db")
    api = FakeDeleteApi(
        {
            "1": True,
            "2": False,
            "3": ApiError("no", http_status=403, api_status="PERMISSION_DENIED"),
        }
    )
    try:
        result = delete_targets(  # type: ignore[arg-type]
            api,
            store,
            [{"session_id": "1"}, {"session_id": "2"}, {"session_id": "3"}],
            max_workers=2,
        )
        assert [item["outcome"] for item in result] == [
            "deleted",
            "already_absent",
            "failed",
        ]
        assert result[2]["api_status"] == "PERMISSION_DENIED"
    finally:
        store.close()


def test_settle_pass_reports_new_sessions_without_deleting_them(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.db")
    api = FakeDeleteApi({"1": True})
    new_session = {
        "id": "2",
        "raw_state": "IN_PROGRESS",
        "lifecycle": "executing",
        "source_name": "sources/one",
    }
    try:
        result = apply_plan_with_settle(  # type: ignore[arg-type]
            api=api,
            store=store,
            plan_id="p",
            list_sessions=lambda: [new_session],
            selector={"nonterminal": True, "include_unknown": False},
            initial_targets=[{"session_id": "1"}],
            settle_seconds=0,
            passes=3,
        )
        assert api.calls == ["1"]
        assert result["appeared_after_snapshot"] == 1
        assert result["appeared_after_snapshot_ids"] == ["2"]
        assert result["stable_after_pass"] is None
        assert result["deleted"] == 1
        receipts = result["passes"]
        assert isinstance(receipts, list)
        assert receipts[0]["appeared_after_snapshot_ids"] == ["2"]
        assert receipts[1]["target_ids"] == []
        assert receipts[2]["target_ids"] == []
    finally:
        store.close()


class FlakyDeleteApi:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def delete_session(self, session_id: str) -> bool:
        self.calls.append(session_id)
        if len(self.calls) == 1:
            raise ApiError("temporary", http_status=503, api_status="UNAVAILABLE")
        return True


def test_settle_pass_retries_only_the_original_planned_target(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.db")
    api = FlakyDeleteApi()
    snapshots = iter(
        [
            [
                {
                    "id": "1",
                    "raw_state": "IN_PROGRESS",
                    "lifecycle": "executing",
                    "source_name": "sources/one",
                }
            ],
            [],
        ]
    )
    try:
        result = apply_plan_with_settle(  # type: ignore[arg-type]
            api=api,
            store=store,
            plan_id="p",
            list_sessions=lambda: next(snapshots),
            selector={"nonterminal": True, "include_unknown": False},
            initial_targets=[{"session_id": "1"}],
            settle_seconds=0,
            passes=3,
        )
        assert api.calls == ["1", "1"]
        assert result["outcome"] == "completed"
        assert result["deleted"] == 1
        assert result["failed"] == []
        assert result["stable_after_pass"] == 2
    finally:
        store.close()


def test_duplicate_plan_targets_are_rejected(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.db")
    api = FakeDeleteApi({"1": True})
    try:
        with pytest.raises(InputError, match="duplicate"):
            apply_plan_with_settle(  # type: ignore[arg-type]
                api=api,
                store=store,
                plan_id="p",
                list_sessions=lambda: [],
                selector={"all_sessions": True},
                initial_targets=[{"session_id": "1"}, {"session_id": "1"}],
            )
    finally:
        store.close()


class FakeRetryApi:
    def get_session(self, session_id: str) -> SessionWire:
        assert session_id == "old"
        return SessionWire.model_validate(
            {
                "name": "sessions/old",
                "id": "old",
                "prompt": "Do it",
                "title": "Original",
                "automationMode": "AUTO_CREATE_PR",
                "requirePlanApproval": True,
                "sourceContext": {
                    "source": "sources/github/acme/repo",
                    "githubRepoContext": {"startingBranch": "main"},
                },
            }
        )

    def iter_sources(self):
        return iter(
            [
                SourceWire.model_validate(
                    {
                        "name": "sources/github/acme/repo",
                        "githubRepo": {"owner": "acme", "repo": "repo"},
                    }
                )
            ]
        )


class FakeRetryController:
    def __init__(self) -> None:
        self.ctx = SimpleNamespace(api=FakeRetryApi(), store=SimpleNamespace())
        self.spec: DispatchSpec | None = None

    @staticmethod
    def _session_source(session: SessionWire):
        context = session.source_context
        assert context is not None
        assert context.github_repo_context is not None
        return (
            context.source,
            context.github_repo_context.starting_branch,
            context.working_branch,
        )

    def dispatch(self, spec: DispatchSpec) -> dict[str, object]:
        self.spec = spec
        return {"outcome": "created", "session_id": "new"}


def test_retry_builds_explicit_new_dispatch_without_mutating_original() -> None:
    controller = FakeRetryController()
    client = JulesClient(controller)  # type: ignore[arg-type]
    result = client.retry_session("old", dispatch_key="retry:old:1")
    assert result["retry_of_session_id"] == "old"
    assert result["session_id"] == "new"
    assert controller.spec is not None
    assert controller.spec.dispatch_key == "retry:old:1"
    assert controller.spec.repo == "acme/repo"
    assert controller.spec.starting_branch == "main"
    assert controller.spec.auto_create_pr is True
    assert controller.spec.require_plan_approval is True
    assert controller.spec.source_refs == ["sessions/old"]
