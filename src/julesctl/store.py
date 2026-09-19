from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .domain.errors import AdmissionError, InputError

_UNRESOLVED_ATTEMPT_STATES = (
    "RESERVED",
    "SEND_STARTED",
    "RECONCILING",
    "INDETERMINATE_NONE",
    "INDETERMINATE_MULTIPLE",
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS work_items (
    dispatch_key TEXT PRIMARY KEY,
    fingerprint TEXT NOT NULL,
    attempt_id TEXT,
    session_id TEXT,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS dispatch_attempts (
    attempt_id TEXT PRIMARY KEY,
    dispatch_key TEXT NOT NULL,
    request_fingerprint TEXT NOT NULL,
    source_name TEXT,
    repo TEXT,
    starting_branch TEXT,
    working_branch TEXT,
    title TEXT NOT NULL,
    prompt_sha256 TEXT NOT NULL,
    require_plan_approval INTEGER NOT NULL DEFAULT 0,
    automation_mode TEXT NOT NULL,
    environment_variables_enabled INTEGER,
    state TEXT NOT NULL,
    session_id TEXT,
    reservation_generation INTEGER NOT NULL,
    baseline_session_ids_json TEXT NOT NULL,
    reserved_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    send_started_at TEXT,
    resolved_at TEXT,
    initial_http_status INTEGER,
    initial_api_status TEXT,
    last_reconcile_at TEXT,
    FOREIGN KEY(dispatch_key) REFERENCES work_items(dispatch_key)
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_attempt_session
ON dispatch_attempts(session_id)
WHERE session_id IS NOT NULL;
CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    session_name TEXT NOT NULL,
    origin TEXT NOT NULL,
    raw_state TEXT,
    lifecycle TEXT NOT NULL,
    archived INTEGER,
    repo TEXT,
    source_name TEXT,
    starting_branch TEXT,
    working_branch TEXT,
    title TEXT,
    prompt_sha256 TEXT,
    pr_url TEXT,
    first_observed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_observed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    deleted_at TEXT
);
CREATE TABLE IF NOT EXISTS activity_receipts (
    session_id TEXT NOT NULL,
    activity_name TEXT NOT NULL,
    activity_id TEXT,
    create_time TEXT,
    event_type TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL,
    first_observed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    first_emitted_at TEXT,
    PRIMARY KEY(session_id, activity_name)
);
CREATE TABLE IF NOT EXISTS deletion_plans (
    plan_id TEXT PRIMARY KEY,
    selector_json TEXT NOT NULL,
    targets_json TEXT NOT NULL,
    state TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_sessions_lifecycle ON sessions(lifecycle, archived);
CREATE INDEX IF NOT EXISTS idx_attempts_state ON dispatch_attempts(state);
CREATE INDEX IF NOT EXISTS idx_attempts_started ON dispatch_attempts(send_started_at);
"""


class StateStore:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._conn = sqlite3.connect(path, timeout=5, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.executescript(_SCHEMA)
        self._conn.execute(
            "INSERT OR IGNORE INTO meta(key,value) VALUES('fleet_frozen','0')"
        )
        self._conn.execute(
            "INSERT OR IGNORE INTO meta(key,value) VALUES('fleet_generation','0')"
        )

    def close(self) -> None:
        self._conn.close()

    @contextmanager
    def immediate(self) -> Iterator[sqlite3.Connection]:
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            yield self._conn
        except Exception:
            self._conn.execute("ROLLBACK")
            raise
        else:
            self._conn.execute("COMMIT")

    @staticmethod
    def _meta_value(conn: sqlite3.Connection, key: str, default: str) -> str:
        row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return str(row["value"]) if row is not None else default

    def is_frozen(self) -> bool:
        return self._meta_value(self._conn, "fleet_frozen", "0") == "1"

    def fleet_generation(self) -> int:
        return int(self._meta_value(self._conn, "fleet_generation", "0"))

    def set_frozen(self, value: bool) -> int:
        with self.immediate() as conn:
            generation = int(self._meta_value(conn, "fleet_generation", "0")) + 1
            conn.execute(
                "INSERT INTO meta(key,value) VALUES('fleet_generation',?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (str(generation),),
            )
            conn.execute(
                "INSERT INTO meta(key,value) VALUES('fleet_frozen',?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                ("1" if value else "0",),
            )
            if value:
                rows = list(
                    conn.execute(
                        "SELECT attempt_id,dispatch_key FROM dispatch_attempts "
                        "WHERE state='RESERVED'"
                    )
                )
                if rows:
                    conn.execute(
                        "UPDATE dispatch_attempts SET state='CANCELLED_LOCAL',resolved_at=CURRENT_TIMESTAMP "
                        "WHERE state='RESERVED'"
                    )
                    for row in rows:
                        conn.execute(
                            "UPDATE work_items SET status='CANCELLED_LOCAL',updated_at=CURRENT_TIMESTAMP "
                            "WHERE dispatch_key=?",
                            (row["dispatch_key"],),
                        )
            return generation

    def get_work(self, dispatch_key: str) -> sqlite3.Row | None:
        return self._conn.execute(
            "SELECT * FROM work_items WHERE dispatch_key=?", (dispatch_key,)
        ).fetchone()

    def starts_last_24h(self) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) AS n FROM dispatch_attempts "
            "WHERE send_started_at >= datetime('now','-24 hours')"
        ).fetchone()
        return int(row["n"] if row else 0)

    def unresolved_attempt_count(self) -> int:
        marks = ",".join("?" for _ in _UNRESOLVED_ATTEMPT_STATES)
        row = self._conn.execute(
            f"SELECT COUNT(*) AS n FROM dispatch_attempts WHERE state IN ({marks})",
            _UNRESOLVED_ATTEMPT_STATES,
        ).fetchone()
        return int(row["n"] if row else 0)

    def reserve_work(
        self,
        *,
        dispatch_key: str,
        fingerprint: str,
        attempt_id: str,
        request_fingerprint: str,
        attempt: dict[str, object],
        baseline_session_ids: list[str],
        max_occupancy: int | None = None,
        max_starts_24h: int | None = None,
    ) -> dict[str, str | None]:
        with self.immediate() as conn:
            if self._meta_value(conn, "fleet_frozen", "0") == "1":
                raise AdmissionError("fleet admission is frozen")
            existing = conn.execute(
                "SELECT * FROM work_items WHERE dispatch_key=?", (dispatch_key,)
            ).fetchone()
            if existing:
                if existing["fingerprint"] != fingerprint:
                    raise InputError("dispatch_key already exists with a different fingerprint")
                return dict(existing)
            if max_occupancy is not None:
                active = conn.execute(
                    "SELECT COUNT(*) AS n FROM sessions WHERE deleted_at IS NULL "
                    "AND lifecycle IN ('executing','actionable','paused','unknown')"
                ).fetchone()
                marks = ",".join("?" for _ in _UNRESOLVED_ATTEMPT_STATES)
                unresolved = conn.execute(
                    f"SELECT COUNT(*) AS n FROM dispatch_attempts WHERE state IN ({marks})",
                    _UNRESOLVED_ATTEMPT_STATES,
                ).fetchone()
                occupancy = int(active["n"] if active else 0) + int(
                    unresolved["n"] if unresolved else 0
                )
                if occupancy >= max_occupancy:
                    raise AdmissionError(
                        f"new-work admission is full ({occupancy}/{max_occupancy})"
                    )
            if max_starts_24h is not None:
                recent = conn.execute(
                    "SELECT COUNT(*) AS n FROM dispatch_attempts "
                    "WHERE send_started_at >= datetime('now','-24 hours')"
                ).fetchone()
                starts = int(recent["n"] if recent else 0)
                if starts >= max_starts_24h:
                    raise AdmissionError(
                        f"rolling start budget is reserved ({starts}/{max_starts_24h})"
                    )
            generation = int(self._meta_value(conn, "fleet_generation", "0"))
            conn.execute(
                "INSERT INTO work_items(dispatch_key,fingerprint,attempt_id,status) VALUES(?,?,?,?)",
                (dispatch_key, fingerprint, attempt_id, "RESERVED"),
            )
            conn.execute(
                """INSERT INTO dispatch_attempts(
                    attempt_id,dispatch_key,request_fingerprint,source_name,repo,starting_branch,
                    working_branch,title,prompt_sha256,require_plan_approval,automation_mode,
                    environment_variables_enabled,state,reservation_generation,
                    baseline_session_ids_json
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    attempt_id,
                    dispatch_key,
                    request_fingerprint,
                    attempt.get("source_name"),
                    attempt.get("repo"),
                    attempt.get("starting_branch"),
                    attempt.get("working_branch"),
                    attempt["title"],
                    attempt["prompt_sha256"],
                    1 if attempt.get("require_plan_approval") else 0,
                    attempt["automation_mode"],
                    (
                        1
                        if attempt.get("environment_variables_enabled") is True
                        else 0
                        if attempt.get("environment_variables_enabled") is False
                        else None
                    ),
                    "RESERVED",
                    generation,
                    json.dumps(sorted(baseline_session_ids)),
                ),
            )
            return {
                "dispatch_key": dispatch_key,
                "fingerprint": fingerprint,
                "attempt_id": attempt_id,
                "session_id": None,
                "status": "RESERVED",
            }

    def begin_send(self, attempt_id: str, *, send_started_at: str) -> None:
        with self.immediate() as conn:
            row = conn.execute(
                "SELECT state,reservation_generation,dispatch_key FROM dispatch_attempts "
                "WHERE attempt_id=?",
                (attempt_id,),
            ).fetchone()
            if row is None:
                raise InputError(f"unknown attempt {attempt_id}")
            if row["state"] != "RESERVED":
                raise InputError(
                    f"attempt {attempt_id} cannot begin send from state {row['state']}"
                )
            if self._meta_value(conn, "fleet_frozen", "0") == "1":
                raise AdmissionError("fleet admission is frozen")
            generation = int(self._meta_value(conn, "fleet_generation", "0"))
            if int(row["reservation_generation"]) != generation:
                raise AdmissionError("dispatch reservation was invalidated by a fleet fence")
            conn.execute(
                "UPDATE dispatch_attempts SET state='SEND_STARTED',send_started_at=? "
                "WHERE attempt_id=?",
                (send_started_at, attempt_id),
            )
            conn.execute(
                "UPDATE work_items SET status='SEND_STARTED',updated_at=CURRENT_TIMESTAMP "
                "WHERE dispatch_key=?",
                (row["dispatch_key"],),
            )

    def get_attempt(self, attempt_id: str) -> sqlite3.Row | None:
        return self._conn.execute(
            "SELECT * FROM dispatch_attempts WHERE attempt_id=?", (attempt_id,)
        ).fetchone()

    def mark_attempt_error(
        self,
        attempt_id: str,
        state: str,
        *,
        http_status: int | None = None,
        api_status: str | None = None,
    ) -> None:
        with self.immediate() as conn:
            row = conn.execute(
                "SELECT dispatch_key FROM dispatch_attempts WHERE attempt_id=?", (attempt_id,)
            ).fetchone()
            if not row:
                raise InputError(f"unknown attempt {attempt_id}")
            conn.execute(
                """UPDATE dispatch_attempts SET
                    state=?,
                    initial_http_status=COALESCE(initial_http_status,?),
                    initial_api_status=COALESCE(initial_api_status,?),
                    last_reconcile_at=CURRENT_TIMESTAMP
                WHERE attempt_id=?""",
                (state, http_status, api_status, attempt_id),
            )
            conn.execute(
                "UPDATE work_items SET status=?,updated_at=CURRENT_TIMESTAMP WHERE dispatch_key=?",
                (state, row["dispatch_key"]),
            )

    def bind_session(self, attempt_id: str, session_id: str, *, reconciled: bool) -> None:
        state = "ADOPTED" if reconciled else "CONFIRMED"
        with self.immediate() as conn:
            row = conn.execute(
                "SELECT dispatch_key FROM dispatch_attempts WHERE attempt_id=?", (attempt_id,)
            ).fetchone()
            if not row:
                raise InputError(f"unknown attempt {attempt_id}")
            conflict = conn.execute(
                "SELECT attempt_id FROM dispatch_attempts "
                "WHERE session_id=? AND attempt_id<>?",
                (session_id, attempt_id),
            ).fetchone()
            if conflict:
                raise InputError(
                    f"session {session_id} is already bound to attempt {conflict['attempt_id']}"
                )
            conn.execute(
                "UPDATE dispatch_attempts SET state=?,session_id=?,resolved_at=CURRENT_TIMESTAMP "
                "WHERE attempt_id=?",
                (state, session_id, attempt_id),
            )
            conn.execute(
                "UPDATE work_items SET status=?,session_id=?,updated_at=CURRENT_TIMESTAMP "
                "WHERE dispatch_key=?",
                (state, session_id, row["dispatch_key"]),
            )

    def upsert_session(self, row: dict[str, object]) -> None:
        self._conn.execute(
            """INSERT INTO sessions(
                session_id,session_name,origin,raw_state,lifecycle,archived,repo,source_name,
                starting_branch,working_branch,title,prompt_sha256,pr_url
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(session_id) DO UPDATE SET
                session_name=excluded.session_name,
                origin=CASE WHEN excluded.origin='managed' THEN 'managed' ELSE sessions.origin END,
                raw_state=COALESCE(excluded.raw_state,sessions.raw_state),
                lifecycle=CASE
                    WHEN excluded.raw_state IS NULL THEN sessions.lifecycle
                    ELSE excluded.lifecycle
                END,
                archived=COALESCE(excluded.archived,sessions.archived),
                repo=COALESCE(excluded.repo,sessions.repo),
                source_name=COALESCE(excluded.source_name,sessions.source_name),
                starting_branch=COALESCE(excluded.starting_branch,sessions.starting_branch),
                working_branch=COALESCE(excluded.working_branch,sessions.working_branch),
                title=COALESCE(excluded.title,sessions.title),
                prompt_sha256=COALESCE(excluded.prompt_sha256,sessions.prompt_sha256),
                pr_url=COALESCE(excluded.pr_url,sessions.pr_url),
                last_observed_at=CURRENT_TIMESTAMP
            """,
            (
                row["session_id"],
                row["session_name"],
                row["origin"],
                row.get("raw_state"),
                row["lifecycle"],
                1 if row.get("archived") else 0 if row.get("archived") is not None else None,
                row.get("repo"),
                row.get("source_name"),
                row.get("starting_branch"),
                row.get("working_branch"),
                row.get("title"),
                row.get("prompt_sha256"),
                row.get("pr_url"),
            ),
        )

    def known_session_ids(self) -> set[str]:
        return {row[0] for row in self._conn.execute("SELECT session_id FROM sessions")}

    def managed_session_ids(self) -> set[str]:
        return {
            row[0]
            for row in self._conn.execute(
                "SELECT session_id FROM dispatch_attempts WHERE session_id IS NOT NULL"
            )
        }

    def reconcile_active_snapshot(self, seen_ids: set[str]) -> None:
        rows = list(
            self._conn.execute(
                "SELECT session_id FROM sessions WHERE deleted_at IS NULL "
                "AND lifecycle IN ('executing','actionable','paused','unknown')"
            )
        )
        missing = [row[0] for row in rows if row[0] not in seen_ids]
        if not missing:
            return
        placeholders = ",".join("?" for _ in missing)
        self._conn.execute(
            f"UPDATE sessions SET lifecycle='not_visible',last_observed_at=CURRENT_TIMESTAMP "
            f"WHERE session_id IN ({placeholders})",
            missing,
        )

    def record_activity(
        self,
        *,
        session_id: str,
        activity_name: str,
        activity_id: str | None,
        create_time: str | None,
        event_type: str,
        payload_sha256: str,
    ) -> bool:
        cur = self._conn.execute(
            """INSERT OR IGNORE INTO activity_receipts(
                session_id,activity_name,activity_id,create_time,event_type,payload_sha256
            ) VALUES(?,?,?,?,?,?)""",
            (session_id, activity_name, activity_id, create_time, event_type, payload_sha256),
        )
        return cur.rowcount == 1

    def active_rows(self) -> list[sqlite3.Row]:
        return list(
            self._conn.execute(
                "SELECT * FROM sessions WHERE deleted_at IS NULL AND lifecycle IN "
                "('executing','actionable','paused','unknown')"
            )
        )

    def create_deletion_plan(
        self,
        plan_id: str,
        selector: dict[str, object],
        targets: list[dict[str, object]],
    ) -> None:
        self._conn.execute(
            "INSERT INTO deletion_plans(plan_id,selector_json,targets_json,state) VALUES(?,?,?,?)",
            (plan_id, json.dumps(selector, sort_keys=True), json.dumps(targets), "PLANNED"),
        )

    def get_deletion_plan(
        self, plan_id: str
    ) -> tuple[dict[str, object], list[dict[str, object]]]:
        row = self._conn.execute(
            "SELECT selector_json,targets_json FROM deletion_plans WHERE plan_id=?", (plan_id,)
        ).fetchone()
        if not row:
            raise InputError(f"unknown deletion plan {plan_id}")
        return json.loads(row["selector_json"]), json.loads(row["targets_json"])

    def mark_deleted(self, session_id: str) -> None:
        self._conn.execute(
            "UPDATE sessions SET deleted_at=CURRENT_TIMESTAMP WHERE session_id=?", (session_id,)
        )
