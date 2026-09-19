from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from ..application.steering import approve_once, archive_once, message_once, unarchive_once
from ..config import Settings, default_database_path, profile_name
from ..controller import JulesController
from ..discovery import fetch_discovery
from ..domain.errors import InputError, JulesCtlError
from ..domain.models import DispatchSpec
from ..store import StateStore
from .agent import (
    activities_command,
    approve_command,
    list_sessions_command,
    message_command,
    new_session,
    patch_command,
    prune_command,
    pull_request_command,
    remove_command,
    result_command,
    retry_command,
    show_session_command,
    watch_command,
)
from .common import error_details as _error_details
from .common import fail as _error
from .output import console, emit_json, emit_jsonl, event, operation
from .queue import queue_app, worker_app

app = typer.Typer(help="Safe control of Google Jules cloud coding sessions.", no_args_is_help=True)
auth_app = typer.Typer(help="Authentication diagnostics")
api_app = typer.Typer(help="Jules API contract diagnostics")
source_app = typer.Typer(help="Jules sources")
session_app = typer.Typer(help="Jules sessions")
fleet_app = typer.Typer(help="Account fleet control")
state_app = typer.Typer(help="Local controller state")
app.add_typer(auth_app, name="auth")
app.add_typer(api_app, name="api")
app.add_typer(source_app, name="source")
app.add_typer(session_app, name="session")
app.add_typer(fleet_app, name="fleet")
app.add_typer(state_app, name="state")
app.add_typer(queue_app, name="queue")
app.add_typer(worker_app, name="worker")
app.command("new")(new_session)
app.command("ls")(list_sessions_command)
app.command("show")(show_session_command)
app.command("activities")(activities_command)
app.command("watch")(watch_command)
app.command("msg")(message_command)
app.command("approve")(approve_command)
app.command("result")(result_command)
app.command("patch")(patch_command)
app.command("pr")(pull_request_command)
app.command("rm")(remove_command)
app.command("prune")(prune_command)
app.command("retry")(retry_command)


def _controller() -> JulesController:
    return JulesController.from_settings(Settings.from_env())


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
        outcome = "completed" if summary.compatible else "drift"
        if json_output:
            emit_json(operation("api.check", outcome, data))
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
            items = [
                s.model_dump(by_alias=True, exclude_none=True) for s in ctl.ctx.api.iter_sources()
            ]
        if json_output:
            emit_json(operation("source.list", "completed", {"items": items}))
        else:
            for item in items:
                console.print(item.get("name"))
    except (JulesCtlError, ValueError) as exc:
        _error("source.list", exc, machine=json_output)


