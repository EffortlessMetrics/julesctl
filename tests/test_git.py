from pathlib import Path
from types import SimpleNamespace

import pytest

import julesctl.git as git_module
from julesctl.domain.errors import InputError
from julesctl.git import infer_branch, infer_github_repo, parse_github_remote


@pytest.mark.parametrize(
    ("remote", "expected"),
    [
        ("https://github.com/EffortlessMetrics/julesctl.git", "EffortlessMetrics/julesctl"),
        ("git@github.com:EffortlessMetrics/julesctl.git", "EffortlessMetrics/julesctl"),
        ("ssh://git@github.com/EffortlessMetrics/julesctl.git", "EffortlessMetrics/julesctl"),
        ("git://github.com/EffortlessMetrics/julesctl", "EffortlessMetrics/julesctl"),
    ],
)
def test_parse_github_remote(remote: str, expected: str) -> None:
    assert parse_github_remote(remote) == expected


@pytest.mark.parametrize(
    "remote",
    [
        "https://gitlab.com/acme/repo.git",
        "git@example.com:acme/repo.git",
        "https://github.com/owner/too/many",
        "",
    ],
)
def test_parse_github_remote_rejects_non_github_or_malformed(remote: str) -> None:
    with pytest.raises(InputError):
        parse_github_remote(remote)


def test_infer_repo_uses_origin(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    observed: list[list[str]] = []

    def run(command: list[str], **_: object) -> SimpleNamespace:
        observed.append(command)
        return SimpleNamespace(returncode=0, stdout="git@github.com:acme/repo.git\n", stderr="")

    monkeypatch.setattr(git_module.subprocess, "run", run)
    assert infer_github_repo(cwd=tmp_path) == "acme/repo"
    assert observed == [["git", "remote", "get-url", "origin"]]


def test_infer_branch_rejects_detached_head(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        git_module.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout="", stderr=""),
    )
    with pytest.raises(InputError, match="detached HEAD"):
        infer_branch(cwd=tmp_path)
