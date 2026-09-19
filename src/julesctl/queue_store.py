from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .domain.errors import AdmissionError, InputError
from .store import StateStore

_QUEUE_SCHEMA_VERSION = "1"
_QUEUE_SCHEMA = """
CREATE TABLE IF NOT EXISTS candidate_queue (
    candidate_id TEXT PRIMARY KEY,
    dispatch_key TEXT NOT NULL UNIQUE,
    spec_json TEXT NOT NULL,
    state TEXT NOT NULL,
    worker_id TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    claimed_at TEXT,
    completed_at TEXT,
    outcome_json TEXT,
    error_json TEXT,
    attempts INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_candidate_queue_state
ON candidate_queue(state, created_at);
"""


class CandidateQueueStore:
    """SQLite queue sharing the controller profile database without the Jules credential."""

    def __init__(self, path: Path, *, profile_name: str = "default") -> None:
        bootstrap = StateStore(path, profile_name=profile_name)
        bootstrap.close()
        self.path = path
        self._conn = sqlite3.connect(path, timeout=5, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.executescript(_QUEUE_SCHEMA)
        row = self._conn.execute(
            "SELECT value FROM meta WHERE key='candidate_queue_schema'"
        ).fetchone()
        if row is None:
            self._conn.execute(
                "INSERT INTO meta(key,value) VALUES('candidate_queue_schema',?)",
                (_QUEUE_SCHEMA_VERSION,),
            )
        elif str(row["value"]) != _QUEUE_SCHEMA_VERSION:
            self._conn.close()
            raise InputError(
                f"candidate queue schema {row['value']!r} is unsupported; "
                f"expected {_QUEUE_SCHEMA_VERSION!r}"
            )

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> CandidateQueueStore:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

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

    def enqueue_candidate(
        self,
        *,
        candidate_id: str,
        dispatch_key: str,
        spec_json: str,
    ) -> dict[str, object]:
        with self.immediate() as conn:
            existing = conn.execute(
                "SELECT * FROM candidate_queue WHERE dispatch_key=?",
                (dispatch_key,),
            ).fetchone()
            if existing is not None:
                if str(existing["spec_json"]) != spec_json:
                    raise InputError(
                        "dispatch_key is already queued with a different candidate"
                    )
                return {
                    "outcome": "existing",
                    "candidate_id": existing["candidate_id"],
                    "dispatch_key": dispatch_key,
                    "state": existing["state"],
                }
            conn.execute(
                """INSERT INTO candidate_queue(
                    candidate_id,dispatch_key,spec_json,state
                ) VALUES(?,?,?,"PENDING")""",
                (candidate_id, dispatch_key, spec_json),
            )
            return {
                "outcome": "queued",
                "candidate_id": candidate_id,
                "dispatch_key": dispatch_key,
                "state": "PENDING",
            }

    def list_candidates(self, *, limit: int = 100) -> list[dict[str, object]]:
        if not 1 <= limit <= 1000:
            raise InputError("candidate list limit must be between 1 and 1000")
        rows = self._conn.execute(
            "SELECT * FROM candidate_queue ORDER BY created_at,candidate_id LIMIT ?",
            (limit,),
        )
        items: list[dict[str, object]] = []
        for row in rows:
            item = dict(row)
            for field in ("outcome_json", "error_json"):
                raw = item.pop(field, None)
                if raw is not None:
                    item[field.removesuffix("_json")] = json.loads(str(raw))
            item.pop("spec_json", None)
            items.append(item)
        return items

    def candidate_counts(self) -> dict[str, int]:
        return {
            str(row["state"]): int(row["n"])
            for row in self._conn.execute(
                "SELECT state,COUNT(*) AS n FROM candidate_queue GROUP BY state"
            )
        }

    def requeue_stale_candidates(self, *, older_than_seconds: int) -> int:
        if older_than_seconds < 1:
            raise InputError("stale claim age must be positive")
        modifier = f"-{older_than_seconds} seconds"
        with self.immediate() as conn:
            cur = conn.execute(
                """UPDATE candidate_queue SET
                    state='PENDING',worker_id=NULL,claimed_at=NULL
                WHERE state='CLAIMED'
                  AND claimed_at < datetime('now',?)""",
                (modifier,),
            )
            return int(cur.rowcount)

    def claim_candidates(
        self,
        *,
        limit: int,
        worker_id: str,
    ) -> list[sqlite3.Row]:
        if not 1 <= limit <= 100:
            raise InputError("claim limit must be between 1 and 100")
        with self.immediate() as conn:
            frozen = conn.execute(
                "SELECT value FROM meta WHERE key='fleet_frozen'"
            ).fetchone()
            if frozen is not None and str(frozen["value"]) == "1":
                raise AdmissionError("fleet admission is frozen")
            rows = list(
                conn.execute(
                    """SELECT * FROM candidate_queue
                    WHERE state='PENDING'
                    ORDER BY created_at,candidate_id
                    LIMIT ?""",
                    (limit,),
                )
            )
            claimed: list[sqlite3.Row] = []
            for row in rows:
                cur = conn.execute(
                    """UPDATE candidate_queue SET
                        state='CLAIMED',worker_id=?,claimed_at=CURRENT_TIMESTAMP,
                        attempts=attempts+1
                    WHERE candidate_id=? AND state='PENDING'""",
                    (worker_id, row["candidate_id"]),
                )
                if cur.rowcount == 1:
                    refreshed = conn.execute(
                        "SELECT * FROM candidate_queue WHERE candidate_id=?",
                        (row["candidate_id"],),
                    ).fetchone()
                    if refreshed is not None:
                        claimed.append(refreshed)
            return claimed

    def release_candidate(self, candidate_id: str) -> None:
        with self.immediate() as conn:
            cur = conn.execute(
                """UPDATE candidate_queue SET
                    state='PENDING',worker_id=NULL,claimed_at=NULL
                WHERE candidate_id=? AND state='CLAIMED'""",
                (candidate_id,),
            )
            if cur.rowcount != 1:
                raise InputError(f"candidate {candidate_id} is not claimed")

    def finish_candidate(
        self,
        candidate_id: str,
        *,
        state: str,
        outcome: dict[str, object] | None = None,
        error: dict[str, object] | None = None,
    ) -> None:
        with self.immediate() as conn:
            cur = conn.execute(
                """UPDATE candidate_queue SET
                    state=?,completed_at=CURRENT_TIMESTAMP,
                    outcome_json=?,error_json=?
                WHERE candidate_id=? AND state='CLAIMED'""",
                (
                    state,
                    json.dumps(outcome, sort_keys=True) if outcome is not None else None,
                    json.dumps(error, sort_keys=True) if error is not None else None,
                    candidate_id,
                ),
            )
            if cur.rowcount != 1:
                raise InputError(f"candidate {candidate_id} is not claimed")
