from __future__ import annotations

import argparse
import json
import os
import re
import sys

from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


ENV_FILE = Path(__file__).resolve().parents[1] / ".env"
ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def load_env_file(path: Path) -> None:
    if not path.is_file():
        raise ValueError(f"environment file does not exist: {path}")
    for line_number, raw_line in enumerate(
        path.read_text(encoding="utf-8-sig").splitlines(),
        start=1,
    ):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            raise ValueError(
                f"invalid environment entry at {path}:{line_number}"
            )
        name, value = line.split("=", 1)
        name = name.strip()
        value = value.strip()
        if not ENV_NAME.fullmatch(name):
            raise ValueError(
                f"invalid environment name at {path}:{line_number}"
            )
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        # Explicit process variables take precedence over the local file.
        os.environ.setdefault(name, value)


def load_manifest(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("manifest must be a JSON object")
    for field in ("agentId", "name", "description", "skills"):
        if not payload.get(field):
            raise ValueError(f"{field} is required")
    if not isinstance(payload["skills"], list) or not payload["skills"]:
        raise ValueError("skills must contain at least one exposed capability")
    if not (
        payload.get("sourceAgentCardUrl")
        or isinstance(payload.get("interface"), dict)
    ):
        raise ValueError(
            "sourceAgentCardUrl or an interface object is required"
        )
    return payload


def attach_agent_token(
    manifest: dict[str, Any],
    token: str,
) -> dict[str, Any]:
    payload = json.loads(json.dumps(manifest))
    if not token:
        return payload
    interface = payload.get("interface")
    if not isinstance(interface, dict):
        raise ValueError(
            "AGENT_ENDPOINT_TOKEN can only be used with an interface object"
        )
    interface.pop("authEnv", None)
    interface.pop("authToken", None)
    interface["authentication"] = {
        "type": "bearer",
        "token": token,
    }
    return payload


def redact_credentials(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: (
                "[WRITE-ONLY]"
                if key.lower() in {"token", "authtoken", "password", "secret"}
                else redact_credentials(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_credentials(item) for item in value]
    return value


def register(
    manifest: dict[str, Any],
    *,
    registry_url: str,
    token: str,
    timeout_seconds: float,
) -> dict[str, Any]:
    body = json.dumps(manifest, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = Request(
        registry_url.rstrip("/") + "/onboarding/v1/register",
        data=body,
        headers=headers,
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"Registry rejected registration ({exc.code}): {detail}"
        ) from exc
    except URLError as exc:
        raise RuntimeError(f"Registry is unavailable: {exc.reason}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("Registry returned an invalid response")
    return payload


def main() -> int:
    env_parser = argparse.ArgumentParser(add_help=False)
    env_parser.add_argument("--env-file", type=Path, default=ENV_FILE)
    env_args, _ = env_parser.parse_known_args()
    try:
        load_env_file(env_args.env_file)
    except (OSError, ValueError) as exc:
        print(f"registration failed: {exc}", file=sys.stderr)
        return 1

    parser = argparse.ArgumentParser(
        description="Register or synchronize an agent with the A2A Registry."
    )
    parser.add_argument("manifest", type=Path)
    parser.add_argument(
        "--env-file",
        type=Path,
        default=env_args.env_file,
        help=f"Environment file (default: {ENV_FILE})",
    )
    parser.add_argument(
        "--registry-url",
        default=os.getenv(
            "A2A_REGISTRY_URL",
            "http://127.0.0.1:4200",
        ),
    )
    parser.add_argument(
        "--token",
        default=os.getenv("REGISTRY_REGISTRATION_TOKEN", ""),
    )
    parser.add_argument(
        "--agent-token",
        default=os.getenv("AGENT_ENDPOINT_TOKEN", ""),
        help=(
            "Write-only bearer token for the registered Agent endpoint. "
            "Prefer the AGENT_ENDPOINT_TOKEN environment variable."
        ),
    )
    parser.add_argument("--timeout", type=float, default=60)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate and print the manifest without sending it.",
    )
    args = parser.parse_args()

    try:
        manifest = attach_agent_token(
            load_manifest(args.manifest),
            args.agent_token,
        )
        if args.dry_run:
            print(json.dumps(
                redact_credentials(manifest),
                ensure_ascii=False,
                indent=2,
            ))
            return 0
        result = register(
            manifest,
            registry_url=args.registry_url,
            token=args.token,
            timeout_seconds=args.timeout,
        )
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"registration failed: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("status") == "ACTIVE" else 2


if __name__ == "__main__":
    raise SystemExit(main())
