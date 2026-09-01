from pathlib import Path

import httpx

from julesctl.api.client import JulesApiClient
from julesctl.config import Settings
from julesctl.controller import JulesController


def test_drain_applies_exact_snapshot_not_new_session(tmp_path: Path) -> None:
    deleted: list[str] = []
    phase = {"after_plan": False}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path.endswith("/sessions"):
            sessions = [{"name": "sessions/1", "id": "1", "state": "IN_PROGRESS"}]
            if phase["after_plan"]:
                sessions.append(
                    {"name": "sessions/2", "id": "2", "state": "IN_PROGRESS"}
                )
            return httpx.Response(200, json={"sessions": sessions})
        if request.method == "DELETE":
            deleted.append(request.url.path.rsplit("/", 1)[-1])
            return httpx.Response(200, json={})
        raise AssertionError((request.method, str(request.url)))

    api = JulesApiClient(
        "key", base_url="https://test", transport=httpx.MockTransport(handler)
    )
    settings = Settings(
        api_key="key",
        base_url="https://test",
        database_path=tmp_path / "state.db",
    )
    ctl = JulesController.from_settings(settings, api=api)
    try:
        plan = ctl.create_drain_plan()
        phase["after_plan"] = True
        ctl.apply_deletion_plan(str(plan["plan_id"]))
        assert deleted == ["1"]
    finally:
        ctl.close()
