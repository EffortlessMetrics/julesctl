from io import StringIO
from pathlib import Path

import pytest

from julesctl.application.specs import derive_title, read_prompt
from julesctl.domain.errors import InputError


class FakeStdin(StringIO):
    def __init__(self, value: str, *, tty: bool = False) -> None:
        super().__init__(value)
        self._tty = tty

    def isatty(self) -> bool:
        return self._tty


def test_prompt_argument_is_preserved() -> None:
    assert read_prompt("line one\r\nline two", file=None) == "line one\r\nline two"


def test_prompt_file_and_argument_conflict(tmp_path: Path) -> None:
    path = tmp_path / "task.md"
    path.write_text("file", encoding="utf-8")
    with pytest.raises(InputError, match="mutually exclusive"):
        read_prompt("argument", file=path)


def test_dash_reads_stdin() -> None:
    assert read_prompt("-", file=None, stdin=FakeStdin("from stdin\n")) == "from stdin\n"


def test_missing_prompt_on_tty_fails() -> None:
    with pytest.raises(InputError, match="provide a prompt"):
        read_prompt(None, file=None, stdin=FakeStdin("", tty=True))


def test_symlinked_prompt_file_is_rejected(tmp_path: Path) -> None:
    target = tmp_path / "target.md"
    target.write_text("task", encoding="utf-8")
    link = tmp_path / "link.md"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symlinks unavailable")
    with pytest.raises(InputError, match="symlink"):
        read_prompt(None, file=link)


def test_title_uses_first_content_line_and_truncates() -> None:
    assert derive_title("\n## Build the thing\nrest") == "Build the thing"
    value = derive_title("x" * 120, limit=20)
    assert len(value) == 20
    assert value.endswith("…")
