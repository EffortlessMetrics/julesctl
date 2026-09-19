from __future__ import annotations

import os
import re
import uuid

import typer

from ..domain.errors import (
    AdmissionError,
    ApiError,
    AuthError,
    IndeterminateError,
    InputError,
)
from .output import emit_json, err_console

_HEADER_SECRET = re.compile(r"(?i)(x-goog-api-key\s*[:=]\s*)\S+")
_ENV_SECRET = re.compile(r"(?i)(JULES_API_KEY\s*=\s*)\S+")


def redact_text(value: str) -> str:
    """Remove known credential forms from diagnostics before they leave the process."""

    result = _HEADER_SECRET.sub(r"\1[REDACTED]", value)
    result = _ENV_SECRET.sub(r"\1[REDACTED]", result)
    api_key = os.environ.get("JULES_API_KEY", "")
    if api_key:
        result = result.replace(api_key, "[REDACTED]")
    return result


def error_kind(exc: Exception) -> str:
    if isinstance(exc, AuthError):
        return "authentication_failed"
    if isinstance(exc, AdmissionError):
        return "admission_denied"
    if isinstance(exc, IndeterminateError):
        return "indeterminate"
    if isinstance(exc, InputError | ValueError):
        return "invalid_input"
    if isinstance(exc, ApiError):
        return "create_outcome_unknown" if exc.create_outcome_uncertain else "api_rejected"
    if isinstance(exc, OSError):
        return "local_io_error"
    return "internal_error"


def error_details(exc: Exception) -> dict[str, object]:
    value: dict[str, object] = {
        "kind": error_kind(exc),
        "message": redact_text(str(exc)),
    }
    if isinstance(exc, ApiError):
        value.update(
            {
                "http_status": exc.http_status,
                "api_status": exc.api_status,
                "transient": exc.create_outcome_uncertain,
                "safe_to_retry": False,
                "reconcile_required": exc.create_outcome_uncertain,
                "retry_after_seconds": exc.retry_after_seconds,
            }
        )
    elif isinstance(exc, IndeterminateError):
        value.update(
            {
                "transient": True,
                "safe_to_retry": False,
                "reconcile_required": True,
            }
        )
    return value


def fail(command: str, exc: Exception, *, machine: bool) -> None:
    if machine:
        emit_json(
            {
                "schema": "julesctl.operation.v1",
                "operation_id": str(uuid.uuid4()),
                "command": command,
                "outcome": "error",
                "error": error_details(exc),
            }
        )
    else:
        err_console.print(f"[red]{redact_text(str(exc))}[/red]")
    raise typer.Exit(getattr(exc, "exit_code", 2)) from exc
