import asyncio
import json
import os
import subprocess
import sys
import tempfile
import threading

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest

from a2a.client import A2ACardResolver, ClientConfig, create_client
from a2a.helpers import new_text_message
from a2a.types import Role, SendMessageRequest


@pytest.mark.asyncio
async def test_official_client_round_trip() -> None:
    notifications: list[dict] = []
    completed_notification = threading.Event()

    class CallbackHandler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("content-length", "0"))
            payload = json.loads(self.rfile.read(length) or b"{}")
            notifications.append(payload)
            state = payload.get("statusUpdate", {}).get("status", {}).get("state")
            if state == "TASK_STATE_COMPLETED":
                completed_notification.set()
            self.send_response(202)
            self.end_headers()

        def log_message(self, _format: str, *_args: object) -> None:
            return

    callback_server = ThreadingHTTPServer(("127.0.0.1", 0), CallbackHandler)
    callback_thread = threading.Thread(
        target=callback_server.serve_forever,
        daemon=True,
    )
    callback_thread.start()
    try:
        with tempfile.TemporaryDirectory() as temp_directory:
            registry_probe = ThreadingHTTPServer(("127.0.0.1", 0), CallbackHandler)
            registry_port = registry_probe.server_port
            registry_probe.server_close()
            gateway_probe = ThreadingHTTPServer(("127.0.0.1", 0), CallbackHandler)
            gateway_port = gateway_probe.server_port
            gateway_probe.server_close()
            registry_url = f"http://127.0.0.1:{registry_port}"
            gateway_url = f"http://127.0.0.1:{gateway_port}"
            env = os.environ.copy()
            env.update({
                "A2A_MOCK": "1",
                "A2A_MOCK_DELAY_SECONDS": "0.5",
                "A2A_PORT": str(gateway_port),
                "A2A_PUBLIC_URL": gateway_url,
                "A2A_GATEWAY_PUBLIC_URL": gateway_url,
                "A2A_REGISTRY_URL": registry_url,
                "REGISTRY_PORT": str(registry_port),
                "REGISTRY_PUBLIC_URL": registry_url,
                "REGISTRY_DB": os.path.join(temp_directory, "registry.db"),
            })
            creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            registry_process = subprocess.Popen(
                [sys.executable, "-m", "registry_service.app"],
                cwd=os.path.join(
                    os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
                    "registry-service",
                ),
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=creationflags,
            )
            gateway_process = subprocess.Popen(
                [sys.executable, "-m", "a2a_server.server"],
                cwd=os.path.dirname(os.path.dirname(__file__)),
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=creationflags,
            )
            try:
                async with httpx.AsyncClient(timeout=10) as http_client:
                    for _ in range(30):
                        try:
                            card = await A2ACardResolver(
                                http_client,
                                registry_url,
                            ).get_agent_card(
                                "registry/v1/agents/planner/card"
                            )
                            break
                        except httpx.HTTPError:
                            await asyncio.sleep(0.1)
                    else:
                        pytest.fail("A2A test server did not start")

                    assert not card.supported_interfaces[0].tenant
                    assert card.supported_interfaces[0].url.endswith(
                        "/a2a/agents/planner"
                    )
                    client = await create_client(
                        card,
                        ClientConfig(
                            streaming=False,
                            httpx_client=http_client,
                        ),
                    )
                    request = SendMessageRequest(
                        message=new_text_message(
                            "smoke",
                            role=Role.ROLE_USER,
                        )
                    )
                    events = [
                        event async for event in client.send_message(request)
                    ]

                    raw_response = await http_client.post(
                        f"{gateway_url}/a2a/agents/planner",
                        headers={"a2a-version": "1.0"},
                        json={
                            "jsonrpc": "2.0",
                            "id": "plugin-request",
                            "method": "SendMessage",
                            "params": {
                                "message": {
                                    "messageId": "plugin-message",
                                    "contextId": "plugin-context",
                                    "role": "ROLE_USER",
                                    "parts": [{"text": "plugin smoke"}],
                                },
                                "configuration": {
                                    "acceptedOutputModes": ["text/plain"],
                                    "returnImmediately": True,
                                },
                            },
                        },
                    )
                    raw_response.raise_for_status()
                    raw_task = raw_response.json()["result"]["task"]
                    assert raw_task["status"]["state"] in {
                        "TASK_STATE_SUBMITTED",
                        "TASK_STATE_WORKING",
                    }

                    push_response = await http_client.post(
                        f"{gateway_url}/a2a/agents/planner",
                        headers={"a2a-version": "1.0"},
                        json={
                            "jsonrpc": "2.0",
                            "id": "push-config",
                            "method": "CreateTaskPushNotificationConfig",
                            "params": {
                                "taskId": raw_task["id"],
                                "url": (
                                    f"http://127.0.0.1:"
                                    f"{callback_server.server_port}/callback"
                                ),
                                "token": "test-token",
                            },
                        },
                    )
                    push_response.raise_for_status()
                    assert (
                        push_response.json()["result"]["taskId"]
                        == raw_task["id"]
                    )

                    for attempt in range(20):
                        get_response = await http_client.post(
                            f"{gateway_url}/a2a/agents/planner",
                            headers={"a2a-version": "1.0"},
                            json={
                                "jsonrpc": "2.0",
                                "id": f"get-{attempt}",
                                "method": "GetTask",
                                "params": {
                                    "id": raw_task["id"],
                                },
                            },
                        )
                        get_response.raise_for_status()
                        raw_task = get_response.json()["result"]
                        if (
                            raw_task["status"]["state"]
                            == "TASK_STATE_COMPLETED"
                        ):
                            break
                        await asyncio.sleep(0.1)
                    else:
                        pytest.fail("Asynchronous A2A task did not complete")
                    await client.close()

                assert events
                assert raw_task["status"]["state"] == "TASK_STATE_COMPLETED"
                assert raw_task["artifacts"][0]["parts"][0]["text"].startswith(
                    "MOCK_PLANNER_RESULT"
                )
                assert await asyncio.to_thread(
                    completed_notification.wait,
                    3,
                )
                assert any(
                    item.get("statusUpdate", {}).get("status", {}).get("state")
                    == "TASK_STATE_COMPLETED"
                    for item in notifications
                )
            finally:
                for process in (gateway_process, registry_process):
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
    finally:
        callback_server.shutdown()
        callback_server.server_close()
