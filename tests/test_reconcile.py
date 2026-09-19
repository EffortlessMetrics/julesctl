from pathlib import Path

import httpx

from julesctl.api.client import JulesApiClient
from julesctl.application.reconcile import reconcile_session_activities
from julesctl.store import StateStore


def _activity(activity_id: str, create_time: str, *, event: str = "progressUpdated") -> dict:
    return {
        "name": f"sessions/1/activities/{activity_id}",
        "id": activity_id,
        "createTime": create_time,
        event: {"title": activity_id},
    }


def test_incremental_reconcile_uses_overlap_and_deduplicates(tmp_path: Path) -> None:
    calls = 0
    observed_cursors: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        observed_cursors.append(request.url.params.get("createTime"))
        if calls == 1:
            activities = [_activity("a", "2026-09-19T01:00:00.000000000Z")]
        else:
            activities = [
                _activity("a", "2026-09-19T01:00:00.000000000Z"),
                _activity("b", "2026-09-19T01:00:00.000000000Z"),
                _activity("c", "2026-09-19T01:00:01.000000000Z"),
            ]
        return httpx.Response(200, json={"activities": activities})

    api = JulesApiClient("k", base_url="https://test", transport=httpx.MockTransport(handler))
    store = StateStore(tmp_path / "state.db")
    try:
        first = reconcile_session_activities(api, store, "1", origin="managed")
        second = reconcile_session_activities(api, store, "1", origin="managed")
        assert [item["activity_id"] for item in first] == ["a"]
        assert [item["activity_id"] for item in second] == ["b", "c"]
        assert observed_cursors[0] is None
        assert observed_cursors[1] is not None
        assert "2026-09-19T00:59:55" in str(observed_cursors[1])
        assert store.activity_cursor("1") == "2026-09-19T01:00:01.000000000Z"
    finally:
        store.close()
        api.close()


def test_filter_rejection_falls_back_to_complete_history(tmp_path: Path) -> None:
    phase = 0
    cursors: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal phase
        value = request.url.params.get("createTime")
        cursors.append(value)
        if phase == 0:
            phase = 1
            return httpx.Response(
                200,
                json={"activities": [_activity("a", "2026-09-19T01:00:00Z")]},
            )
        if value is not None:
            return httpx.Response(
                400,
                json={"error": {"status": "INVALID_ARGUMENT", "message": "bad filter"}},
            )
        return httpx.Response(
            200,
            json={
                "activities": [
                    _activity("a", "2026-09-19T01:00:00Z"),
                    _activity("b", "2026-09-19T01:00:02Z", event="agentMessaged"),
                ]
            },
        )

    api = JulesApiClient("k", base_url="https://test", transport=httpx.MockTransport(handler))
    store = StateStore(tmp_path / "state.db")
    try:
        reconcile_session_activities(api, store, "1", origin="external")
        events = reconcile_session_activities(api, store, "1", origin="external")
        assert [item["activity_id"] for item in events] == ["b"]
        assert events[0]["activity_filter_fallback"] is True
        assert cursors[-2] is not None
        assert cursors[-1] is None
    finally:
        store.close()
        api.close()


def test_cursor_does_not_advance_past_invalid_timestamp(tmp_path: Path) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "activities": [
                    _activity("bad", "not-a-timestamp"),
                    _activity("good", "2026-09-19T01:00:02.123456789Z"),
                ]
            },
        )

    api = JulesApiClient("k", base_url="https://test", transport=httpx.MockTransport(handler))
    store = StateStore(tmp_path / "state.db")
    try:
        events = reconcile_session_activities(api, store, "1", origin="managed")
        assert {item["activity_id"] for item in events} == {"bad", "good"}
        assert store.activity_cursor("1") == "2026-09-19T01:00:02.123456789Z"
    finally:
        store.close()
        api.close()
