from __future__ import annotations

import uuid
from collections.abc import Iterable

from .application.artifacts import collect_artifacts, select_patch, select_pull_request
from .application.prune import apply_plan_with_settle, select_prune_targets, snapshot_targets
from .application.sessions import filter_sessions
from .application.steering import approve_once, message_once
from .application.watch import watch_session
from .config import Settings
from .controller import JulesController
from .domain.errors import InputError
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

    def list_sessions(
        self,
        *,
        all_history: bool = False,
        states: list[str] | None = None,
        repo: str | None = None,
        since: str | None = None,
        older_than: str | None = None,
        active: bool = False,
        nonterminal: bool = False,
    ) -> list[dict[str, object]]:
        sessions = self._controller.list_sessions(all_history=all_history)
        source_name: str | None = None
        if repo is not None:
            source_name = str(self.resolve_source(repo)["source_name"])
        return filter_sessions(
            sessions,
            states=states,
            source_name=source_name,
            since=since,
            older_than=older_than,
            active=active,
            nonterminal=nonterminal,
        )

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

    def watch(
        self,
        session_id: str,
        *,
        poll_interval_seconds: float = 3.0,
        settle_seconds: float = 1.0,
        timeout_seconds: float | None = None,
        max_polls: int | None = None,
    ) -> Iterable[dict[str, object]]:
        session = self._controller.ctx.api.get_session(session_id)
        origin = (
            "managed"
            if session.id in self._controller.ctx.store.managed_session_ids()
            else "external"
        )
        return watch_session(
            self._controller.ctx.api,
            self._controller.ctx.store,
            session.id,
            origin=origin,
            poll_interval_seconds=poll_interval_seconds,
            settle_seconds=settle_seconds,
            timeout_seconds=timeout_seconds,
            max_polls=max_polls,
        )

    def send_message(self, session_id: str, prompt: str) -> dict[str, object]:
        return message_once(self._controller.ctx.api, session_id, prompt)

    def approve_plan(self, session_id: str) -> dict[str, object]:
        return approve_once(self._controller.ctx.api, session_id)

    def delete_session(self, session_id: str) -> bool:
        removed = self._controller.ctx.api.delete_session(session_id)
        self._controller.ctx.store.mark_deleted(session_id)
        return removed

    def result(self, session_id: str) -> dict[str, object]:
        session = self._controller.ctx.api.get_session(session_id)
        activities = list(self._controller.ctx.api.iter_activities(session.id))
        origin = (
            "managed"
            if session.id in self._controller.ctx.store.managed_session_ids()
            else "external"
        )
        self._controller._remember_session(session, origin=origin)
        return {
            "session": self._controller.normalize_session(session, origin=origin),
            **collect_artifacts(session, activities),
            "activities": [
                activity.model_dump(by_alias=True, exclude_none=True) for activity in activities
            ],
        }

    def patch(self, session_id: str, *, index: int = -1) -> dict[str, object]:
        return select_patch(self.result(session_id), index=index)

    def pull_request(
        self,
        session_id: str,
        *,
        index: int = -1,
    ) -> dict[str, object]:
        return select_pull_request(self.result(session_id), index=index)

    def remove_sessions(
        self,
        session_ids: list[str],
        *,
        max_workers: int = 4,
    ) -> dict[str, object]:
        if not session_ids:
            raise InputError("at least one session ID is required")
        targets: list[dict[str, object]] = [
            {"session_id": value.removeprefix("sessions/")} for value in session_ids
        ]
        plan_id = "rm_" + uuid.uuid4().hex[:16]
        selector: dict[str, object] = {"explicit_session_ids": list(session_ids)}
        self._controller.ctx.store.create_deletion_plan(plan_id, selector, targets)
        return apply_plan_with_settle(
            api=self._controller.ctx.api,
            store=self._controller.ctx.store,
            plan_id=plan_id,
            list_sessions=lambda: self.list_sessions(all_history=True),
            selector={"all_sessions": False, "states": ["__never__"]},
            initial_targets=targets,
            max_workers=max_workers,
        )

    def plan_prune(
        self,
        *,
        states: list[str] | None = None,
        repo: str | None = None,
        older_than: str | None = None,
        nonterminal: bool = False,
        all_sessions: bool = False,
        include_unknown: bool = False,
    ) -> dict[str, object]:
        source_name: str | None = None
        if repo is not None:
            source_name = str(self.resolve_source(repo)["source_name"])
        selector: dict[str, object] = {
            "states": states or [],
            "source_name": source_name,
            "repo": repo,
            "older_than": older_than,
            "nonterminal": nonterminal,
            "all_sessions": all_sessions,
            "include_unknown": include_unknown,
        }
        sessions = self.list_sessions(all_history=True)
        selected = select_prune_targets(
            sessions,
            states=states,
            source_name=source_name,
            older_than=older_than,
            nonterminal=nonterminal,
            all_sessions=all_sessions,
            include_unknown=include_unknown,
        )
        targets = snapshot_targets(selected)
        plan_id = "prune_" + uuid.uuid4().hex[:16]
        self._controller.ctx.store.create_deletion_plan(plan_id, selector, targets)
        return {
            "plan_id": plan_id,
            "selector": selector,
            "targets": targets,
            "outcome": "planned",
        }

    def apply_prune(
        self,
        plan_id: str,
        *,
        max_workers: int = 4,
        settle_seconds: float = 0.0,
        passes: int = 1,
    ) -> dict[str, object]:
        selector, targets = self._controller.ctx.store.get_deletion_plan(plan_id)
        return apply_plan_with_settle(
            api=self._controller.ctx.api,
            store=self._controller.ctx.store,
            plan_id=plan_id,
            list_sessions=lambda: self.list_sessions(all_history=True),
            selector=selector,
            initial_targets=targets,
            max_workers=max_workers,
            settle_seconds=settle_seconds,
            passes=passes,
        )

    def retry_session(
        self,
        session_id: str,
        *,
        dispatch_key: str,
        title: str | None = None,
    ) -> dict[str, object]:
        if not dispatch_key.strip():
            raise InputError("retry requires a non-empty dispatch key")
        session = self._controller.ctx.api.get_session(session_id)
        if session.prompt is None:
            raise InputError("session does not expose its original prompt")
        source_name, starting_branch, _working_branch = self._controller._session_source(session)
        repo: str | None = None
        if source_name is not None:
            matches = [
                source
                for source in self._controller.ctx.api.iter_sources()
                if source.name == source_name and source.github_repo is not None
            ]
            if len(matches) != 1 or matches[0].github_repo is None:
                raise InputError(
                    f"cannot resolve source {source_name!r} back to one connected repository"
                )
            repo = f"{matches[0].github_repo.owner}/{matches[0].github_repo.repo}"
        spec = DispatchSpec(
            dispatch_key=dispatch_key,
            repo=repo,
            starting_branch=starting_branch,
            title=title or session.title or f"Retry {session.id}",
            prompt=session.prompt,
            auto_create_pr=session.automation_mode == "AUTO_CREATE_PR",
            require_plan_approval=bool(session.require_plan_approval),
            caller="retry",
            source_refs=[session.name],
        )
        result = self.dispatch(spec)
        return {
            **result,
            "retry_of_session_id": session.id,
            "retry_of_session_name": session.name,
        }

    def resolve_source(self, repo: str) -> dict[str, object]:
        return self._controller.resolve_source(repo)

    def reconcile(self) -> list[dict[str, object]]:
        return self._controller.reconcile()
