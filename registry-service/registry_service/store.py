from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import uuid

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

from a2a.server.request_handlers.response_helpers import agent_card_to_dict
from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill

from registry_service.secret_store import RegistrySecretStore


_ENV_PATTERN = re.compile(r"^\$\{([A-Z0-9_]+)(?::-(.*))?\}$")
_AGENT_ID_PATTERN = re.compile(r"[a-z0-9][a-z0-9._-]{0,127}")

ADAPTER_CATALOG: dict[str, dict[str, Any]] = {
    "openclaw-responses": {
        "id": "openclaw-responses",
        "name": "OpenClaw Responses",
        "description": "通过 OpenClaw Responses API 调用指定的内部 Agent。",
        "bestFor": "已启用 Responses API 的 OpenClaw Agent",
        "requestMethod": "POST",
        "endpointTemplate": "{endpoint}/v1/responses",
        "requirements": [
            "Endpoint 是 OpenClaw Gateway 的基础地址。",
            "已知目标 OpenClaw Agent ID。",
            "可通过环境变量提供 Gateway Bearer Token。",
        ],
        "requestExample": {
            "model": "openclaw/{agentId}",
            "input": "The delegated task text",
            "user": "a2a:{contextId}",
            "stream": False,
        },
        "responseDescription": (
            "优先读取 output_text；若不存在，则合并 output 消息中 "
            "type 为 output_text 的文本内容。"
        ),
        "responseExample": {
            "output_text": "The specialist agent result",
        },
        "requiresAgentId": True,
        "defaultEndpoint": "http://127.0.0.1:18789",
        "defaultAuthEnv": "OPENCLAW_GATEWAY_TOKEN",
        "defaultTimeoutSeconds": 600,
    },
    "declarative-http": {
        "id": "declarative-http",
        "name": "Declarative HTTP",
        "description": (
            "Maps the canonical text invocation to a synchronous JSON HTTP "
            "endpoint through a validated request template."
        ),
        "bestFor": (
            "Synchronous JSON agents whose request and response fields differ "
            "from the platform defaults"
        ),
        "requestMethod": "POST",
        "endpointTemplate": "{endpoint}",
        "requirements": [
            "The endpoint accepts a synchronous JSON POST request.",
            "The request body can be expressed with input/contextId templates.",
            "The final text is available through a deterministic JSON selector.",
        ],
        "requestExample": {
            "query": "{{input}}",
            "session_id": "{{contextId}}",
        },
        "responseDescription": (
            "Reads the first non-empty value selected by responseTextSelectors."
        ),
        "responseExample": {
            "data": {
                "answer": "The specialist agent result",
            }
        },
        "requiresAgentId": False,
        "defaultEndpoint": "",
        "defaultAuthEnv": "",
        "defaultTimeoutSeconds": 600,
    },
}


def _expand_environment(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _expand_environment(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_expand_environment(item) for item in value]
    if not isinstance(value, str):
        return value
    match = _ENV_PATTERN.match(value)
    if not match:
        return value
    name, default = match.groups()
    return os.getenv(name, default or "")


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True)


def _content_hash(value: Any) -> str:
    return f"sha256:{hashlib.sha256(_canonical_json(value).encode()).hexdigest()}"


def _require_http_url(value: str, field_name: str) -> None:
    if not value.startswith(("http://", "https://")):
        raise ValueError(f"{field_name} must be an absolute HTTP(S) URL")


def _normalize_skills(raw_skills: Any) -> list[dict[str, Any]]:
    if not isinstance(raw_skills, list):
        raise ValueError("skills must be an array")
    skills: list[dict[str, Any]] = []
    seen_skill_ids: set[str] = set()
    for item in raw_skills:
        if not isinstance(item, dict):
            raise ValueError("each skill must be an object")
        skill_id = str(item.get("id", "")).strip()
        skill_name = str(item.get("name", "")).strip()
        skill_description = str(item.get("description", "")).strip()
        if not skill_id or not skill_name or not skill_description:
            raise ValueError("each skill requires id, name and description")
        if skill_id in seen_skill_ids:
            raise ValueError(f"duplicate skill id: {skill_id}")
        seen_skill_ids.add(skill_id)
        skills.append({
            "id": skill_id,
            "name": skill_name,
            "description": skill_description,
            "tags": [str(value) for value in item.get("tags", [])],
            "examples": [str(value) for value in item.get("examples", [])],
            "inputModes": [str(value) for value in item.get("inputModes", [])],
            "outputModes": [str(value) for value in item.get("outputModes", [])],
        })
    return skills


