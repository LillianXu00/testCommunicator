from __future__ import annotations

import sqlite3
import uuid

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from skill_evolution.models import EvolutionPolicy


class EvolutionStore:
    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.database_path, timeout=30)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS evolution_policy_state (
                    policy_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    next_run_at TEXT NOT NULL,
                    lease_until TEXT,
                    checkpoint TEXT,
                    evidence_hash TEXT,
                    last_outcome TEXT,
                    last_candidate_path TEXT,
                    last_published_version TEXT,
                    last_error TEXT,
                    last_started_at TEXT,
                    last_completed_at TEXT,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS evolution_runs (
                    run_id TEXT PRIMARY KEY,
                    policy_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    evidence_count INTEGER NOT NULL DEFAULT 0,
                    evidence_hash TEXT,
                    outcome TEXT,
                    candidate_path TEXT,
                    published_version TEXT,
                    error TEXT,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    FOREIGN KEY(policy_id) REFERENCES evolution_policy_state(policy_id)
                )
                """
            )

    def claim(
        self,
        policy: EvolutionPolicy,
        *,
        now: datetime | None = None,
        lease_seconds: int = 3600,
        force: bool = False,
    ) -> str | None:
        current = (now or datetime.now(UTC)).astimezone(UTC)
        current_text = current.isoformat()
        run_id = f"evo-{uuid.uuid4().hex}"
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM evolution_policy_state WHERE policy_id = ?",
                (policy.policy_id,),
            ).fetchone()
            if row is None:
                first_run = (
                    current
                    if policy.run_on_startup
                    else current + timedelta(seconds=policy.interval_seconds)
                )
                connection.execute(
                    """
                    INSERT INTO evolution_policy_state (
                        policy_id, status, next_run_at, updated_at
                    ) VALUES (?, 'IDLE', ?, ?)
                    """,
                    (policy.policy_id, first_run.isoformat(), current_text),
                )
                row = connection.execute(
                    "SELECT * FROM evolution_policy_state WHERE policy_id = ?",
                    (policy.policy_id,),
                ).fetchone()
            lease_until = str(row["lease_until"] or "")
            if row["status"] == "RUNNING" and lease_until:
                if datetime.fromisoformat(lease_until) > current:
                    return None
            if not force and datetime.fromisoformat(row["next_run_at"]) > current:
                return None
            new_lease = current + timedelta(seconds=lease_seconds)
            connection.execute(
                """
                UPDATE evolution_policy_state
                SET status = 'RUNNING', lease_until = ?, last_started_at = ?,
                    last_error = NULL, updated_at = ?
                WHERE policy_id = ?
                """,
                (new_lease.isoformat(), current_text, current_text, policy.policy_id),
            )
            connection.execute(
                """
                INSERT INTO evolution_runs (
                    run_id, policy_id, status, started_at
                ) VALUES (?, ?, 'RUNNING', ?)
                """,
                (run_id, policy.policy_id, current_text),
            )
        return run_id

    def checkpoint(self, policy_id: str) -> str:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT checkpoint FROM evolution_policy_state WHERE policy_id = ?",
                (policy_id,),
            ).fetchone()
        return str(row["checkpoint"] or "") if row else ""

    def last_evidence_hash(self, policy_id: str) -> str:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT evidence_hash FROM evolution_policy_state WHERE policy_id = ?",
                (policy_id,),
            ).fetchone()
        return str(row["evidence_hash"] or "") if row else ""

    def recover_running(
        self,
        *,
        reason: str = "Recovered after the previous scheduler process stopped",
        now: datetime | None = None,
    ) -> int:
        """Release RUNNING leases after the operator has stopped the scheduler."""
        current = (now or datetime.now(UTC)).astimezone(UTC)
        current_text = current.isoformat()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            run_ids = [
                str(row["run_id"])
                for row in connection.execute(
                    "SELECT run_id FROM evolution_runs WHERE status = 'RUNNING'"
                ).fetchall()
            ]
            if not run_ids:
                return 0
            connection.execute(
                """
                UPDATE evolution_runs
                SET status = 'FAILED', error = ?, finished_at = ?
                WHERE status = 'RUNNING'
                """,
                (reason, current_text),
            )
            connection.execute(
                """
                UPDATE evolution_policy_state
                SET status = 'IDLE', next_run_at = ?, lease_until = NULL,
                    last_error = ?, updated_at = ?
                WHERE status = 'RUNNING'
                """,
                (current_text, reason, current_text),
            )
        return len(run_ids)

    def complete(
        self,
        policy: EvolutionPolicy,
        run_id: str,
        *,
        outcome: str,
        evidence_count: int,
        evidence_digest: str = "",
        checkpoint: str | None = None,
        candidate_path: str = "",
        published_version: str = "",
        now: datetime | None = None,
    ) -> None:
        current = (now or datetime.now(UTC)).astimezone(UTC)
        next_run = current + timedelta(seconds=policy.interval_seconds)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                UPDATE evolution_policy_state
                SET status = 'IDLE', next_run_at = ?, lease_until = NULL,
                    checkpoint = COALESCE(?, checkpoint),
                    evidence_hash = CASE WHEN ? = '' THEN evidence_hash ELSE ? END,
                    last_outcome = ?, last_candidate_path = ?,
                    last_published_version = CASE WHEN ? = ''
                        THEN last_published_version ELSE ? END,
                    last_error = NULL, last_completed_at = ?, updated_at = ?
                WHERE policy_id = ?
                """,
                (
                    next_run.isoformat(),
                    checkpoint,
                    evidence_digest,
                    evidence_digest,
                    outcome,
                    candidate_path,
                    published_version,
                    published_version,
                    current.isoformat(),
                    current.isoformat(),
                    policy.policy_id,
                ),
            )
            connection.execute(
                """
                UPDATE evolution_runs
                SET status = 'COMPLETED', evidence_count = ?, evidence_hash = ?,
                    outcome = ?, candidate_path = ?, published_version = ?,
                    finished_at = ?
                WHERE run_id = ?
                """,
                (
                    evidence_count,
                    evidence_digest,
                    outcome,
                    candidate_path,
                    published_version,
                    current.isoformat(),
                    run_id,
                ),
            )

    def fail(
        self,
        policy: EvolutionPolicy,
        run_id: str,
        error: str,
        *,
        now: datetime | None = None,
    ) -> None:
        current = (now or datetime.now(UTC)).astimezone(UTC)
        retry_at = current + timedelta(seconds=min(policy.interval_seconds, 300))
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                UPDATE evolution_policy_state
                SET status = 'IDLE', next_run_at = ?, lease_until = NULL,
                    last_error = ?, updated_at = ?
                WHERE policy_id = ?
                """,
                (retry_at.isoformat(), error, current.isoformat(), policy.policy_id),
            )
            connection.execute(
                """
                UPDATE evolution_runs
                SET status = 'FAILED', error = ?, finished_at = ?
                WHERE run_id = ?
                """,
                (error, current.isoformat(), run_id),
            )

    def states(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM evolution_policy_state ORDER BY policy_id"
            ).fetchall()
        return [dict(row) for row in rows]
