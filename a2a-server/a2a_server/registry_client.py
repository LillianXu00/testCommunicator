from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
import uuid

import httpx


@dataclass(frozen=True)
class GatewayAgentDefinition:
    agent_id: str
    integration_mode: str
    name: str
    skills: tuple[dict[str, Any], ...] = ()
    backend: dict[str, Any] | None = None

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> GatewayAgentDefinition:
        backend = payload.get("backend")
        return cls(
            agent_id=str(payload.get("agentId", "")),
            integration_mode=str(payload.get("integrationMode", "")),
            name=str(payload.get("name", "")),
            skills=tuple(
                dict(skill)
                for skill in payload.get("skills", [])
                if isinstance(skill, dict)
            ),
            backend=dict(backend) if isinstance(backend, dict) else None,
        )


@dataclass(frozen=True)
class GatewayAgentRecord:
    definition: GatewayAgentDefinition
    revision: int


class RegistryClient:
    """Reads active managed-agent definitions from the Registry control plane."""

    def __init__(
        self,
        base_url: str,
        *,
        service_token: str = "",
        timeout_seconds: float = 10,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.service_token = service_token
        self.client = httpx.AsyncClient(
            timeout=timeout_seconds,
            transport=transport,
        )

    async def get(self, agent_id: str) -> GatewayAgentRecord | None:
        headers = (
            {"Authorization": f"Bearer {self.service_token}"}
            if self.service_token
            else {}
        )
        response = await self.client.get(
            f"{self.base_url}/registry/v1/internal/agents/{agent_id}",
            headers=headers,
        )
        if response.status_code == 404:
            return None
        response.raise_for_status()
        payload = response.json()
        definition = payload.get("definition")
        if not isinstance(definition, dict):
            raise RuntimeError(
                "Registry returned an invalid internal Agent definition"
            )
        return GatewayAgentRecord(
            definition=GatewayAgentDefinition.from_payload(definition),
            revision=int(payload.get("revision", 0)),
        )

    async def report_task_event(
        self,
        *,
        agent_id: str,
        task_id: str,
        context_id: str,
        state: str,
        message: str,
        event_id: str | None = None,
        task_sequence: int | None = None,
        metadata: dict[str, Any] | None = None,
        source: str = "managed-gateway",
    ) -> None:
        headers = (
            {"Authorization": f"Bearer {self.service_token}"}
            if self.service_token
            else {}
        )
        response = await self.client.post(
            f"{self.base_url}/registry/v1/internal/task-events",
            headers=headers,
            json={
                "eventId": event_id or f"evt-{uuid.uuid4().hex}",
                "agentId": agent_id,
                "taskId": task_id,
                "contextId": context_id,
                "taskSequence": task_sequence,
                "type": "status-update",
                "state": state,
                "message": message,
                "timestamp": datetime.now(UTC).isoformat(),
                "source": source,
                "metadata": metadata or {},
            },
        )
        response.raise_for_status()

    async def claim_task_execution(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        response = await self.client.post(
            f"{self.base_url}/registry/v1/internal/task-executions/claim",
            headers=self._service_headers(),
            json=payload,
        )
        response.raise_for_status()
        result = response.json()
        if not isinstance(result, dict):
            raise RuntimeError("Registry returned an invalid execution claim")
        return result

    async def complete_task_execution(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        response = await self.client.post(
            f"{self.base_url}/registry/v1/internal/task-executions/complete",
            headers=self._service_headers(),
            json=payload,
        )
        response.raise_for_status()
        result = response.json()
        if not isinstance(result, dict):
            raise RuntimeError("Registry returned an invalid execution result")
        return result

    async def claim_task_experience(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        response = await self.client.post(
            f"{self.base_url}/registry/v1/internal/task-experiences/claim",
            headers=self._service_headers(),
            json=payload,
        )
        response.raise_for_status()
        result = response.json()
        if not isinstance(result, dict):
            raise RuntimeError("Registry returned an invalid experience claim")
        return result

    async def complete_task_experience(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        response = await self.client.post(
            f"{self.base_url}/registry/v1/internal/task-experiences/complete",
            headers=self._service_headers(),
            json=payload,
        )
        response.raise_for_status()
        result = response.json()
        if not isinstance(result, dict):
            raise RuntimeError("Registry returned an invalid experience result")
        return result

    async def release_task_experience(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        response = await self.client.post(
            f"{self.base_url}/registry/v1/internal/task-experiences/release",
            headers=self._service_headers(),
            json=payload,
        )
        response.raise_for_status()
        result = response.json()
        if not isinstance(result, dict):
            raise RuntimeError("Registry returned an invalid experience release")
        return result

    def _service_headers(self) -> dict[str, str]:
        return (
            {"Authorization": f"Bearer {self.service_token}"}
            if self.service_token
            else {}
        )

    async def aclose(self) -> None:
        await self.client.aclose()
