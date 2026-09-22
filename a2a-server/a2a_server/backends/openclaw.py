from __future__ import annotations

import asyncio
import os
from typing import Any

import httpx


def extract_output_text(response: dict[str, Any]) -> str:
    direct = response.get("output_text")
    if isinstance(direct, str) and direct.strip():
        return direct.strip()

    chunks: list[str] = []
    for item in response.get("output", []):
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        for content in item.get("content", []):
            if not isinstance(content, dict):
                continue
            text_value = content.get("text")
            if content.get("type") == "output_text" and isinstance(text_value, str):
                chunks.append(text_value)
    return "\n".join(chunks).strip()


class OpenClawResponsesBackend:
    def __init__(self, gateway_url: str, token: str, agent_id: str = "planner", timeout_seconds: float = 600) -> None:
        self.gateway_url = gateway_url.rstrip("/")
        self.token = token
        self.agent_id = agent_id
        self.timeout_seconds = timeout_seconds

    async def invoke(self, prompt: str, context_id: str) -> str:
        if os.getenv("A2A_MOCK") == "1":
            delay = float(os.getenv("A2A_MOCK_DELAY_SECONDS", "0"))
            if delay > 0:
                await asyncio.sleep(delay)
            return f"MOCK_PLANNER_RESULT: {prompt}"
        if not self.token:
            raise RuntimeError("OPENCLAW_GATEWAY_TOKEN is required")

        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.post(
                f"{self.gateway_url}/v1/responses",
                headers={"Authorization": f"Bearer {self.token}"},
                json={
                    "model": f"openclaw/{self.agent_id}",
                    "input": prompt,
                    "user": f"a2a:{context_id}",
                    "stream": False,
                },
            )
            response.raise_for_status()

        text_value = extract_output_text(response.json())
        if not text_value:
            raise RuntimeError("OpenClaw Responses returned no output text")
        return text_value
