from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Annotated

import typer

from ..application.specs import derive_title, read_prompt
from ..client import JulesClient
from ..domain.errors import ApiError, JulesCtlError
from ..domain.models import DispatchSpec
from ..git import infer_branch, infer_github_repo
from .output import console, emit_json, emit_jsonl, err_console, operation


def _error_details(exc: Exception) -> dict[str, object]:
    value: dict[str, object] = {
        "kind": exc.__class__.__name__,
        "message": str(exc),
    }
    if isinstance(exc, ApiError):
        value["http_status"] = exc.http_status
        value["api_status"] = exc.api_status
        value["reconcile_required"] = exc.create_outcome_uncertain
    return value


def _fail(command: str, exc: Exception, *, machine: bool) -> None:
    if machine:
        emit_json(
            {
                "schema": "julesctl.operation.v1",
                "operation_id": str(uuid.uuid4()),
                "command": command,
                "outcome": "error",
                "error": _error_details(exc),
            }
        )
    else:
        err_console.print(f"[red]{exc}[/red]")
    raise typer.Exit(getattr(exc, "exit_code", 2)) from exc


def _dispatch_one(spec: DispatchSpec) -> dict[str, object]:
    with JulesClient.from_env() as client:
        return client.dispatch(spec)


def new_session(
    prompt: Annotated[str | None, typer.Argument(help="Prompt text, or '-' for stdin")] = None,
    file: Annotated[
        Path | None,
        typer.Option("--file", exists=True, dir_okay=False, readable=True),
    ] = None,
    repo: Annotated[str | None, typer.Option("--repo")] = None,
    branch: Annotated[str | None, typer.Option("--branch")] = None,
    repoless: Annotated[bool, typer.Option("--repoless")] = False,
    default_branch: Annotated[bool, typer.Option("--default-branch")] = False,
    title: Annotated[str | None, typer.Option("--title")] = None,
    auto_pr: Annotated[bool, typer.Option("--auto-pr/--no-auto-pr")] = True,
    require_plan_approval: Annotated[
        bool,
        typer.Option("--require-plan-approval", "--require-approval"),
    ] = False,
    dispatch_key: Annotated[str | None, typer.Option("--dispatch-key")] = None,
    parallel: Annotated[int, typer.Option("--parallel", min=1, max=100)] = 1,
    json_output: Annotated[bool, typer.Option("--json")] = False,
    jsonl: Annotated[bool, typer.Option("--jsonl")] = False,
) -> None:
    """Create one or more bounded Jules sessions from a deterministic task packet."""

    machine = json_output or jsonl
    try:
        if json_output and jsonl:
            raise ValueError("--json and --jsonl are mutually exclusive")
        task = read_prompt(prompt, file=file)
        if repoless:
            if repo is not None or branch is not None or default_branch:
                raise ValueError("--repoless cannot be combined with repository or branch options")
            resolved_repo = None
            resolved_branch = None
        else:
            resolved_repo = repo or infer_github_repo()
            if branch is not None and default_branch:
                raise ValueError("--branch and --default-branch are mutually exclusive")
            resolved_branch = None if default_branch else branch or infer_branch()

        root_key = dispatch_key or f"manual:{uuid.uuid4()}"
        session_title = title or derive_title(task)
        specs = [
            DispatchSpec(
                dispatch_key=(root_key if parallel == 1 else f"{root_key}:parallel:{index + 1}"),
                repo=resolved_repo,
                starting_branch=resolved_branch,
                title=session_title,
                prompt=task,
                auto_create_pr=auto_pr,
                require_plan_approval=require_plan_approval,
                caller="cli",
            )
            for index in range(parallel)
        ]

        items: list[dict[str, object] | None] = [None] * parallel
        max_workers = min(parallel, 8)
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(_dispatch_one, spec): index for index, spec in enumerate(specs)
            }
            for future in as_completed(futures):
                index = futures[future]
                try:
                    result = future.result()
                except JulesCtlError as exc:
                    items[index] = {
                        "index": index,
                        "dispatch_key": specs[index].dispatch_key,
                        "outcome": "error",
                        "error": _error_details(exc),
                    }
                else:
                    items[index] = {
                        "index": index,
                        "dispatch_key": specs[index].dispatch_key,
                        **result,
                    }

        resolved_items = [item for item in items if item is not None]
        partial = any(item.get("outcome") == "error" for item in resolved_items)
        data = {
            "root_dispatch_key": root_key,
            "repo": resolved_repo,
            "branch": resolved_branch,
            "parallel": parallel,
            "items": resolved_items,
        }
        if jsonl:
            emit_jsonl(
                [
                    {**item, "schema": "julesctl.dispatch-result.v1"}
                    for item in resolved_items
                ]
            )
        elif json_output:
            emit_json(operation("new", "partial" if partial else "completed", data))
        else:
            console.print(data)
        if partial:
            raise typer.Exit(6)
    except typer.Exit:
        raise
    except (JulesCtlError, ValueError) as exc:
        _fail("new", exc, machine=machine)
