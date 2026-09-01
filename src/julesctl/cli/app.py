from __future__ import annotations

from pathlib import Path
from typing import Annotated, Callable

import typer

from ..config import Settings
from ..controller import JulesController
from ..domain.errors import JulesCtlError
from ..domain.models import DispatchSpec
from .output import console, emit_json, emit_jsonl, operation

app = typer.Typer(help="Safe control of Google Jules cloud coding sessions.", no_args_is_help=True)
auth_app = typer.Typer(help="Authentication diagnostics")
source_app = typer.Typer(help="Jules sources")
session_app = typer.Typer(help="Jules sessions")
fleet_app = typer.Typer(help="Account fleet control")
app.add_typer(auth_app, name="auth")
app.add_typer(source_app, name="source")
app.add_typer(session_app, name="session")
app.add_typer(fleet_app, name="fleet")


def _controller() -> JulesController:
    return JulesController.from_settings(Settings.from_env())


def _run_json(command: str, fn: Callable[[JulesController], object]) -> None:
    try:
        with _controller() as ctl:
            result = fn(ctl)
        emit_json(operation(command, "completed", result))
    except (JulesCtlError, ValueError) as exc:
        exit_code = getattr(exc, "exit_code", 2)
        emit_json(
            {
                "schema": "julesctl.operation.v1",
                "command": command,
                "outcome": "error",
                "error": {"kind": exc.__class__.__name__, "message": str(exc)},
            }
        )
        raise typer.Exit(exit_code) from exc


@auth_app.command("check")
def auth_check(json_output: Annotated[bool, typer.Option("--json")] = False) -> None:
    if json_output:
        _run_json("auth.check", lambda ctl: ctl.auth_check())
        return
    with _controller() as ctl:
        ctl.auth_check()
    console.print("Jules API authentication succeeded")


@source_app.command("list")
def source_list(json_output: Annotated[bool, typer.Option("--json")] = False) -> None:
    with _controller() as ctl:
        items = [
            source.model_dump(by_alias=True, exclude_none=True)
            for source in ctl.ctx.api.iter_sources()
        ]
    if json_output:
        emit_json(operation("source.list", "completed", {"items": items}))
    else:
        for item in items:
            console.print(item.get("name"))


@source_app.command("resolve")
def source_resolve(
    repo: str,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    with _controller() as ctl:
        result = ctl.resolve_source(repo)
    if json_output:
        emit_json(operation("source.resolve", "completed", result))
    else:
        console.print(result)


@app.command("dispatch")
def dispatch(
    spec: Annotated[Path, typer.Option("--spec", exists=True, dir_okay=False, readable=True)],
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    try:
        candidate = DispatchSpec.model_validate_json(spec.read_text(encoding="utf-8"))
        with _controller() as ctl:
            result = ctl.dispatch(candidate)
        if json_output:
            emit_json(operation("dispatch", str(result["outcome"]), result))
        else:
            console.print(result)
    except (JulesCtlError, ValueError) as exc:
        if json_output:
            emit_json(
                {
                    "schema": "julesctl.operation.v1",
                    "command": "dispatch",
                    "outcome": "error",
                    "error": {"kind": exc.__class__.__name__, "message": str(exc)},
                }
            )
        else:
            console.print(f"[red]{exc}[/red]")
        raise typer.Exit(getattr(exc, "exit_code", 2)) from exc


@session_app.command("list")
def session_list(
    all_history: Annotated[bool, typer.Option("--all-history")] = False,
    json_output: Annotated[bool, typer.Option("--json")] = False,
    jsonl: Annotated[bool, typer.Option("--jsonl")] = False,
) -> None:
    with _controller() as ctl:
        items = ctl.list_sessions(all_history=all_history)
    if jsonl:
        emit_jsonl(items)
    elif json_output:
        emit_json(operation("session.list", "completed", {"items": items}))
    else:
        for item in items:
            console.print(
                f"{item['id']}\t{item['raw_state']}\t{item.get('title') or ''}"
            )


@session_app.command("show")
def session_show(
    session_id: str,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    with _controller() as ctl:
        result = ctl.normalize_session(ctl.ctx.api.get_session(session_id))
    if json_output:
        emit_json(operation("session.show", "completed", result))
    else:
        console.print(result)


@session_app.command("message")
def session_message(
    session_id: str,
    file: Annotated[Path, typer.Option("--file", exists=True, dir_okay=False, readable=True)],
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    prompt = file.read_text(encoding="utf-8")
    with _controller() as ctl:
        ctl.ctx.api.send_message(session_id, prompt)
    if json_output:
        emit_json(operation("session.message", "completed", {"session_id": session_id}))


@session_app.command("approve")
def session_approve(
    session_id: str,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    with _controller() as ctl:
        ctl.ctx.api.approve_plan(session_id)
    if json_output:
        emit_json(operation("session.approve", "completed", {"session_id": session_id}))


@session_app.command("result")
def session_result(
    session_id: str,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    with _controller() as ctl:
        result = ctl.session_result(session_id)
    if json_output:
        emit_json(operation("session.result", "completed", result))
    else:
        console.print(result)


@session_app.command("delete")
def session_delete(
    session_ids: list[str],
    yes: Annotated[bool, typer.Option("--yes")] = False,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    if not yes:
        raise typer.BadParameter("--yes is required for deletion")
    results = []
    with _controller() as ctl:
        for session_id in session_ids:
            removed = ctl.ctx.api.delete_session(session_id)
            ctl.ctx.store.mark_deleted(session_id)
            results.append(
                {
                    "session_id": session_id,
                    "outcome": "deleted" if removed else "already_absent",
                }
            )
    if json_output:
        emit_json(operation("session.delete", "completed", {"items": results}))


@app.command("reconcile")
def reconcile(jsonl: Annotated[bool, typer.Option("--jsonl")] = False) -> None:
    with _controller() as ctl:
        events = ctl.reconcile()
    if jsonl:
        emit_jsonl(events)
    else:
        for event in events:
            console.print(event)


@fleet_app.command("status")
def fleet_status(json_output: Annotated[bool, typer.Option("--json")] = False) -> None:
    with _controller() as ctl:
        result = ctl.capacity()
    if json_output:
        emit_json(operation("fleet.status", "completed", result))
    else:
        console.print(result)


@fleet_app.command("freeze")
def fleet_freeze(json_output: Annotated[bool, typer.Option("--json")] = False) -> None:
    with _controller() as ctl:
        ctl.ctx.store.set_frozen(True)
    if json_output:
        emit_json(operation("fleet.freeze", "completed", {"frozen": True}))


@fleet_app.command("unfreeze")
def fleet_unfreeze(json_output: Annotated[bool, typer.Option("--json")] = False) -> None:
    with _controller() as ctl:
        ctl.ctx.store.set_frozen(False)
    if json_output:
        emit_json(operation("fleet.unfreeze", "completed", {"frozen": False}))


@fleet_app.command("drain")
def fleet_drain(
    apply: Annotated[str | None, typer.Option("--apply", metavar="PLAN_ID")] = None,
    yes: Annotated[bool, typer.Option("--yes")] = False,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    with _controller() as ctl:
        if apply:
            if not yes:
                raise typer.BadParameter("--yes is required with --apply")
            result = ctl.apply_deletion_plan(apply)
        else:
            result = ctl.create_drain_plan()
    if json_output:
        emit_json(operation("fleet.drain", "completed", result))
    else:
        console.print(result)