@dataclass(frozen=True)
class AgentDefinition:
    agent_id: str
    integration_mode: str
    name: str
    description: str
    version: str
    skills: list[dict[str, Any]]
    input_modes: list[str]
    output_modes: list[str]
    capabilities: dict[str, bool]
    backend: dict[str, Any] | None = None
    source_agent_card_url: str | None = None
    status: str = "ACTIVE"

    @classmethod
    def from_payload(
        cls,
        payload: dict[str, Any],
        *,
        agent_id_override: str | None = None,
        source_card: dict[str, Any] | None = None,
    ) -> AgentDefinition:
        agent_id = str(agent_id_override or payload.get("agentId", "")).strip()
        if not agent_id:
            raise ValueError("agentId is required")
        if not _AGENT_ID_PATTERN.fullmatch(agent_id):
            raise ValueError(
                "agentId must contain only lowercase letters, digits, '.', '_' or '-'"
            )

        status = str(payload.get("status", "ACTIVE")).strip().upper()
        if status not in {"ACTIVE", "DISABLED"}:
            raise ValueError("status must be ACTIVE or DISABLED")

        # Definitions written before integrationMode was introduced are managed
        # adapter records and remain valid without a migration.
        integration_mode = str(
            payload.get(
                "integrationMode",
                "managed-adapter" if payload.get("backend") else "",
            )
        ).strip()
        if integration_mode not in {"native-a2a", "managed-adapter"}:
            raise ValueError(
                "integrationMode must be native-a2a or managed-adapter"
            )

        if integration_mode == "native-a2a":
            source_url = str(payload.get("sourceAgentCardUrl", "")).strip()
            _require_http_url(source_url, "sourceAgentCardUrl")
            if source_card is None:
                # This branch is used when reading persisted records. The public
                # card snapshot remains in card_json and is not rebuilt.
                name = str(payload.get("name", "")).strip()
                description = str(payload.get("description", "")).strip()
                version = str(payload.get("version", "1.0.0")).strip()
                skills = _normalize_skills(payload.get("skills", []))
                input_modes = [
                    str(value)
                    for value in payload.get("inputModes", ["text/plain"])
                ]
                output_modes = [
                    str(value)
                    for value in payload.get("outputModes", ["text/plain"])
                ]
                raw_capabilities = payload.get("capabilities", {})
            else:
                name = str(source_card.get("name", "")).strip()
                description = str(source_card.get("description", "")).strip()
                version = str(source_card.get("version", "1.0.0")).strip()
                skills = _normalize_skills(source_card.get("skills", []))
                input_modes = [
                    str(value)
                    for value in source_card.get(
                        "defaultInputModes", ["text/plain"]
                    )
                ]
                output_modes = [
                    str(value)
                    for value in source_card.get(
                        "defaultOutputModes", ["text/plain"]
                    )
                ]
                raw_capabilities = source_card.get("capabilities", {})
            if not name or not description:
                raise ValueError("the native Agent Card requires name and description")
            if not isinstance(raw_capabilities, dict):
                raise ValueError("Agent Card capabilities must be an object")
            return cls(
                agent_id=agent_id,
                integration_mode=integration_mode,
                name=name,
                description=description,
                version=version,
                skills=skills,
                input_modes=input_modes,
                output_modes=output_modes,
                capabilities={
                    "streaming": bool(raw_capabilities.get("streaming", False)),
                    "pushNotifications": bool(
                        raw_capabilities.get("pushNotifications", False)
                    ),
                },
                source_agent_card_url=source_url,
                status=status,
            )

        name = str(payload.get("name", "")).strip()
        description = str(payload.get("description", "")).strip()
        version = str(payload.get("version", "1.0.0")).strip()
        if not name or not description:
            raise ValueError("name and description are required")
        skills = _normalize_skills(payload.get("skills", []))

        backend = payload.get("backend")
        if not isinstance(backend, dict):
            raise ValueError("backend is required for managed-adapter agents")
        if "authToken" in backend:
            raise ValueError("backend.authToken is write-only")
        auth_env = str(backend.get("authEnv", "")).strip()
        secret_ref = str(backend.get("secretRef", "")).strip()
        if auth_env and secret_ref:
            raise ValueError(
                "backend.authEnv and backend.secretRef cannot both be set"
            )
        adapter_type = str(backend.get("adapterType", "")).strip()
        endpoint = str(backend.get("endpoint", "")).strip()
        if adapter_type not in ADAPTER_CATALOG:
            raise ValueError(f"unsupported backend.adapterType: {adapter_type}")
        _require_http_url(endpoint, "backend.endpoint")
        timeout_seconds = float(backend.get("timeoutSeconds", 600))
        if timeout_seconds < 1 or timeout_seconds > 86400:
            raise ValueError("backend.timeoutSeconds must be between 1 and 86400")
        if adapter_type == "openclaw-responses" and not str(
            backend.get("agentId", "")
        ).strip():
            raise ValueError("backend.agentId is required for openclaw-responses")
        if adapter_type == "declarative-http":
            request_body = backend.get("requestBody")
            selectors = backend.get("responseTextSelectors")
            if not isinstance(request_body, dict) or not request_body:
                raise ValueError(
                    "backend.requestBody is required for declarative-http"
                )
            if not isinstance(selectors, list) or not selectors:
                raise ValueError(
                    "backend.responseTextSelectors is required for "
                    "declarative-http"
                )
            if not all(
                isinstance(selector, str) and selector.startswith("$")
                for selector in selectors
            ):
                raise ValueError(
                    "backend.responseTextSelectors must contain JSON selectors"
                )

        raw_capabilities = payload.get("capabilities", {})
        if not isinstance(raw_capabilities, dict):
            raise ValueError("capabilities must be an object")
        return cls(
            agent_id=agent_id,
            integration_mode=integration_mode,
            name=name,
            description=description,
            version=version,
            skills=skills,
            input_modes=[
                str(value)
                for value in payload.get("inputModes", ["text/plain"])
            ],
            output_modes=[
                str(value)
                for value in payload.get("outputModes", ["text/plain"])
            ],
            capabilities={
                "streaming": bool(raw_capabilities.get("streaming", False)),
                "pushNotifications": bool(
                    raw_capabilities.get("pushNotifications", True)
                ),
            },
            backend=dict(backend),
            status=status,
        )

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "agentId": self.agent_id,
            "integrationMode": self.integration_mode,
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "skills": self.skills,
            "inputModes": self.input_modes,
            "outputModes": self.output_modes,
            "capabilities": self.capabilities,
            "status": self.status,
        }
        if self.integration_mode == "native-a2a":
            payload["sourceAgentCardUrl"] = self.source_agent_card_url
        else:
            payload["backend"] = self.backend
        return payload


