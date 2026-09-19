from __future__ import annotations

import sys
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
                [{**item, "schema": "julesctl.dispatch-result.v1"} for item in resolved_items]
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


def list_sessions_command(
    state: Annotated[list[str] | None, typer.Option("--state")] = None,
    repo: Annotated[str | None, typer.Option("--repo")] = None,
    since: Annotated[str | None, typer.Option("--since")] = None,
    older_than: Annotated[str | None, typer.Option("--older-than")] = None,
    active: Annotated[bool, typer.Option("--active")] = False,
    nonterminal: Annotated[bool, typer.Option("--nonterminal")] = False,
    all_history: Annotated[bool, typer.Option("--all-history")] = False,
    json_output: Annotated[bool, typer.Option("--json")] = False,
    jsonl: Annotated[bool, typer.Option("--jsonl")] = False,
) -> None:
    """List the complete session fleet and apply filters locally."""

    machine = json_output or jsonl
    try:
        if json_output and jsonl:
            raise ValueError("--json and --jsonl are mutually exclusive")
        with JulesClient.from_env() as client:
            items = client.list_sessions(
                all_history=all_history,
                states=state,
                repo=repo,
                since=since,
                older_than=older_than,
                active=active,
                nonterminal=nonterminal,
            )
        if jsonl:
            emit_jsonl([{**item, "schema": "julesctl.session.v1"} for item in items])
        elif json_output:
            emit_json(operation("ls", "completed", {"items": items}))
        else:
            for item in items:
                console.print(f"{item['id']}\t{item['raw_state']}\t{item.get('title') or ''}")
    except (JulesCtlError, ValueError) as exc:
        _fail("ls", exc, machine=machine)


