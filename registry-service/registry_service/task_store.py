from __future__ import annotations

import json
import sqlite3
import uuid

from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


TERMINAL_STATES = {
    "TASK_STATE_COMPLETED",
    "TASK_STATE_FAILED",
    "TASK_STATE_CANCELED",
    "TASK_STATE_REJECTED",
}
ACTIVE_STATES = {
    "TASK_STATE_SUBMITTED",
    "TASK_STATE_WORKING",
    "TASK_STATE_INPUT_REQUIRED",
    "TASK_STATE_AUTH_REQUIRED",
}
VALID_STATES = ACTIVE_STATES | TERMINAL_STATES | {"TASK_STATE_UNSPECIFIED"}


class TaskEventStore:
    """Persists canonical A2A task events and their latest task projection."""

    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path)
        self._initialize()

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.database_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
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
                CREATE TABLE IF NOT EXISTS task_runs (
                    agent_id TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    context_id TEXT,
                    state TEXT NOT NULL,
                    status_message TEXT NOT NULL,
                    source TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    finished_at TEXT,
                    last_sequence INTEGER NOT NULL,
                    last_task_sequence INTEGER NOT NULL,
                    metadata_json TEXT NOT NULL,
                    PRIMARY KEY(agent_id, task_id),
                    FOREIGN KEY(agent_id) REFERENCES agents(agent_id)
                        ON DELETE CASCADE
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS task_events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT NOT NULL UNIQUE,
                    agent_id TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    context_id TEXT,
                    task_sequence INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    state TEXT NOT NULL,
                    message TEXT NOT NULL,
                    source TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    FOREIGN KEY(agent_id) REFERENCES agents(agent_id)
                        ON DELETE CASCADE
                )
                """
            )
            run_columns = {
                row["name"]
                for row in connection.execute(
                    "PRAGMA table_info(task_runs)"
                ).fetchall()
            }
            if "last_task_sequence" not in run_columns:
                connection.execute(
                    """
                    ALTER TABLE task_runs
                    ADD COLUMN last_task_sequence INTEGER NOT NULL DEFAULT 0
                    """
                )
            event_columns = {
                row["name"]
                for row in connection.execute(
                    "PRAGMA table_info(task_events)"
                ).fetchall()
            }
            if "task_sequence" not in event_columns:
                connection.execute(
                    """
                    ALTER TABLE task_events
                    ADD COLUMN task_sequence INTEGER NOT NULL DEFAULT 0
                    """
                )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_task_runs_agent_state
                ON task_runs(agent_id, state, updated_at DESC)
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_task_events_agent_sequence
                ON task_events(agent_id, sequence DESC)
                """
            )

    def record(self, payload: dict[str, Any]) -> dict[str, Any]:
        agent_id = str(payload.get("agentId", "")).strip()
        task_id = str(payload.get("taskId", "")).strip()
        context_id = str(payload.get("contextId", "")).strip() or None
        state = str(payload.get("state", "")).strip().upper()
        event_type = str(payload.get("type", "status-update")).strip()
        message = str(payload.get("message", "")).strip()
        source = str(payload.get("source", "unknown")).strip() or "unknown"
        occurred_at = str(payload.get("timestamp", "")).strip() or datetime.now(
            UTC
        ).isoformat()
        metadata = payload.get("metadata", {})
        if not agent_id or not task_id:
            raise ValueError("agentId and taskId are required")
        if state not in VALID_STATES:
            raise ValueError(f"unsupported A2A task state: {state}")
        if not isinstance(metadata, dict):
            raise ValueError("metadata must be an object")
        event_id = str(payload.get("eventId", "")).strip() or f"evt-{uuid.uuid4().hex}"
        metadata_json = json.dumps(
            metadata,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )

        with self._connect() as connection:
            if connection.execute(
                "SELECT 1 FROM agents WHERE agent_id = ?",
                (agent_id,),
            ).fetchone() is None:
                raise KeyError("Agent not found")
            existing_event = connection.execute(
                "SELECT * FROM task_events WHERE event_id = ?",
                (event_id,),
            ).fetchone()
            if existing_event is not None:
                return self._event_payload(existing_event, duplicate=True)
            requested_task_sequence = payload.get("taskSequence")
            if requested_task_sequence is None:
                previous = connection.execute(
                    """
                    SELECT last_task_sequence FROM task_runs
                    WHERE agent_id = ? AND task_id = ?
                    """,
                    (agent_id, task_id),
                ).fetchone()
                task_sequence = (
                    int(previous["last_task_sequence"]) + 1
                    if previous
                    else 1
                )
            else:
                task_sequence = int(requested_task_sequence)
                if task_sequence < 1:
                    raise ValueError("taskSequence must be at least 1")
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO task_events (
                    event_id, agent_id, task_id, context_id, task_sequence,
                    event_type, state, message, source, occurred_at,
                    metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    agent_id,
                    task_id,
                    context_id,
                    task_sequence,
                    event_type,
                    state,
                    message,
                    source,
                    occurred_at,
                    metadata_json,
                ),
            )
            if cursor.rowcount == 0:
                row = connection.execute(
                    "SELECT * FROM task_events WHERE event_id = ?",
                    (event_id,),
                ).fetchone()
                return self._event_payload(row, duplicate=True)
            sequence = int(cursor.lastrowid)
            finished_at = occurred_at if state in TERMINAL_STATES else None
            connection.execute(
                """
                INSERT INTO task_runs (
                    agent_id, task_id, context_id, state, status_message,
                    source, created_at, updated_at, finished_at,
                    last_sequence, last_task_sequence, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(agent_id, task_id) DO UPDATE SET
                    context_id = excluded.context_id,
                    state = excluded.state,
                    status_message = excluded.status_message,
                    source = excluded.source,
                    updated_at = excluded.updated_at,
                    finished_at = excluded.finished_at,
                    last_sequence = excluded.last_sequence,
                    last_task_sequence = excluded.last_task_sequence,
                    metadata_json = excluded.metadata_json
                WHERE excluded.last_task_sequence > task_runs.last_task_sequence
                """,
                (
                    agent_id,
                    task_id,
                    context_id,
                    state,
                    message,
                    source,
                    occurred_at,
                    occurred_at,
                    finished_at,
                    sequence,
                    task_sequence,
                    metadata_json,
                ),
            )
            row = connection.execute(
                "SELECT * FROM task_events WHERE sequence = ?",
                (sequence,),
            ).fetchone()
        return self._event_payload(row)

    def agent_projection(self, agent_id: str, *, recent_limit: int = 5) -> dict[str, Any]:
        with self._connect() as connection:
            active_rows = connection.execute(
                """
                SELECT * FROM task_runs
                WHERE agent_id = ? AND state IN (?, ?, ?, ?)
                ORDER BY updated_at DESC
                """,
                (agent_id, *sorted(ACTIVE_STATES)),
            ).fetchall()
            recent_rows = connection.execute(
                """
                SELECT * FROM task_runs
                WHERE agent_id = ?
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (agent_id, recent_limit),
            ).fetchall()

        states = {str(row["state"]) for row in active_rows}
        if "TASK_STATE_WORKING" in states:
            activity = "WORKING"
        elif "TASK_STATE_AUTH_REQUIRED" in states:
            activity = "AUTH_REQUIRED"
        elif "TASK_STATE_INPUT_REQUIRED" in states:
            activity = "WAITING_INPUT"
        elif "TASK_STATE_SUBMITTED" in states:
            activity = "QUEUED"
        else:
            activity = "IDLE"
        current = self._run_payload(active_rows[0]) if active_rows else None
        return {
            "activity": activity,
            "activeTaskCount": len(active_rows),
            "workingTaskCount": sum(
                row["state"] == "TASK_STATE_WORKING" for row in active_rows
            ),
            "waitingTaskCount": sum(
                row["state"]
                in {"TASK_STATE_INPUT_REQUIRED", "TASK_STATE_AUTH_REQUIRED"}
                for row in active_rows
            ),
            "currentTask": current,
            "recentTasks": [self._run_payload(row) for row in recent_rows],
        }

    def task_events(self, agent_id: str, task_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM task_events
                WHERE agent_id = ? AND task_id = ?
                ORDER BY task_sequence, sequence
                """,
                (agent_id, task_id),
            ).fetchall()
        return [self._event_payload(row) for row in rows]

    @staticmethod
    def _run_payload(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "agentId": row["agent_id"],
            "taskId": row["task_id"],
            "contextId": row["context_id"],
            "state": row["state"],
            "message": row["status_message"],
            "source": row["source"],
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
            "finishedAt": row["finished_at"],
            "lastTaskSequence": int(row["last_task_sequence"]),
            "metadata": json.loads(row["metadata_json"]),
        }

    @staticmethod
    def _event_payload(row: sqlite3.Row, *, duplicate: bool = False) -> dict[str, Any]:
        return {
            "sequence": int(row["sequence"]),
            "eventId": row["event_id"],
            "agentId": row["agent_id"],
            "taskId": row["task_id"],
            "contextId": row["context_id"],
            "taskSequence": int(row["task_sequence"]),
            "type": row["event_type"],
            "state": row["state"],
            "message": row["message"],
            "source": row["source"],
            "timestamp": row["occurred_at"],
            "metadata": json.loads(row["metadata_json"]),
            "duplicate": duplicate,
        }
