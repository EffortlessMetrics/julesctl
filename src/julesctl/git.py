from __future__ import annotations

import re
import shutil
import subprocess  # nosec B404
from pathlib import Path
from urllib.parse import urlsplit

from .domain.errors import InputError

_SCP_GITHUB = re.compile(r"^(?:[^@/]+@)?github\.com:(?P<path>[^?#]+)$", re.IGNORECASE)


def _normalize_repo_path(path: str) -> str:
    candidate = path.strip().strip("/")
    if candidate.endswith(".git"):
        candidate = candidate[:-4]
    parts = candidate.split("/")
    if len(parts) != 2 or not all(parts):
        raise InputError(f"GitHub remote must identify owner/repo: {path!r}")
    return f"{parts[0]}/{parts[1]}"


def parse_github_remote(remote: str) -> str:
    """Return canonical owner/repo from common GitHub remote URL forms."""

    value = remote.strip()
    if not value:
        raise InputError("Git remote URL is empty")
    scp = _SCP_GITHUB.fullmatch(value)
    if scp:
        return _normalize_repo_path(scp.group("path"))

    parsed = urlsplit(value)
    if parsed.hostname and parsed.hostname.casefold() == "github.com":
        return _normalize_repo_path(parsed.path)
    raise InputError("origin is not a GitHub repository")


def _run_git(args: list[str], *, cwd: Path) -> str:
    git_executable = shutil.which("git")
    if git_executable is None:
        raise InputError("git executable was not found on PATH")
    try:
        completed = subprocess.run(  # nosec B603
            [git_executable, *args],
            cwd=cwd,
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
            shell=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise InputError(f"unable to run git: {exc}") from exc
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or "git command failed"
        raise InputError(detail)
    return completed.stdout.strip()


def infer_github_repo(*, cwd: Path | None = None) -> str:
    root = (cwd or Path.cwd()).resolve()
    return parse_github_remote(_run_git(["remote", "get-url", "origin"], cwd=root))


def infer_branch(*, cwd: Path | None = None) -> str:
    root = (cwd or Path.cwd()).resolve()
    branch = _run_git(["branch", "--show-current"], cwd=root)
    if not branch:
        raise InputError("Git is in detached HEAD state; pass --branch or --default-branch")
    return branch
