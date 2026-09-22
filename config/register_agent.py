from __future__ import annotations

import argparse
import json
import os
import sys

from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen


DEFAULT_DEFINITION = Path(__file__).with_name("dongjian-agent.json")
DEFAULT_REGISTRY_URL = "http://127.0.0.1:4200"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Register or update one Agent in the local A2A Registry.",
    )
    parser.add_argument(
        "definition",
        nargs="?",
        type=Path,
        default=DEFAULT_DEFINITION,
        help=f"Agent Definition JSON (default: {DEFAULT_DEFINITION.name})",
    )
    parser.add_argument(
        "--registry-url",
        default=os.getenv("A2A_REGISTRY_URL", DEFAULT_REGISTRY_URL),
        help=f"Registry base URL (default: {DEFAULT_REGISTRY_URL})",
    )
    parser.add_argument(
        "--admin-token",
        default=os.getenv(
            "REGISTRY_ADMIN_TOKEN",
            os.getenv("A2A_REGISTRY_ADMIN_TOKEN", ""),
        ),
        help="Registry admin token; prefer the REGISTRY_ADMIN_TOKEN environment variable.",
    )
    parser.add_argument(
        "--update",
        action="store_true",
        help="Use PUT on the agent-specific endpoint instead of POST.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=30,
        help="HTTP timeout in seconds (default: 30).",
    )
    return parser.parse_args()


def load_definition(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"Agent Definition not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in {path}: {exc}") from exc

    if not isinstance(payload, dict):
        raise ValueError("Agent Definition must be a JSON object")
    agent_id = payload.get("agentId")
    if not isinstance(agent_id, str) or not agent_id.strip():
        raise ValueError("Agent Definition requires a non-empty agentId")
    return payload


def register_agent(
    payload: dict[str, Any],
    *,
    registry_url: str,
    admin_token: str,
    update: bool,
    timeout: float,
) -> dict[str, Any]:
    base_url = registry_url.rstrip("/")
    agent_id = str(payload["agentId"]).strip()
    if update:
        url = f"{base_url}/registry/v1/agents/{quote(agent_id, safe='')}"
        method = "PUT"
    else:
        url = f"{base_url}/registry/v1/agents"
        method = "POST"

    headers = {"Content-Type": "application/json"}
    if admin_token:
        headers["Authorization"] = f"Bearer {admin_token}"

    request = Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=headers,
        method=method,
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            result = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        details = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"Registry rejected {method} {url}: HTTP {exc.code}: {details}"
        ) from exc
    except URLError as exc:
        raise RuntimeError(
            f"Could not reach the Registry at {base_url}. "
            "Start it with 'npm run bridge' and try again. "
            f"Reason: {exc.reason}"
        ) from exc

    if not isinstance(result, dict):
        raise RuntimeError("Registry returned an invalid response")
    return result


def main() -> int:
    args = parse_args()
    try:
        payload = load_definition(args.definition.resolve())
        result = register_agent(
            payload,
            registry_url=args.registry_url,
            admin_token=args.admin_token,
            update=args.update,
            timeout=args.timeout,
        )
    except (ValueError, RuntimeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print(
        f"{'Updated' if args.update else 'Registered'} "
        f"agent '{result.get('agentId', payload['agentId'])}'."
    )
    print(f"Revision: {result.get('revision', 'unknown')}")
    print(f"Agent Card: {result.get('cardUrl', 'not returned')}")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
