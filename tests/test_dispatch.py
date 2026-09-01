from pathlib import Path

import httpx

from julesctl.api.client import JulesApiClient
from julesctl.config import Settings
from julesctl.controller import JulesController
from julesctl.domain.models import DispatchSpec


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        api_key="key",
        base_url="https://test",
        database_path=tmp_path / "state.db",
    )


def test_failed_precondition_after_create_is_reconciled_without_second_post(
    tmp_path: Path,
) -> None:
    creates = 0
    session = {
        "name": "sessions/123",
        "id": "123",
        "title": "Fix parser",
        "prompt": "do it",
        "state": "IN_PROGRESS",
        "sourceContext": {
            "source": "sources/github/acme/repo",
            "githubRepoContext": {"startingBranch": "main"},
        },
    }

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal creates
        if request.method == "GET" and request.url.path.endswith("/sources"):
            return httpx.Response(
                200,
                json={
                    "sources": [
                        {
                            "name": "sources/github/acme/repo",
                            "id": "github/acme/repo",
                            "githubRepo": {
                                "owner": "acme",
                                "repo": "repo",
                                "defaultBranch": {"displayName": "main"},
                            },
                        }
                    ]
                },
            )
        if request.method == "POST" and request.url.path.endswith("/sessions"):
            creates += 1
            return httpx.Response(
                400,
                json={
                    "error": {
                        "code": 400,
                        "status": "FAILED_PRECONDITION",
                        "message": "Precondition check failed",
                    }
                },
            )
        if request.method == "GET" and request.url.path.endswith("/sessions"):
            return httpx.Response(200, json={"sessions": [session]})
        raise AssertionError((request.method, str(request.url)))

    api = JulesApiClient(
        "key", base_url="https://test", transport=httpx.MockTransport(handler)
    )
    ctl = JulesController.from_settings(_settings(tmp_path), api=api)
    try:
        result = ctl.dispatch(
            DispatchSpec(
                dispatch_key="github:acme/repo:issue:1",
                repo="acme/repo",
                starting_branch="main",
                title="Fix parser",
                prompt="do it",
            ),
            reconcile_delays=(0.0,),
        )
        assert result["outcome"] == "reconciled"
        assert creates == 1
    finally:
        ctl.close()


def test_same_dispatch_key_returns_existing_without_second_create(tmp_path: Path) -> None:
    creates = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal creates
        if request.method == "GET" and request.url.path.endswith("/sources"):
            return httpx.Response(
                200,
                json={
                    "sources": [
                        {
                            "name": "sources/github/acme/repo",
                            "id": "github/acme/repo",
                            "githubRepo": {
                                "owner": "acme",
                                "repo": "repo",
                                "defaultBranch": {"displayName": "main"},
                            },
                        }
                    ]
                },
            )
        if request.method == "POST" and request.url.path.endswith("/sessions"):
            creates += 1
            return httpx.Response(
                200,
                json={
                    "name": "sessions/9",
                    "id": "9",
                    "title": "Fix parser",
                    "prompt": "do it",
                    "state": "QUEUED",
                    "sourceContext": {
                        "source": "sources/github/acme/repo",
                        "githubRepoContext": {"startingBranch": "main"},
                    },
                },
            )
        raise AssertionError((request.method, str(request.url)))

    api = JulesApiClient(
        "key", base_url="https://test", transport=httpx.MockTransport(handler)
    )
    spec = DispatchSpec(
        dispatch_key="github:acme/repo:issue:1",
        repo="acme/repo",
        starting_branch="main",
        title="Fix parser",
        prompt="do it",
    )
    ctl = JulesController.from_settings(_settings(tmp_path), api=api)
    try:
        first = ctl.dispatch(spec, reconcile_delays=(0.0,))
        second = ctl.dispatch(spec, reconcile_delays=(0.0,))
        assert first["outcome"] == "created"
        assert second["outcome"] == "existing"
        assert creates == 1
    finally:
        ctl.close()
