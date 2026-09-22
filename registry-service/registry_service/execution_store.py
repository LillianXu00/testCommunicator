from __future__ import annotations

import json
import sqlite3
import uuid

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any


TERMINAL_EXECUTION_STATES = {"COMPLETED", "FAILED"}


class TaskExecutionStore:
    """Durable idempotency and lease records for queue-backed executions."""

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
                CREATE TABLE IF NOT EXISTS task_executions (
                    agent_id TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    message_id TEXT NOT NULL UNIQUE,
                    context_id TEXT NOT NULL,
                    state TEXT NOT NULL,
                    claim_token TEXT,
                    claimed_by TEXT,
                    lease_until TEXT,
                    attempt INTEGER NOT NULL,
                    result_text TEXT NOT NULL,
                    error TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    finished_at TEXT,
                    PRIMARY KEY(agent_id, task_id),
                    FOREIGN KEY(agent_id) REFERENCES agents(agent_id)
                        ON DELETE CASCADE
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS task_experiences (
                    agent_id TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    message_id TEXT NOT NULL UNIQUE,
                    state TEXT NOT NULL,
                    claim_token TEXT,
                    claimed_by TEXT,
                    lease_until TEXT,
                    attempt INTEGER NOT NULL,
                    report_json TEXT NOT NULL,
                    memo_name TEXT NOT NULL,
                    last_error TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    finished_at TEXT,
                    PRIMARY KEY(agent_id, task_id),
                    FOREIGN KEY(agent_id, task_id)
                        REFERENCES task_executions(agent_id, task_id)
                        ON DELETE CASCADE
                )
                """
            )

    def claim(self, payload: dict[str, Any]) -> dict[str, Any]:
        agent_id = str(payload.get("agentId", "")).strip()
        task_id = str(payload.get("taskId", "")).strip()
        message_id = str(payload.get("messageId", "")).strip()
        context_id = str(payload.get("contextId", "")).strip()
        worker_id = str(payload.get("workerId", "")).strip()
        lease_seconds = float(payload.get("leaseSeconds", 900))
        if not all((agent_id, task_id, message_id, context_id, worker_id)):
            raise ValueError(
                "agentId, taskId, messageId, contextId and workerId are required"
            )
        if lease_seconds < 1 or lease_seconds > 86400:
            raise ValueError("leaseSeconds must be between 1 and 86400")

        now = datetime.now(UTC)
        now_text = now.isoformat()
        lease_until = (now + timedelta(seconds=lease_seconds)).isoformat()
        claim_token = f"claim-{uuid.uuid4().hex}"
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if connection.execute(
                "SELECT 1 FROM agents WHERE agent_id = ?",
                (agent_id,),
            ).fetchone() is None:
                raise KeyError("Agent not found")
            row = connection.execute(
                """
                SELECT * FROM task_executions
                WHERE agent_id = ? AND task_id = ?
                """,
                (agent_id, task_id),
            ).fetchone()
            if row is None:
                connection.execute(
                    """
                    INSERT INTO task_executions (
                        agent_id, task_id, message_id, context_id, state,
                        claim_token, claimed_by, lease_until, attempt,
                        result_text, error, created_at, updated_at, finished_at
                    ) VALUES (?, ?, ?, ?, 'RUNNING', ?, ?, ?, 1, '', '', ?, ?, NULL)
                    """,
                    (
                        agent_id,
                        task_id,
                        message_id,
                        context_id,
                        claim_token,
                        worker_id,
                        lease_until,
                        now_text,
                        now_text,
                    ),
                )
                return {
                    "decision": "EXECUTE",
                    "claimToken": claim_token,
                    "attempt": 1,
                    "leaseUntil": lease_until,
                }

            if str(row["message_id"]) != message_id:
                raise ValueError("taskId is already associated with another messageId")
            if str(row["state"]) in TERMINAL_EXECUTION_STATES:
                return {
                    "decision": "REPLAY",
                    **self._result_payload(row),
                }
            current_lease = str(row["lease_until"] or "")
            if current_lease and datetime.fromisoformat(current_lease) > now:
                retry_after = max(
                    100,
                    int(
                        (
                            datetime.fromisoformat(current_lease) - now
                        ).total_seconds()
                        * 1000
                    ),
                )
                return {
                    "decision": "BUSY",
                    "retryAfterMs": retry_after,
                    "attempt": int(row["attempt"]),
                }

            attempt = int(row["attempt"]) + 1
            connection.execute(
                """
                UPDATE task_executions
                SET claim_token = ?, claimed_by = ?, lease_until = ?,
                    attempt = ?, updated_at = ?
                WHERE agent_id = ? AND task_id = ?
                """,
                (
                    claim_token,
                    worker_id,
                    lease_until,
                    attempt,
                    now_text,
                    agent_id,
                    task_id,
                ),
            )
            return {
                "decision": "EXECUTE",
                "claimToken": claim_token,
                "attempt": attempt,
                "leaseUntil": lease_until,
            }

    def complete(self, payload: dict[str, Any]) -> dict[str, Any]:
        agent_id = str(payload.get("agentId", "")).strip()
        task_id = str(payload.get("taskId", "")).strip()
        message_id = str(payload.get("messageId", "")).strip()
        claim_token = str(payload.get("claimToken", "")).strip()
        state = str(payload.get("state", "")).strip().upper()
        result_text = str(payload.get("text", ""))
        error = str(payload.get("error", ""))
        if not all((agent_id, task_id, message_id, claim_token)):
            raise ValueError(
                "agentId, taskId, messageId and claimToken are required"
            )
        if state not in TERMINAL_EXECUTION_STATES:
            raise ValueError("state must be COMPLETED or FAILED")
        now = datetime.now(UTC).isoformat()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT * FROM task_executions
                WHERE agent_id = ? AND task_id = ?
                """,
                (agent_id, task_id),
            ).fetchone()
            if row is None:
                raise KeyError("Task execution not found")
            if str(row["message_id"]) != message_id:
                raise ValueError("messageId does not match the task execution")
            if str(row["state"]) in TERMINAL_EXECUTION_STATES:
                return {"duplicate": True, **self._result_payload(row)}
            if str(row["claim_token"] or "") != claim_token:
                raise ValueError("execution claim is stale")
            connection.execute(
                """
                UPDATE task_executions
                SET state = ?, result_text = ?, error = ?, lease_until = NULL,
                    updated_at = ?, finished_at = ?
                WHERE agent_id = ? AND task_id = ?
                """,
                (
                    state,
                    result_text,
                    error,
                    now,
                    now,
                    agent_id,
                    task_id,
                ),
            )
            updated = connection.execute(
                """
                SELECT * FROM task_executions
                WHERE agent_id = ? AND task_id = ?
                """,
                (agent_id, task_id),
            ).fetchone()
        return {"duplicate": False, **self._result_payload(updated)}

    def claim_experience(self, payload: dict[str, Any]) -> dict[str, Any]:
        agent_id = str(payload.get("agentId", "")).strip()
        task_id = str(payload.get("taskId", "")).strip()
        message_id = str(payload.get("messageId", "")).strip()
        worker_id = str(payload.get("workerId", "")).strip()
        lease_seconds = float(payload.get("leaseSeconds", 900))
        if not all((agent_id, task_id, message_id, worker_id)):
            raise ValueError("agentId, taskId, messageId and workerId are required")
        if lease_seconds < 1 or lease_seconds > 86400:
            raise ValueError("leaseSeconds must be between 1 and 86400")

        now = datetime.now(UTC)
        now_text = now.isoformat()
        lease_until = (now + timedelta(seconds=lease_seconds)).isoformat()
        claim_token = f"experience-claim-{uuid.uuid4().hex}"
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            execution = connection.execute(
                """
                SELECT state FROM task_executions
                WHERE agent_id = ? AND task_id = ?
                """,
                (agent_id, task_id),
            ).fetchone()
            if execution is None:
                raise KeyError("Task execution not found")
            if str(execution["state"]) != "COMPLETED":
                raise ValueError("experience can only be collected for a completed task")
            row = connection.execute(
                """
                SELECT * FROM task_experiences
                WHERE agent_id = ? AND task_id = ?
                """,
                (agent_id, task_id),
            ).fetchone()
            if row is None:
                connection.execute(
                    """
                    INSERT INTO task_experiences (
                        agent_id, task_id, message_id, state, claim_token,
                        claimed_by, lease_until, attempt, report_json,
                        memo_name, last_error, created_at, updated_at, finished_at
                    ) VALUES (?, ?, ?, 'RUNNING', ?, ?, ?, 1, '', '', '', ?, ?, NULL)
                    """,
                    (
                        agent_id,
                        task_id,
                        message_id,
                        claim_token,
                        worker_id,
                        lease_until,
                        now_text,
                        now_text,
                    ),
                )
                return {
                    "decision": "EXECUTE",
                    "claimToken": claim_token,
                    "attempt": 1,
                    "leaseUntil": lease_until,
                }
            if str(row["message_id"]) != message_id:
                raise ValueError("task experience has another messageId")
            if str(row["state"]) == "COMPLETED":
                return {"decision": "REPLAY", **self._experience_payload(row)}
            current_lease = str(row["lease_until"] or "")
            if current_lease and datetime.fromisoformat(current_lease) > now:
                retry_after = max(
                    100,
                    int(
                        (datetime.fromisoformat(current_lease) - now).total_seconds()
                        * 1000
                    ),
                )
                return {
                    "decision": "BUSY",
                    "retryAfterMs": retry_after,
                    "attempt": int(row["attempt"]),
                }
            attempt = int(row["attempt"]) + 1
            connection.execute(
                """
                UPDATE task_experiences
                SET state = 'RUNNING', claim_token = ?, claimed_by = ?,
                    lease_until = ?, attempt = ?, updated_at = ?
                WHERE agent_id = ? AND task_id = ?
                """,
                (
                    claim_token,
                    worker_id,
                    lease_until,
                    attempt,
                    now_text,
                    agent_id,
                    task_id,
                ),
            )
            return {
                "decision": "EXECUTE",
                "claimToken": claim_token,
                "attempt": attempt,
                "leaseUntil": lease_until,
            }

    def complete_experience(self, payload: dict[str, Any]) -> dict[str, Any]:
        agent_id, task_id, message_id, claim_token = self._experience_keys(payload)
        report = payload.get("report")
        if not isinstance(report, dict):
            raise ValueError("report must be an object")
        report_json = json.dumps(
            report,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        memo_name = str(payload.get("memoName", "")).strip()
        now = datetime.now(UTC).isoformat()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = self._experience_row(connection, agent_id, task_id)
            if str(row["message_id"]) != message_id:
                raise ValueError("messageId does not match the task experience")
            if str(row["state"]) == "COMPLETED":
                return {"duplicate": True, **self._experience_payload(row)}
            if str(row["claim_token"] or "") != claim_token:
                raise ValueError("experience claim is stale")
            connection.execute(
                """
                UPDATE task_experiences
                SET state = 'COMPLETED', report_json = ?, memo_name = ?,
                    last_error = '', lease_until = NULL, updated_at = ?,
                    finished_at = ?
                WHERE agent_id = ? AND task_id = ?
                """,
                (report_json, memo_name, now, now, agent_id, task_id),
            )
            updated = self._experience_row(connection, agent_id, task_id)
        return {"duplicate": False, **self._experience_payload(updated)}

    def release_experience(self, payload: dict[str, Any]) -> dict[str, Any]:
        agent_id, task_id, message_id, claim_token = self._experience_keys(payload)
        error = str(payload.get("error", ""))[:4000]
        now = datetime.now(UTC).isoformat()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = self._experience_row(connection, agent_id, task_id)
            if str(row["message_id"]) != message_id:
                raise ValueError("messageId does not match the task experience")
            if str(row["state"]) == "COMPLETED":
                return {"duplicate": True, **self._experience_payload(row)}
            if str(row["claim_token"] or "") != claim_token:
                raise ValueError("experience claim is stale")
            connection.execute(
                """
                UPDATE task_experiences
                SET state = 'PENDING', claim_token = NULL, claimed_by = NULL,
                    lease_until = NULL, last_error = ?, updated_at = ?
                WHERE agent_id = ? AND task_id = ?
                """,
                (error, now, agent_id, task_id),
            )
            updated = self._experience_row(connection, agent_id, task_id)
        return {"duplicate": False, **self._experience_payload(updated)}

    @staticmethod
    def _experience_keys(payload: dict[str, Any]) -> tuple[str, str, str, str]:
        values = tuple(
            str(payload.get(key, "")).strip()
            for key in ("agentId", "taskId", "messageId", "claimToken")
        )
        if not all(values):
            raise ValueError("agentId, taskId, messageId and claimToken are required")
        return values

    @staticmethod
    def _experience_row(
        connection: sqlite3.Connection,
        agent_id: str,
        task_id: str,
    ) -> sqlite3.Row:
        row = connection.execute(
            """
            SELECT * FROM task_experiences
            WHERE agent_id = ? AND task_id = ?
            """,
            (agent_id, task_id),
        ).fetchone()
        if row is None:
            raise KeyError("Task experience not found")
        return row

    @staticmethod
    def _experience_payload(row: sqlite3.Row) -> dict[str, Any]:
        report_text = str(row["report_json"] or "")
        return {
            "agentId": row["agent_id"],
            "taskId": row["task_id"],
            "messageId": row["message_id"],
            "state": row["state"],
            "report": json.loads(report_text) if report_text else {},
            "memoName": row["memo_name"],
            "lastError": row["last_error"],
            "attempt": int(row["attempt"]),
            "finishedAt": row["finished_at"],
        }

    @staticmethod
    def _result_payload(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "agentId": row["agent_id"],
            "taskId": row["task_id"],
            "messageId": row["message_id"],
            "contextId": row["context_id"],
            "state": row["state"],
            "text": row["result_text"],
            "error": row["error"],
            "attempt": int(row["attempt"]),
            "finishedAt": row["finished_at"],
        }
