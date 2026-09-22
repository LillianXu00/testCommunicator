import json
import threading

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from starlette.testclient import TestClient

from a2a_server.registry_client import (
    GatewayAgentDefinition,
    GatewayAgentRecord,
)
from a2a_server.queueing import TaskExecutionResult
from a2a_server.server import create_app


class FakeRegistryClient:
    def __init__(self, definition: GatewayAgentDefinition) -> None:
        self.definition = definition
        self.events: list[dict] = []

    async def get(self, agent_id: str) -> GatewayAgentRecord | None:
        if agent_id != self.definition.agent_id:
            return None
        return GatewayAgentRecord(self.definition, revision=1)

    async def report_task_event(self, **event) -> None:
        self.events.append(event)


class FakeDispatcher:
    def __init__(self) -> None:
        self.commands = []

    async def dispatch(self, command):
        self.commands.append(command)
        return TaskExecutionResult(
            message_id=command.message_id,
            task_id=command.task_id,
            agent_id=command.agent_id,
            state="COMPLETED",
            text="QUEUED_RESULT",
        )


def test_gateway_hot_loads_declarative_agent_from_registry() -> None:
    received: list[dict] = []

    class AgentHandler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("content-length", "0"))
            payload = json.loads(self.rfile.read(length) or b"{}")
            received.append({
                "payload": payload,
                "authorization": self.headers.get("authorization"),
            })
            body = json.dumps({
                "data": {
                    "answer": (
                        f"DECLARATIVE_RESULT: {payload['query']} "
                        f"[{payload['session_id']}]"
                    )
                }
            }).encode()
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, _format: str, *_args: object) -> None:
            return

    backend_server = ThreadingHTTPServer(("127.0.0.1", 0), AgentHandler)
    backend_thread = threading.Thread(
        target=backend_server.serve_forever,
        daemon=True,
    )
    backend_thread.start()
    definition = GatewayAgentDefinition(
        agent_id="dynamic-agent",
        integration_mode="managed-adapter",
        name="Dynamic Agent",
        backend={
            "adapterType": "declarative-http",
            "endpoint": (
                f"http://127.0.0.1:{backend_server.server_port}/invoke"
            ),
            "requestBody": {
                "query": "{{input}}",
                "session_id": "{{contextId}}",
            },
            "responseTextSelectors": ["$.data.answer"],
            "timeoutSeconds": 30,
            "authToken": "resolved-registry-token",
        },
    )
    registry = FakeRegistryClient(definition)
    try:
        with TestClient(
            create_app(
                registry_client=registry,  # type: ignore[arg-type]
            )
        ) as client:
            response = client.post(
                "/a2a/agents/dynamic-agent",
                headers={"a2a-version": "1.0"},
                json={
                    "jsonrpc": "2.0",
                    "id": "dynamic-send",
                    "method": "SendMessage",
                    "params": {
                        "message": {
                            "messageId": "dynamic-message",
                            "contextId": "dynamic-context",
                            "role": "ROLE_USER",
                            "parts": [{"text": "hello"}],
                        },
                    },
                },
            )
        assert response.status_code == 200
        task = response.json()["result"]["task"]
        assert task["status"]["state"] == "TASK_STATE_COMPLETED"
        assert task["artifacts"][0]["parts"][0]["text"] == (
            "DECLARATIVE_RESULT: hello [dynamic-context]"
        )
        assert received == [{
            "payload": {
                "query": "hello",
                "session_id": "dynamic-context",
            },
            "authorization": "Bearer resolved-registry-token",
        }]
        assert [event["state"] for event in registry.events] == [
            "TASK_STATE_SUBMITTED",
            "TASK_STATE_WORKING",
            "TASK_STATE_COMPLETED",
        ]
        assert [event["task_sequence"] for event in registry.events] == [
            1,
            2,
            3,
        ]
        assert len({event["event_id"] for event in registry.events}) == 3
        assert len({event["task_id"] for event in registry.events}) == 1
    finally:
        backend_server.shutdown()
        backend_server.server_close()


def test_gateway_dispatches_managed_execution_through_queue() -> None:
    definition = GatewayAgentDefinition(
        agent_id="queued-agent",
        integration_mode="managed-adapter",
        name="Queued Agent",
        backend={
            "adapterType": "declarative-http",
            "endpoint": "http://127.0.0.1:9999/invoke",
            "requestBody": {"input": "{{input}}"},
            "responseTextSelectors": ["$.output"],
        },
    )
    registry = FakeRegistryClient(definition)
    dispatcher = FakeDispatcher()

    with TestClient(
        create_app(
            registry_client=registry,  # type: ignore[arg-type]
            task_dispatcher=dispatcher,
        )
    ) as client:
        response = client.post(
            "/a2a/agents/queued-agent",
            headers={"a2a-version": "1.0"},
            json={
                "jsonrpc": "2.0",
                "id": "queued-send",
                "method": "SendMessage",
                "params": {
                    "message": {
                        "messageId": "queued-message",
                        "contextId": "queued-context",
                        "role": "ROLE_USER",
                        "parts": [{"text": "queued prompt"}],
                    },
                },
            },
        )

    assert response.status_code == 200
    task = response.json()["result"]["task"]
    assert task["status"]["state"] == "TASK_STATE_COMPLETED"
    assert task["artifacts"][0]["parts"][0]["text"] == "QUEUED_RESULT"
    assert len(dispatcher.commands) == 1
    command = dispatcher.commands[0]
    assert command.agent_id == "queued-agent"
    assert command.context_id == "queued-context"
    assert command.prompt == "queued prompt"
    assert command.message_id == f"execute:queued-agent:{command.task_id}"
