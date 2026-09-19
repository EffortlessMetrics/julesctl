import json
from pathlib import Path

import jsonschema
from typer.testing import CliRunner

from julesctl.cli.app import app
from julesctl.cli.output import event, operation

runner = CliRunner()
ROOT = Path(__file__).resolve().parents[1]


def _schema(name: str) -> dict[str, object]:
    return json.loads((ROOT / "contracts" / "schemas" / name).read_text(encoding="utf-8"))


def test_freeze_works_without_api_key_and_emits_valid_json(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("JULESCTL_HOME", str(tmp_path))
    monkeypatch.delenv("JULES_API_KEY", raising=False)
    result = runner.invoke(app, ["fleet", "freeze", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    jsonschema.validate(payload, _schema("operation-v1.schema.json"))
    assert payload["command"] == "fleet.freeze"
    assert payload["data"]["frozen"] is True


def test_human_freeze_uses_stdout_not_stderr(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("JULESCTL_HOME", str(tmp_path))
    monkeypatch.delenv("JULES_API_KEY", raising=False)
    result = runner.invoke(app, ["fleet", "freeze"])
    assert result.exit_code == 0, result.output
    assert "frozen" in result.stdout
    assert result.stderr == ""


def test_missing_api_key_is_structured_authentication_error(monkeypatch) -> None:
    monkeypatch.delenv("JULES_API_KEY", raising=False)
    result = runner.invoke(app, ["auth", "check", "--json"])
    assert result.exit_code == 3
    payload = json.loads(result.stdout)
    jsonschema.validate(payload, _schema("operation-v1.schema.json"))
    assert payload["error"]["kind"] == "authentication_failed"


def test_operation_and_event_examples_validate() -> None:
    operation_payload = operation("test", "completed", {"ok": True})
    event_payload = event({"type": "completed", "session_id": "123"})
    jsonschema.validate(operation_payload, _schema("operation-v1.schema.json"))
    jsonschema.validate(event_payload, _schema("event-v1.schema.json"))
