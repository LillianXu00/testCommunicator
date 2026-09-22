from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys
import time

from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen


ROOT = Path(__file__).resolve().parents[1]


def wait_for_registry(process: subprocess.Popen[bytes], url: str) -> None:
    for _ in range(60):
        if process.poll() is not None:
            raise RuntimeError(
                f"Registry exited with status {process.returncode}"
            )
        try:
            with urlopen(url.rstrip("/") + "/health", timeout=0.5):
                return
        except (OSError, URLError):
            time.sleep(0.1)
    raise RuntimeError("Registry did not become ready")


def stop(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()


def main() -> int:
    environment = os.environ.copy()
    service_token = (
        environment.get("REGISTRY_SERVICE_TOKEN")
        or environment.get("A2A_REGISTRY_SERVICE_TOKEN")
        or secrets.token_urlsafe(32)
    )
    environment["REGISTRY_SERVICE_TOKEN"] = service_token
    environment["A2A_REGISTRY_SERVICE_TOKEN"] = service_token
    if not environment.get("OPENCLAW_GATEWAY_TOKEN"):
        openclaw_config = Path.home() / ".openclaw" / "openclaw.json"
        if openclaw_config.exists():
            config = json.loads(openclaw_config.read_text(encoding="utf-8"))
            token = (
                config.get("gateway", {})
                .get("auth", {})
                .get("token", "")
            )
            if token:
                environment["OPENCLAW_GATEWAY_TOKEN"] = str(token)
    registry_url = environment.setdefault(
        "A2A_REGISTRY_URL",
        "http://127.0.0.1:4200",
    )
    environment.setdefault("REGISTRY_HOST", "127.0.0.1")
    environment.setdefault("REGISTRY_PORT", "4200")
    environment.setdefault("REGISTRY_PUBLIC_URL", registry_url)
    environment.setdefault(
        "A2A_GATEWAY_PUBLIC_URL",
        "http://127.0.0.1:4101",
    )
    environment.setdefault("A2A_HOST", "127.0.0.1")
    environment.setdefault("A2A_PORT", "4101")
    environment.setdefault(
        "A2A_PUBLIC_URL",
        environment["A2A_GATEWAY_PUBLIC_URL"],
    )

    registry = subprocess.Popen(
        [sys.executable, "-m", "registry_service.app"],
        cwd=ROOT / "registry-service",
        env=environment,
    )
    gateway: subprocess.Popen[bytes] | None = None
    worker: subprocess.Popen[bytes] | None = None
    skill_evolution: subprocess.Popen[bytes] | None = None
    try:
        wait_for_registry(registry, registry_url)
        if environment.get("A2A_BROKER_URL"):
            worker = subprocess.Popen(
                [sys.executable, "-m", "a2a_server.worker"],
                cwd=ROOT / "a2a-server",
                env=environment,
            )
        if environment.get("SKILL_EVOLUTION_ENABLED", "").strip() == "1":
            skill_evolution = subprocess.Popen(
                [sys.executable, "-m", "skill_evolution"],
                cwd=ROOT / "skill-evolution-service",
                env=environment,
            )
        gateway = subprocess.Popen(
            [sys.executable, "-m", "a2a_server.server"],
            cwd=ROOT / "a2a-server",
            env=environment,
        )
        while True:
            registry_status = registry.poll()
            gateway_status = gateway.poll()
            if registry_status is not None:
                return registry_status
            if gateway_status is not None:
                return gateway_status
            if worker is not None:
                worker_status = worker.poll()
                if worker_status is not None:
                    return worker_status
            if skill_evolution is not None:
                evolution_status = skill_evolution.poll()
                if evolution_status is not None:
                    return evolution_status
            time.sleep(0.25)
    except KeyboardInterrupt:
        return 130
    finally:
        if gateway is not None:
            stop(gateway)
        if worker is not None:
            stop(worker)
        if skill_evolution is not None:
            stop(skill_evolution)
        stop(registry)


if __name__ == "__main__":
    raise SystemExit(main())
