import asyncio
import json
import os

import httpx

from a2a.client import ClientConfig, create_client
from a2a.client.card_resolver import parse_agent_card
from a2a.helpers import new_text_message
from a2a.types import Role, SendMessageRequest
from google.protobuf.json_format import MessageToDict


async def main() -> None:
    registry_url = os.getenv(
        "A2A_REGISTRY_URL",
        "http://127.0.0.1:4200",
    ).rstrip("/")
    async with httpx.AsyncClient(timeout=30) as http_client:
        catalog_response = await http_client.get(
            f"{registry_url}/registry/v1/agent-cards"
        )
        catalog_response.raise_for_status()
        catalog = catalog_response.json()
        planner_entry = next(
            item for item in catalog["agents"] if item["agentId"] == "planner"
        )
        card = parse_agent_card(planner_entry["agentCard"])
        client = await create_client(
            card,
            ClientConfig(streaming=False, httpx_client=http_client),
        )
        message = new_text_message(
            "A2A registry and per-agent gateway routing smoke test",
            role=Role.ROLE_USER,
        )
        request = SendMessageRequest(message=message)
        async for event in client.send_message(request):
            print(json.dumps(MessageToDict(event), ensure_ascii=True, indent=2))
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
