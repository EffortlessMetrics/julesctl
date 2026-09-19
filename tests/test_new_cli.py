from __future__ import annotations

import json
from typing import ClassVar

from typer.testing import CliRunner

import julesctl.cli.agent as agent_module
from julesctl.cli.app import app

runner = CliRunner()


class FakeClient:
    calls: ClassVar[list[object]] = []

    def __enter__(self):
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def dispatch(self, spec):
        self.calls.append(spec)
        return {"outcome": "created", "session_id": f"session-{spec.dispatch_key}"}


def _install_fakes(monkeypatch) -> None:
    FakeClient.calls = []
    monkeypatch.setattr(
        agent_module.JulesClient,
        "from_env",
        classmethod(lambda cls: FakeClient()),
    )
    monkeypatch.setattr(agent_module, "infer_github_repo", lambda: "acme/repo")
    monkeypatch.setattr(agent_module, "infer_branch", lambda: "feature")


def test_new_infers_git_context_and_emits_machine_envelope(monkeypatch) -> None:
    _install_fakes(monkeypatch)
    result = runner.invoke(
        app,
        ["new", "Implement the parser fix", "--dispatch-key", "issue:1", "--json"],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["command"] == "new"
    assert payload["data"]["repo"] == "acme/repo"
    assert payload["data"]["branch"] == "feature"
    spec = FakeClient.calls[0]
    assert spec.title == "Implement the parser fix"
    assert spec.dispatch_key == "issue:1"


def test_new_parallel_assigns_distinct_dispatch_keys(monkeypatch) -> None:
    _install_fakes(monkeypatch)
    result = runner.invoke(
        app,
        [
            "new",
            "Run independent attempt",
            "--dispatch-key",
            "work",
            "--parallel",
            "3",
            "--jsonl",
        ],
    )
    assert result.exit_code == 0, result.output
    rows = [json.loads(line) for line in result.stdout.splitlines()]
    assert len(rows) == 3
    assert {row["dispatch_key"] for row in rows} == {
        "work:parallel:1",
        "work:parallel:2",
        "work:parallel:3",
    }
    assert all(row["schema"] == "julesctl.dispatch-result.v1" for row in rows)


def test_new_repoless_rejects_repo(monkeypatch) -> None:
    _install_fakes(monkeypatch)
    result = runner.invoke(
        app,
        ["new", "task", "--repoless", "--repo", "acme/repo", "--json"],
    )
    assert result.exit_code == 2
    payload = json.loads(result.stdout)
    assert payload["outcome"] == "error"
    assert "repoless" in payload["error"]["message"]


def test_new_default_branch_does_not_infer_current_branch(monkeypatch) -> None:
    _install_fakes(monkeypatch)
    monkeypatch.setattr(
        agent_module,
        "infer_branch",
        lambda: (_ for _ in ()).throw(AssertionError("must not infer branch")),
    )
    result = runner.invoke(
        app,
        ["new", "task", "--repo", "acme/repo", "--default-branch", "--json"],
    )
    assert result.exit_code == 0, result.output
    assert FakeClient.calls[0].starting_branch is None
