from __future__ import annotations

import os

from a2a_server.backends.base import AgentBackend
from a2a_server.backends.declarative_http import DeclarativeHttpBackend
from a2a_server.backends.openclaw import OpenClawResponsesBackend
from a2a_server.registry_client import GatewayAgentDefinition


class BackendFactory:
    def create(self, definition: GatewayAgentDefinition) -> AgentBackend:
        backend = definition.backend
        if backend is None:
            raise RuntimeError("Native A2A agents do not use a managed backend")
        timeout_seconds = float(backend.get("timeoutSeconds", 600))
        adapter_type = backend["adapterType"]
        resolved_token = (
            str(backend["authToken"])
            if backend.get("authToken")
            else None
        )
        if adapter_type == "openclaw-responses":
            return OpenClawResponsesBackend(
                gateway_url=str(backend["endpoint"]),
                token=(
                    resolved_token
                    or os.getenv(
                        str(
                            backend.get(
                                "authEnv",
                                "OPENCLAW_GATEWAY_TOKEN",
                            )
                        ),
                        "",
                    )
                ),
                agent_id=str(backend.get("agentId", definition.agent_id)),
                timeout_seconds=timeout_seconds,
            )
        if adapter_type == "declarative-http":
            return DeclarativeHttpBackend(
                endpoint=str(backend["endpoint"]),
                request_body=dict(backend["requestBody"]),
                response_text_selectors=[
                    str(value)
                    for value in backend["responseTextSelectors"]
                ],
                token=resolved_token,
                auth_env=(
                    str(backend["authEnv"])
                    if backend.get("authEnv")
                    else None
                ),
                timeout_seconds=timeout_seconds,
            )
        raise RuntimeError(f"Unsupported backend adapter: {adapter_type}")
