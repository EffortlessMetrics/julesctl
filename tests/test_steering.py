import httpx

from julesctl.api.client import JulesApiClient
from julesctl.application.steering import approve_once, message_once


def test_message_lost_response_reconciles_from_activity() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        if request.method == "POST":
            calls += 1
            return httpx.Response(503, json={"error": {"status": "UNAVAILABLE"}})
        return httpx.Response(
            200,
            json={
                "activities": [
                    {
                        "name": "sessions/1/activities/a",
                        "id": "a",
                        "userMessaged": {"userMessage": "continue"},
                    }
                ]
            },
        )

    api = JulesApiClient("k", base_url="https://test", transport=httpx.MockTransport(handler))
    try:
        result = message_once(api, "1", "continue")
        assert result["outcome"] == "reconciled"
        assert calls == 1
    finally:
        api.close()


def test_approval_lost_response_reconciles_from_state() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(503, json={"error": {"status": "UNAVAILABLE"}})
        if request.url.path.endswith("/sessions/1"):
            return httpx.Response(
                200,
                json={"name": "sessions/1", "id": "1", "state": "IN_PROGRESS"},
            )
        return httpx.Response(200, json={"activities": []})

    api = JulesApiClient("k", base_url="https://test", transport=httpx.MockTransport(handler))
    try:
        assert approve_once(api, "1")["outcome"] == "reconciled"
    finally:
        api.close()
