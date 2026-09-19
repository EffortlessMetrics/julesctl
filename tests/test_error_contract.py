from __future__ import annotations

import json

from typer.testing import CliRunner

from julesctl.cli.app import app
from julesctl.cli.common import error_details, redact_text
from julesctl.domain.errors import ApiError, InputError

runner = CliRunner()


def test_redaction_removes_environment_and_header_forms(monkeypatch) -> None:
    monkeypatch.setenv("JULES_API_KEY", "super-secret-key")
    value = redact_text(
        "JULES_API_KEY=super-secret-key x-goog-api-key: another-secret super-secret-key"
    )
    assert "super-secret-key" not in value
    assert "another-secret" not in value
    assert value.count("[REDACTED]") == 3


def test_redaction_removes_url_userinfo_from_error_contract() -> None:
    remote = "https://git-user:remote-token@example.invalid/owner/repo.git"
    value = error_details(InputError(f"invalid origin: {remote}"))
    assert "git-user" not in value["message"]
    assert "remote-token" not in value["message"]
    assert "https://[REDACTED]@example.invalid/owner/repo.git" in value["message"]


def test_error_contract_uses_stable_kinds_and_retry_evidence() -> None:
    invalid = error_details(InputError("bad input"))
    assert invalid["kind"] == "invalid_input"

    api = error_details(
        ApiError(
            "busy",
            http_status=429,
            api_status="RESOURCE_EXHAUSTED",
            retry_after_seconds=7.0,
        )
    )
    assert api == {
        "kind": "create_outcome_unknown",
        "message": "busy",
        "http_status": 429,
        "api_status": "RESOURCE_EXHAUSTED",
        "transient": True,
        "safe_to_retry": False,
        "reconcile_required": True,
        "retry_after_seconds": 7.0,
    }


def test_queue_cli_uses_common_machine_error_contract(monkeypatch) -> None:
    monkeypatch.delenv("JULES_API_KEY", raising=False)
    result = runner.invoke(app, ["worker", "run-once", "--json"])
    assert result.exit_code == 3
    payload = json.loads(result.stdout)
    assert payload["schema"] == "julesctl.operation.v1"
    assert payload["error"]["kind"] == "authentication_failed"
