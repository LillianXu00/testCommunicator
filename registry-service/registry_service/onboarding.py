from __future__ import annotations

import json
import os

from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from registry_service.store import AgentRegistry


FetchAgentCard = Callable[[str], Awaitable[dict[str, Any]]]
_RESULT_KEYS = (
    "output",
    "outputText",
    "output_text",
    "answer",
    "report",
    "text",
    "content",
    "result",
)


def _render_template(value: Any, *, input_text: str, context_id: str) -> Any:
    if isinstance(value, dict):
        return {
            key: _render_template(
                item,
                input_text=input_text,
                context_id=context_id,
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [
            _render_template(
                item,
                input_text=input_text,
                context_id=context_id,
            )
            for item in value
        ]
    if not isinstance(value, str):
        return value
    return (
        value.replace("{{input}}", input_text)
        .replace("{{contextId}}", context_id)
    )


def _candidate_selectors(value: Any, path: str = "$") -> list[str]:
    if isinstance(value, str) and value.strip():
        return [path]
    if isinstance(value, dict):
        candidates: list[str] = []
        for key in _RESULT_KEYS:
            if key not in value:
                continue
            candidates.extend(
                _candidate_selectors(value[key], f"{path}.{key}")
            )
        if candidates:
            return candidates
        for key, item in value.items():
            candidates.extend(_candidate_selectors(item, f"{path}.{key}"))
        return candidates
    if isinstance(value, list):
        candidates: list[str] = []
        for index, item in enumerate(value):
            candidates.extend(
                _candidate_selectors(item, f"{path}[{index}]")
            )
        return candidates
    return []


def _redact_credentials(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: (
                "[REDACTED]"
                if key.lower() in {"token", "authtoken", "password", "secret"}
                else _redact_credentials(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_credentials(item) for item in value]
    return value


def _backend_auth(
    interface: dict[str, Any],
    *,
    default_auth_env: str = "",
) -> dict[str, str]:
    authentication = interface.get("authentication")
    auth_token = interface.get("authToken")
    auth_env = interface.get("authEnv")
    explicitly_configured = (
        authentication is not None
        or auth_token is not None
        or auth_env is not None
    )

    if authentication is not None:
        if not isinstance(authentication, dict):
            raise ValueError("interface.authentication must be an object")
        auth_type = str(authentication.get("type", "bearer")).strip().lower()
        if auth_type == "none":
            if authentication.get("token") or authentication.get("env"):
                raise ValueError(
                    "authentication type none cannot contain token or env"
                )
            return {}
        if auth_type != "bearer":
            raise ValueError(
                "only bearer or none authentication is currently supported"
            )
        auth_token = authentication.get("token", auth_token)
        auth_env = authentication.get("env", auth_env)

    token = str(auth_token or "")
    environment_name = str(auth_env or "").strip()
    if token and environment_name:
        raise ValueError(
            "provide either a bearer token or an auth environment variable"
        )
    if token:
        return {"authToken": token}
    if environment_name:
        return {"authEnv": environment_name}
    if default_auth_env and not explicitly_configured:
        return {"authEnv": default_auth_env}
    return {}


class OnboardingService:
    """Deterministic first-pass self-registration orchestration."""

    def __init__(
        self,
        registry: AgentRegistry,
        *,
        fetch_native_agent_card: FetchAgentCard,
    ) -> None:
        self.registry = registry
        self.fetch_native_agent_card = fetch_native_agent_card

    async def register(self, manifest: dict[str, Any]) -> dict[str, Any]:
        agent_id = str(manifest.get("agentId", "")).strip()
        if not agent_id:
            raise ValueError("agentId is required")

        source_card_url = str(
            manifest.get("sourceAgentCardUrl", "")
        ).strip()
        interface = manifest.get("interface", {})
        if not isinstance(interface, dict):
            raise ValueError("interface must be an object")
        if not source_card_url and interface.get("kind") == "native-a2a":
            source_card_url = str(interface.get("agentCardUrl", "")).strip()

        if source_card_url:
            card = await self.fetch_native_agent_card(source_card_url)
            record = self.registry.upsert(
                {
                    "agentId": agent_id,
                    "integrationMode": "native-a2a",
                    "sourceAgentCardUrl": source_card_url,
                    "status": "ACTIVE",
                },
                source_card=card,
            )
            return self._registration_result(
                record,
                decision="NATIVE_A2A",
            )

        payload, decision, inferred = await self._managed_payload(
            manifest,
            interface,
        )
        if payload is None:
            request = self.registry.create_adapter_request({
                "agentName": str(
                    manifest.get("name") or agent_id
                ),
                "contact": str(
                    manifest.get("owner") or "self-registration"
                ),
                "endpoint": str(interface.get("endpoint", "")),
                "documentationUrl": str(
                    interface.get("documentationUrl", "")
                ),
                "notes": json.dumps(
                    {
                        "agentId": agent_id,
                        "interface": _redact_credentials(interface),
                    },
                    ensure_ascii=True,
                ),
            })
            return {
                "agentId": agent_id,
                "status": "ADAPTER_REQUIRED",
                "decision": "NO_COMPATIBLE_ADAPTER",
                "adapterRequestId": request["requestId"],
            }

        record = self.registry.upsert(payload)
        result = self._registration_result(record, decision=decision)
        result["inferredResponseMapping"] = inferred
        return result

    async def _managed_payload(
        self,
        manifest: dict[str, Any],
        interface: dict[str, Any],
    ) -> tuple[dict[str, Any] | None, str, bool]:
        endpoint = str(interface.get("endpoint", "")).strip()
        kind = str(interface.get("kind", "")).strip().lower()
        if kind == "openclaw-responses":
            auth = _backend_auth(
                interface,
                default_auth_env="OPENCLAW_GATEWAY_TOKEN",
            )
            backend = {
                "adapterType": "openclaw-responses",
                "endpoint": endpoint,
                "agentId": str(
                    interface.get("agentId") or manifest.get("agentId")
                ),
                "timeoutSeconds": float(
                    interface.get("timeoutSeconds", 600)
                ),
                **auth,
            }
            return (
                self._agent_payload(manifest, backend),
                "EXISTING_ADAPTER",
                False,
            )

        # Keep http-json as a compatibility alias for older manifests. New
        # registrations should identify this mapping as declarative-http.
        if kind == "http-json":
            kind = "declarative-http"
        if kind != "declarative-http":
            return None, "NO_COMPATIBLE_ADAPTER", False
        method = str(interface.get("method", "POST")).upper()
        execution_mode = str(
            interface.get("executionMode", "sync")
        ).lower()
        if method != "POST" or execution_mode != "sync":
            return None, "NO_COMPATIBLE_ADAPTER", False

        auth = _backend_auth(interface)
        request_contract = interface.get("request", {})
        response_contract = interface.get("response", {})
        if not isinstance(request_contract, dict):
            raise ValueError("interface.request must be an object")
        if not isinstance(response_contract, dict):
            raise ValueError("interface.response must be an object")
        request_body = request_contract.get(
            "body",
            {
                "input": "{{input}}",
                "contextId": "{{contextId}}",
            },
        )
        if not isinstance(request_body, dict) or not request_body:
            raise ValueError("interface.request.body must be a non-empty object")
        selectors = response_contract.get("textSelectors", [])
        if isinstance(selectors, str):
            selectors = [selectors]
        if not isinstance(selectors, list):
            raise ValueError(
                "interface.response.textSelectors must be an array"
            )
        selectors = [
            str(selector).strip()
            for selector in selectors
            if str(selector).strip()
        ]

        inferred = False
        if not selectors:
            test_case = manifest.get("testCase")
            if isinstance(test_case, dict) and test_case.get("input"):
                selectors = await self._probe_response(
                    endpoint=endpoint,
                    request_body=request_body,
                    auth_env=str(auth.get("authEnv", "")).strip(),
                    auth_token=str(auth.get("authToken", "")),
                    timeout_seconds=float(
                        interface.get("timeoutSeconds", 30)
                    ),
                    input_text=str(test_case["input"]),
                )
                inferred = True
        if not selectors:
            return None, "NO_COMPATIBLE_ADAPTER", False

        backend = {
            "adapterType": "declarative-http",
            "endpoint": endpoint,
            "timeoutSeconds": float(
                interface.get("timeoutSeconds", 600)
            ),
            "requestBody": request_body,
            "responseTextSelectors": selectors,
            **auth,
        }
        decision = "GENERATED_DECLARATIVE_ADAPTER"

        return self._agent_payload(manifest, backend), decision, inferred

    async def _probe_response(
        self,
        *,
        endpoint: str,
        request_body: dict[str, Any],
        auth_env: str,
        auth_token: str,
        timeout_seconds: float,
        input_text: str,
    ) -> list[str]:
        headers: dict[str, str] = {}
        token = auth_token
        if not token and auth_env:
            token = os.getenv(auth_env, "")
            if not token:
                raise ValueError(
                    f"{auth_env} is required to probe the agent endpoint"
                )
        if token:
            headers["Authorization"] = f"Bearer {token}"
        body = _render_template(
            request_body,
            input_text=input_text,
            context_id="self-registration-probe",
        )
        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            response = await client.post(
                endpoint,
                headers=headers,
                json=body,
            )
            response.raise_for_status()
        candidates = _candidate_selectors(response.json())
        if not candidates:
            raise ValueError(
                "the test response contains no non-empty text result"
            )
        return candidates[:1]

    @staticmethod
    def _agent_payload(
        manifest: dict[str, Any],
        backend: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "agentId": manifest.get("agentId"),
            "integrationMode": "managed-adapter",
            "name": manifest.get("name"),
            "description": manifest.get("description"),
            "version": manifest.get("version", "1.0.0"),
            "skills": manifest.get("skills", []),
            "inputModes": manifest.get(
                "inputModes",
                ["text/plain"],
            ),
            "outputModes": manifest.get(
                "outputModes",
                ["text/plain"],
            ),
            "capabilities": manifest.get(
                "capabilities",
                {
                    "streaming": False,
                    "pushNotifications": True,
                },
            ),
            "backend": backend,
            "status": "ACTIVE",
        }

    def _registration_result(
        self,
        record: Any,
        *,
        decision: str,
    ) -> dict[str, Any]:
        catalog = self.registry.catalog_entry(record)
        return {
            "agentId": record.definition.agent_id,
            "status": record.definition.status,
            "decision": decision,
            "revision": record.revision,
            "cardRevision": record.card_revision,
            "cardUrl": catalog["cardUrl"],
            "a2aUrl": record.card["supportedInterfaces"][0]["url"],
            "adapter": (
                record.definition.backend.get("adapterType")
                if record.definition.backend
                else None
            ),
        }
