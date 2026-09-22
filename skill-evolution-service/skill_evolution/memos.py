from __future__ import annotations

import json
import re

from datetime import datetime
from typing import Any

import httpx

from skill_evolution.models import MemoRecord, parse_timestamp


TAG_PATTERN = re.compile(r"(?<!\w)#([\w-]+)", re.UNICODE)


class MemosClient:
    """MemOS Cloud search client used as Skill evolution evidence input."""

    def __init__(
        self,
        base_url: str,
        token: str,
        user_id: str,
        *,
        timeout_seconds: float = 30,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.user_id = user_id
        self.timeout_seconds = timeout_seconds
        self.transport = transport

    async def list_since(
        self,
        *,
        checkpoint: str = "",
        evidence_query: str,
        maximum_records: int = 50,
    ) -> list[MemoRecord]:
        del checkpoint  # MemOS Cloud search has no chronological cursor API.
        if not self.token:
            raise RuntimeError("MEMOS_API_KEY or MEMOS_TOKEN is required")
        if not self.user_id:
            raise RuntimeError("MEMOS_USER_ID is required")
        query = evidence_query.strip()
        if not query:
            raise RuntimeError("taskProfile.evidenceQuery is required")
        async with httpx.AsyncClient(
            timeout=self.timeout_seconds,
            transport=self.transport,
        ) as client:
            response = await client.post(
                f"{self.base_url}/search/memory",
                headers={
                    "Authorization": f"Token {self.token}",
                    "Content-Type": "application/json",
                },
                json={
                    "user_id": self.user_id,
                    "query": query,
                    "memory_limit_number": maximum_records,
                    "include_preference": False,
                    "include_tool_memory": False,
                    "include_skill": False,
                    "source": "A2A_SKILL_EVOLUTION",
                },
            )
            response.raise_for_status()
        payload = response.json()
        data = _cloud_data(payload, operation="search/memory")
        raw_memories = data.get("memory_detail_list", [])
        if not isinstance(raw_memories, list):
            raise RuntimeError(
                "MemOS Cloud search response has no memory_detail_list array"
            )
        records: list[MemoRecord] = []
        for raw in raw_memories:
            if not isinstance(raw, dict):
                continue
            record = self._record(raw)
            if not record.content.strip():
                continue
            records.append(record)
        records.sort(key=lambda item: (parse_timestamp(item.update_time), item.name))
        return records[:maximum_records]

    @staticmethod
    def _record(payload: dict[str, Any]) -> MemoRecord:
        content = str(
            payload.get("memory_value")
            or payload.get("content")
            or payload.get("memory")
            or ""
        )
        raw_tags = payload.get("tags", [])
        tags = {
            match.group(1).lower()
            for match in TAG_PATTERN.finditer(content)
        }
        if isinstance(raw_tags, list):
            for raw_tag in raw_tags:
                if isinstance(raw_tag, dict):
                    value = raw_tag.get("name") or raw_tag.get("tag") or ""
                else:
                    value = raw_tag
                normalized = str(value).strip().lstrip("#").lower()
                if normalized:
                    tags.add(normalized)
        create_time = str(
            payload.get("create_time")
            or payload.get("createTime")
            or payload.get("created_at")
            or ""
        )
        update_time = str(
            payload.get("update_time")
            or payload.get("updateTime")
            or payload.get("updated_at")
            or create_time
        )
        return MemoRecord(
            name=str(
                payload.get("memory_key")
                or payload.get("name")
                or payload.get("id")
                or payload.get("conversation_id")
                or ""
            ),
            content=content,
            creator=str(
                payload.get("user_id")
                or payload.get("creator")
                or ""
            ),
            create_time=create_time,
            update_time=update_time,
            tags=tuple(sorted(tags)),
        )


def _cloud_data(payload: Any, *, operation: str) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise RuntimeError(f"MemOS Cloud {operation} response must be an object")
    code = payload.get("code")
    if code not in (None, 0, "0"):
        raise RuntimeError(
            f"MemOS Cloud {operation} failed code={code}: "
            f"{payload.get('message', '')}"
        )
    data = payload.get("data", {})
    if not isinstance(data, dict):
        raise RuntimeError(f"MemOS Cloud {operation} data must be an object")
    return data


def latest_checkpoint(records: list[MemoRecord]) -> str:
    """Retained for store compatibility; Cloud search dedupes by evidence hash."""
    if not records:
        return ""
    latest: tuple[datetime, str, str] = max(
        (parse_timestamp(record.update_time), record.name, record.update_time)
        for record in records
    )
    return json.dumps(
        {"updateTime": latest[2], "name": latest[1]},
        ensure_ascii=True,
        separators=(",", ":"),
    )


def parse_checkpoint(value: str) -> tuple[datetime, str]:
    text = str(value or "").strip()
    if not text:
        return parse_timestamp(""), ""
    if text.startswith("{"):
        payload = json.loads(text)
        if isinstance(payload, dict):
            return (
                parse_timestamp(str(payload.get("updateTime", ""))),
                str(payload.get("name", "")),
            )
    return parse_timestamp(text), ""
