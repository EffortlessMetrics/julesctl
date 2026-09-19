from __future__ import annotations

import uuid
from collections.abc import Iterable

from .application.steering import approve_once, message_once
from .config import Settings
from .controller import JulesController
from .domain.models import ActivityWire, DispatchSpec


class JulesClient:
    """Stable Python facade over the controller and raw Jules adapter."""

    def __init__(self, controller: JulesController) -> None:
        self._controller = controller

    @classmethod
    def from_settings(cls, settings: Settings) -> JulesClient:
        return cls(JulesController.from_settings(settings))

    @classmethod
    def from_env(cls) -> JulesClient:
        return cls.from_settings(Settings.from_env())

    def close(self) -> None:
        self._controller.close()

    def __enter__(self) -> JulesClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def dispatch(self, spec: DispatchSpec) -> dict[str, object]:
        return self._controller.dispatch(spec)

    def create_session(
        self,
        *,
        prompt: str,
        repo: str | None = None,
        branch: str | None = None,
        title: str,
        dispatch_key: str | None = None,
        auto_create_pr: bool = True,
        require_plan_approval: bool = False,
    ) -> dict[str, object]:
        spec = DispatchSpec(
            dispatch_key=dispatch_key or f"manual:{uuid.uuid4()}",
            repo=repo,
            starting_branch=branch,
            title=title,
            prompt=prompt,
            auto_create_pr=auto_create_pr,
            require_plan_approval=require_plan_approval,
            caller="python",
        )
        return self.dispatch(spec)

    def list_sessions(self, *, all_history: bool = False) -> list[dict[str, object]]:
        return self._controller.list_sessions(all_history=all_history)

    def get_session(self, session_id: str) -> dict[str, object]:
        session = self._controller.ctx.api.get_session(session_id)
        origin = (
            "managed"
            if session.id in self._controller.ctx.store.managed_session_ids()
            else "external"
        )
        self._controller._remember_session(session, origin=origin)
        return self._controller.normalize_session(session, origin=origin)

    def iter_activities(self, session_id: str) -> Iterable[ActivityWire]:
        return self._controller.ctx.api.iter_activities(session_id)

    def send_message(self, session_id: str, prompt: str) -> dict[str, object]:
        return message_once(self._controller.ctx.api, session_id, prompt)

    def approve_plan(self, session_id: str) -> dict[str, object]:
        return approve_once(self._controller.ctx.api, session_id)

    def delete_session(self, session_id: str) -> bool:
        removed = self._controller.ctx.api.delete_session(session_id)
        self._controller.ctx.store.mark_deleted(session_id)
        return removed

    def result(self, session_id: str) -> dict[str, object]:
        return self._controller.session_result(session_id)

    def resolve_source(self, repo: str) -> dict[str, object]:
        return self._controller.resolve_source(repo)

    def reconcile(self) -> list[dict[str, object]]:
        return self._controller.reconcile()
