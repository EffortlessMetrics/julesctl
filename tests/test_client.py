from __future__ import annotations

from types import SimpleNamespace

from julesctl.client import JulesClient
from julesctl.domain.models import DispatchSpec


class FakeController:
    def __init__(self) -> None:
        self.dispatched: list[DispatchSpec] = []
        self.closed = False
        self.ctx = SimpleNamespace()

    def close(self) -> None:
        self.closed = True

    def dispatch(self, spec: DispatchSpec) -> dict[str, object]:
        self.dispatched.append(spec)
        return {"outcome": "created", "dispatch_key": spec.dispatch_key}


def test_python_client_builds_dispatch_spec() -> None:
    controller = FakeController()
    client = JulesClient(controller)  # type: ignore[arg-type]
    result = client.create_session(
        prompt="Do it",
        repo="acme/repo",
        branch="main",
        title="Do it",
        dispatch_key="work:1",
        auto_create_pr=False,
        require_plan_approval=True,
    )
    assert result["outcome"] == "created"
    spec = controller.dispatched[0]
    assert spec.dispatch_key == "work:1"
    assert spec.repo == "acme/repo"
    assert spec.starting_branch == "main"
    assert spec.auto_create_pr is False
    assert spec.require_plan_approval is True
    client.close()
    assert controller.closed is True
