import json
import re
import sqlite3
import threading

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from starlette.testclient import TestClient

from registry_service.app import create_app


def test_registry_exposes_bootstrapped_planner_card(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("A2A_MOCK", "1")
    with TestClient(create_app(registry_path=tmp_path / "registry.db")) as client:
        catalog_response = client.get("/registry/v1/agent-cards")
        card_response = client.get("/registry/v1/agents/planner/card")

    assert catalog_response.status_code == 200
    catalog = catalog_response.json()
    assert catalog["total"] == 1
    assert catalog["agents"][0]["agentId"] == "planner"
    assert catalog["agents"][0]["cardUrl"].endswith(
        "/registry/v1/agents/planner/card"
    )

    assert card_response.status_code == 200
    card = card_response.json()
    assert card["name"] == "OpenClaw Planner"
    assert (
        card["supportedInterfaces"][0]["url"]
        == "http://127.0.0.1:4101/a2a/agents/planner"
    )
    assert "tenant" not in card["supportedInterfaces"][0]
    assert card["supportedInterfaces"][0]["protocolVersion"] == "1.0"
    assert card["capabilities"]["pushNotifications"] is True
    assert card["skills"][0]["id"] == "forward-report"


def test_registry_dynamically_registers_a_second_agent(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("A2A_MOCK", "1")
    payload = {
        "agentId": "legal-agent",
        "name": "Legal Agent",
        "description": "Reviews contracts.",
        "version": "1.0.0",
        "skills": [{
            "id": "contract-review",
            "name": "Contract review",
            "description": "Reviews contract terms.",
        }],
        "backend": {
            "adapterType": "declarative-http",
            "endpoint": "http://127.0.0.1:9999/invoke",
            "requestBody": {
                "input": "{{input}}",
                "contextId": "{{contextId}}",
            },
            "responseTextSelectors": ["$.output"],
        },
    }
    database_path = tmp_path / "registry.db"
    with TestClient(create_app(registry_path=database_path)) as client:
        registration = client.post("/registry/v1/agents", json=payload)
        catalog = client.get("/registry/v1/agent-cards").json()
        payload["description"] = "Reviews contracts and legal policies."
        update = client.put("/registry/v1/agents/legal-agent", json=payload)

    assert registration.status_code == 200
    assert registration.json()["integrationMode"] == "managed-adapter"
    assert registration.json()["agentCard"]["supportedInterfaces"][0]["url"].endswith(
        "/a2a/agents/legal-agent"
    )
    assert update.status_code == 200
    assert update.json()["revision"] == 2
    assert {item["agentId"] for item in catalog["agents"]} == {
        "planner",
        "legal-agent",
    }

    # Registry records survive application restarts.
    with TestClient(create_app(registry_path=database_path)) as client:
        persisted = client.get("/registry/v1/agents/legal-agent/card")
    assert persisted.status_code == 200
    assert persisted.json()["skills"][0]["id"] == "contract-review"
    assert persisted.json()["description"] == "Reviews contracts and legal policies."


def test_registry_migrates_legacy_generic_http_records(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("A2A_MOCK", "1")
    database_path = tmp_path / "registry.db"
    payload = {
        "agentId": "legacy-agent",
        "name": "Legacy Agent",
        "description": "A record created before Generic HTTP was removed.",
        "skills": [{
            "id": "answer",
            "name": "Answer",
            "description": "Answers one request.",
        }],
        "backend": {
            "adapterType": "declarative-http",
            "endpoint": "http://127.0.0.1:9999/invoke",
            "requestBody": {"input": "{{input}}"},
            "responseTextSelectors": ["$.output"],
        },
    }
    with TestClient(create_app(registry_path=database_path)) as client:
        registered = client.post("/registry/v1/agents", json=payload)
    assert registered.status_code == 200

    with sqlite3.connect(database_path) as connection:
        row = connection.execute(
            "SELECT definition_json FROM agents WHERE agent_id = ?",
            ("legacy-agent",),
        ).fetchone()
        legacy = json.loads(row[0])
        legacy["backend"]["adapterType"] = "generic-http"
        legacy["backend"].pop("requestBody")
        legacy["backend"].pop("responseTextSelectors")
        connection.execute(
            "UPDATE agents SET definition_json = ? WHERE agent_id = ?",
            (json.dumps(legacy), "legacy-agent"),
        )

    with TestClient(create_app(registry_path=database_path)) as client:
        migrated = client.get("/registry/v1/admin/agents/legacy-agent")

    assert migrated.status_code == 200
    backend = migrated.json()["definition"]["backend"]
    assert backend["adapterType"] == "declarative-http"
    assert backend["requestBody"] == {
        "input": "{{input}}",
        "contextId": "{{contextId}}",
    }
    assert backend["responseTextSelectors"] == [
        "$",
        "$.output",
        "$.outputText",
        "$.output_text",
        "$.text",
    ]


def test_registry_encrypts_unique_agent_tokens_and_only_resolves_them_internally(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("A2A_MOCK", "1")
    monkeypatch.setenv("REGISTRY_SERVICE_TOKEN", "gateway-service-token")
    database_path = tmp_path / "registry.db"

    def payload(agent_id: str, token: str) -> dict:
        return {
            "agentId": agent_id,
            "name": agent_id,
            "description": f"Managed agent {agent_id}.",
            "version": "1.0.0",
            "skills": [{
                "id": "answer",
                "name": "Answer",
                "description": "Answers one test question.",
            }],
            "backend": {
                "adapterType": "declarative-http",
                "endpoint": f"http://127.0.0.1:9999/{agent_id}",
                "requestBody": {"input": "{{input}}"},
                "responseTextSelectors": ["$.output"],
                "authToken": token,
            },
        }

    with TestClient(create_app(registry_path=database_path)) as client:
        first = client.post(
            "/registry/v1/agents",
            json=payload("secret-agent-a", "token-for-agent-a"),
        )
        second = client.post(
            "/registry/v1/agents",
            json=payload("secret-agent-b", "token-for-agent-b"),
        )
        admin = client.get(
            "/registry/v1/admin/agents",
        ).json()["agents"]
        internal_a = client.get(
            "/registry/v1/internal/agents/secret-agent-a",
            headers={"Authorization": "Bearer gateway-service-token"},
        )
        unauthorized = client.get(
            "/registry/v1/internal/agents/secret-agent-a",
        )

    assert first.status_code == 200
    assert second.status_code == 200
    definitions = {
        item["agentId"]: item["definition"]
        for item in admin
        if item["agentId"].startswith("secret-agent-")
    }
    backend_a = definitions["secret-agent-a"]["backend"]
    backend_b = definitions["secret-agent-b"]["backend"]
    assert backend_a["secretRef"] == "agent:secret-agent-a:bearer"
    assert backend_b["secretRef"] == "agent:secret-agent-b:bearer"
    assert backend_a["secretRef"] != backend_b["secretRef"]
    assert "authToken" not in backend_a
    assert "authToken" not in backend_b
    assert unauthorized.status_code == 401
    assert internal_a.status_code == 200
    assert (
        internal_a.json()["definition"]["backend"]["authToken"]
        == "token-for-agent-a"
    )

    with sqlite3.connect(database_path) as connection:
        definitions_json = "\n".join(
            row[0] for row in connection.execute(
                "SELECT definition_json FROM agents"
            ).fetchall()
        )
        ciphertexts = [
            bytes(row[0])
            for row in connection.execute(
                "SELECT ciphertext FROM agent_secrets"
            ).fetchall()
        ]
    assert "token-for-agent-a" not in definitions_json
    assert "token-for-agent-b" not in definitions_json
    assert len(ciphertexts) == 2
    assert all(b"token-for-agent-" not in value for value in ciphertexts)

    # The generated key file makes encrypted credentials survive a restart.
    with TestClient(create_app(registry_path=database_path)) as client:
        persisted = client.get(
            "/registry/v1/internal/agents/secret-agent-b",
            headers={"Authorization": "Bearer gateway-service-token"},
        )
    assert (
        persisted.json()["definition"]["backend"]["authToken"]
        == "token-for-agent-b"
    )


def test_registry_imports_native_a2a_card_and_deletes_agent(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("A2A_MOCK", "1")
    remote_card = {
        "name": "External Writer",
        "description": "Writes content through its provider-managed A2A server.",
        "version": "2.1.0",
        "supportedInterfaces": [{
            "url": "http://writer.example.test/a2a",
            "protocolBinding": "JSONRPC",
            "protocolVersion": "1.0",
        }],
        "capabilities": {
            "streaming": False,
            "pushNotifications": False,
        },
        "defaultInputModes": ["text/plain"],
        "defaultOutputModes": ["text/plain"],
        "skills": [{
            "id": "write",
            "name": "Write",
            "description": "Writes a structured article.",
            "inputModes": ["text/plain"],
            "outputModes": ["text/plain"],
        }],
    }

    class CardHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            body = json.dumps(remote_card).encode()
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, _format: str, *_args: object) -> None:
            return

    card_server = ThreadingHTTPServer(("127.0.0.1", 0), CardHandler)
    card_thread = threading.Thread(target=card_server.serve_forever, daemon=True)
    card_thread.start()
    try:
        card_url = (
            f"http://127.0.0.1:{card_server.server_port}"
            "/.well-known/agent-card.json"
        )
        with TestClient(create_app(registry_path=tmp_path / "registry.db")) as client:
            inspected = client.post(
                "/registry/v1/agent-cards/inspect",
                json={"url": card_url},
            )
            registered = client.post(
                "/registry/v1/agents",
                json={
                    "agentId": "external-writer",
                    "integrationMode": "native-a2a",
                    "sourceAgentCardUrl": card_url,
                    "status": "ACTIVE",
                },
            )
            admin = client.get(
                "/registry/v1/admin/agents/external-writer"
            )
            deleted = client.delete(
                "/registry/v1/agents/external-writer"
            )
            missing = client.get(
                "/registry/v1/agents/external-writer/card"
            )

        assert inspected.status_code == 200
        assert inspected.json()["agentCard"]["name"] == "External Writer"
        assert registered.status_code == 200
        assert registered.json()["integrationMode"] == "native-a2a"
        assert (
            registered.json()["agentCard"]["supportedInterfaces"][0]["url"]
            == "http://writer.example.test/a2a"
        )
        assert admin.json()["definition"]["sourceAgentCardUrl"] == card_url
        assert "backend" not in admin.json()["definition"]
        assert deleted.json() == {"deleted": True}
        assert missing.status_code == 404
    finally:
        card_server.shutdown()
        card_server.server_close()


def test_registry_ui_and_admin_catalog_are_separated(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("A2A_MOCK", "1")
    monkeypatch.setenv("A2A_REGISTRY_ADMIN_TOKEN", "test-admin-token")
    authorization = {"Authorization": "Bearer test-admin-token"}

    with TestClient(create_app(registry_path=tmp_path / "registry.db")) as client:
        page = client.get("/registry-ui/")
        unauthorized = client.get("/registry/v1/admin/agents")
        registration = client.post(
            "/registry/v1/agents",
            headers=authorization,
            json={
                "agentId": "disabled-agent",
                "name": "Disabled Agent",
                "description": "A disabled test agent.",
                "skills": [{
                    "id": "disabled-skill",
                    "name": "Disabled skill",
                    "description": "A capability hidden from discovery.",
                }],
                "backend": {
                    "adapterType": "declarative-http",
                    "endpoint": "http://127.0.0.1:9999/invoke",
                    "requestBody": {"input": "{{input}}"},
                    "responseTextSelectors": ["$.output"],
                },
                "status": "DISABLED",
            },
        )
        admin_catalog = client.get(
            "/registry/v1/admin/agents",
            headers=authorization,
        ).json()
        public_catalog = client.get("/registry/v1/agent-cards").json()

    assert page.status_code == 200
    assert "Agent Center" in page.text
    asset_path = re.search(r'src="(/registry-ui/assets/[^"]+\.js)"', page.text)
    assert asset_path is not None
    assert client.get(asset_path.group(1)).status_code == 200
    assert unauthorized.status_code == 401
    assert registration.status_code == 200
    assert {agent["agentId"] for agent in admin_catalog["agents"]} == {
        "planner",
        "disabled-agent",
    }
    disabled = next(
        agent
        for agent in admin_catalog["agents"]
        if agent["agentId"] == "disabled-agent"
    )
    assert disabled["definition"]["backend"]["adapterType"] == (
        "declarative-http"
    )
    assert {agent["agentId"] for agent in public_catalog["agents"]} == {
        "planner",
    }


def test_runtime_status_reports_online_and_disabled_agents(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("A2A_MOCK", "1")

    class HealthHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            self.send_response(204)
            self.end_headers()

        def log_message(self, _format: str, *_args: object) -> None:
            return

    backend = ThreadingHTTPServer(("127.0.0.1", 0), HealthHandler)
    thread = threading.Thread(target=backend.serve_forever, daemon=True)
    thread.start()
    try:
        def definition(agent_id: str, status: str) -> dict:
            return {
                "agentId": agent_id,
                "name": agent_id,
                "description": "Status monitor test agent.",
                "skills": [{
                    "id": "ping",
                    "name": "Ping",
                    "description": "Checks connectivity.",
                }],
                "status": status,
                "backend": {
                    "adapterType": "declarative-http",
                    "endpoint": (
                        f"http://127.0.0.1:{backend.server_port}/invoke"
                    ),
                    "requestBody": {"input": "{{input}}"},
                    "responseTextSelectors": ["$.output"],
                },
            }

        with TestClient(create_app(registry_path=tmp_path / "registry.db")) as client:
            client.post(
                "/registry/v1/agents",
                json=definition("online-agent", "ACTIVE"),
            )
            client.post(
                "/registry/v1/agents",
                json=definition("disabled-agent", "DISABLED"),
            )
            response = client.post("/registry/v1/admin/agent-status/refresh")

        assert response.status_code == 200
        statuses = {
            item["agentId"]: item for item in response.json()["statuses"]
        }
        assert statuses["online-agent"]["availability"] == "ONLINE"
        assert statuses["online-agent"]["latencyMs"] >= 1
        assert statuses["disabled-agent"]["availability"] == "DISABLED"
    finally:
        backend.shutdown()
        backend.server_close()


def test_task_events_drive_concurrent_agent_activity(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("A2A_MOCK", "1")
    monkeypatch.setenv("REGISTRY_SERVICE_TOKEN", "event-token")
    headers = {"Authorization": "Bearer event-token"}

    def event(event_id: str, task_id: str, state: str) -> dict:
        return {
            "eventId": event_id,
            "agentId": "planner",
            "taskId": task_id,
            "contextId": f"context-{task_id}",
            "state": state,
            "message": f"{task_id}: {state}",
            "source": "test-gateway",
        }

    with TestClient(create_app(registry_path=tmp_path / "registry.db")) as client:
        unauthorized = client.post(
            "/registry/v1/internal/task-events",
            json=event("unauthorized", "task-a", "TASK_STATE_WORKING"),
        )
        for payload in [
            event("a-submitted", "task-a", "TASK_STATE_SUBMITTED"),
            event("a-working", "task-a", "TASK_STATE_WORKING"),
            event("b-submitted", "task-b", "TASK_STATE_SUBMITTED"),
            event("b-working", "task-b", "TASK_STATE_WORKING"),
        ]:
            assert client.post(
                "/registry/v1/internal/task-events",
                headers=headers,
                json=payload,
            ).status_code == 201

        busy = client.get("/registry/v1/admin/agent-status").json()
        planner = next(
            item for item in busy["statuses"] if item["agentId"] == "planner"
        )
        assert planner["activity"] == "WORKING"
        assert planner["activeTaskCount"] == 2
        assert planner["workingTaskCount"] == 2

        client.post(
            "/registry/v1/internal/task-events",
            headers=headers,
            json=event("a-completed", "task-a", "TASK_STATE_COMPLETED"),
        )
        one_running = client.get(
            "/registry/v1/admin/agents/planner/tasks"
        ).json()
        assert one_running["activity"] == "WORKING"
        assert one_running["activeTaskCount"] == 1

        completed = event(
            "b-completed", "task-b", "TASK_STATE_COMPLETED"
        )
        assert client.post(
            "/registry/v1/internal/task-events",
            headers=headers,
            json=completed,
        ).status_code == 201
        duplicate = client.post(
            "/registry/v1/internal/task-events",
            headers=headers,
            json=completed,
        )
        idle = client.get("/registry/v1/admin/agents/planner/tasks").json()
        timeline = client.get(
            "/registry/v1/admin/agents/planner/tasks/task-a/events"
        ).json()

    assert unauthorized.status_code == 401
    assert duplicate.status_code == 200
    assert duplicate.json()["duplicate"] is True
    assert idle["activity"] == "IDLE"
    assert idle["activeTaskCount"] == 0
    assert [item["state"] for item in timeline["events"]] == [
        "TASK_STATE_SUBMITTED",
        "TASK_STATE_WORKING",
        "TASK_STATE_COMPLETED",
    ]


def test_queue_execution_claim_is_idempotent(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("A2A_MOCK", "1")
    monkeypatch.setenv("REGISTRY_SERVICE_TOKEN", "worker-token")
    headers = {"Authorization": "Bearer worker-token"}
    claim = {
        "agentId": "planner",
        "taskId": "queued-task",
        "messageId": "execute:planner:queued-task",
        "contextId": "queued-context",
        "workerId": "worker-a",
        "leaseSeconds": 60,
    }

    with TestClient(create_app(registry_path=tmp_path / "registry.db")) as client:
        first = client.post(
            "/registry/v1/internal/task-executions/claim",
            headers=headers,
            json=claim,
        )
        busy = client.post(
            "/registry/v1/internal/task-executions/claim",
            headers=headers,
            json={**claim, "workerId": "worker-b"},
        )
        completed = client.post(
            "/registry/v1/internal/task-executions/complete",
            headers=headers,
            json={
                "agentId": "planner",
                "taskId": "queued-task",
                "messageId": "execute:planner:queued-task",
                "claimToken": first.json()["claimToken"],
                "state": "COMPLETED",
                "text": "queued result",
            },
        )
        replay = client.post(
            "/registry/v1/internal/task-executions/claim",
            headers=headers,
            json={**claim, "workerId": "worker-c"},
        )

    assert first.status_code == 200
    assert first.json()["decision"] == "EXECUTE"
    assert busy.json()["decision"] == "BUSY"
    assert completed.json()["state"] == "COMPLETED"
    assert completed.json()["duplicate"] is False
    assert replay.json()["decision"] == "REPLAY"
    assert replay.json()["text"] == "queued result"


def test_task_experience_claim_is_idempotent_and_retryable(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("A2A_MOCK", "1")
    monkeypatch.setenv("REGISTRY_SERVICE_TOKEN", "worker-token")
    headers = {"Authorization": "Bearer worker-token"}
    execution_claim = {
        "agentId": "planner",
        "taskId": "experience-task",
        "messageId": "execute:planner:experience-task",
        "contextId": "experience-context",
        "workerId": "task-worker",
        "leaseSeconds": 60,
    }
    experience_claim = {
        "agentId": "planner",
        "taskId": "experience-task",
        "messageId": "experience:planner:experience-task",
        "workerId": "experience-worker",
        "leaseSeconds": 60,
    }

    with TestClient(create_app(registry_path=tmp_path / "registry.db")) as client:
        task_claim = client.post(
            "/registry/v1/internal/task-executions/claim",
            headers=headers,
            json=execution_claim,
        ).json()
        client.post(
            "/registry/v1/internal/task-executions/complete",
            headers=headers,
            json={
                "agentId": "planner",
                "taskId": "experience-task",
                "messageId": "execute:planner:experience-task",
                "claimToken": task_claim["claimToken"],
                "state": "COMPLETED",
                "text": "report",
            },
        )
        first = client.post(
            "/registry/v1/internal/task-experiences/claim",
            headers=headers,
            json=experience_claim,
        )
        released = client.post(
            "/registry/v1/internal/task-experiences/release",
            headers=headers,
            json={
                **experience_claim,
                "claimToken": first.json()["claimToken"],
                "error": "temporary Memos outage",
            },
        )
        retry = client.post(
            "/registry/v1/internal/task-experiences/claim",
            headers=headers,
            json=experience_claim,
        )
        completed = client.post(
            "/registry/v1/internal/task-experiences/complete",
            headers=headers,
            json={
                **experience_claim,
                "claimToken": retry.json()["claimToken"],
                "report": {"executionProcess": {}, "taskImprovement": {}},
                "memoName": "memos/experience-1",
            },
        )
        replay = client.post(
            "/registry/v1/internal/task-experiences/claim",
            headers=headers,
            json=experience_claim,
        )

    assert first.json()["decision"] == "EXECUTE"
    assert released.json()["state"] == "PENDING"
    assert retry.json()["decision"] == "EXECUTE"
    assert retry.json()["attempt"] == 2
    assert completed.json()["state"] == "COMPLETED"
    assert replay.json()["decision"] == "REPLAY"
    assert replay.json()["memoName"] == "memos/experience-1"


def test_task_projection_ignores_out_of_order_events(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("A2A_MOCK", "1")
    monkeypatch.setenv("REGISTRY_SERVICE_TOKEN", "event-token")
    headers = {"Authorization": "Bearer event-token"}

    def event(sequence: int, state: str) -> dict:
        return {
            "eventId": f"task:planner:ordered-task:{sequence}",
            "agentId": "planner",
            "taskId": "ordered-task",
            "contextId": "ordered-context",
            "taskSequence": sequence,
            "state": state,
            "message": state,
            "source": "test-gateway",
        }

    with TestClient(create_app(registry_path=tmp_path / "registry.db")) as client:
        for payload in (
            event(1, "TASK_STATE_SUBMITTED"),
            event(3, "TASK_STATE_COMPLETED"),
            event(2, "TASK_STATE_WORKING"),
        ):
            assert client.post(
                "/registry/v1/internal/task-events",
                headers=headers,
                json=payload,
            ).status_code == 201
        projection = client.get(
            "/registry/v1/admin/agents/planner/tasks"
        ).json()
        timeline = client.get(
            "/registry/v1/admin/agents/planner/tasks/ordered-task/events"
        ).json()["events"]

    current = next(
        task
        for task in projection["recentTasks"]
        if task["taskId"] == "ordered-task"
    )
    assert current["state"] == "TASK_STATE_COMPLETED"
    assert current["lastTaskSequence"] == 3
    assert [item["taskSequence"] for item in timeline] == [1, 2, 3]


def test_adapter_catalog_exposes_compatibility_contract(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("A2A_MOCK", "1")

    with TestClient(create_app(registry_path=tmp_path / "registry.db")) as client:
        response = client.get("/registry/v1/adapters")

    assert response.status_code == 200
    adapters = response.json()["adapters"]
    assert {adapter["id"] for adapter in adapters} == {
        "openclaw-responses",
        "declarative-http",
    }
    for adapter in adapters:
        assert adapter["requestMethod"] == "POST"
        assert adapter["endpointTemplate"]
        assert adapter["bestFor"]
        assert adapter["requirements"]
        assert isinstance(adapter["requestExample"], dict)
        assert isinstance(adapter["responseExample"], dict)
        assert adapter["responseDescription"]


def test_registry_rejects_removed_generic_http_adapter(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("A2A_MOCK", "1")
    payload = {
        "agentId": "obsolete-adapter-agent",
        "name": "Obsolete Adapter Agent",
        "description": "Attempts to use a removed adapter.",
        "skills": [{
            "id": "answer",
            "name": "Answer",
            "description": "Answers one request.",
        }],
        "backend": {
            "adapterType": "generic-http",
            "endpoint": "http://127.0.0.1:9999/invoke",
        },
    }

    with TestClient(create_app(registry_path=tmp_path / "registry.db")) as client:
        response = client.post("/registry/v1/agents", json=payload)

    assert response.status_code == 400
    assert "unsupported backend.adapterType" in response.json()["error"]


def test_self_registration_generates_declarative_mapping(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("REGISTRY_SERVICE_TOKEN", "gateway-token")
    manifest = {
        "agentId": "insight-agent",
        "owner": "insight-team",
        "name": "Insight Agent",
        "description": "Produces insight reports.",
        "skills": [{
            "id": "insight-report",
            "name": "Insight report",
            "description": "Produces an insight report from supplied materials.",
        }],
        "interface": {
            "kind": "declarative-http",
            "endpoint": "http://127.0.0.1:9999/invoke",
            "method": "POST",
            "executionMode": "sync",
            "request": {
                "body": {
                    "query": "{{input}}",
                    "session_id": "{{contextId}}",
                }
            },
            "response": {
                "textSelectors": ["$.data.report"],
            },
        },
    }

    with TestClient(create_app(registry_path=tmp_path / "registry.db")) as client:
        registered = client.post(
            "/onboarding/v1/register",
            json=manifest,
        )
        unauthorized = client.get(
            "/registry/v1/internal/agents/insight-agent",
        )
        internal = client.get(
            "/registry/v1/internal/agents/insight-agent",
            headers={"Authorization": "Bearer gateway-token"},
        )

    assert registered.status_code == 200
    result = registered.json()
    assert result["status"] == "ACTIVE"
    assert result["decision"] == "GENERATED_DECLARATIVE_ADAPTER"
    assert result["adapter"] == "declarative-http"
    assert unauthorized.status_code == 401
    backend = internal.json()["definition"]["backend"]
    assert backend["requestBody"]["query"] == "{{input}}"
    assert backend["responseTextSelectors"] == ["$.data.report"]


def test_self_registration_accepts_write_only_bearer_token(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("REGISTRY_SERVICE_TOKEN", "gateway-token")
    manifest = {
        "agentId": "authenticated-agent",
        "name": "Authenticated Agent",
        "description": "Requires a provider bearer token.",
        "skills": [{
            "id": "answer",
            "name": "Answer",
            "description": "Answers authenticated requests.",
        }],
        "interface": {
            "kind": "declarative-http",
            "endpoint": "http://127.0.0.1:9999/invoke",
            "authentication": {
                "type": "bearer",
                "token": "provider-supplied-secret",
            },
            "request": {
                "body": {"input": "{{input}}"},
            },
            "response": {
                "textSelectors": ["$.answer"],
            },
        },
    }

    with TestClient(create_app(registry_path=tmp_path / "registry.db")) as client:
        registered = client.post("/onboarding/v1/register", json=manifest)
        admin = client.get(
            "/registry/v1/admin/agents/authenticated-agent"
        )
        internal = client.get(
            "/registry/v1/internal/agents/authenticated-agent",
            headers={"Authorization": "Bearer gateway-token"},
        )

    assert registered.status_code == 200
    admin_backend = admin.json()["definition"]["backend"]
    assert admin_backend["secretRef"] == (
        "agent:authenticated-agent:bearer"
    )
    assert "authToken" not in admin_backend
    assert internal.json()["definition"]["backend"]["authToken"] == (
        "provider-supplied-secret"
    )


def test_self_registration_routes_complex_contract_to_adapter_request(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("REGISTRY_REGISTRATION_TOKEN", "registration-token")
    manifest = {
        "agentId": "streaming-agent",
        "owner": "stream-team",
        "name": "Streaming Agent",
        "description": "Streams specialist results.",
        "skills": [{
            "id": "stream",
            "name": "Stream",
            "description": "Streams a long-running result.",
        }],
        "interface": {
            "kind": "declarative-http",
            "endpoint": "http://127.0.0.1:9999/stream",
            "method": "POST",
            "executionMode": "stream",
        },
    }

    with TestClient(create_app(registry_path=tmp_path / "registry.db")) as client:
        unauthorized = client.post(
            "/onboarding/v1/register",
            json=manifest,
        )
        submitted = client.post(
            "/onboarding/v1/register",
            headers={"Authorization": "Bearer registration-token"},
            json=manifest,
        )

    assert unauthorized.status_code == 401
    assert submitted.status_code == 202
    assert submitted.json()["status"] == "ADAPTER_REQUIRED"
    assert submitted.json()["adapterRequestId"].startswith("adapter-")


def test_self_registration_infers_response_mapping_from_safe_probe(
    monkeypatch,
    tmp_path,
) -> None:
    received: list[dict] = []

    class ProbeHandler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("content-length", "0"))
            payload = json.loads(self.rfile.read(length) or b"{}")
            received.append(payload)
            body = json.dumps({
                "data": {
                    "report": f"PROBE_RESULT: {payload['query']}",
                }
            }).encode()
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, _format: str, *_args: object) -> None:
            return

    probe_server = ThreadingHTTPServer(("127.0.0.1", 0), ProbeHandler)
    probe_thread = threading.Thread(
        target=probe_server.serve_forever,
        daemon=True,
    )
    probe_thread.start()
    manifest = {
        "agentId": "probe-agent",
        "owner": "probe-team",
        "name": "Probe Agent",
        "description": "Demonstrates response mapping inference.",
        "skills": [{
            "id": "probe",
            "name": "Probe",
            "description": "Returns a deterministic probe report.",
        }],
        "interface": {
            "kind": "declarative-http",
            "endpoint": (
                f"http://127.0.0.1:{probe_server.server_port}/invoke"
            ),
            "request": {
                "body": {
                    "query": "{{input}}",
                    "session_id": "{{contextId}}",
                }
            },
            "response": {},
        },
        "testCase": {
            "input": "safe registration probe",
        },
    }
    try:
        with TestClient(
            create_app(registry_path=tmp_path / "registry.db")
        ) as client:
            registered = client.post(
                "/onboarding/v1/register",
                json=manifest,
            )
            internal = client.get(
                "/registry/v1/internal/agents/probe-agent",
            )

        assert registered.status_code == 200
        assert registered.json()["inferredResponseMapping"] is True
        assert registered.json()["decision"] == (
            "GENERATED_DECLARATIVE_ADAPTER"
        )
        assert received == [{
            "query": "safe registration probe",
            "session_id": "self-registration-probe",
        }]
        backend = internal.json()["definition"]["backend"]
        assert backend["responseTextSelectors"] == ["$.data.report"]
    finally:
        probe_server.shutdown()
        probe_server.server_close()