def show_session_command(
    session_id: str,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    try:
        with JulesClient.from_env() as client:
            result = client.get_session(session_id)
        if json_output:
            emit_json(operation("show", "completed", result))
        else:
            console.print(result)
    except (JulesCtlError, ValueError) as exc:
        _fail("show", exc, machine=json_output)


def activities_command(
    session_id: str,
    json_output: Annotated[bool, typer.Option("--json")] = False,
    jsonl: Annotated[bool, typer.Option("--jsonl")] = False,
) -> None:
    machine = json_output or jsonl
    try:
        if json_output and jsonl:
            raise ValueError("--json and --jsonl are mutually exclusive")
        with JulesClient.from_env() as client:
            items = [
                activity.model_dump(by_alias=True, exclude_none=True)
                for activity in client.iter_activities(session_id)
            ]
        if jsonl:
            emit_jsonl(
                [
                    {**item, "schema": "julesctl.activity.v1", "session_id": session_id}
                    for item in items
                ]
            )
        elif json_output:
            emit_json(operation("activities", "completed", {"items": items}))
        else:
            for item in items:
                console.print(item)
    except (JulesCtlError, ValueError) as exc:
        _fail("activities", exc, machine=machine)


def watch_command(
    session_id: str,
    poll_interval: Annotated[float, typer.Option("--poll-interval", min=0)] = 3.0,
    settle: Annotated[float, typer.Option("--settle", min=0)] = 1.0,
    timeout: Annotated[float | None, typer.Option("--timeout", min=0.001)] = None,
    max_polls: Annotated[int | None, typer.Option("--max-polls", min=1)] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
    jsonl: Annotated[bool, typer.Option("--jsonl")] = False,
) -> None:
    machine = json_output or jsonl
    try:
        if json_output and jsonl:
            raise ValueError("--json and --jsonl are mutually exclusive")
        with JulesClient.from_env() as client:
            events = client.watch(
                session_id,
                poll_interval_seconds=poll_interval,
                settle_seconds=settle,
                timeout_seconds=timeout,
                max_polls=max_polls,
            )
            if jsonl:
                for item in events:
                    emit_json({**item, "schema": "julesctl.event.v1"})
                return
            collected = []
            for item in events:
                collected.append(item)
                if not json_output:
                    console.print(item)
        if json_output:
            emit_json(operation("watch", "completed", {"items": collected}))
    except (JulesCtlError, ValueError) as exc:
        _fail("watch", exc, machine=machine)


def message_command(
    session_id: str,
    prompt: Annotated[str | None, typer.Argument(help="Message text, or '-' for stdin")] = None,
    file: Annotated[
        Path | None,
        typer.Option("--file", exists=True, dir_okay=False, readable=True),
    ] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    try:
        message = read_prompt(prompt, file=file)
        with JulesClient.from_env() as client:
            result = client.send_message(session_id, message)
        if json_output:
            emit_json(operation("msg", str(result["outcome"]), result))
        else:
            console.print(result)
    except (JulesCtlError, ValueError) as exc:
        _fail("msg", exc, machine=json_output)


def approve_command(
    session_id: str,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    try:
        with JulesClient.from_env() as client:
            result = client.approve_plan(session_id)
        if json_output:
            emit_json(operation("approve", str(result["outcome"]), result))
        else:
            console.print(result)
    except (JulesCtlError, ValueError) as exc:
        _fail("approve", exc, machine=json_output)


def result_command(
    session_id: str,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Return normalized session, activity, PR, patch, shell, and media evidence."""

    try:
        with JulesClient.from_env() as client:
            result = client.result(session_id)
        if json_output:
            emit_json(operation("result", "completed", result))
        else:
            console.print(result)
    except (JulesCtlError, ValueError) as exc:
        _fail("result", exc, machine=json_output)


def patch_command(
    session_id: str,
    index: Annotated[int, typer.Option("--index")] = -1,
    output: Annotated[Path | None, typer.Option("--output", dir_okay=False)] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Emit one exact unified-diff artifact without Rich formatting."""

    try:
        with JulesClient.from_env() as client:
            result = client.patch(session_id, index=index)
        if json_output:
            emit_json(operation("patch", "completed", result))
            return
        patch = result.get("unidiff_patch")
        if not isinstance(patch, str):
            raise ValueError("selected patch has no unidiff content")
        if output is not None:
            output.write_text(patch, encoding="utf-8")
            return
        sys.stdout.write(patch)
        if patch and not patch.endswith("\n"):
            sys.stdout.write("\n")
    except (JulesCtlError, ValueError, OSError) as exc:
        _fail("patch", exc, machine=json_output)


def pull_request_command(
    session_id: str,
    index: Annotated[int, typer.Option("--index")] = -1,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Return one pull-request output from a Jules session."""

    try:
        with JulesClient.from_env() as client:
            result = client.pull_request(session_id, index=index)
        if json_output:
            emit_json(operation("pr", "completed", result))
        else:
            console.print(result)
    except (JulesCtlError, ValueError) as exc:
        _fail("pr", exc, machine=json_output)


def remove_command(
    session_ids: list[str],
    yes: Annotated[bool, typer.Option("--yes")] = False,
    concurrency: Annotated[int, typer.Option("--concurrency", min=1, max=32)] = 4,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Delete an explicit list of session IDs and preserve per-target outcomes."""

    try:
        if not yes:
            raise ValueError("--yes is required for deletion")
        with JulesClient.from_env() as client:
            result = client.remove_sessions(session_ids, max_workers=concurrency)
        if json_output:
            emit_json(operation("rm", str(result["outcome"]), result))
        else:
            console.print(result)
        if result["outcome"] == "partial":
            raise typer.Exit(6)
    except typer.Exit:
        raise
    except (JulesCtlError, ValueError) as exc:
        _fail("rm", exc, machine=json_output)


def prune_command(
    state: Annotated[list[str] | None, typer.Option("--state")] = None,
    repo: Annotated[str | None, typer.Option("--repo")] = None,
    older_than: Annotated[str | None, typer.Option("--older-than")] = None,
    nonterminal: Annotated[bool, typer.Option("--nonterminal")] = False,
    all_sessions: Annotated[bool, typer.Option("--all")] = False,
    include_unknown: Annotated[bool, typer.Option("--include-unknown")] = False,
    apply: Annotated[str | None, typer.Option("--apply", metavar="PLAN_ID")] = None,
    yes: Annotated[bool, typer.Option("--yes")] = False,
    concurrency: Annotated[int, typer.Option("--concurrency", min=1, max=32)] = 4,
    settle: Annotated[float, typer.Option("--settle", min=0)] = 0.0,
    passes: Annotated[int, typer.Option("--passes", min=1, max=20)] = 1,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Preview an exact fleet deletion plan, or apply one reviewed plan."""

    try:
        with JulesClient.from_env() as client:
            if apply is not None:
                if not yes:
                    raise ValueError("--yes is required with --apply")
                if any([state, repo, older_than, nonterminal, all_sessions, include_unknown]):
                    raise ValueError("selectors cannot be combined with --apply")
                result = client.apply_prune(
                    apply,
                    max_workers=concurrency,
                    settle_seconds=settle,
                    passes=passes,
                )
            else:
                if yes:
                    raise ValueError("--yes has no effect without --apply PLAN_ID")
                result = client.plan_prune(
                    states=state,
                    repo=repo,
                    older_than=older_than,
                    nonterminal=nonterminal,
                    all_sessions=all_sessions,
                    include_unknown=include_unknown,
                )
        if json_output:
            emit_json(operation("prune", str(result["outcome"]), result))
        else:
            console.print(result)
        if result["outcome"] == "partial":
            raise typer.Exit(6)
    except typer.Exit:
        raise
    except (JulesCtlError, ValueError) as exc:
        _fail("prune", exc, machine=json_output)


def retry_command(
    session_id: str,
    dispatch_key: Annotated[str, typer.Option("--dispatch-key")],
    title: Annotated[str | None, typer.Option("--title")] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Create one explicit replacement dispatch without mutating the original attempt."""

    try:
        with JulesClient.from_env() as client:
            result = client.retry_session(
                session_id,
                dispatch_key=dispatch_key,
                title=title,
            )
        if json_output:
            emit_json(operation("retry", str(result["outcome"]), result))
        else:
            console.print(result)
    except (JulesCtlError, ValueError) as exc:
        _fail("retry", exc, machine=json_output)
