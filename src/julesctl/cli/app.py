from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from ..application.steering import approve_once, archive_once, message_once, unarchive_once
from ..config import Settings
from ..controller import JulesController
from ..discovery import fetch_discovery
from ..domain.errors import JulesCtlError
from ..domain.models import DispatchSpec
from .output import console, emit_json, emit_jsonl, operation

app = typer.Typer(help="Safe control of Google Jules cloud coding sessions.", no_args_is_help=True)
auth_app = typer.Typer(help="Authentication diagnostics")
api_app = typer.Typer(help="Jules API contract diagnostics")
source_app = typer.Typer(help="Jules sources")
session_app = typer.Typer(help="Jules sessions")
fleet_app = typer.Typer(help="Account fleet control")
app.add_typer(auth_app, name="auth")
app.add_typer(api_app, name="api")
app.add_typer(source_app, name="source")
app.add_typer(session_app, name="session")
app.add_typer(fleet_app, name="fleet")


def _controller() -> JulesController:
    return JulesController.from_settings(Settings.from_env())


def _error(command: str, exc: Exception, *, machine: bool) -> None:
    if machine:
        emit_json({
            "schema": "julesctl.operation.v1",
            "command": command,
            "outcome": "error",
            "error": {"kind": exc.__class__.__name__, "message": str(exc)},
        })
    else:
        console.print(f"[red]{exc}[/red]")
    raise typer.Exit(getattr(exc, "exit_code", 2)) from exc


@auth_app.command("check")
def auth_check(json_output: Annotated[bool, typer.Option("--json")] = False) -> None:
    try:
        with _controller() as ctl:
            result = ctl.auth_check()
        if json_output:
            emit_json(operation("auth.check", "completed", result))
        else:
            console.print("Jules API authentication succeeded")
    except (JulesCtlError, ValueError) as exc:
        _error("auth.check", exc, machine=json_output)


@api_app.command("check")
def api_check(json_output: Annotated[bool, typer.Option("--json")] = False) -> None:
    try:
        _payload, summary = fetch_discovery()
        data = {
            "revision": summary.revision,
            "digest": summary.digest,
            "operations": list(summary.operations),
            "missing_expected": list(summary.missing_expected),
            "unexpected": list(summary.unexpected),
            "compatible": summary.compatible,
        }
        if json_output:
            emit_json(operation("api.check", "completed" if summary.compatible else "drift", data))
        else:
            console.print(data)
        if not summary.compatible:
            raise typer.Exit(4)
    except JulesCtlError as exc:
        _error("api.check", exc, machine=json_output)


@source_app.command("list")
def source_list(json_output: Annotated[bool, typer.Option("--json")] = False) -> None:
    try:
        with _controller() as ctl:
            items = [s.model_dump(by_alias=True, exclude_none=True) for s in ctl.ctx.api.iter_sources()]
        if json_output:
            emit_json(operation("source.list", "completed", {"items": items}))
        else:
            for item in items:
                console.print(item.get("name"))
    except (JulesCtlError, ValueError) as exc:
        _error("source.list", exc, machine=json_output)


@source_app.command("resolve")
def source_resolve(repo: str, json_output: Annotated[bool, typer.Option("--json")] = False) -> None:
    try:
        with _controller() as ctl:
            result = ctl.resolve_source(repo)
        if json_output:
            emit_json(operation("source.resolve", "completed", result))
        else:
            console.print(result)
    except (JulesCtlError, ValueError) as exc:
        _error("source.resolve", exc, machine=json_output)


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
        _error("dispatch", exc, machine=json_output)


@session_app.command("list")
def session_list(
    all_history: Annotated[bool, typer.Option("--all-history")] = False,
    json_output: Annotated[bool, typer.Option("--json")] = False,
    jsonl: Annotated[bool, typer.Option("--jsonl")] = False,
) -> None:
    try:
        with _controller() as ctl:
            items = ctl.list_sessions(all_history=all_history)
        if jsonl:
            emit_jsonl(items)
        elif json_output:
            emit_json(operation("session.list", "completed", {"items": items}))
        else:
            for item in items:
                console.print(f"{item['id']}\t{item['raw_state']}\t{item.get('title') or ''}")
    except (JulesCtlError, ValueError) as exc:
        _error("session.list", exc, machine=json_output or jsonl)


@session_app.command("show")
def session_show(session_id: str, json_output: Annotated[bool, typer.Option("--json")] = False) -> None:
    try:
        with _controller() as ctl:
            result = ctl.normalize_session(ctl.ctx.api.get_session(session_id))
        if json_output:
            emit_json(operation("session.show", "completed", result))
        else:
            console.print(result)
    except (JulesCtlError, ValueError) as exc:
        _error("session.show", exc, machine=json_output)


