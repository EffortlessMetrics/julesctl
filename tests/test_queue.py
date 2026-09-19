from pathlib import Path
from types import SimpleNamespace

import pytest

from julesctl.application.queue import enqueue_candidate, load_candidate, run_worker_once
from julesctl.config import Settings
from julesctl.controller import JulesController
from julesctl.domain.errors import InputError
from julesctl.domain.models import DispatchSpec
from julesctl.store import StateStore


def _spec(key: str, *, repo: str = "acme/repo") -> DispatchSpec:
    return DispatchSpec(
        dispatch_key=key,
        repo=repo,
        starting_branch="main",
        title="Bounded task",
        prompt="Do the bounded task and verify it.",
    )


def test_candidate_file_rejects_symlink(tmp_path: Path) -> None:
    target = tmp_path / "candidate.json"
    target.write_text(_spec("one").model_dump_json(by_alias=True), encoding="utf-8")
    link = tmp_path / "candidate-link.json"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symlinks are unavailable on this platform")
    with pytest.raises(InputError, match="symlink"):
        load_candidate(link)


def test_duplicate_dispatch_key_requires_identical_candidate(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.db")
    try:
        first = enqueue_candidate(store, _spec("same"))
        second = enqueue_candidate(store, _spec("same"))
        assert first["outcome"] == "queued"
        assert second["outcome"] == "existing"
        changed = _spec("same").model_copy(update={"title": "Changed"})
        with pytest.raises(InputError, match="different candidate"):
            enqueue_candidate(store, changed)
    finally:
        store.close()


def test_claim_release_and_complete_are_transactional(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.db")
    try:
        queued = enqueue_candidate(store, _spec("one"))
        candidate_id = str(queued["candidate_id"])
        claimed = store.claim_candidates(limit=1, worker_id="worker")
        assert [row["candidate_id"] for row in claimed] == [candidate_id]
        assert store.claim_candidates(limit=1, worker_id="other") == []
        store.release_candidate(candidate_id)
        assert len(store.claim_candidates(limit=1, worker_id="other")) == 1
        store.finish_candidate(candidate_id, state="COMPLETED", outcome={"outcome": "created"})
        assert store.candidate_counts() == {"COMPLETED": 1}
    finally:
        store.close()


def test_worker_enforces_repo_allowlist_and_records_results(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(api_key="key", database_path=tmp_path / "state.db")
    store = StateStore(settings.database_path)
    try:
        enqueue_candidate(store, _spec("allowed", repo="acme/repo"))
        enqueue_candidate(store, _spec("blocked", repo="other/repo"))
    finally:
        store.close()

    dispatched: list[str] = []

    class FakeController:
        def __init__(self) -> None:
            self.ctx = SimpleNamespace(store=StateStore(settings.database_path))

        def __enter__(self):
            return self

        def __exit__(self, *_: object) -> None:
            self.ctx.store.close()

        def dispatch(self, spec: DispatchSpec) -> dict[str, object]:
            dispatched.append(spec.dispatch_key)
            return {"outcome": "created", "session_id": "123"}

    monkeypatch.setattr(
        JulesController,
        "from_settings",
        classmethod(lambda cls, _settings: FakeController()),
    )

    outcomes = run_worker_once(
        settings,
        max_items=10,
        allow_repos=["acme/repo"],
        allow_repoless=False,
    )
    assert dispatched == ["allowed"]
    assert [item["outcome"] for item in outcomes] == ["created", "rejected_policy"]

    check = StateStore(settings.database_path)
    try:
        assert check.candidate_counts() == {"COMPLETED": 1, "REJECTED_POLICY": 1}
    finally:
        check.close()
