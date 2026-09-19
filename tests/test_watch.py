from pathlib import Path

import httpx

from julesctl.api.client import JulesApiClient
from julesctl.application.watch import watch_session
from julesctl.store import StateStore


def test_watch_emits_state_activities_and_terminal_without_heartbeats(tmp_path: Path) -> None:
    session_reads = 0
    activity_reads = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal session_reads, activity_reads
        if "/activities" in request.url.path:
            activity_reads += 1
            activities = []
            if activity_reads == 1:
                activities = [
                    {
                        "name": "sessions/1/activities/a",
                        "id": "a",
                        "createTime": "2026-09-19T10:00:00Z",
                        "progressUpdated": {"title": "working"},
                    }
                ]
            elif activity_reads >= 2:
                activities = [
                    {
                        "name": "sessions/1/activities/a",
                        "id": "a",
                        "createTime": "2026-09-19T10:00:00Z",
                        "progressUpdated": {"title": "working"},
                    },
                    {
                        "name": "sessions/1/activities/b",
                        "id": "b",
                        "createTime": "2026-09-19T10:00:01Z",
                        "sessionCompleted": {},
                    },
                ]
            return httpx.Response(200, json={"activities": activities})
        session_reads += 1
        state = "IN_PROGRESS" if session_reads == 1 else "COMPLETED"
        return httpx.Response(
            200,
            json={"name": "sessions/1", "id": "1", "state": state},
        )

    api = JulesApiClient(
        "k",
        base_url="https://test",
        transport=httpx.MockTransport(handler),
    )
    store = StateStore(tmp_path / "state.db")
    sleeps: list[float] = []
    try:
        events = list(
            watch_session(
                api,
                store,
                "1",
                origin="managed",
                poll_interval_seconds=0.25,
                settle_seconds=0.5,
                sleep=sleeps.append,
            )
        )
        assert [item["type"] for item in events] == [
            "state",
            "progress_updated",
            "state",
            "session_completed",
            "terminal",
        ]
        assert sleeps == [0.25, 0.5]
        assert store.activity_cursor("1") == "2026-09-19T10:00:01Z"
    finally:
        store.close()
        api.close()


def test_watch_can_stop_after_bounded_polls(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "/activities" in request.url.path:
            return httpx.Response(200, json={"activities": []})
        return httpx.Response(
            200,
            json={"name": "sessions/1", "id": "1", "state": "IN_PROGRESS"},
        )

    api = JulesApiClient(
        "k",
        base_url="https://test",
        transport=httpx.MockTransport(handler),
    )
    store = StateStore(tmp_path / "state.db")
    try:
        events = list(
            watch_session(
                api,
                store,
                "1",
                origin="external",
                poll_interval_seconds=0,
                settle_seconds=0,
                max_polls=2,
            )
        )
        assert events == [
            {
                "type": "state",
                "session_id": "1",
                "state": "IN_PROGRESS",
                "lifecycle": "executing",
                "action_required": "none",
            }
        ]
    finally:
        store.close()
        api.close()
