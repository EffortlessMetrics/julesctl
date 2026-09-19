from __future__ import annotations

import json

from typer.testing import CliRunner

import julesctl.cli.agent as agent_module
from julesctl.cli.app import app

runner = CliRunner()


class FakeActivity:
    def __init__(self, activity_id: str) -> None:
        self.activity_id = activity_id

    def model_dump(self, **_: object) -> dict[str, object]:
        return {"name": f"activities/{self.activity_id}", "id": self.activity_id}


class FakeClient:
    def __enter__(self):
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def list_sessions(self, **kwargs):
        self.list_kwargs = kwargs
        return [
            {
                "id": "1",
                "raw_state": "IN_PROGRESS",
                "lifecycle": "executing",
                "title": "task",
            }
        ]

    def get_session(self, session_id: str):
        return {"id": session_id, "raw_state": "IN_PROGRESS"}

    def iter_activities(self, session_id: str):
        return [FakeActivity("a")]

    def watch(self, session_id: str, **_: object):
        return iter(
            [
                {"type": "state", "session_id": session_id, "state": "IN_PROGRESS"},
                {"type": "terminal", "session_id": session_id, "state": "COMPLETED"},
            ]
        )

    def send_message(self, session_id: str, prompt: str):
        return {"outcome": "completed", "session_id": session_id, "prompt": prompt}

    def approve_plan(self, session_id: str):
        return {"outcome": "completed", "session_id": session_id}


def _fake(monkeypatch):
    client = FakeClient()
    monkeypatch.setattr(
        agent_module.JulesClient,
        "from_env",
        classmethod(lambda cls: client),
    )
    return client


def test_ls_jsonl_has_stable_schema(monkeypatch) -> None:
    _fake(monkeypatch)
    result = runner.invoke(app, ["ls", "--active", "--jsonl"])
    assert result.exit_code == 0, result.output
    row = json.loads(result.stdout)
    assert row["schema"] == "julesctl.session.v1"
    assert row["id"] == "1"


def test_watch_jsonl_streams_records(monkeypatch) -> None:
    _fake(monkeypatch)
    result = runner.invoke(app, ["watch", "1", "--jsonl", "--poll-interval", "0"])
    assert result.exit_code == 0, result.output
    rows = [json.loads(line) for line in result.stdout.splitlines()]
    assert [row["type"] for row in rows] == ["state", "terminal"]
    assert all(row["schema"] == "julesctl.event.v1" for row in rows)


def test_activities_and_message_aliases(monkeypatch) -> None:
    _fake(monkeypatch)
    activities = runner.invoke(app, ["activities", "1", "--json"])
    assert activities.exit_code == 0, activities.output
    assert json.loads(activities.stdout)["data"]["items"][0]["id"] == "a"

    message = runner.invoke(app, ["msg", "1", "continue", "--json"])
    assert message.exit_code == 0, message.output
    assert json.loads(message.stdout)["data"]["prompt"] == "continue"


def test_show_and_approve_aliases(monkeypatch) -> None:
    _fake(monkeypatch)
    shown = runner.invoke(app, ["show", "1", "--json"])
    assert shown.exit_code == 0, shown.output
    assert json.loads(shown.stdout)["data"]["id"] == "1"

    approved = runner.invoke(app, ["approve", "1", "--json"])
    assert approved.exit_code == 0, approved.output
    assert json.loads(approved.stdout)["data"]["outcome"] == "completed"
