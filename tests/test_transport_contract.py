from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest

from julesctl.api.client import JulesApiClient
from julesctl.domain.errors import ApiError


def _client(
    handler: httpx.MockTransport,
    *,
    sleeps: list[float] | None = None,
    now: datetime | None = None,
) -> JulesApiClient:
    return JulesApiClient(
        "key",
        base_url="https://test",
        transport=handler,
        sleep=(sleeps.append if sleeps is not None else lambda _seconds: None),
        random_value=lambda: 0.0,
        now=(lambda: now) if now is not None else lambda: datetime.now(UTC),
    )


def test_read_honors_delta_retry_after() -> None:
    calls = 0
    sleeps: list[float] = []

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(
                429,
                headers={"Retry-After": "3"},
                json={"error": {"status": "RESOURCE_EXHAUSTED"}},
            )
        return httpx.Response(200, json={"sessions": []})

    api = _client(httpx.MockTransport(handler), sleeps=sleeps)
    try:
        assert list(api.iter_sessions()) == []
        assert sleeps == [3.0]
        assert calls == 2
    finally:
        api.close()


def test_read_honors_http_date_retry_after() -> None:
    calls = 0
    sleeps: list[float] = []
    now = datetime(2026, 9, 19, 3, 0, 0, tzinfo=UTC)

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(
                503,
                headers={"Retry-After": "Sat, 19 Sep 2026 03:00:05 GMT"},
                json={"error": {"status": "UNAVAILABLE"}},
            )
        return httpx.Response(200, json={"sessions": []})

    api = _client(httpx.MockTransport(handler), sleeps=sleeps, now=now)
    try:
        assert list(api.iter_sessions()) == []
        assert sleeps == [5.0]
    finally:
        api.close()


def test_read_retries_transport_failure_with_backoff() -> None:
    calls = 0
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise httpx.ReadTimeout("lost response", request=request)
        return httpx.Response(200, json={"sessions": []})

    api = _client(httpx.MockTransport(handler), sleeps=sleeps)
    try:
        assert list(api.iter_sessions()) == []
        assert sleeps == [0.25]
    finally:
        api.close()


def test_non_retryable_read_fails_without_sleep() -> None:
    calls = 0
    sleeps: list[float] = []

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            400,
            json={"error": {"status": "INVALID_ARGUMENT", "message": "bad"}},
        )

    api = _client(httpx.MockTransport(handler), sleeps=sleeps)
    try:
        with pytest.raises(ApiError, match="bad"):
            list(api.iter_sessions())
        assert calls == 1
        assert sleeps == []
    finally:
        api.close()


def test_create_is_never_replayed_by_transport_adapter() -> None:
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(503, json={"error": {"status": "UNAVAILABLE"}})

    api = _client(httpx.MockTransport(handler))
    try:
        with pytest.raises(ApiError):
            api.create_session({"prompt": "one attempt"})
        assert calls == 1
    finally:
        api.close()


def test_delete_retry_can_resolve_to_already_absent() -> None:
    calls = 0
    sleeps: list[float] = []

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(503, json={"error": {"status": "UNAVAILABLE"}})
        return httpx.Response(404, json={"error": {"status": "NOT_FOUND"}})

    api = _client(httpx.MockTransport(handler), sleeps=sleeps)
    try:
        assert api.delete_session("sessions/123") is False
        assert sleeps == [0.25]
    finally:
        api.close()


def test_activity_cursor_uses_documented_create_time_parameter() -> None:
    observed: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        observed.append(request)
        return httpx.Response(200, json={"activities": []})

    api = _client(httpx.MockTransport(handler))
    try:
        assert (
            list(
                api.iter_activities(
                    "sessions/123",
                    create_time="2026-09-19T03:00:00Z",
                )
            )
            == []
        )
        assert observed[0].url.params["createTime"] == "2026-09-19T03:00:00Z"
        assert "filter" not in observed[0].url.params
    finally:
        api.close()


def test_session_list_does_not_send_undocumented_filter() -> None:
    observed: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        observed.append(request)
        return httpx.Response(200, json={"sessions": []})

    api = _client(httpx.MockTransport(handler))
    try:
        assert list(api.iter_sessions()) == []
        assert "filter" not in observed[0].url.params
    finally:
        api.close()


def test_get_activity_accepts_full_resource_name() -> None:
    observed: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        observed.append(request.url.path)
        return httpx.Response(
            200,
            json={"name": "sessions/123/activities/456", "id": "456"},
        )

    api = _client(httpx.MockTransport(handler))
    try:
        activity = api.get_activity("sessions/123", "sessions/123/activities/456")
        assert activity.id == "456"
        assert observed == ["/sessions/123/activities/456"]
    finally:
        api.close()


def test_retry_delay_is_capped() -> None:
    calls = 0
    sleeps: list[float] = []

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(429, headers={"Retry-After": "999"})
        return httpx.Response(200, json={"sessions": []})

    api = JulesApiClient(
        "key",
        base_url="https://test",
        transport=httpx.MockTransport(handler),
        sleep=sleeps.append,
        random_value=lambda: 0.0,
        max_retry_delay_seconds=7.0,
    )
    try:
        assert list(api.iter_sessions()) == []
        assert sleeps == [7.0]
    finally:
        api.close()


@pytest.mark.parametrize("value", [0.0, -1.0])
def test_timeout_must_be_positive(value: float) -> None:
    with pytest.raises(ValueError, match="timeout_seconds"):
        JulesApiClient("key", timeout_seconds=value)