@source_app.command("resolve")
def source_resolve(
    repo: str,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
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
        if json_output and jsonl:
            raise InputError("--json and --jsonl are mutually exclusive")
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
def session_show(
    session_id: str,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    try:
        with _controller() as ctl:
            session = ctl.ctx.api.get_session(session_id)
            origin = "managed" if session.id in ctl.ctx.store.managed_session_ids() else "external"
            ctl._remember_session(session, origin=origin)
            result = ctl.normalize_session(session, origin=origin)
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
def session_approve(
    session_id: str,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
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
def session_result(
    session_id: str,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
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
def session_pr(
    session_id: str,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
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
                raise InputError(f"unknown attempt {attempt}")
            if not ctl._candidate_matches(row, session):
                raise InputError("session does not match the stored dispatch attempt")
            ctl.ctx.store.bind_session(attempt, session.id, reconciled=True)
            ctl._remember_session(
                session,
                origin="managed",
                repo=row["repo"],
                prompt_sha256=row["prompt_sha256"],
            )
            result = {"session_id": session.id, "attempt_id": attempt, "outcome": "adopted"}
        if json_output:
            emit_json(operation("session.adopt", "adopted", result))
        else:
            console.print(result)
    except (JulesCtlError, ValueError) as exc:
        _error("session.adopt", exc, machine=json_output)


def _bulk_lifecycle(
    command: str,
    session_ids: list[str],
    *,
    desired_archived: bool,
    json_output: bool,
) -> None:
    try:
        if not session_ids:
            raise InputError("at least one session ID is required")
        items: list[dict[str, object]] = []
        with _controller() as ctl:
            for sid in session_ids:
                try:
                    result = (
                        archive_once(ctl.ctx.api, sid)
                        if desired_archived
                        else unarchive_once(ctl.ctx.api, sid)
                    )
                    items.append(result)
                except JulesCtlError as exc:
                    items.append(
                        {"session_id": sid, "outcome": "error", "error": _error_details(exc)}
                    )
        outcome = (
            "partial" if any(item.get("outcome") == "error" for item in items) else "completed"
        )
        if json_output:
            emit_json(operation(command, outcome, {"items": items}))
        else:
            console.print(items)
        if outcome == "partial":
            raise typer.Exit(6)
    except typer.Exit:
        raise
    except (JulesCtlError, ValueError) as exc:
        _error(command, exc, machine=json_output)


@session_app.command("archive")
def session_archive(
    session_ids: list[str],
    yes: Annotated[bool, typer.Option("--yes")] = False,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    if not yes:
        _error("session.archive", InputError("--yes is required for archive"), machine=json_output)
    _bulk_lifecycle(
        "session.archive",
        session_ids,
        desired_archived=True,
        json_output=json_output,
    )


@session_app.command("unarchive")
def session_unarchive(
    session_ids: list[str],
    yes: Annotated[bool, typer.Option("--yes")] = False,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    if not yes:
        _error(
            "session.unarchive", InputError("--yes is required for unarchive"), machine=json_output
        )
    _bulk_lifecycle(
        "session.unarchive",
        session_ids,
        desired_archived=False,
        json_output=json_output,
    )


@session_app.command("delete")
def session_delete(
    session_ids: list[str],
    yes: Annotated[bool, typer.Option("--yes")] = False,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    try:
        if not yes:
            raise InputError("--yes is required for deletion")
        if not session_ids:
            raise InputError("at least one session ID is required")
        items: list[dict[str, object]] = []
        with _controller() as ctl:
            for session_id in session_ids:
                try:
                    removed = ctl.ctx.api.delete_session(session_id)
                    ctl.ctx.store.mark_deleted(session_id)
                    items.append(
                        {
                            "session_id": session_id,
                            "outcome": "deleted" if removed else "already_absent",
                        }
                    )
                except JulesCtlError as exc:
                    items.append(
                        {"session_id": session_id, "outcome": "error", "error": _error_details(exc)}
                    )
        outcome = (
            "partial" if any(item.get("outcome") == "error" for item in items) else "completed"
        )
        if json_output:
            emit_json(operation("session.delete", outcome, {"items": items}))
        else:
            console.print(items)
        if outcome == "partial":
            raise typer.Exit(6)
    except typer.Exit:
        raise
    except (JulesCtlError, ValueError) as exc:
        _error("session.delete", exc, machine=json_output)


@app.command("reconcile")
def reconcile(jsonl: Annotated[bool, typer.Option("--jsonl")] = False) -> None:
    try:
        with _controller() as ctl:
            events = ctl.reconcile()
        if jsonl:
            emit_jsonl([event(item) for item in events])
        else:
            for item in events:
                console.print(item)
    except (JulesCtlError, ValueError) as exc:
        _error("reconcile", exc, machine=jsonl)


@state_app.command("check")
def state_check(
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    try:
        selected_profile = profile_name()
        path = default_database_path(selected_profile)
        store = StateStore(path, profile_name=selected_profile)
        try:
            result = {
                "profile": selected_profile,
                "path": str(path),
                "schema_version": store.schema_version(),
                "integrity": store.integrity_check(),
                "unresolved_attempts": store.unresolved_attempt_count(),
            }
        finally:
            store.close()
        if json_output:
            emit_json(operation("state.check", "completed", result))
        else:
            console.print(result)
    except (JulesCtlError, ValueError) as exc:
        _error("state.check", exc, machine=json_output)


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
    try:
        selected_profile = profile_name()
        store = StateStore(
            default_database_path(selected_profile),
            profile_name=selected_profile,
        )
        try:
            generation = store.set_frozen(True)
        finally:
            store.close()
        result = {"frozen": True, "generation": generation}
        if json_output:
            emit_json(operation("fleet.freeze", "completed", result))
        else:
            console.print(result)
    except (JulesCtlError, ValueError) as exc:
        _error("fleet.freeze", exc, machine=json_output)


@fleet_app.command("unfreeze")
def fleet_unfreeze(json_output: Annotated[bool, typer.Option("--json")] = False) -> None:
    try:
        selected_profile = profile_name()
        store = StateStore(
            default_database_path(selected_profile),
            profile_name=selected_profile,
        )
        try:
            generation = store.set_frozen(False)
        finally:
            store.close()
        result = {"frozen": False, "generation": generation}
        if json_output:
            emit_json(operation("fleet.unfreeze", "completed", result))
        else:
            console.print(result)
    except (JulesCtlError, ValueError) as exc:
        _error("fleet.unfreeze", exc, machine=json_output)


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
                    raise InputError("--yes is required with --apply")
                result = ctl.apply_deletion_plan(apply)
            else:
                result = ctl.create_drain_plan()
        outcome = str(result.get("outcome", "completed"))
        if json_output:
            emit_json(operation("fleet.drain", outcome, result))
        else:
            console.print(result)
        if outcome == "partial":
            raise typer.Exit(6)
    except typer.Exit:
        raise
    except (JulesCtlError, ValueError) as exc:
        _error("fleet.drain", exc, machine=json_output)
