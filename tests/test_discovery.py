import httpx
import pytest

from julesctl.discovery import fetch_discovery
from julesctl.domain.errors import ApiError


def test_discovery_http_failure_is_domain_error() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"error": {"status": "UNAVAILABLE"}})

    with pytest.raises(ApiError, match="HTTP 503"):
        fetch_discovery(transport=httpx.MockTransport(handler))


def test_discovery_invalid_json_is_domain_error() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not json")

    with pytest.raises(ApiError, match="not valid JSON"):
        fetch_discovery(transport=httpx.MockTransport(handler))


def test_discovery_non_object_is_domain_error() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[])

    with pytest.raises(ApiError, match="not an object"):
        fetch_discovery(transport=httpx.MockTransport(handler))
