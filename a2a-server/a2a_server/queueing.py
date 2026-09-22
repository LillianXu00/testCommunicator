from __future__ import annotations

import asyncio
import hashlib
import json
import uuid

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol


@dataclass(frozen=True)
class TaskCommand:
    message_id: str
    task_id: str
    context_id: str
    agent_id: str
    prompt: str
    created_at: str

    @classmethod
    def create(
        cls,
        *,
        task_id: str,
        context_id: str,
        agent_id: str,
        prompt: str,
    ) -> TaskCommand:
        return cls(
            message_id=f"execute:{agent_id}:{task_id}",
            task_id=task_id,
            context_id=context_id,
            agent_id=agent_id,
            prompt=prompt,
            created_at=datetime.now(UTC).isoformat(),
        )

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> TaskCommand:
        values = {
            field: str(payload.get(field, "")).strip()
            for field in (
                "messageId",
                "taskId",
                "contextId",
                "agentId",
                "prompt",
                "createdAt",
            )
        }
        if not all(values.values()):
            raise ValueError("task command is missing required fields")
        return cls(
            message_id=values["messageId"],
            task_id=values["taskId"],
            context_id=values["contextId"],
            agent_id=values["agentId"],
            prompt=values["prompt"],
            created_at=values["createdAt"],
        )

    def to_payload(self) -> dict[str, Any]:
        return {
            "schemaVersion": 1,
            "type": "ExecuteTask",
            "messageId": self.message_id,
            "taskId": self.task_id,
            "contextId": self.context_id,
            "agentId": self.agent_id,
            "prompt": self.prompt,
            "createdAt": self.created_at,
        }


@dataclass(frozen=True)
class TaskExecutionResult:
    message_id: str
    task_id: str
    agent_id: str
    state: str
    text: str = ""
    error: str = ""

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> TaskExecutionResult:
        result = cls(
            message_id=str(payload.get("messageId", "")).strip(),
            task_id=str(payload.get("taskId", "")).strip(),
            agent_id=str(payload.get("agentId", "")).strip(),
            state=str(payload.get("state", "")).strip().upper(),
            text=str(payload.get("text", "")),
            error=str(payload.get("error", "")),
        )
        if not result.message_id or not result.task_id or not result.agent_id:
            raise ValueError("task result is missing required fields")
        if result.state not in {"COMPLETED", "FAILED"}:
            raise ValueError(f"unsupported task result state: {result.state}")
        return result

    def to_payload(self) -> dict[str, Any]:
        return {
            "schemaVersion": 1,
            "type": "TaskResult",
            "messageId": self.message_id,
            "taskId": self.task_id,
            "agentId": self.agent_id,
            "state": self.state,
            "text": self.text,
            "error": self.error,
            "finishedAt": datetime.now(UTC).isoformat(),
        }


@dataclass(frozen=True)
class ExperienceCommand:
    message_id: str
    task_id: str
    context_id: str
    agent_id: str
    prompt: str
    result_text: str
    created_at: str

    @classmethod
    def create(
        cls,
        *,
        task_id: str,
        context_id: str,
        agent_id: str,
        prompt: str,
        result_text: str,
    ) -> ExperienceCommand:
        return cls(
            message_id=f"experience:{agent_id}:{task_id}",
            task_id=task_id,
            context_id=context_id,
            agent_id=agent_id,
            prompt=prompt,
            result_text=result_text,
            created_at=datetime.now(UTC).isoformat(),
        )

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> ExperienceCommand:
        values = {
            field: str(payload.get(field, "")).strip()
            for field in (
                "messageId",
                "taskId",
                "contextId",
                "agentId",
                "prompt",
                "resultText",
                "createdAt",
            )
        }
        if not all(values.values()):
            raise ValueError("experience command is missing required fields")
        return cls(
            message_id=values["messageId"],
            task_id=values["taskId"],
            context_id=values["contextId"],
            agent_id=values["agentId"],
            prompt=values["prompt"],
            result_text=values["resultText"],
            created_at=values["createdAt"],
        )

    def to_payload(self) -> dict[str, Any]:
        return {
            "schemaVersion": 1,
            "type": "CollectExperience",
            "messageId": self.message_id,
            "taskId": self.task_id,
            "contextId": self.context_id,
            "agentId": self.agent_id,
            "prompt": self.prompt,
            "resultText": self.result_text,
            "createdAt": self.created_at,
        }


class TaskDispatcher(Protocol):
    async def dispatch(self, command: TaskCommand) -> TaskExecutionResult:
        ...


