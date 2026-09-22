from __future__ import annotations

import os

from contextlib import asynccontextmanager

import httpx
import uvicorn

from a2a.server.context import ServerCallContext
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_jsonrpc_routes
from a2a.server.routes.common import DefaultServerCallContextBuilder
from a2a.server.tasks import (
    BasePushNotificationSender,
    InMemoryPushNotificationConfigStore,
    InMemoryTaskStore,
)
from a2a.types import AgentCapabilities, AgentCard, AgentInterface
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from a2a_server.gateway_executor import RegistryGatewayExecutor
from a2a_server.queueing import RabbitTaskDispatcher, TaskDispatcher
from a2a_server.registry_client import RegistryClient


class AgentPathContextBuilder(DefaultServerCallContextBuilder):
    """Adds the agent selected by the gateway URL to the A2A call context."""

    def build(self, request: Request) -> ServerCallContext:
        context = super().build(request)
        context.state["agent_id"] = request.path_params.get("agent_id", "")
        return context


def resolve_agent_scope(context: ServerCallContext) -> str:
    """Keep task and push state isolated by routed agent and caller."""
    return f"{context.state.get('agent_id', '')}:{context.user.user_name}"


def build_gateway_card(public_url: str) -> AgentCard:
    return AgentCard(
        name="Managed A2A Gateway",
        description=(
            "A shared A2A 1.0 data-plane gateway backed by an independent "
            "Agent Registry."
        ),
        version="0.4.0",
        supported_interfaces=[
            AgentInterface(
                url=public_url.rstrip("/") + "/",
                protocol_binding="JSONRPC",
                protocol_version="1.0",
            )
        ],
        capabilities=AgentCapabilities(
            streaming=True,
            push_notifications=True,
        ),
        default_input_modes=["text/plain"],
        default_output_modes=["text/plain"],
        skills=[],
    )


def create_app(
    *,
    registry_client: RegistryClient | None = None,
    task_dispatcher: TaskDispatcher | None = None,
) -> Starlette:
    host = os.getenv("A2A_HOST", "127.0.0.1")
    port = int(os.getenv("A2A_PORT", "4101"))
    public_url = os.getenv("A2A_PUBLIC_URL", f"http://{host}:{port}")
    directory = registry_client or RegistryClient(
        os.getenv("A2A_REGISTRY_URL", "http://127.0.0.1:4200"),
        service_token=os.getenv("A2A_REGISTRY_SERVICE_TOKEN", ""),
    )
    owns_registry_client = registry_client is None
    broker_url = os.getenv("A2A_BROKER_URL", "").strip()
    dispatcher = task_dispatcher
    if dispatcher is None and broker_url:
        dispatcher = RabbitTaskDispatcher(
            broker_url,
            prefix=os.getenv("A2A_QUEUE_PREFIX", "a2a.task"),
            partitions=int(os.getenv("A2A_QUEUE_PARTITIONS", "8")),
            result_timeout_seconds=float(
                os.getenv("A2A_QUEUE_RESULT_TIMEOUT_SECONDS", "900")
            ),
        )

    push_config_store = InMemoryPushNotificationConfigStore(
        owner_resolver=resolve_agent_scope
    )
    push_http_client = httpx.AsyncClient(timeout=15)
    handler = DefaultRequestHandler(
        agent_executor=RegistryGatewayExecutor(
            directory,
            task_dispatcher=dispatcher,
        ),
        task_store=InMemoryTaskStore(owner_resolver=resolve_agent_scope),
        agent_card=build_gateway_card(public_url),
        push_config_store=push_config_store,
        push_sender=BasePushNotificationSender(
            push_http_client,
            push_config_store,
        ),
    )

    async def health(_request: Request) -> JSONResponse:
        try:
            registry_health = await directory.client.get(
                f"{directory.base_url}/health"
            )
            registry_health.raise_for_status()
        except httpx.HTTPError as exc:
            return JSONResponse(
                {
                    "status": "degraded",
                    "service": "a2a-gateway",
                    "registry": "unavailable",
                    "error": str(exc),
                },
                status_code=503,
            )
        return JSONResponse({
            "status": "ok",
            "service": "a2a-gateway",
            "registry": "available",
            "executionMode": "rabbitmq" if dispatcher else "inline",
        })

    @asynccontextmanager
    async def lifespan(_app: Starlette):
        start = getattr(dispatcher, "start", None)
        if callable(start):
            await start()
        try:
            yield
        finally:
            close = getattr(dispatcher, "close", None)
            if callable(close):
                await close()
            await push_http_client.aclose()
            if owns_registry_client:
                await directory.aclose()

    return Starlette(
        routes=[
            Route("/health", health, methods=["GET"]),
            *create_jsonrpc_routes(
                handler,
                "/a2a/agents/{agent_id}",
                context_builder=AgentPathContextBuilder(),
            ),
        ],
        lifespan=lifespan,
    )


def main() -> None:
    uvicorn.run(
        create_app(),
        host=os.getenv("A2A_HOST", "127.0.0.1"),
        port=int(os.getenv("A2A_PORT", "4101")),
    )


if __name__ == "__main__":
    main()
