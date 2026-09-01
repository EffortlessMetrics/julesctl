from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .domain.errors import AdmissionError, InputError

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
    fingerprint TEXT NOT NULL,
    source_name TEXT,
    repo TEXT,
    starting_branch TEXT,
    working_branch TEXT,
    title TEXT NOT NULL,
    prompt_sha256 TEXT NOT NULL,
    state TEXT NOT NULL,
    session_id TEXT,
    started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    resolved_at TEXT,
    http_status INTEGER,
    api_status TEXT,
    FOREIGN KEY(dispatch_key) REFERENCES work_items(dispatch_key)
);
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

    def is_frozen(self) -> bool:
        row = self._conn.execute("SELECT value FROM meta WHERE key='fleet_frozen'").fetchone()
        return row is not None and row["value"] == "1"

    def set_frozen(self, value: bool) -> None:
        self._conn.execute(
            "INSERT INTO meta(key,value) VALUES('fleet_frozen',?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            ("1" if value else "0",),
        )

    def get_work(self, dispatch_key: str) -> sqlite3.Row | None:
        return self._conn.execute(
            "SELECT * FROM work_items WHERE dispatch_key=?", (dispatch_key,)
        ).fetchone()

    def reserve_work(
        self,
        *,
        dispatch_key: str,
        fingerprint: str,
        attempt_id: str,
        attempt: dict[str, object],
    ) -> dict[str, str | None]:
        with self.immediate() as conn:
            if self.is_frozen():
                raise AdmissionError("fleet admission is frozen")
            existing = conn.execute(
                "SELECT * FROM work_items WHERE dispatch_key=?", (dispatch_key,)
            ).fetchone()
            if existing:
                if existing["fingerprint"] != fingerprint:
                    raise InputError("dispatch_key already exists with a different fingerprint")
                return dict(existing)
            conn.execute(
                "INSERT INTO work_items(dispatch_key,fingerprint,attempt_id,status) VALUES(?,?,?,?)",
                (dispatch_key, fingerprint, attempt_id, "SEND_STARTED"),
            )
            conn.execute(
                """INSERT INTO dispatch_attempts(
                    attempt_id,dispatch_key,fingerprint,source_name,repo,starting_branch,
                    working_branch,title,prompt_sha256,state
                ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (
                    attempt_id,
                    dispatch_key,
                    fingerprint,
                    attempt.get("source_name"),
                    attempt.get("repo"),
                    attempt.get("starting_branch"),
                    attempt.get("working_branch"),
                    attempt["title"],
                    attempt["prompt_sha256"],
                    "SEND_STARTED",
                ),
            )
            return {
                "dispatch_key": dispatch_key,
                "fingerprint": fingerprint,
                "attempt_id": attempt_id,
                "session_id": None,
                "status": "SEND_STARTED",
            }

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
                "UPDATE dispatch_attempts SET state=?,http_status=?,api_status=? WHERE attempt_id=?",
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
                raw_state=excluded.raw_state,
                lifecycle=excluded.lifecycle,
                archived=excluded.archived,
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
