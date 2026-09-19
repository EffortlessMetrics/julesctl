from __future__ import annotations

import json
import sqlite3
import time
import uuid
from dataclasses import dataclass

from .api.client import JulesApiClient
from .application.reconcile import reconcile_session_activities
from .config import Settings
from .domain.errors import AdmissionError, ApiError, IndeterminateError, InputError
from .domain.fingerprints import request_fingerprint, sha256_text
from .domain.models import DispatchSpec, SessionWire
from .domain.states import classify_state
from .store import StateStore
from .timestamps import GoogleTimestamp


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
        cls, settings: Settings, *, api: JulesApiClient | None = None
    ) -> JulesController:
        client = api or JulesApiClient(settings.api_key, base_url=settings.base_url)
        return cls(
            ControllerContext(
                settings, client, StateStore(settings.database_path, profile_name=settings.profile)
            )
        )

    @classmethod
    def from_env(cls) -> JulesController:
        return cls.from_settings(Settings.from_env())

    def close(self) -> None:
        self.ctx.api.close()
        self.ctx.store.close()

    def __enter__(self) -> JulesController:
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
                "raw_state": session.state,
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
        managed = self.ctx.store.managed_session_ids()
        result: list[dict[str, object]] = []
        sessions = list(self.ctx.api.iter_sessions())
        seen_ids = {session.id for session in sessions}
        for session in sessions:
            origin = "managed" if session.id in managed else "external"
            self._remember_session(session, origin=origin)
            result.append(self.normalize_session(session, origin=origin))
        if not all_history:
            self.ctx.store.reconcile_active_snapshot(seen_ids)
        return result

    def normalize_session(
        self, session: SessionWire, *, origin: str = "unknown"
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

    @staticmethod
    def _spec_fingerprint(
        *,
        source_name: str | None,
        starting_branch: str | None,
        prompt_hash: str,
        spec: DispatchSpec,
    ) -> str:
        return request_fingerprint(
            {
                "schema": 1,
                "source_name": source_name,
                "starting_branch": starting_branch,
                "prompt_sha256": prompt_hash,
                "title": spec.title,
                "require_plan_approval": spec.require_plan_approval,
                "automation_mode": (
                    "AUTO_CREATE_PR" if spec.auto_create_pr else "AUTOMATION_MODE_UNSPECIFIED"
                ),
                "environment_variables_enabled": None,
            }
        )

    @staticmethod
    def _request_body(
        *,
        spec: DispatchSpec,
        source_name: str | None,
        starting_branch: str | None,
    ) -> dict[str, object]:
        body: dict[str, object] = {"prompt": spec.prompt, "title": spec.title}
        if source_name:
            body["sourceContext"] = {
                "source": source_name,
                "githubRepoContext": {"startingBranch": starting_branch},
            }
        if spec.require_plan_approval:
            body["requirePlanApproval"] = True
        if spec.auto_create_pr:
            body["automationMode"] = "AUTO_CREATE_PR"
        return body

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
        fingerprint = self._spec_fingerprint(
            source_name=source_name,
            starting_branch=starting_branch,
            prompt_hash=prompt_hash,
            spec=spec,
        )
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
            attempt = self.ctx.store.get_attempt(str(existing["attempt_id"]))
            if attempt is None:
                raise InputError("dispatch work item has no attempt record")
            if attempt["state"] == "RESERVED":
                return self._send_reserved_attempt(
                    spec,
                    attempt_id=str(attempt["attempt_id"]),
                    source_name=source_name,
                    starting_branch=starting_branch,
                    fingerprint=fingerprint,
                    prompt_hash=prompt_hash,
                    reconcile_delays=reconcile_delays,
                )
            if attempt["state"] == "CANCELLED_LOCAL":
                raise AdmissionError("dispatch reservation was cancelled by fleet freeze")
            if attempt["state"] == "DEFINITIVELY_REJECTED":
                raise InputError(
                    "previous dispatch attempt was definitively rejected; "
                    "create an explicit new attempt before retrying"
                )
            return self.reconcile_attempt(str(existing["attempt_id"]), delays=reconcile_delays)

        remote_sessions = list(self.ctx.api.iter_sessions())
        managed = self.ctx.store.managed_session_ids()
        for session in remote_sessions:
            origin = "managed" if session.id in managed else "external"
            self._remember_session(session, origin=origin)
        self.ctx.store.reconcile_active_snapshot({session.id for session in remote_sessions})

        attempt_id = str(uuid.uuid4())
        request_body = self._request_body(
            spec=spec,
            source_name=source_name,
            starting_branch=starting_branch,
        )
        request_hash = request_fingerprint(
            {
                "schema": 1,
                "body": request_body,
            }
        )
        reservation = self.ctx.store.reserve_work(
            dispatch_key=spec.dispatch_key,
            fingerprint=fingerprint,
            attempt_id=attempt_id,
            request_fingerprint=request_hash,
            attempt={
                "source_name": source_name,
                "repo": spec.repo,
                "starting_branch": starting_branch,
                "working_branch": None,
                "title": spec.title,
                "prompt_sha256": prompt_hash,
                "require_plan_approval": spec.require_plan_approval,
                "automation_mode": (
                    "AUTO_CREATE_PR" if spec.auto_create_pr else "AUTOMATION_MODE_UNSPECIFIED"
                ),
                "environment_variables_enabled": None,
            },
            baseline_session_ids=[session.id for session in remote_sessions],
            max_occupancy=self.ctx.settings.new_work_target,
            max_starts_24h=max(
                self.ctx.settings.configured_rolling_start_limit
                - self.ctx.settings.rolling_start_reserve,
                0,
            ),
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
            return self.reconcile_attempt(str(reservation["attempt_id"]), delays=reconcile_delays)

        return self._send_reserved_attempt(
            spec,
            attempt_id=attempt_id,
            source_name=source_name,
            starting_branch=starting_branch,
            fingerprint=fingerprint,
            prompt_hash=prompt_hash,
            reconcile_delays=reconcile_delays,
        )

    def _send_reserved_attempt(
        self,
        spec: DispatchSpec,
        *,
        attempt_id: str,
        source_name: str | None,
        starting_branch: str | None,
        fingerprint: str,
        prompt_hash: str,
        reconcile_delays: tuple[float, ...],
    ) -> dict[str, object]:
        self.ctx.store.begin_send(
            attempt_id,
            send_started_at=GoogleTimestamp.now().raw,
        )
        body = self._request_body(
            spec=spec,
            source_name=source_name,
            starting_branch=starting_branch,
        )
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
        attempt = self.ctx.store.get_attempt(attempt_id)
        return {
            "outcome": "created",
            "session": self.normalize_session(session, origin="managed"),
            "dispatch_key": spec.dispatch_key,
            "fingerprint": fingerprint,
            "request_fingerprint": attempt["request_fingerprint"] if attempt else None,
            "attempt_id": attempt_id,
        }

    def _candidate_matches(self, attempt: sqlite3.Row, session: SessionWire) -> bool:
        source, start, working = self._session_source(session)
        if source != attempt["source_name"]:
            return False
        if start != attempt["starting_branch"]:
            return False
        if attempt["working_branch"] is not None and working != attempt["working_branch"]:
            return False
        if session.title != attempt["title"]:
            return False
        if session.prompt is None or sha256_text(session.prompt) != attempt["prompt_sha256"]:
            return False
        if session.require_plan_approval is not None:
            expected = bool(attempt["require_plan_approval"])
            if session.require_plan_approval is not expected:
                return False
        if (
            session.automation_mode is not None
            and session.automation_mode != attempt["automation_mode"]
        ):
            return False
        sent_at = attempt["send_started_at"]
        if sent_at and session.create_time:
            try:
                sent = GoogleTimestamp.parse(str(sent_at)).unix_nanoseconds
                created = GoogleTimestamp.parse(session.create_time).unix_nanoseconds
            except ValueError:
                return False
            if created < sent - 60_000_000_000:
                return False
        return True

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
                "request_fingerprint": attempt["request_fingerprint"],
            }
        if attempt["state"] == "RESERVED":
            raise InputError("reserved attempt has not begun network transmission")
        baseline = set(json.loads(attempt["baseline_session_ids_json"] or "[]"))
        candidates: list[SessionWire] = []
        for delay in delays:
            if delay:
                time.sleep(delay)
            candidates = []
            for listed in self.ctx.api.iter_sessions():
                if listed.id in baseline:
                    continue
                try:
                    session = self.ctx.api.get_session(listed.id)
                except ApiError as exc:
                    if exc.http_status == 404:
                        continue
                    raise
                if self._candidate_matches(attempt, session):
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
                    "request_fingerprint": attempt["request_fingerprint"],
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
        managed = self.ctx.store.managed_session_ids()
        sessions = list(self.ctx.api.iter_sessions())
        self.ctx.store.reconcile_active_snapshot({session.id for session in sessions})
        for session in sessions:
            origin = "managed" if session.id in managed else "external"
            self._remember_session(session, origin=origin)
            events.extend(
                reconcile_session_activities(
                    self.ctx.api,
                    self.ctx.store,
                    session.id,
                    origin=origin,
                )
            )
        return events

    def session_result(self, session_id: str) -> dict[str, object]:
        session = self.ctx.api.get_session(session_id)
        origin = "managed" if session.id in self.ctx.store.managed_session_ids() else "external"
        self._remember_session(session, origin=origin)
        activities = [
            activity.model_dump(by_alias=True, exclude_none=True)
            for activity in self.ctx.api.iter_activities(session.id)
        ]
        return {
            "session": self.normalize_session(session, origin=origin),
            "activities": activities,
        }

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
                failed.append({"session_id": sid, "status": exc.http_status, "error": str(exc)})
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
        unresolved = self.ctx.store.unresolved_attempt_count()
        occupied = sum(counts.values()) + unresolved
        return {
            "observed": {
                **counts,
                "unresolved_attempts": unresolved,
                "conservative_occupancy": occupied,
            },
            "configured_limits": {
                "concurrency": self.ctx.settings.configured_concurrency_limit,
                "rolling_24h_starts": self.ctx.settings.configured_rolling_start_limit,
                "new_work_target": self.ctx.settings.new_work_target,
                "reactive_reserve": self.ctx.settings.reactive_reserve,
                "rolling_start_reserve": self.ctx.settings.rolling_start_reserve,
            },
            "managed_starts_rolling_24h": self.ctx.store.starts_last_24h(),
            "available_new_work_slots": max(self.ctx.settings.new_work_target - occupied, 0),
            "coverage": "best_effort",
        }
