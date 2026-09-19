from pathlib import Path

import httpx
import pytest

from julesctl.api.client import JulesApiClient
from julesctl.config import Settings
from julesctl.controller import JulesController
from julesctl.domain.errors import ApiError, InputError
from julesctl.domain.models import DispatchSpec


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        api_key="key",
        base_url="https://test",
        database_path=tmp_path / "state.db",
    )


def _source_payload() -> dict[str, object]:
    return {
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
    }


def _session_payload() -> dict[str, object]:
    return {
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


def test_failed_precondition_after_create_is_reconciled_without_second_post(
    tmp_path: Path,
) -> None:
    creates = 0
    created = False
    session = _session_payload()

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal creates, created
        if request.method == "GET" and request.url.path.endswith("/sources"):
            return httpx.Response(200, json=_source_payload())
        if request.method == "POST" and request.url.path.endswith("/sessions"):
            creates += 1
            created = True
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
            return httpx.Response(200, json={"sessions": [session] if created else []})
        if request.method == "GET" and request.url.path.endswith("/sessions/123"):
            return httpx.Response(200, json=session)
        raise AssertionError((request.method, str(request.url)))

    api = JulesApiClient("key", base_url="https://test", transport=httpx.MockTransport(handler))
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


def test_malformed_success_after_create_is_reconciled(tmp_path: Path) -> None:
    creates = 0
    created = False
    session = _session_payload()

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal creates, created
        if request.method == "GET" and request.url.path.endswith("/sources"):
            return httpx.Response(200, json=_source_payload())
        if request.method == "POST" and request.url.path.endswith("/sessions"):
            creates += 1
            created = True
            return httpx.Response(200, content=b"not-json")
        if request.method == "GET" and request.url.path.endswith("/sessions"):
            return httpx.Response(200, json={"sessions": [session] if created else []})
        if request.method == "GET" and request.url.path.endswith("/sessions/123"):
            return httpx.Response(200, json=session)
        raise AssertionError((request.method, str(request.url)))

    api = JulesApiClient("key", base_url="https://test", transport=httpx.MockTransport(handler))
    ctl = JulesController.from_settings(_settings(tmp_path), api=api)
    try:
        result = ctl.dispatch(
            DispatchSpec(
                dispatch_key="github:acme/repo:issue:2",
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
            return httpx.Response(200, json=_source_payload())
        if request.method == "GET" and request.url.path.endswith("/sessions"):
            return httpx.Response(200, json={"sessions": []})
        if request.method == "POST" and request.url.path.endswith("/sessions"):
            creates += 1
            return httpx.Response(200, json=_session_payload())
        raise AssertionError((request.method, str(request.url)))

    api = JulesApiClient("key", base_url="https://test", transport=httpx.MockTransport(handler))
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


def test_definitive_rejection_is_not_reconciled_on_repeat(tmp_path: Path) -> None:
    creates = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal creates
        if request.method == "GET" and request.url.path.endswith("/sources"):
            return httpx.Response(200, json=_source_payload())
        if request.method == "GET" and request.url.path.endswith("/sessions"):
            return httpx.Response(200, json={"sessions": []})
        if request.method == "POST" and request.url.path.endswith("/sessions"):
            creates += 1
            return httpx.Response(
                400,
                json={"error": {"status": "INVALID_ARGUMENT", "message": "bad request"}},
            )
        raise AssertionError((request.method, str(request.url)))

    api = JulesApiClient("key", base_url="https://test", transport=httpx.MockTransport(handler))
    spec = DispatchSpec(
        dispatch_key="github:acme/repo:issue:3",
        repo="acme/repo",
        starting_branch="main",
        title="Fix parser",
        prompt="do it",
    )
    ctl = JulesController.from_settings(_settings(tmp_path), api=api)
    try:
        with pytest.raises(ApiError, match="bad request"):
            ctl.dispatch(spec, reconcile_delays=(0.0,))
        with pytest.raises(InputError, match="definitively rejected"):
            ctl.dispatch(spec, reconcile_delays=(0.0,))
        assert creates == 1
    finally:
        ctl.close()
