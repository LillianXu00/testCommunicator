from __future__ import annotations

import os
import asyncio
import json

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
import uvicorn

from a2a.server.request_handlers.response_helpers import agent_card_to_dict
from a2a.types import AgentCard
from google.protobuf.json_format import ParseDict
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from registry_service.onboarding import OnboardingService
from registry_service.execution_store import TaskExecutionStore
from registry_service.status import AgentStatusMonitor
from registry_service.store import ADAPTER_CATALOG, AgentRegistry
from registry_service.task_store import TaskEventStore


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATABASE_PATH = PROJECT_ROOT / ".runtime" / "agent-registry.sqlite3"
DEFAULT_BOOTSTRAP_PATH = PROJECT_ROOT / "config" / "planner-agent.json"
STATIC_DIRECTORY = Path(__file__).resolve().parent / "static"


def _token_authorized(request: Request, environment_name: str) -> bool:
    configured_token = os.getenv(environment_name, "")
    if not configured_token:
        return True
    return request.headers.get("authorization", "") == f"Bearer {configured_token}"


def _admin_authorized(request: Request) -> bool:
    token_name = (
        "REGISTRY_ADMIN_TOKEN"
        if os.getenv("REGISTRY_ADMIN_TOKEN")
        else "A2A_REGISTRY_ADMIN_TOKEN"
    )
    return _token_authorized(request, token_name)


def _service_authorized(request: Request) -> bool:
    return _token_authorized(request, "REGISTRY_SERVICE_TOKEN")


async def fetch_native_agent_card(card_url: str) -> dict[str, Any]:
    if not card_url.startswith(("http://", "https://")):
        raise ValueError("sourceAgentCardUrl must be an absolute HTTP(S) URL")
    async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
        response = await client.get(card_url)
        response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError("the Agent Card endpoint must return a JSON object")
    card = ParseDict(payload, AgentCard())
    normalized = agent_card_to_dict(card)
    interfaces = normalized.get("supportedInterfaces", [])
    if not any(
        str(item.get("protocolBinding", "")).upper() == "JSONRPC"
        and str(item.get("url", "")).startswith(("http://", "https://"))
        for item in interfaces
        if isinstance(item, dict)
    ):
        raise ValueError(
            "the current Manager client requires a JSONRPC AgentInterface"
        )
    return normalized


