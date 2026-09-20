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
        return {
            "name": f"activities/{self.activity_id}",
            "id": self.activity_id,
            "artifacts": [
                {
                    "media": {
                        "mimeType": "image/png",
                        "data": "aGVsbG8=",
                        "futureMediaField": "retained",
                    }
                }
            ],
        }


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

    def result(self, session_id: str):
        return {
            "session": {"id": session_id},
            "pull_requests": [{"url": "https://github.com/acme/repo/pull/1"}],
            "patches": [{"unidiff_patch": "diff --git a/a b/a\n"}],
            "bash_outputs": [],
            "media": [],
            "activities": [],
        }

    def patch(self, session_id: str, *, index: int = -1):
        return {
            "session_id": session_id,
            "index": index,
            "unidiff_patch": "diff --git a/a b/a\n",
        }

    def pull_request(self, session_id: str, *, index: int = -1):
        return {
            "session_id": session_id,
            "index": index,
            "url": "https://github.com/acme/repo/pull/1",
        }

    def remove_sessions(self, session_ids: list[str], *, max_workers: int = 4):
        return {
            "outcome": "completed",
            "deleted": len(session_ids),
            "already_absent": 0,
            "failed": [],
            "max_workers": max_workers,
        }

    def plan_prune(self, **kwargs):
        return {
            "outcome": "planned",
            "plan_id": "prune_1",
            "selector": kwargs,
            "targets": [{"session_id": "1"}],
        }

    def apply_prune(self, plan_id: str, **kwargs):
        return {
            "outcome": "completed",
            "plan_id": plan_id,
            "deleted": 1,
            "already_absent": 0,
            "failed": [],
            **kwargs,
        }

    def retry_session(self, session_id: str, *, dispatch_key: str, title=None):
        return {
            "outcome": "created",
            "retry_of_session_id": session_id,
            "dispatch_key": dispatch_key,
            "title": title,
        }


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
    item = json.loads(activities.stdout)["data"]["items"][0]
    assert item["id"] == "a"
    media = item["artifacts"][0]["media"]
    assert "data" not in media
    assert media == {
        "mimeType": "image/png",
        "futureMediaField": "retained",
        "inlineDataOmitted": True,
        "decodedBytes": 5,
    }

    activities_jsonl = runner.invoke(app, ["activities", "1", "--jsonl"])
    assert activities_jsonl.exit_code == 0, activities_jsonl.output
    row = json.loads(activities_jsonl.stdout)
    assert "data" not in row["artifacts"][0]["media"]
    assert row["schema"] == "julesctl.activity.v1"

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


def test_result_patch_and_pr_commands(monkeypatch, tmp_path) -> None:
    _fake(monkeypatch)

    result = runner.invoke(app, ["result", "1", "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["data"]["session"]["id"] == "1"

    patch = runner.invoke(app, ["patch", "1"])
    assert patch.exit_code == 0, patch.output
    assert patch.stdout == "diff --git a/a b/a\n"

    output = tmp_path / "change.diff"
    saved = runner.invoke(app, ["patch", "1", "--output", str(output)])
    assert saved.exit_code == 0, saved.output
    assert saved.stdout == ""
    assert output.read_text(encoding="utf-8") == "diff --git a/a b/a\n"

    pr = runner.invoke(app, ["pr", "1", "--json"])
    assert pr.exit_code == 0, pr.output
    assert json.loads(pr.stdout)["data"]["url"].endswith("/pull/1")


def test_rm_prune_and_retry_commands(monkeypatch) -> None:
    _fake(monkeypatch)

    denied = runner.invoke(app, ["rm", "1", "--json"])
    assert denied.exit_code == 2
    assert json.loads(denied.stdout)["outcome"] == "error"

    removed = runner.invoke(app, ["rm", "1", "2", "--yes", "--json"])
    assert removed.exit_code == 0, removed.output
    assert json.loads(removed.stdout)["data"]["deleted"] == 2

    planned = runner.invoke(app, ["prune", "--nonterminal", "--json"])
    assert planned.exit_code == 0, planned.output
    assert json.loads(planned.stdout)["data"]["plan_id"] == "prune_1"

    applied = runner.invoke(
        app,
        ["prune", "--apply", "prune_1", "--yes", "--passes", "2", "--json"],
    )
    assert applied.exit_code == 0, applied.output
    assert json.loads(applied.stdout)["data"]["passes"] == 2

    retried = runner.invoke(
        app,
        ["retry", "old", "--dispatch-key", "retry:old:1", "--json"],
    )
    assert retried.exit_code == 0, retried.output
    assert json.loads(retried.stdout)["data"]["retry_of_session_id"] == "old"
