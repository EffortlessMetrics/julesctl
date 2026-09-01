from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass

from .api.client import JulesApiClient
from .config import Settings
from .domain.errors import ApiError, IndeterminateError, InputError
from .domain.fingerprints import request_fingerprint, sha256_text, short_digest
from .domain.models import DispatchSpec, SessionWire
from .domain.states import classify_state
from .store import StateStore


@dataclass(frozen=True)
class ControllerContext:
    settings: Settings
    api: JulesApiClient
    store: StateStore


class JulesController:
    def __init__(self, context: ControllerContext) -> None:
        self.ctx = context

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        *,
        api: JulesApiClient | None = None,
    ) -> "JulesController":
        client = api or JulesApiClient(settings.api_key, base_url=settings.base_url)
        return cls(ControllerContext(settings, client, StateStore(settings.database_path)))

    @classmethod
    def from_env(cls) -> "JulesController":
        return cls.from_settings(Settings.from_env())

    def close(self) -> None:
        self.ctx.api.close()
        self.ctx.store.close()

    def __enter__(self) -> "JulesController":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    @staticmethod
    def _session_pr(session: SessionWire) -> dict[str, str | None] | None:
        for output in session.outputs:
            if output.pull_request:
                pr = output.pull_request
                return {
                    "url": pr.url,
                    "title": pr.title,
                    "description": pr.description,
                    "base_ref": pr.base_ref,
                    "head_ref": pr.head_ref,
                }
        return None

    @staticmethod
    def _session_source(session: SessionWire) -> tuple[str | None, str | None, str | None]:
        context = session.source_context
        if not context:
            return None, None, None
        start = context.github_repo_context.starting_branch if context.github_repo_context else None
        return context.source, start, context.working_branch

    def _remember_session(
        self,
        session: SessionWire,
        *,
        origin: str,
        repo: str | None = None,
        prompt_sha256: str | None = None,
    ) -> None:
        source, start, working = self._session_source(session)
        state = classify_state(session.state)
        pr = self._session_pr(session)
        self.ctx.store.upsert_session(
            {
                "session_id": session.id,
                "session_name": session.name,
                "origin": origin,
                "raw_state": state.raw,
                "lifecycle": state.lifecycle,
                "archived": session.archived,
                "repo": repo,
                "source_name": source,
                "starting_branch": start,
                "working_branch": working,
                "title": session.title,
                "prompt_sha256": prompt_sha256
                or (sha256_text(session.prompt) if session.prompt else None),
                "pr_url": pr["url"] if pr else None,
            }
        )

    def auth_check(self) -> dict[str, object]:
        first = next(iter(self.ctx.api.iter_sources(page_size=1)), None)
        return {"ok": True, "source_access": first is not None}

    def resolve_source(self, repo: str) -> dict[str, object]:
        source = self.ctx.api.resolve_source(repo)
        default = (
            source.github_repo.default_branch.display_name
            if source.github_repo and source.github_repo.default_branch
            else None
        )
        return {"repo": repo, "source_name": source.name, "default_branch": default}

    def list_sessions(self, *, all_history: bool = False) -> list[dict[str, object]]:
        filter_value = "archived = true OR archived = false" if all_history else None
        known = self.ctx.store.known_session_ids()
        sessions = list(self.ctx.api.iter_sessions(filter_value=filter_value))
        result: list[dict[str, object]] = []
        for session in sessions:
            origin = "managed" if session.id in known else "external"
            self._remember_session(session, origin=origin)
            result.append(self.normalize_session(session, origin=origin))
        return result

    def normalize_session(
        self,
        session: SessionWire,
        *,
        origin: str = "unknown",
    ) -> dict[str, object]:
        state = classify_state(session.state)
        source, start, working = self._session_source(session)
        return {
            "id": session.id,
            "name": session.name,
            "title": session.title,
            "raw_state": state.raw,
            "lifecycle": state.lifecycle,
            "action_required": state.action_required,
            "archived": session.archived,
            "source_name": source,
            "starting_branch": start,
            "working_branch": working,
            "url": session.url,
            "pr": self._session_pr(session),
            "origin": origin,
            "create_time": session.create_time,
            "update_time": session.update_time,
        }

    def dispatch(
        self,
        spec: DispatchSpec,
        *,
        reconcile_delays: tuple[float, ...] = (0.0, 1.0, 2.0, 4.0, 8.0),
    ) -> dict[str, object]:
        source_name: str | None = None
        starting_branch = spec.starting_branch
        if spec.repo:
            source = self.ctx.api.resolve_source(spec.repo)
            source_name = source.name
            if not starting_branch:
                if not source.github_repo or not source.github_repo.default_branch:
                    raise InputError(
                        "starting branch was not supplied and source has no default branch"
                    )
                starting_branch = source.github_repo.default_branch.display_name
        elif starting_branch:
            raise InputError("starting_branch requires repo")

        prompt_hash = sha256_text(spec.prompt)
        fingerprint_data: dict[str, object] = {
            "schema": 1,
            "source_name": source_name,
            "starting_branch": starting_branch,
            "prompt_sha256": prompt_hash,
            "title": spec.title,
            "require_plan_approval": spec.require_plan_approval,
            "automation_mode": (
                "AUTO_CREATE_PR" if spec.auto_create_pr else "AUTOMATION_MODE_UNSPECIFIED"
            ),
            "environment_variables_enabled": False,
        }
        fingerprint = request_fingerprint(fingerprint_data)
        existing = self.ctx.store.get_work(spec.dispatch_key)
        if existing is not None:
            if existing["fingerprint"] != fingerprint:
                raise InputError("dispatch_key already exists with a different fingerprint")
            if existing["session_id"]:
                return {
                    "outcome": "existing",
                    "session_id": existing["session_id"],
                    "dispatch_key": spec.dispatch_key,
                    "fingerprint": fingerprint,
                    "attempt_id": existing["attempt_id"],
                }
            return self.reconcile_attempt(str(existing["attempt_id"]), delays=reconcile_delays)

        attempt_id = str(uuid.uuid4())
        working_branch = None
        if spec.repo:
            working_branch = (
                f"julesctl/{short_digest(spec.dispatch_key, 12)}/{short_digest(attempt_id, 8)}"
            )
        reservation = self.ctx.store.reserve_work(
            dispatch_key=spec.dispatch_key,
            fingerprint=fingerprint,
            attempt_id=attempt_id,
            attempt={
                "source_name": source_name,
                "repo": spec.repo,
                "starting_branch": starting_branch,
                "working_branch": working_branch,
                "title": spec.title,
                "prompt_sha256": prompt_hash,
            },
        )
        if reservation.get("attempt_id") != attempt_id:
            if reservation.get("session_id"):
                return {
                    "outcome": "existing",
                    "session_id": reservation["session_id"],
                    "dispatch_key": spec.dispatch_key,
                    "fingerprint": fingerprint,
                    "attempt_id": reservation["attempt_id"],
                }
            return self.reconcile_attempt(
                str(reservation["attempt_id"]), delays=reconcile_delays
            )

        body: dict[str, object] = {"prompt": spec.prompt, "title": spec.title}
        if source_name:
            body["sourceContext"] = {
                "source": source_name,
                "githubRepoContext": {"startingBranch": starting_branch},
                "workingBranch": working_branch,
                "environmentVariablesEnabled": False,
            }
        if spec.require_plan_approval:
            body["requirePlanApproval"] = True
        if spec.auto_create_pr:
            body["automationMode"] = "AUTO_CREATE_PR"

        try:
            session = self.ctx.api.create_session(body)
        except ApiError as exc:
            if not exc.create_outcome_uncertain:
                self.ctx.store.mark_attempt_error(
                    attempt_id,
                    "DEFINITIVELY_REJECTED",
                    http_status=exc.http_status,
                    api_status=exc.api_status,
                )
                raise
            self.ctx.store.mark_attempt_error(
                attempt_id,
                "RECONCILING",
                http_status=exc.http_status,
                api_status=exc.api_status,
            )
            result = self.reconcile_attempt(attempt_id, delays=reconcile_delays)
            result["original_http_status"] = exc.http_status
            result["original_api_status"] = exc.api_status
            return result

        self.ctx.store.bind_session(attempt_id, session.id, reconciled=False)
        self._remember_session(
            session,
            origin="managed",
            repo=spec.repo,
            prompt_sha256=prompt_hash,
        )
        return {
            "outcome": "created",
            "session": self.normalize_session(session, origin="managed"),
            "dispatch_key": spec.dispatch_key,
            "fingerprint": fingerprint,
            "attempt_id": attempt_id,
        }

    def reconcile_attempt(
        self,
        attempt_id: str,
        *,
        delays: tuple[float, ...] = (0.0, 1.0, 2.0, 4.0, 8.0),
    ) -> dict[str, object]:
        attempt = self.ctx.store.get_attempt(attempt_id)
        if not attempt:
            raise InputError(f"unknown attempt {attempt_id}")
        if attempt["session_id"]:
            return {
                "outcome": "existing",
                "session_id": attempt["session_id"],
                "attempt_id": attempt_id,
                "fingerprint": attempt["fingerprint"],
            }

        candidates: list[SessionWire] = []
        for delay in delays:
            if delay:
                time.sleep(delay)
            candidates = []
            for session in self.ctx.api.iter_sessions():
                source, start, working = self._session_source(session)
                if attempt["working_branch"] and working and working != attempt["working_branch"]:
                    continue
                if attempt["source_name"] and source != attempt["source_name"]:
                    continue
                if attempt["starting_branch"] and start and start != attempt["starting_branch"]:
                    continue
                if session.title and session.title != attempt["title"]:
                    continue
                if session.prompt and sha256_text(session.prompt) != attempt["prompt_sha256"]:
                    continue
                candidates.append(session)
            if len(candidates) == 1:
                session = candidates[0]
                self.ctx.store.bind_session(attempt_id, session.id, reconciled=True)
                self._remember_session(
                    session,
                    origin="managed",
                    repo=attempt["repo"],
                    prompt_sha256=attempt["prompt_sha256"],
                )
                return {
                    "outcome": "reconciled",
                    "session": self.normalize_session(session, origin="managed"),
                    "attempt_id": attempt_id,
                    "fingerprint": attempt["fingerprint"],
                }
            if len(candidates) > 1:
                break

        ids = [session.id for session in candidates]
        state = "INDETERMINATE_MULTIPLE" if len(ids) > 1 else "INDETERMINATE_NONE"
        self.ctx.store.mark_attempt_error(attempt_id, state)
        raise IndeterminateError(
            f"create outcome is indeterminate; candidate sessions: {ids or 'none'}"
        )

    def reconcile(self) -> list[dict[str, object]]:
        events: list[dict[str, object]] = []
        known = self.ctx.store.known_session_ids()
        for session in self.ctx.api.iter_sessions():
            origin = "managed" if session.id in known else "external"
            self._remember_session(session, origin=origin)
            for activity in self.ctx.api.iter_activities(session.id):
                payload = json.dumps(
                    activity.model_dump(by_alias=True, exclude_none=True),
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                    default=str,
                )
                is_new = self.ctx.store.record_activity(
                    session_id=session.id,
                    activity_name=activity.name,
                    activity_id=activity.id,
                    create_time=activity.create_time,
                    event_type=activity.event_type(),
                    payload_sha256=sha256_text(payload),
                )
                if is_new:
                    events.append(
                        {
                            "type": activity.event_type(),
                            "session_id": session.id,
                            "activity_id": activity.id,
                            "activity_name": activity.name,
                            "create_time": activity.create_time,
                            "origin": origin,
                        }
                    )
        return events

    def session_result(self, session_id: str) -> dict[str, object]:
        session = self.ctx.api.get_session(session_id)
        origin = "managed" if session.id in self.ctx.store.known_session_ids() else "external"
        self._remember_session(session, origin=origin)
        activities = [
            activity.model_dump(by_alias=True, exclude_none=True)
            for activity in self.ctx.api.iter_activities(session.id)
        ]
        return {"session": self.normalize_session(session, origin=origin), "activities": activities}

    def create_drain_plan(self) -> dict[str, object]:
        self.list_sessions(all_history=False)
        targets = [
            {
                "session_id": row["session_id"],
                "snapshot_state": row["raw_state"],
                "snapshot_archived": (
                    bool(row["archived"]) if row["archived"] is not None else None
                ),
            }
            for row in self.ctx.store.active_rows()
            if row["lifecycle"] != "unknown"
        ]
        plan_id = "drain_" + uuid.uuid4().hex[:16]
        selector = {
            "lifecycles": ["executing", "actionable", "paused"],
            "include_unknown": False,
        }
        self.ctx.store.create_deletion_plan(plan_id, selector, targets)
        return {"plan_id": plan_id, "selector": selector, "targets": targets}

    def apply_deletion_plan(self, plan_id: str) -> dict[str, object]:
        _selector, targets = self.ctx.store.get_deletion_plan(plan_id)
        deleted = 0
        already_absent = 0
        failed: list[dict[str, object]] = []
        for target in targets:
            sid = str(target["session_id"])
            try:
                removed = self.ctx.api.delete_session(sid)
                self.ctx.store.mark_deleted(sid)
                if removed:
                    deleted += 1
                else:
                    already_absent += 1
            except ApiError as exc:
                failed.append(
                    {"session_id": sid, "status": exc.http_status, "error": str(exc)}
                )
        return {
            "plan_id": plan_id,
            "deleted": deleted,
            "already_absent": already_absent,
            "failed": failed,
            "outcome": "partial" if failed else "completed",
        }

    def capacity(self) -> dict[str, object]:
        self.list_sessions(all_history=False)
        rows = self.ctx.store.active_rows()
        counts = {"executing": 0, "actionable": 0, "paused": 0, "unknown": 0}
        for row in rows:
            lifecycle = row["lifecycle"]
            if lifecycle in counts:
                counts[lifecycle] += 1
        occupied = sum(counts.values())
        return {
            "observed": {**counts, "conservative_occupancy": occupied},
            "configured_limits": {
                "concurrency": self.ctx.settings.configured_concurrency_limit,
                "rolling_24h_starts": self.ctx.settings.configured_rolling_start_limit,
                "new_work_target": self.ctx.settings.new_work_target,
                "reactive_reserve": self.ctx.settings.reactive_reserve,
            },
            "available_new_work_slots": max(
                self.ctx.settings.new_work_target - occupied, 0
            ),
            "coverage": "best_effort",
        }
