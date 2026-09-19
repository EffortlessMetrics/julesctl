from __future__ import annotations

import json
import uuid
from typing import Any

from rich.console import Console

console = Console()
err_console = Console(stderr=True)


def operation(
    command: str,
    outcome: str,
    data: object | None = None,
    **meta: object,
) -> dict[str, object]:
    value: dict[str, object] = {
        "schema": "julesctl.operation.v1",
        "operation_id": str(uuid.uuid4()),
        "command": command,
        "outcome": outcome,
    }
    if data is not None:
        value["data"] = data
    if meta:
        value["meta"] = meta
    return value


def event(value: dict[str, Any]) -> dict[str, Any]:
    return {"schema": "julesctl.event.v1", **value}


def emit_json(value: object) -> None:
    print(
        json.dumps(
            value,
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
            default=str,
        )
    )


def emit_jsonl(values: list[dict[str, Any]]) -> None:
    for value in values:
        emit_json(value)