def create_registry_routes(
    registry: AgentRegistry,
    onboarding: OnboardingService,
    status_monitor: AgentStatusMonitor,
    task_store: TaskEventStore,
    execution_store: TaskExecutionStore,
    event_subscribers: set[asyncio.Queue[dict[str, Any]]],
) -> list[Route]:
    def runtime_statuses() -> list[dict[str, Any]]:
        return [
            {
                **status.to_payload(),
                **task_store.agent_projection(status.agent_id),
            }
            for status in status_monitor.snapshot()
        ]

    async def health(_request: Request) -> JSONResponse:
        return JSONResponse({
            "status": "ok",
            "service": "agent-registry",
            "activeAgents": len(registry.list()),
            "capabilities": {
                "managedSecrets": True,
            },
        })

    async def list_agents(_request: Request) -> JSONResponse:
        agents = [
            {
                "agentId": record.definition.agent_id,
                "integrationMode": record.definition.integration_mode,
                "name": record.definition.name,
                "description": record.definition.description,
                "status": record.definition.status,
                "revision": record.revision,
                "cardRevision": record.card_revision,
                "skills": record.card.get("skills", []),
            }
            for record in registry.list()
        ]
        return JSONResponse({"agents": agents, "total": len(agents)})

    async def list_agent_cards(_request: Request) -> JSONResponse:
        agents = [registry.catalog_entry(record) for record in registry.list()]
        return JSONResponse({"agents": agents, "total": len(agents)})

    async def list_agent_status(request: Request) -> JSONResponse:
        if not _admin_authorized(request):
            return JSONResponse({"error": "Unauthorized"}, status_code=401)
        statuses = runtime_statuses()
        return JSONResponse({"statuses": statuses, "total": len(statuses)})

    async def refresh_agent_status(request: Request) -> JSONResponse:
        if not _admin_authorized(request):
            return JSONResponse({"error": "Unauthorized"}, status_code=401)
        await status_monitor.refresh()
        statuses = runtime_statuses()
        return JSONResponse({"statuses": statuses, "total": len(statuses)})

    async def ingest_task_event(request: Request) -> JSONResponse:
        if not _service_authorized(request):
            return JSONResponse({"error": "Unauthorized"}, status_code=401)
        try:
            payload: Any = await request.json()
            if not isinstance(payload, dict):
                raise ValueError("request body must be a JSON object")
            event = task_store.record(payload)
        except KeyError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)
        except (ValueError, TypeError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        if not event["duplicate"]:
            for queue in tuple(event_subscribers):
                try:
                    queue.put_nowait(event)
                except asyncio.QueueFull:
                    pass
        return JSONResponse(event, status_code=200 if event["duplicate"] else 201)

    async def claim_task_execution(request: Request) -> JSONResponse:
        if not _service_authorized(request):
            return JSONResponse({"error": "Unauthorized"}, status_code=401)
        try:
            payload: Any = await request.json()
            if not isinstance(payload, dict):
                raise ValueError("request body must be a JSON object")
            result = execution_store.claim(payload)
        except KeyError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)
        except (ValueError, TypeError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return JSONResponse(result)

    async def complete_task_execution(request: Request) -> JSONResponse:
        if not _service_authorized(request):
            return JSONResponse({"error": "Unauthorized"}, status_code=401)
        try:
            payload: Any = await request.json()
            if not isinstance(payload, dict):
                raise ValueError("request body must be a JSON object")
            result = execution_store.complete(payload)
        except KeyError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)
        except (ValueError, TypeError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return JSONResponse(result)

    async def claim_task_experience(request: Request) -> JSONResponse:
        if not _service_authorized(request):
            return JSONResponse({"error": "Unauthorized"}, status_code=401)
        try:
            payload: Any = await request.json()
            if not isinstance(payload, dict):
                raise ValueError("request body must be a JSON object")
            result = execution_store.claim_experience(payload)
        except KeyError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)
        except (ValueError, TypeError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return JSONResponse(result)

    async def complete_task_experience(request: Request) -> JSONResponse:
        if not _service_authorized(request):
            return JSONResponse({"error": "Unauthorized"}, status_code=401)
        try:
            payload: Any = await request.json()
            if not isinstance(payload, dict):
                raise ValueError("request body must be a JSON object")
            result = execution_store.complete_experience(payload)
        except KeyError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)
        except (ValueError, TypeError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return JSONResponse(result)

    async def release_task_experience(request: Request) -> JSONResponse:
        if not _service_authorized(request):
            return JSONResponse({"error": "Unauthorized"}, status_code=401)
        try:
            payload: Any = await request.json()
            if not isinstance(payload, dict):
                raise ValueError("request body must be a JSON object")
            result = execution_store.release_experience(payload)
        except KeyError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)
        except (ValueError, TypeError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return JSONResponse(result)

    async def stream_task_events(request: Request) -> StreamingResponse | JSONResponse:
        if not _admin_authorized(request):
            return JSONResponse({"error": "Unauthorized"}, status_code=401)

        async def events():
            queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=256)
            event_subscribers.add(queue)
            try:
                yield ": connected\n\n"
                while True:
                    try:
                        event = await asyncio.wait_for(queue.get(), timeout=15)
                    except TimeoutError:
                        yield ": keepalive\n\n"
                        continue
                    yield (
                        f"id: {event['sequence']}\n"
                        "event: task-status\n"
                        f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                    )
            finally:
                event_subscribers.discard(queue)

        return StreamingResponse(
            events(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    async def list_agent_tasks(request: Request) -> JSONResponse:
        if not _admin_authorized(request):
            return JSONResponse({"error": "Unauthorized"}, status_code=401)
        agent_id = request.path_params["agent_id"]
        projection = task_store.agent_projection(agent_id, recent_limit=20)
        return JSONResponse(projection)

    async def list_task_events(request: Request) -> JSONResponse:
        if not _admin_authorized(request):
            return JSONResponse({"error": "Unauthorized"}, status_code=401)
        events = task_store.task_events(
            request.path_params["agent_id"],
            request.path_params["task_id"],
        )
        return JSONResponse({"events": events, "total": len(events)})

    async def list_admin_agents(request: Request) -> JSONResponse:
        if not _admin_authorized(request):
            return JSONResponse({"error": "Unauthorized"}, status_code=401)
        agents = [
            {
                **registry.catalog_entry(record),
                "updatedAt": record.updated_at,
                "definition": record.definition.to_payload(),
            }
            for record in registry.list(active_only=False)
        ]
        return JSONResponse({"agents": agents, "total": len(agents)})

    async def get_admin_agent(request: Request) -> JSONResponse:
        if not _admin_authorized(request):
            return JSONResponse({"error": "Unauthorized"}, status_code=401)
        record = registry.get(
            request.path_params["agent_id"],
            active_only=False,
        )
        if record is None:
            return JSONResponse({"error": "Agent not found"}, status_code=404)
        return JSONResponse({
            **registry.catalog_entry(record),
            "updatedAt": record.updated_at,
            "definition": record.definition.to_payload(),
        })

    async def get_internal_agent(request: Request) -> JSONResponse:
        if not _service_authorized(request):
            return JSONResponse({"error": "Unauthorized"}, status_code=401)
        record = registry.get(request.path_params["agent_id"])
        if record is None:
            return JSONResponse(
                {"error": "Agent not found or inactive"},
                status_code=404,
            )
        definition = record.definition.to_payload()
        backend = definition.get("backend")
        secret_ref = (
            str(backend.get("secretRef", "")).strip()
            if isinstance(backend, dict)
            else ""
        )
        if secret_ref:
            if not os.getenv("REGISTRY_SERVICE_TOKEN"):
                return JSONResponse(
                    {
                        "error": (
                            "REGISTRY_SERVICE_TOKEN is required to resolve "
                            "managed agent credentials"
                        )
                    },
                    status_code=503,
                )
            try:
                backend["authToken"] = registry.resolve_secret(
                    record.definition.agent_id,
                    secret_ref,
                )
            except KeyError:
                return JSONResponse(
                    {"error": "Agent credential not found"},
                    status_code=503,
                )
        return JSONResponse({
            "agentId": record.definition.agent_id,
            "revision": record.revision,
            "definition": definition,
        })

    async def get_agent_card(request: Request) -> JSONResponse:
        record = registry.get(request.path_params["agent_id"])
        if record is None:
            return JSONResponse(
                {"error": "Agent not found or inactive"},
                status_code=404,
            )
        return JSONResponse(
            record.card,
            headers={
                "ETag": f'"{record.card_revision}"',
                "Cache-Control": "public, max-age=60, must-revalidate",
            },
        )

    async def upsert_agent(request: Request) -> JSONResponse:
        if not _admin_authorized(request):
            return JSONResponse({"error": "Unauthorized"}, status_code=401)
        try:
            payload: Any = await request.json()
            if not isinstance(payload, dict):
                raise ValueError("request body must be a JSON object")
            source_card = None
            integration_mode = payload.get(
                "integrationMode",
                "managed-adapter" if payload.get("backend") else "",
            )
            if integration_mode == "native-a2a":
                source_card = await fetch_native_agent_card(
                    str(payload.get("sourceAgentCardUrl", "")).strip()
                )
            record = registry.upsert(
                payload,
                agent_id=request.path_params.get("agent_id"),
                source_card=source_card,
            )
        except (ValueError, TypeError, httpx.HTTPError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return JSONResponse(registry.catalog_entry(record))

    async def self_register(request: Request) -> JSONResponse:
        if not _token_authorized(request, "REGISTRY_REGISTRATION_TOKEN"):
            return JSONResponse({"error": "Unauthorized"}, status_code=401)
        try:
            payload: Any = await request.json()
            if not isinstance(payload, dict):
                raise ValueError("request body must be a JSON object")
            result = await onboarding.register(payload)
        except (ValueError, TypeError, httpx.HTTPError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        status_code = 200 if result["status"] == "ACTIVE" else 202
        return JSONResponse(result, status_code=status_code)

    async def delete_agent(request: Request) -> JSONResponse:
        if not _admin_authorized(request):
            return JSONResponse({"error": "Unauthorized"}, status_code=401)
        deleted = registry.delete(request.path_params["agent_id"])
        if not deleted:
            return JSONResponse({"error": "Agent not found"}, status_code=404)
        return JSONResponse({"deleted": True})

    async def inspect_native_card(request: Request) -> JSONResponse:
        if not _admin_authorized(request):
            return JSONResponse({"error": "Unauthorized"}, status_code=401)
        try:
            payload: Any = await request.json()
            if not isinstance(payload, dict):
                raise ValueError("request body must be a JSON object")
            card = await fetch_native_agent_card(
                str(payload.get("url", "")).strip()
            )
        except (ValueError, TypeError, httpx.HTTPError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return JSONResponse({"agentCard": card})

    async def list_adapters(_request: Request) -> JSONResponse:
        adapters = list(ADAPTER_CATALOG.values())
        return JSONResponse({"adapters": adapters, "total": len(adapters)})

    async def create_adapter_request(request: Request) -> JSONResponse:
        if not _admin_authorized(request):
            return JSONResponse({"error": "Unauthorized"}, status_code=401)
        try:
            payload: Any = await request.json()
            if not isinstance(payload, dict):
                raise ValueError("request body must be a JSON object")
            result = registry.create_adapter_request(payload)
        except (ValueError, TypeError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return JSONResponse(result, status_code=201)

    async def list_adapter_requests(request: Request) -> JSONResponse:
        if not _admin_authorized(request):
            return JSONResponse({"error": "Unauthorized"}, status_code=401)
        requests = registry.list_adapter_requests()
        return JSONResponse({"requests": requests, "total": len(requests)})

    return [
        Route("/health", health, methods=["GET"]),
        Route("/registry/v1/agents", list_agents, methods=["GET"]),
        Route("/registry/v1/agent-cards", list_agent_cards, methods=["GET"]),
        Route(
            "/registry/v1/admin/agent-status",
            list_agent_status,
            methods=["GET"],
        ),
        Route(
            "/registry/v1/admin/agent-status/refresh",
            refresh_agent_status,
            methods=["POST"],
        ),
        Route(
            "/registry/v1/internal/task-events",
            ingest_task_event,
            methods=["POST"],
        ),
        Route(
            "/registry/v1/internal/task-executions/claim",
            claim_task_execution,
            methods=["POST"],
        ),
        Route(
            "/registry/v1/internal/task-executions/complete",
            complete_task_execution,
            methods=["POST"],
        ),
        Route(
            "/registry/v1/internal/task-experiences/claim",
            claim_task_experience,
            methods=["POST"],
        ),
        Route(
            "/registry/v1/internal/task-experiences/complete",
            complete_task_experience,
            methods=["POST"],
        ),
        Route(
            "/registry/v1/internal/task-experiences/release",
            release_task_experience,
            methods=["POST"],
        ),
        Route(
            "/registry/v1/admin/task-events/stream",
            stream_task_events,
            methods=["GET"],
        ),
        Route(
            "/registry/v1/admin/agents/{agent_id}/tasks",
            list_agent_tasks,
            methods=["GET"],
        ),
        Route(
            "/registry/v1/admin/agents/{agent_id}/tasks/{task_id}/events",
            list_task_events,
            methods=["GET"],
        ),
        Route("/registry/v1/adapters", list_adapters, methods=["GET"]),
        Route(
            "/registry/v1/agent-cards/inspect",
            inspect_native_card,
            methods=["POST"],
        ),
        Route("/registry/v1/admin/agents", list_admin_agents, methods=["GET"]),
        Route(
            "/registry/v1/admin/agents/{agent_id}",
            get_admin_agent,
            methods=["GET"],
        ),
        Route(
            "/registry/v1/internal/agents/{agent_id}",
            get_internal_agent,
            methods=["GET"],
        ),
        Route(
            "/registry/v1/admin/adapter-requests",
            list_adapter_requests,
            methods=["GET"],
        ),
        Route(
            "/registry/v1/adapter-requests",
            create_adapter_request,
            methods=["POST"],
        ),
        Route("/registry/v1/agents", upsert_agent, methods=["POST"]),
        Route("/registry/v1/agents/{agent_id}", upsert_agent, methods=["PUT"]),
        Route(
            "/registry/v1/agents/{agent_id}",
            delete_agent,
            methods=["DELETE"],
        ),
        Route(
            "/registry/v1/agents/{agent_id}/card",
            get_agent_card,
            methods=["GET"],
        ),
        Route(
            "/onboarding/v1/register",
            self_register,
            methods=["POST"],
        ),
    ]


def create_app(
    *,
    registry_path: str | Path | None = None,
    bootstrap_path: str | Path | None = None,
) -> Starlette:
    host = os.getenv("REGISTRY_HOST", "127.0.0.1")
    port = int(os.getenv("REGISTRY_PORT", "4200"))
    public_url = os.getenv(
        "REGISTRY_PUBLIC_URL",
        f"http://{host}:{port}",
    )
    gateway_public_url = os.getenv(
        "A2A_GATEWAY_PUBLIC_URL",
        "http://127.0.0.1:4101",
    )
    registry = AgentRegistry(
        registry_path
        or os.getenv("REGISTRY_DB", str(DEFAULT_DATABASE_PATH)),
        gateway_public_url=gateway_public_url,
        registry_public_url=public_url,
    )
    selected_bootstrap_path = (
        bootstrap_path
        if bootstrap_path is not None
        else os.getenv(
            "REGISTRY_BOOTSTRAP_FILE",
            str(DEFAULT_BOOTSTRAP_PATH),
        )
    )
    if selected_bootstrap_path:
        registry.seed_from_file(selected_bootstrap_path)

    onboarding = OnboardingService(
        registry,
        fetch_native_agent_card=fetch_native_agent_card,
    )
    status_monitor = AgentStatusMonitor(
        registry,
        interval_seconds=float(os.getenv("REGISTRY_STATUS_INTERVAL", "10")),
        timeout_seconds=float(os.getenv("REGISTRY_STATUS_TIMEOUT", "3")),
    )
    task_store = TaskEventStore(registry.database_path)
    execution_store = TaskExecutionStore(registry.database_path)
    event_subscribers: set[asyncio.Queue[dict[str, Any]]] = set()

    @asynccontextmanager
    async def lifespan(_app: Starlette):
        await status_monitor.start()
        try:
            yield
        finally:
            await status_monitor.stop()

    app = Starlette(
        routes=[
            *create_registry_routes(
                registry,
                onboarding,
                status_monitor,
                task_store,
                execution_store,
                event_subscribers,
            ),
            Mount(
                "/registry-ui",
                app=StaticFiles(directory=STATIC_DIRECTORY, html=True),
                name="registry-ui",
            ),
        ],
        lifespan=lifespan,
    )
    app.state.registry = registry
    app.state.status_monitor = status_monitor
    app.state.task_store = task_store
    app.state.execution_store = execution_store
    return app


def main() -> None:
    uvicorn.run(
        create_app(),
        host=os.getenv("REGISTRY_HOST", "127.0.0.1"),
        port=int(os.getenv("REGISTRY_PORT", "4200")),
    )


if __name__ == "__main__":
    main()
