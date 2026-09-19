from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from ..application.queue import (
    enqueue_candidate,
    load_candidate,
    queue_status,
    run_worker_once,
)
from ..config import Settings, default_database_path, profile_name
from ..domain.errors import JulesCtlError
from ..queue_store import CandidateQueueStore
from .common import fail as _error
from .output import console, emit_json, emit_jsonl, operation

queue_app = typer.Typer(help="Unprivileged candidate queue")
worker_app = typer.Typer(help="Credentialed queue worker")


def _queue_store() -> CandidateQueueStore:
    selected_profile = profile_name()
    return CandidateQueueStore(
        default_database_path(selected_profile),
        profile_name=selected_profile,
    )


@queue_app.command("submit")
def submit_candidate(
    spec: Annotated[Path, typer.Option("--spec", exists=True, dir_okay=False)],
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    try:
        candidate = load_candidate(spec)
        with _queue_store() as store:
            result = enqueue_candidate(store, candidate)
        if json_output:
            emit_json(operation("queue.submit", str(result["outcome"]), result))
        else:
            console.print(result)
    except (JulesCtlError, ValueError) as exc:
        _error("queue.submit", exc, machine=json_output)


@queue_app.command("status")
def candidate_queue_status(
    limit: Annotated[int, typer.Option("--limit", min=1, max=1000)] = 100,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    try:
        with _queue_store() as store:
            result = queue_status(store, limit=limit)
        if json_output:
            emit_json(operation("queue.status", "completed", result))
        else:
            console.print(result)
    except (JulesCtlError, ValueError) as exc:
        _error("queue.status", exc, machine=json_output)


@worker_app.command("run-once")
def worker_run_once(
    max_items: Annotated[int, typer.Option("--max", min=1, max=100)] = 5,
    allow_repo: Annotated[list[str] | None, typer.Option("--allow-repo")] = None,
    allow_repoless: Annotated[bool, typer.Option("--allow-repoless")] = False,
    json_output: Annotated[bool, typer.Option("--json")] = False,
    jsonl: Annotated[bool, typer.Option("--jsonl")] = False,
) -> None:
    try:
        if json_output and jsonl:
            raise ValueError("--json and --jsonl are mutually exclusive")
        outcomes = run_worker_once(
            Settings.from_env(),
            max_items=max_items,
            allow_repos=allow_repo or [],
            allow_repoless=allow_repoless,
        )
        if jsonl:
            emit_jsonl([{"schema": "julesctl.worker-result.v1", **item} for item in outcomes])
        elif json_output:
            emit_json(
                operation(
                    "worker.run-once",
                    "completed",
                    {"items": outcomes},
                )
            )
        else:
            console.print(outcomes)
    except (JulesCtlError, ValueError) as exc:
        _error("worker.run-once", exc, machine=json_output or jsonl)
