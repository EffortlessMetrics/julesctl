from __future__ import annotations

import sys
from pathlib import Path
from typing import TextIO

from ..domain.errors import InputError

MAX_PROMPT_BYTES = 1024 * 1024


def _validate_prompt(prompt: str) -> str:
    encoded = prompt.encode("utf-8")
    if len(encoded) > MAX_PROMPT_BYTES:
        raise InputError(f"prompt is {len(encoded)} bytes; maximum is {MAX_PROMPT_BYTES} bytes")
    if not prompt.strip():
        raise InputError("prompt must not be empty")
    return prompt


def read_prompt(
    positional: str | None,
    *,
    file: Path | None,
    stdin: TextIO | None = None,
) -> str:
    """Resolve exactly one prompt source without normalizing its contents."""

    input_stream = stdin or sys.stdin
    if positional is not None and file is not None:
        raise InputError("prompt argument and --file are mutually exclusive")
    if positional == "-":
        positional = None
        use_stdin = True
    else:
        use_stdin = positional is None and file is None

    if file is not None:
        if file.is_symlink():
            raise InputError("prompt file must not be a symlink")
        if not file.is_file():
            raise InputError("prompt file must be a regular file")
        if file.stat().st_size > MAX_PROMPT_BYTES:
            raise InputError(
                f"prompt file is {file.stat().st_size} bytes; maximum is {MAX_PROMPT_BYTES} bytes"
            )
        try:
            return _validate_prompt(file.read_text(encoding="utf-8"))
        except UnicodeDecodeError as exc:
            raise InputError("prompt file must be UTF-8") from exc
    if use_stdin:
        if input_stream.isatty():
            raise InputError("provide a prompt argument, --file, or piped stdin")
        return _validate_prompt(input_stream.read())
    assert positional is not None
    return _validate_prompt(positional)


def derive_title(prompt: str, *, limit: int = 80) -> str:
    for raw_line in prompt.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        while line.startswith("#"):
            line = line[1:].lstrip()
        if line:
            return line if len(line) <= limit else line[: limit - 1].rstrip() + "…"
    return "Jules task"
