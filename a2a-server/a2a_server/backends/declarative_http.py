from __future__ import annotations

import os
import re

from typing import Any

import httpx


_ARRAY_SEGMENT = re.compile(r"^([^\[]+)?\[(\d+)\]$")


def render_request_template(
    value: Any,
    *,
    prompt: str,
    context_id: str,
) -> Any:
    if isinstance(value, dict):
        return {
            key: render_request_template(
                item,
                prompt=prompt,
                context_id=context_id,
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [
            render_request_template(
                item,
                prompt=prompt,
                context_id=context_id,
            )
            for item in value
        ]
    if not isinstance(value, str):
        return value
    return (
        value.replace("{{input}}", prompt)
        .replace("{{contextId}}", context_id)
    )


def select_json_value(payload: Any, selector: str) -> Any:
    if selector == "$":
        return payload
    if not selector.startswith("$."):
        raise ValueError(f"unsupported JSON selector: {selector}")
    current = payload
    for segment in selector[2:].split("."):
        match = _ARRAY_SEGMENT.fullmatch(segment)
        if match:
            key, raw_index = match.groups()
            if key:
                if not isinstance(current, dict):
                    return None
                current = current.get(key)
            if not isinstance(current, list):
                return None
            index = int(raw_index)
            if index >= len(current):
                return None
            current = current[index]
            continue
        if not isinstance(current, dict):
            return None
        current = current.get(segment)
    return current


class DeclarativeHttpBackend:
    """Executes a validated synchronous JSON mapping without loading code."""

    def __init__(
        self,
        endpoint: str,
        *,
        request_body: dict[str, Any],
        response_text_selectors: list[str],
        token: str | None = None,
        auth_env: str | None = None,
        timeout_seconds: float = 600,
    ) -> None:
        self.endpoint = endpoint
        self.request_body = request_body
        self.response_text_selectors = response_text_selectors
        self.token = token
        self.auth_env = auth_env
        self.timeout_seconds = timeout_seconds

    async def invoke(self, prompt: str, context_id: str) -> str:
        headers: dict[str, str] = {}
        token = self.token
        if not token and self.auth_env:
            token = os.getenv(self.auth_env, "")
            if not token:
                raise RuntimeError(f"{self.auth_env} is required")
        if token:
            headers["Authorization"] = f"Bearer {token}"
        request_body = render_request_template(
            self.request_body,
            prompt=prompt,
            context_id=context_id,
        )
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.post(
                self.endpoint,
                headers=headers,
                json=request_body,
            )
            response.raise_for_status()
        payload = response.json()
        for selector in self.response_text_selectors:
            value = select_json_value(payload, selector)
            if isinstance(value, str) and value.strip():
                return value.strip()
        raise RuntimeError(
            "Declarative HTTP agent returned no text for configured selectors"
        )
