from __future__ import annotations

import asyncio
import ssl
import time

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

from registry_service.store import AgentRecord, AgentRegistry


@dataclass(frozen=True)
class AgentRuntimeStatus:
    agent_id: str
    availability: str
    activity: str
    checked_at: str
    latency_ms: int | None
    detail: str
    endpoint: str | None

    def to_payload(self) -> dict[str, Any]:
        payload = asdict(self)
        return {
            "agentId": payload["agent_id"],
            "availability": payload["availability"],
            "activity": payload["activity"],
            "checkedAt": payload["checked_at"],
            "latencyMs": payload["latency_ms"],
            "detail": payload["detail"],
            "endpoint": payload["endpoint"],
        }


class AgentStatusMonitor:
    """Maintains low-impact endpoint availability snapshots for the UI."""

    def __init__(
        self,
        registry: AgentRegistry,
        *,
        interval_seconds: float = 10,
        timeout_seconds: float = 3,
    ) -> None:
        self.registry = registry
        self.interval_seconds = interval_seconds
        self.timeout_seconds = timeout_seconds
        self._statuses: dict[str, AgentRuntimeStatus] = {}
        self._task: asyncio.Task[None] | None = None
        self._refresh_lock = asyncio.Lock()

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        task = self._task
        self._task = None
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def refresh(self) -> list[AgentRuntimeStatus]:
        async with self._refresh_lock:
            records = self.registry.list(active_only=False)
            statuses = await asyncio.gather(
                *(self._probe(record) for record in records)
            )
            self._statuses = {
                status.agent_id: status for status in statuses
            }
            return statuses

    def snapshot(self) -> list[AgentRuntimeStatus]:
        records = self.registry.list(active_only=False)
        now = datetime.now(UTC).isoformat()
        active_ids = {record.definition.agent_id for record in records}
        self._statuses = {
            agent_id: status
            for agent_id, status in self._statuses.items()
            if agent_id in active_ids
        }
        result: list[AgentRuntimeStatus] = []
        for record in records:
            status = self._statuses.get(record.definition.agent_id)
            if status is None:
                availability = (
                    "DISABLED"
                    if record.definition.status == "DISABLED"
                    else "UNKNOWN"
                )
                status = AgentRuntimeStatus(
                    agent_id=record.definition.agent_id,
                    availability=availability,
                    activity="IDLE",
                    checked_at=now,
                    latency_ms=None,
                    detail=(
                        "Agent is disabled"
                        if availability == "DISABLED"
                        else "Awaiting first availability check"
                    ),
                    endpoint=self._endpoint(record),
                )
            result.append(status)
        return result

    async def _run(self) -> None:
        while True:
            try:
                await self.refresh()
            except Exception:
                # One monitor failure must not take down Registry.
                pass
            await asyncio.sleep(self.interval_seconds)

    async def _probe(self, record: AgentRecord) -> AgentRuntimeStatus:
        now = datetime.now(UTC).isoformat()
        endpoint = self._endpoint(record)
        if record.definition.status == "DISABLED":
            return AgentRuntimeStatus(
                agent_id=record.definition.agent_id,
                availability="DISABLED",
                activity="IDLE",
                checked_at=now,
                latency_ms=None,
                detail="Agent is disabled",
                endpoint=endpoint,
            )
        if not endpoint:
            return AgentRuntimeStatus(
                agent_id=record.definition.agent_id,
                availability="UNKNOWN",
                activity="IDLE",
                checked_at=now,
                latency_ms=None,
                detail="No probeable HTTP endpoint is registered",
                endpoint=None,
            )

        parsed = urlparse(endpoint)
        host = parsed.hostname
        if not host:
            return AgentRuntimeStatus(
                agent_id=record.definition.agent_id,
                availability="UNKNOWN",
                activity="IDLE",
                checked_at=now,
                latency_ms=None,
                detail="The registered endpoint has no hostname",
                endpoint=endpoint,
            )
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        ssl_context = ssl.create_default_context() if parsed.scheme == "https" else None
        started = time.perf_counter()
        writer: asyncio.StreamWriter | None = None
        try:
            _reader, writer = await asyncio.wait_for(
                asyncio.open_connection(
                    host,
                    port,
                    ssl=ssl_context,
                    server_hostname=host if ssl_context else None,
                ),
                timeout=self.timeout_seconds,
            )
            latency_ms = max(1, round((time.perf_counter() - started) * 1000))
            availability = "ONLINE"
            detail = "Endpoint accepted a network connection"
        except TimeoutError:
            latency_ms = None
            availability = "OFFLINE"
            detail = f"Connection timed out after {self.timeout_seconds:g}s"
        except (OSError, ssl.SSLError) as exc:
            latency_ms = None
            availability = "OFFLINE"
            detail = str(exc) or exc.__class__.__name__
        finally:
            if writer is not None:
                writer.close()
                try:
                    await writer.wait_closed()
                except OSError:
                    pass
        return AgentRuntimeStatus(
            agent_id=record.definition.agent_id,
            availability=availability,
            activity="IDLE",
            checked_at=now,
            latency_ms=latency_ms,
            detail=detail,
            endpoint=endpoint,
        )

    @staticmethod
    def _endpoint(record: AgentRecord) -> str | None:
        if record.definition.integration_mode == "managed-adapter":
            backend = record.definition.backend or {}
            endpoint = str(backend.get("endpoint", "")).strip()
            return endpoint or None
        for interface in record.card.get("supportedInterfaces", []):
            if not isinstance(interface, dict):
                continue
            endpoint = str(interface.get("url", "")).strip()
            if endpoint.startswith(("http://", "https://")):
                return endpoint
        return None
