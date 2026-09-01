from __future__ import annotations

import json
from typing import Any

from rich.console import Console

console = Console(stderr=True)


def operation(
    command: str,
    outcome: str,
    data: object | None = None,
    **meta: object,
) -> dict[str, object]:
    value: dict[str, object] = {
        "schema": "julesctl.operation.v1",
        "command": command,
        "outcome": outcome,
    }
    if data is not None:
        value["data"] = data
    if meta:
        value["meta"] = meta
    return value


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
