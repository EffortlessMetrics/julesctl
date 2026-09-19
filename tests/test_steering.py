import httpx
import pytest

from julesctl.api.client import JulesApiClient
from julesctl.application.steering import approve_once, archive_once, message_once
from julesctl.domain.errors import IndeterminateError


def test_message_lost_response_reconciles_only_new_activity() -> None:
    posted = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal posted
        if request.method == "POST":
            posted = True
            return httpx.Response(503, json={"error": {"status": "UNAVAILABLE"}})
        activities = [
            {
                "name": "sessions/1/activities/old",
                "id": "old",
                "userMessaged": {"userMessage": "continue"},
            }
        ]
        if posted:
            activities.append(
                {
                    "name": "sessions/1/activities/new",
                    "id": "new",
                    "userMessaged": {"userMessage": "continue"},
                }
            )
        return httpx.Response(200, json={"activities": activities})

    api = JulesApiClient("k", base_url="https://test", transport=httpx.MockTransport(handler))
    try:
        result = message_once(api, "1", "continue")
        assert result["outcome"] == "reconciled"
    finally:
        api.close()


def test_historical_equal_message_does_not_prove_delivery() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(503, json={"error": {"status": "UNAVAILABLE"}})
        return httpx.Response(
            200,
            json={
                "activities": [
                    {
                        "name": "sessions/1/activities/old",
                        "id": "old",
                        "userMessaged": {"userMessage": "continue"},
                    }
                ]
            },
        )

    api = JulesApiClient("k", base_url="https://test", transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(IndeterminateError, match="new matching"):
            message_once(api, "1", "continue")
    finally:
        api.close()


def test_approval_lost_response_reconciles_from_new_activity() -> None:
    posted = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal posted
        if request.method == "POST":
            posted = True
            return httpx.Response(503, json={"error": {"status": "UNAVAILABLE"}})
        if request.url.path.endswith("/sessions/1"):
            state = "AWAITING_PLAN_APPROVAL"
            return httpx.Response(200, json={"name": "sessions/1", "id": "1", "state": state})
        activities = [{"name": "sessions/1/activities/plan", "id": "plan", "planGenerated": {}}]
        if posted:
            activities.append(
                {"name": "sessions/1/activities/approved", "id": "approved", "planApproved": {}}
            )
        return httpx.Response(200, json={"activities": activities})

    api = JulesApiClient("k", base_url="https://test", transport=httpx.MockTransport(handler))
    try:
        assert approve_once(api, "1")["outcome"] == "reconciled"
    finally:
        api.close()


def test_historical_plan_approval_does_not_prove_new_approval() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(503, json={"error": {"status": "UNAVAILABLE"}})
        if request.url.path.endswith("/sessions/1"):
            return httpx.Response(
                200,
                json={"name": "sessions/1", "id": "1", "state": "AWAITING_PLAN_APPROVAL"},
            )
        return httpx.Response(
            200,
            json={
                "activities": [
                    {
                        "name": "sessions/1/activities/old-approved",
                        "id": "old-approved",
                        "planApproved": {},
                    }
                ]
            },
        )

    api = JulesApiClient("k", base_url="https://test", transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(IndeterminateError, match="approval outcome"):
            approve_once(api, "1")
    finally:
        api.close()


def test_approval_is_noop_when_session_already_moved() -> None:
    posts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal posts
        if request.method == "POST":
            posts += 1
            return httpx.Response(200, json={})
        return httpx.Response(200, json={"name": "sessions/1", "id": "1", "state": "IN_PROGRESS"})

    api = JulesApiClient("k", base_url="https://test", transport=httpx.MockTransport(handler))
    try:
        result = approve_once(api, "1")
        assert result["outcome"] == "existing"
        assert posts == 0
    finally:
        api.close()


def test_archive_is_noop_when_already_archived() -> None:
    posts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal posts
        if request.method == "POST":
            posts += 1
        return httpx.Response(
            200,
            json={"name": "sessions/1", "id": "1", "state": "COMPLETED", "archived": True},
        )

    api = JulesApiClient("k", base_url="https://test", transport=httpx.MockTransport(handler))
    try:
        result = archive_once(api, "1")
        assert result["outcome"] == "existing"
        assert posts == 0
    finally:
        api.close()


def test_approval_lost_response_reconciles_from_state_transition_only() -> None:
    posted = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal posted
        if request.method == "POST":
            posted = True
            return httpx.Response(503, json={"error": {"status": "UNAVAILABLE"}})
        if request.url.path.endswith("/sessions/1"):
            state = "IN_PROGRESS" if posted else "AWAITING_PLAN_APPROVAL"
            return httpx.Response(
                200,
                json={"name": "sessions/1", "id": "1", "state": state},
            )
        return httpx.Response(
            200,
            json={
                "activities": [
                    {
                        "name": "sessions/1/activities/plan",
                        "id": "plan",
                        "planGenerated": {},
                    }
                ]
            },
        )

    api = JulesApiClient(
        "k",
        base_url="https://test",
        transport=httpx.MockTransport(handler),
    )
    try:
        result = approve_once(api, "1")
        assert result["outcome"] == "reconciled"
        assert result["observed_state"] == "IN_PROGRESS"
    finally:
        api.close()