def task_partition(task_id: str, partition_count: int) -> int:
    if partition_count < 1:
        raise ValueError("partition_count must be at least 1")
    digest = hashlib.sha256(task_id.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") % partition_count


def command_exchange_name(prefix: str) -> str:
    return f"{prefix}.commands"


def command_queue_name(prefix: str, partition: int) -> str:
    return f"{prefix}.commands.{partition}"


def experience_exchange_name(prefix: str) -> str:
    return f"{prefix}.experience"


def experience_queue_name(prefix: str, partition: int) -> str:
    return f"{prefix}.experience.{partition}"


def dead_letter_exchange_name(prefix: str) -> str:
    return f"{prefix}.dead"


def command_queue_arguments(prefix: str) -> dict[str, Any]:
    return {
        "x-queue-type": "quorum",
        "x-single-active-consumer": True,
        "x-delivery-limit": 5,
        "x-dead-letter-exchange": dead_letter_exchange_name(prefix),
    }


def experience_queue_arguments(prefix: str) -> dict[str, Any]:
    return {
        "x-queue-type": "quorum",
        "x-single-active-consumer": True,
        "x-delivery-limit": 5,
        "x-dead-letter-exchange": dead_letter_exchange_name(prefix),
    }


def require_aio_pika():
    try:
        import aio_pika
    except ImportError as exc:
        raise RuntimeError(
            "RabbitMQ mode requires aio-pika; install the a2a-server dependencies"
        ) from exc
    return aio_pika


async def declare_command_topology(
    channel: Any,
    *,
    prefix: str,
    partitions: int,
) -> Any:
    aio_pika = require_aio_pika()
    exchange = await channel.declare_exchange(
        command_exchange_name(prefix),
        aio_pika.ExchangeType.DIRECT,
        durable=True,
    )
    dead_exchange = await channel.declare_exchange(
        dead_letter_exchange_name(prefix),
        aio_pika.ExchangeType.FANOUT,
        durable=True,
    )
    dead_queue = await channel.declare_queue(
        f"{prefix}.dead",
        durable=True,
        arguments={"x-queue-type": "quorum"},
    )
    await dead_queue.bind(dead_exchange)
    for partition in range(partitions):
        queue = await channel.declare_queue(
            command_queue_name(prefix, partition),
            durable=True,
            arguments=command_queue_arguments(prefix),
        )
        await queue.bind(exchange, routing_key=str(partition))
    return exchange


async def declare_experience_topology(
    channel: Any,
    *,
    prefix: str,
    partitions: int,
) -> Any:
    aio_pika = require_aio_pika()
    exchange = await channel.declare_exchange(
        experience_exchange_name(prefix),
        aio_pika.ExchangeType.DIRECT,
        durable=True,
    )
    for partition in range(partitions):
        queue = await channel.declare_queue(
            experience_queue_name(prefix, partition),
            durable=True,
            arguments=experience_queue_arguments(prefix),
        )
        await queue.bind(exchange, routing_key=str(partition))
    return exchange


class RabbitTaskDispatcher:
    def __init__(
        self,
        broker_url: str,
        *,
        prefix: str = "a2a.task",
        partitions: int = 8,
        result_timeout_seconds: float = 900,
    ) -> None:
        self.broker_url = broker_url
        self.prefix = prefix
        self.partitions = partitions
        self.result_timeout_seconds = result_timeout_seconds
        self.connection: Any = None
        self.channel: Any = None
        self.exchange: Any = None
        self.reply_queue: Any = None
        self.pending: dict[str, asyncio.Future[TaskExecutionResult]] = {}
        self._start_lock = asyncio.Lock()

    async def start(self) -> None:
        if self.connection is not None and not self.connection.is_closed:
            return
        async with self._start_lock:
            if self.connection is not None and not self.connection.is_closed:
                return
            aio_pika = require_aio_pika()
            self.connection = await aio_pika.connect_robust(self.broker_url)
            self.channel = await self.connection.channel(
                publisher_confirms=True,
                on_return_raises=True,
            )
            self.exchange = await declare_command_topology(
                self.channel,
                prefix=self.prefix,
                partitions=self.partitions,
            )
            self.reply_queue = await self.channel.declare_queue(
                f"{self.prefix}.results.{uuid.uuid4().hex}",
                exclusive=True,
                auto_delete=True,
            )
            await self.reply_queue.consume(self._on_result)

    async def _on_result(self, message: Any) -> None:
        async with message.process(requeue=False):
            payload = json.loads(message.body.decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("task result must be a JSON object")
            result = TaskExecutionResult.from_payload(payload)
            correlation_id = str(message.correlation_id or result.message_id)
            future = self.pending.get(correlation_id)
            if future is not None and not future.done():
                future.set_result(result)

    async def dispatch(self, command: TaskCommand) -> TaskExecutionResult:
        await self.start()
        aio_pika = require_aio_pika()
        loop = asyncio.get_running_loop()
        future: asyncio.Future[TaskExecutionResult] = loop.create_future()
        if command.message_id in self.pending:
            raise RuntimeError(f"task command is already pending: {command.task_id}")
        self.pending[command.message_id] = future
        try:
            message = aio_pika.Message(
                body=json.dumps(
                    command.to_payload(),
                    ensure_ascii=False,
                    separators=(",", ":"),
                ).encode("utf-8"),
                content_type="application/json",
                delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
                message_id=command.message_id,
                correlation_id=command.message_id,
                reply_to=self.reply_queue.name,
                timestamp=datetime.now(UTC),
            )
            partition = task_partition(command.task_id, self.partitions)
            await self.exchange.publish(
                message,
                routing_key=str(partition),
                mandatory=True,
            )
            return await asyncio.wait_for(
                future,
                timeout=self.result_timeout_seconds,
            )
        finally:
            self.pending.pop(command.message_id, None)

    async def close(self) -> None:
        for future in self.pending.values():
            if not future.done():
                future.set_exception(RuntimeError("RabbitMQ dispatcher closed"))
        self.pending.clear()
        if self.connection is not None and not self.connection.is_closed:
            await self.connection.close()
