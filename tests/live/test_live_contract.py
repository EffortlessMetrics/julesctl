from __future__ import annotations

import os
import time
import uuid

import pytest

from julesctl.api.client import JulesApiClient
from julesctl.config import DEFAULT_BASE_URL

pytestmark = pytest.mark.live


def _require_live() -> str:
    if os.environ.get("JULES_LIVE_TEST") != "1":
        pytest.skip("set JULES_LIVE_TEST=1 to run Jules live probes")
    key = os.environ.get("JULES_API_KEY", "").strip()
    if not key:
        pytest.fail("JULES_API_KEY is required for live probes")
    return key


def _require_budget(starts: int) -> None:
    raw = os.environ.get("JULES_LIVE_TASK_BUDGET", "0")
    try:
        budget = int(raw)
    except ValueError as exc:
        pytest.fail(f"JULES_LIVE_TASK_BUDGET must be an integer: {exc}")
    if budget < starts:
        pytest.skip(f"live probe requires task budget {starts}; configured budget is {budget}")


def _client() -> JulesApiClient:
    return JulesApiClient(
        _require_live(),
        base_url=os.environ.get("JULES_API_BASE", DEFAULT_BASE_URL),
        timeout_seconds=float(os.environ.get("JULES_LIVE_HTTP_TIMEOUT_SECONDS", "30")),
    )


def _wait_terminal(api: JulesApiClient, session_id: str) -> str:
    timeout = float(os.environ.get("JULES_LIVE_TIMEOUT_SECONDS", "600"))
    deadline = time.monotonic() + timeout
    last_state = "STATE_UNSPECIFIED"
    while time.monotonic() < deadline:
        session = api.get_session(session_id)
        last_state = session.state or "STATE_UNSPECIFIED"
        if last_state in {"COMPLETED", "FAILED", "CANCELLED"}:
            return last_state
        time.sleep(10)
    pytest.fail(f"session {session_id} did not reach a terminal state; last state={last_state}")


def test_live_read_only_contract() -> None:
    """Verify authentication, source pagination, fleet pagination, and activities."""

    with _client() as api:
        sources = list(api.iter_sources())
        sessions = list(api.iter_sessions(filter_value="archived = true OR archived = false"))
        assert all(source.name for source in sources)
        assert all(session.id and session.name for session in sessions)
        if sessions:
            session = api.get_session(sessions[0].id)
            assert session.id == sessions[0].id
            list(api.iter_activities(session.id))


def test_live_repoless_lifecycle() -> None:
    """Create, observe, and delete one repoless session without repository effects."""

    _require_budget(1)
    marker = uuid.uuid4().hex
    with _client() as api:
        session = api.create_session(
            {
                "title": f"julesctl repoless live probe {marker[:8]}",
                "prompt": (
                    "Return a concise response containing the exact marker "
                    f"{marker}. Do not access or modify any repository."
                ),
            }
        )
        try:
            assert session.id
            state = _wait_terminal(api, session.id)
            assert state in {"COMPLETED", "FAILED", "CANCELLED"}
            activities = list(api.iter_activities(session.id))
            assert activities
        finally:
            api.delete_session(session.id)


def test_live_source_lifecycle() -> None:
    """Create and clean one source-backed session without automatic PR creation."""

    _require_budget(1)
    repo = os.environ.get("JULES_TEST_REPO", "").strip()
    if not repo:
        pytest.skip("JULES_TEST_REPO is required for the source-backed probe")
    branch = os.environ.get("JULES_TEST_BRANCH", "").strip()
    marker = uuid.uuid4().hex
    with _client() as api:
        source = api.resolve_source(repo)
        if not branch:
            if not source.github_repo or not source.github_repo.default_branch:
                pytest.fail("fixture source has no default branch and JULES_TEST_BRANCH is unset")
            branch = source.github_repo.default_branch.display_name
        session = api.create_session(
            {
                "title": f"julesctl source live probe {marker[:8]}",
                "prompt": (
                    "Inspect the repository and report its top-level structure plus "
                    f"the exact marker {marker}. Do not edit files, create a branch, "
                    "or open a pull request."
                ),
                "sourceContext": {
                    "source": source.name,
                    "githubRepoContext": {"startingBranch": branch},
                },
            }
        )
        try:
            assert session.id
            state = _wait_terminal(api, session.id)
            assert state in {"COMPLETED", "FAILED", "CANCELLED"}
            refreshed = api.get_session(session.id)
            assert refreshed.source_context is not None
            assert refreshed.source_context.source == source.name
            list(api.iter_activities(session.id))
        finally:
            api.delete_session(session.id)
