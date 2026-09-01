import json

import httpx
import pytest

from julesctl.api.client import JulesApiClient
from julesctl.domain.errors import ApiError


def test_pagination_crosses_empty_page_and_deduplicates() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        token = request.url.params.get("pageToken")
        if not token:
            return httpx.Response(
                200,
                json={
                    "sessions": [{"name": "sessions/1", "id": "1"}],
                    "nextPageToken": "a",
                },
            )
        if token == "a":
            return httpx.Response(200, json={"sessions": [], "nextPageToken": "b"})
        return httpx.Response(
            200,
            json={
                "sessions": [
                    {"name": "sessions/1", "id": "1"},
                    {"name": "sessions/2", "id": "2"},
                ]
            },
        )

    client = JulesApiClient(
        "k", base_url="https://test", transport=httpx.MockTransport(handler)
    )
    try:
        assert [session.id for session in client.iter_sessions()] == ["1", "2"]
    finally:
        client.close()


def test_repeated_page_token_fails_closed() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"sessions": [], "nextPageToken": "same"})

    client = JulesApiClient(
        "k", base_url="https://test", transport=httpx.MockTransport(handler)
    )
    try:
        with pytest.raises(ApiError, match="repeated nextPageToken"):
            list(client.iter_sessions())
    finally:
        client.close()


def test_unknown_output_fields_are_retained() -> None:
    payload = {
        "name": "sessions/1",
        "id": "1",
        "state": "FUTURE_STATE",
        "futureField": {"x": 1},
        "outputs": [{"changeSet": {"source": "x"}, "futureOutput": True}],
    }

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=json.dumps(payload))

    client = JulesApiClient(
        "k", base_url="https://test", transport=httpx.MockTransport(handler)
    )
    try:
        session = client.get_session("1")
        assert session.state == "FUTURE_STATE"
        assert session.model_extra and session.model_extra["futureField"] == {"x": 1}
        assert session.outputs[0].model_extra
        assert session.outputs[0].model_extra["futureOutput"] is True
    finally:
        client.close()