@session_app.command("message")
def session_message(
    session_id: str,
    file: Annotated[Path, typer.Option("--file", exists=True, dir_okay=False, readable=True)],
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    try:
        prompt = file.read_text(encoding="utf-8")
        with _controller() as ctl:
            result = message_once(ctl.ctx.api, session_id, prompt)
        if json_output:
            emit_json(operation("session.message", str(result["outcome"]), result))
        else:
            console.print(result)
    except (JulesCtlError, ValueError) as exc:
        _error("session.message", exc, machine=json_output)


@session_app.command("approve")
def session_approve(session_id: str, json_output: Annotated[bool, typer.Option("--json")] = False) -> None:
    try:
        with _controller() as ctl:
            result = approve_once(ctl.ctx.api, session_id)
        if json_output:
            emit_json(operation("session.approve", str(result["outcome"]), result))
        else:
            console.print(result)
    except (JulesCtlError, ValueError) as exc:
        _error("session.approve", exc, machine=json_output)


@session_app.command("result")
def session_result(session_id: str, json_output: Annotated[bool, typer.Option("--json")] = False) -> None:
    try:
        with _controller() as ctl:
            result = ctl.session_result(session_id)
        if json_output:
            emit_json(operation("session.result", "completed", result))
        else:
            console.print(result)
    except (JulesCtlError, ValueError) as exc:
        _error("session.result", exc, machine=json_output)


@session_app.command("pr")
def session_pr(session_id: str, json_output: Annotated[bool, typer.Option("--json")] = False) -> None:
    try:
        with _controller() as ctl:
            session = ctl.ctx.api.get_session(session_id)
            normalized = ctl.normalize_session(session)
        result = {"session_id": session_id, "pr": normalized["pr"]}
        if json_output:
            emit_json(operation("session.pr", "completed", result))
        else:
            console.print(result)
    except (JulesCtlError, ValueError) as exc:
        _error("session.pr", exc, machine=json_output)


@session_app.command("adopt")
def session_adopt(
    session_id: str,
    attempt: Annotated[str, typer.Option("--attempt")],
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    try:
        with _controller() as ctl:
            session = ctl.ctx.api.get_session(session_id)
            row = ctl.ctx.store.get_attempt(attempt)
            if row is None:
                raise ValueError(f"unknown attempt {attempt}")
            ctl.ctx.store.bind_session(attempt, session.id, reconciled=True)
            ctl._remember_session(session, origin="managed", repo=row["repo"], prompt_sha256=row["prompt_sha256"])
            result = {"session_id": session.id, "attempt_id": attempt, "outcome": "adopted"}
        if json_output:
            emit_json(operation("session.adopt", "adopted", result))
        else:
            console.print(result)
    except (JulesCtlError, ValueError) as exc:
        _error("session.adopt", exc, machine=json_output)


@session_app.command("archive")
def session_archive(
    session_ids: list[str],
    yes: Annotated[bool, typer.Option("--yes")] = False,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    if not yes:
        raise typer.BadParameter("--yes is required for archive")
    try:
        with _controller() as ctl:
            items = [archive_once(ctl.ctx.api, sid) for sid in session_ids]
        if json_output:
            emit_json(operation("session.archive", "completed", {"items": items}))
        else:
            console.print(items)
    except (JulesCtlError, ValueError) as exc:
        _error("session.archive", exc, machine=json_output)


@session_app.command("unarchive")
def session_unarchive(
    session_ids: list[str],
    yes: Annotated[bool, typer.Option("--yes")] = False,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    if not yes:
        raise typer.BadParameter("--yes is required for unarchive")
    try:
        with _controller() as ctl:
            items = [unarchive_once(ctl.ctx.api, sid) for sid in session_ids]
        if json_output:
            emit_json(operation("session.unarchive", "completed", {"items": items}))
        else:
            console.print(items)
    except (JulesCtlError, ValueError) as exc:
        _error("session.unarchive", exc, machine=json_output)


@session_app.command("delete")
def session_delete(
    session_ids: list[str],
    yes: Annotated[bool, typer.Option("--yes")] = False,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    if not yes:
        raise typer.BadParameter("--yes is required for deletion")
    try:
        items = []
        with _controller() as ctl:
            for session_id in session_ids:
                removed = ctl.ctx.api.delete_session(session_id)
                ctl.ctx.store.mark_deleted(session_id)
                items.append({"session_id": session_id, "outcome": "deleted" if removed else "already_absent"})
        if json_output:
            emit_json(operation("session.delete", "completed", {"items": items}))
        else:
            console.print(items)
    except (JulesCtlError, ValueError) as exc:
        _error("session.delete", exc, machine=json_output)


@app.command("reconcile")
def reconcile(jsonl: Annotated[bool, typer.Option("--jsonl")] = False) -> None:
    try:
        with _controller() as ctl:
            events = ctl.reconcile()
        if jsonl:
            emit_jsonl(events)
        else:
            for event in events:
                console.print(event)
    except (JulesCtlError, ValueError) as exc:
        _error("reconcile", exc, machine=jsonl)


@fleet_app.command("status")
def fleet_status(json_output: Annotated[bool, typer.Option("--json")] = False) -> None:
    try:
        with _controller() as ctl:
            result = ctl.capacity()
        if json_output:
            emit_json(operation("fleet.status", "completed", result))
        else:
            console.print(result)
    except (JulesCtlError, ValueError) as exc:
        _error("fleet.status", exc, machine=json_output)


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
    try:
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
    except (JulesCtlError, ValueError) as exc:
        _error("fleet.drain", exc, machine=json_output)