@dataclass(frozen=True)
class AgentRecord:
    definition: AgentDefinition
    revision: int
    card: dict[str, Any]
    card_revision: str
    updated_at: str


class AgentRegistry:
    def __init__(
        self,
        database_path: str | Path,
        *,
        gateway_public_url: str,
        registry_public_url: str,
    ) -> None:
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.gateway_public_url = gateway_public_url.rstrip("/")
        self.registry_public_url = registry_public_url.rstrip("/")
        self.secret_store = RegistrySecretStore(self.database_path)
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
                CREATE TABLE IF NOT EXISTS agents (
                    agent_id TEXT PRIMARY KEY,
                    definition_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    card_json TEXT NOT NULL,
                    card_revision TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS adapter_requests (
                    request_id TEXT PRIMARY KEY,
                    payload_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS agent_secrets (
                    secret_ref TEXT PRIMARY KEY,
                    agent_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    ciphertext BLOB NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(agent_id, kind),
                    FOREIGN KEY(agent_id) REFERENCES agents(agent_id)
                        ON DELETE CASCADE
                )
                """
            )
            self._migrate_legacy_generic_http(connection)
        self.rebuild_public_cards()

    @staticmethod
    def _migrate_legacy_generic_http(
        connection: sqlite3.Connection,
    ) -> None:
        """Normalize the removed Generic HTTP preset into declarative config."""
        rows = connection.execute(
            "SELECT agent_id, definition_json FROM agents"
        ).fetchall()
        for row in rows:
            payload = json.loads(row["definition_json"])
            backend = payload.get("backend")
            if not isinstance(backend, dict):
                continue
            if backend.get("adapterType") != "generic-http":
                continue
            backend["adapterType"] = "declarative-http"
            backend["requestBody"] = {
                "input": "{{input}}",
                "contextId": "{{contextId}}",
            }
            backend["responseTextSelectors"] = [
                "$",
                "$.output",
                "$.outputText",
                "$.output_text",
                "$.text",
            ]
            connection.execute(
                "UPDATE agents SET definition_json = ? WHERE agent_id = ?",
                (_canonical_json(payload), row["agent_id"]),
            )

    def build_card(self, definition: AgentDefinition) -> dict[str, Any]:
        if definition.integration_mode != "managed-adapter":
            raise ValueError("native Agent Cards must be supplied by the provider")
        skills = [
            AgentSkill(
                id=item["id"],
                name=item["name"],
                description=item["description"],
                tags=item["tags"],
                examples=item["examples"],
                input_modes=item["inputModes"],
                output_modes=item["outputModes"],
            )
            for item in definition.skills
        ]
        gateway_url = (
            f"{self.gateway_public_url}/a2a/agents/"
            f"{quote(definition.agent_id, safe='')}"
        )
        card = AgentCard(
            name=definition.name,
            description=definition.description,
            version=definition.version,
            supported_interfaces=[
                AgentInterface(
                    url=gateway_url,
                    protocol_binding="JSONRPC",
                    protocol_version="1.0",
                )
            ],
            capabilities=AgentCapabilities(
                streaming=definition.capabilities["streaming"],
                push_notifications=definition.capabilities["pushNotifications"],
            ),
            default_input_modes=definition.input_modes,
            default_output_modes=definition.output_modes,
            skills=skills,
        )
        return agent_card_to_dict(card)

    def upsert(
        self,
        payload: dict[str, Any],
        *,
        agent_id: str | None = None,
        source_card: dict[str, Any] | None = None,
    ) -> AgentRecord:
        payload = json.loads(json.dumps(payload))
        resolved_agent_id = str(agent_id or payload.get("agentId", "")).strip()
        backend = payload.get("backend")
        auth_token: str | None = None
        clear_auth_secret = False
        expected_secret_ref = (
            f"agent:{resolved_agent_id}:bearer" if resolved_agent_id else ""
        )
        if isinstance(backend, dict):
            raw_auth_token = backend.pop("authToken", None)
            clear_auth_secret = bool(backend.pop("clearAuthSecret", False))
            if raw_auth_token is not None:
                auth_token = str(raw_auth_token)
                if not auth_token:
                    raise ValueError("backend.authToken cannot be empty")
                if len(auth_token) > 16384:
                    raise ValueError("backend.authToken is too large")
                backend.pop("authEnv", None)
                backend["secretRef"] = expected_secret_ref
            elif backend.get("authEnv"):
                backend.pop("secretRef", None)
            elif clear_auth_secret:
                backend.pop("secretRef", None)
            elif backend.get("secretRef"):
                if str(backend["secretRef"]) != expected_secret_ref:
                    raise ValueError(
                        "backend.secretRef must belong to the current agent"
                    )
            elif resolved_agent_id:
                existing = self.get(resolved_agent_id, active_only=False)
                existing_backend = (
                    existing.definition.backend
                    if existing and existing.definition.backend
                    else {}
                )
                existing_ref = str(existing_backend.get("secretRef", ""))
                if existing_ref == expected_secret_ref:
                    backend["secretRef"] = existing_ref

        definition = AgentDefinition.from_payload(
            payload,
            agent_id_override=agent_id,
            source_card=source_card,
        )
        definition_json = _canonical_json(definition.to_payload())
        card = (
            source_card
            if definition.integration_mode == "native-a2a"
            else self.build_card(definition)
        )
        if card is None:
            raise ValueError("a native A2A registration requires an Agent Card")
        card_json = _canonical_json(card)
        card_revision = _content_hash(card)
        now = datetime.now(UTC).isoformat()

        with self._connect() as connection:
            existing = connection.execute(
                "SELECT definition_json, card_json, revision FROM agents WHERE agent_id = ?",
                (definition.agent_id,),
            ).fetchone()
            unchanged = bool(
                existing
                and existing["definition_json"] == definition_json
                and existing["card_json"] == card_json
            )
            secret_changed = False
            if auth_token is not None:
                secret_row = connection.execute(
                    """
                    SELECT ciphertext
                    FROM agent_secrets
                    WHERE secret_ref = ? AND agent_id = ?
                    """,
                    (expected_secret_ref, definition.agent_id),
                ).fetchone()
                existing_token = (
                    self.secret_store.decrypt(secret_row["ciphertext"])
                    if secret_row
                    else None
                )
                secret_changed = existing_token != auth_token
            revision = (
                int(existing["revision"])
                if unchanged and not secret_changed
                else int(existing["revision"]) + 1
                if existing
                else 1
            )
            connection.execute(
                """
                INSERT INTO agents (
                    agent_id, definition_json, status, revision,
                    card_json, card_revision, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(agent_id) DO UPDATE SET
                    definition_json = excluded.definition_json,
                    status = excluded.status,
                    revision = excluded.revision,
                    card_json = excluded.card_json,
                    card_revision = excluded.card_revision,
                    updated_at = excluded.updated_at
                """,
                (
                    definition.agent_id,
                    definition_json,
                    definition.status,
                    revision,
                    card_json,
                    card_revision,
                    now,
                ),
            )
            if auth_token is not None:
                connection.execute(
                    """
                    INSERT INTO agent_secrets (
                        secret_ref, agent_id, kind, ciphertext, updated_at
                    ) VALUES (?, ?, 'bearer', ?, ?)
                    ON CONFLICT(secret_ref) DO UPDATE SET
                        agent_id = excluded.agent_id,
                        kind = excluded.kind,
                        ciphertext = excluded.ciphertext,
                        updated_at = excluded.updated_at
                    """,
                    (
                        expected_secret_ref,
                        definition.agent_id,
                        self.secret_store.encrypt(auth_token),
                        now,
                    ),
                )
            elif clear_auth_secret or (
                isinstance(backend, dict)
                and backend.get("authEnv")
            ):
                connection.execute(
                    "DELETE FROM agent_secrets WHERE agent_id = ?",
                    (definition.agent_id,),
                )
        return AgentRecord(definition, revision, card, card_revision, now)

    def delete(self, agent_id: str) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM agents WHERE agent_id = ?",
                (agent_id,),
            )
        return cursor.rowcount > 0

    def seed_from_file(self, path: str | Path) -> AgentRecord | None:
        path = Path(path)
        if not path.exists():
            return None
        payload = _expand_environment(json.loads(path.read_text(encoding="utf-8")))
        payload.setdefault("integrationMode", "managed-adapter")
        agent_id = str(payload.get("agentId", "")).strip()
        existing = self.get(agent_id, active_only=False) if agent_id else None
        return existing or self.upsert(payload)

    def get(self, agent_id: str, *, active_only: bool = True) -> AgentRecord | None:
        query = "SELECT * FROM agents WHERE agent_id = ?"
        params: tuple[Any, ...] = (agent_id,)
        if active_only:
            query += " AND status = 'ACTIVE'"
        with self._connect() as connection:
            row = connection.execute(query, params).fetchone()
        return self._row_to_record(row) if row else None

    def list(self, *, active_only: bool = True) -> list[AgentRecord]:
        query = "SELECT * FROM agents"
        if active_only:
            query += " WHERE status = 'ACTIVE'"
        query += " ORDER BY agent_id"
        with self._connect() as connection:
            rows = connection.execute(query).fetchall()
        return [self._row_to_record(row) for row in rows]

    def resolve_secret(self, agent_id: str, secret_ref: str) -> str:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT ciphertext
                FROM agent_secrets
                WHERE secret_ref = ? AND agent_id = ?
                """,
                (secret_ref, agent_id),
            ).fetchone()
        if row is None:
            raise KeyError("Agent credential not found")
        return self.secret_store.decrypt(row["ciphertext"])

    def rebuild_public_cards(self) -> None:
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM agents").fetchall()
            for row in rows:
                definition = AgentDefinition.from_payload(
                    json.loads(row["definition_json"])
                )
                if definition.integration_mode == "native-a2a":
                    continue
                card = self.build_card(definition)
                card_json = _canonical_json(card)
                connection.execute(
                    """
                    UPDATE agents
                    SET card_json = ?, card_revision = ?
                    WHERE agent_id = ?
                    """,
                    (card_json, _content_hash(card), definition.agent_id),
                )

    def catalog_entry(self, record: AgentRecord) -> dict[str, Any]:
        return {
            "agentId": record.definition.agent_id,
            "integrationMode": record.definition.integration_mode,
            "status": record.definition.status,
            "revision": record.revision,
            "cardRevision": record.card_revision,
            "cardUrl": (
                f"{self.registry_public_url}/registry/v1/agents/"
                f"{quote(record.definition.agent_id, safe='')}/card"
            ),
            "agentCard": record.card,
        }

    def create_adapter_request(self, payload: dict[str, Any]) -> dict[str, Any]:
        agent_name = str(payload.get("agentName", "")).strip()
        endpoint = str(payload.get("endpoint", "")).strip()
        documentation_url = str(payload.get("documentationUrl", "")).strip()
        contact = str(payload.get("contact", "")).strip()
        notes = str(payload.get("notes", "")).strip()
        if not agent_name or not endpoint or not contact:
            raise ValueError("agentName, endpoint and contact are required")
        _require_http_url(endpoint, "endpoint")
        if documentation_url:
            _require_http_url(documentation_url, "documentationUrl")
        normalized = {
            "agentName": agent_name,
            "endpoint": endpoint,
            "documentationUrl": documentation_url,
            "contact": contact,
            "notes": notes,
        }
        request_id = f"adapter-{uuid.uuid4().hex[:12]}"
        now = datetime.now(UTC).isoformat()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO adapter_requests (
                    request_id, payload_json, status, created_at, updated_at
                ) VALUES (?, ?, 'SUBMITTED', ?, ?)
                """,
                (request_id, _canonical_json(normalized), now, now),
            )
        return {
            "requestId": request_id,
            "status": "SUBMITTED",
            **normalized,
            "createdAt": now,
            "updatedAt": now,
        }

    def list_adapter_requests(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM adapter_requests ORDER BY created_at DESC"
            ).fetchall()
        return [
            {
                "requestId": row["request_id"],
                "status": row["status"],
                **json.loads(row["payload_json"]),
                "createdAt": row["created_at"],
                "updatedAt": row["updated_at"],
            }
            for row in rows
        ]

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> AgentRecord:
        definition = AgentDefinition.from_payload(json.loads(row["definition_json"]))
        return AgentRecord(
            definition=definition,
            revision=int(row["revision"]),
            card=json.loads(row["card_json"]),
            card_revision=str(row["card_revision"]),
            updated_at=str(row["updated_at"]),
        )
