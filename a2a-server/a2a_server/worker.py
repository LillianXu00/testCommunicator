from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
import uuid

from typing import Any

from a2a_server.backends.factory import BackendFactory
from a2a_server.experience import ExperienceCollector, MemosExperienceSink
from a2a_server.queueing import (
    ExperienceCommand,
    TaskCommand,
    TaskExecutionResult,
    command_exchange_name,
    command_queue_arguments,
    command_queue_name,
    declare_command_topology,
    declare_experience_topology,
    experience_exchange_name,
    experience_queue_arguments,
    experience_queue_name,
    require_aio_pika,
    task_partition,
)
from a2a_server.registry_client import RegistryClient


logger = logging.getLogger(__name__)


class TaskQueueWorker:
    def __init__(
        self,
        broker_url: str,
        registry: RegistryClient,
        *,
        prefix: str = "a2a.task",
        partitions: int = 8,
        lease_seconds: float = 1200,
        worker_id: str | None = None,
        backend_factory: BackendFactory | None = None,
        experience_collector: ExperienceCollector | None = None,
    ) -> None:
        self.broker_url = broker_url
        self.registry = registry
        self.prefix = prefix
        self.partitions = partitions
        self.lease_seconds = lease_seconds
        self.worker_id = worker_id or (
            f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"
        )
        self.backend_factory = backend_factory or BackendFactory()
        self.experience_collector = experience_collector
        self.connection: Any = None
        self.publisher_channel: Any = None
        self.experience_exchange: Any = None
        self.consumer_channels: list[Any] = []

    async def start(self) -> None:
        aio_pika = require_aio_pika()
        self.connection = await aio_pika.connect_robust(self.broker_url)
        self.publisher_channel = await self.connection.channel(
            publisher_confirms=True,
            on_return_raises=True,
        )
        await declare_command_topology(
            self.publisher_channel,
            prefix=self.prefix,
            partitions=self.partitions,
        )
        if self.experience_collector is not None:
            self.experience_exchange = await declare_experience_topology(
                self.publisher_channel,
                prefix=self.prefix,
                partitions=self.partitions,
            )
        for partition in range(self.partitions):
            channel = await self.connection.channel()
            await channel.set_qos(prefetch_count=1)
            exchange = await channel.declare_exchange(
                command_exchange_name(self.prefix),
                aio_pika.ExchangeType.DIRECT,
                durable=True,
            )
            queue = await channel.declare_queue(
                command_queue_name(self.prefix, partition),
                durable=True,
                arguments=command_queue_arguments(self.prefix),
            )
            await queue.bind(exchange, routing_key=str(partition))
            await queue.consume(self._handle_message)
            self.consumer_channels.append(channel)
        if self.experience_collector is not None:
            for partition in range(self.partitions):
                channel = await self.connection.channel()
                await channel.set_qos(prefetch_count=1)
                exchange = await channel.declare_exchange(
                    experience_exchange_name(self.prefix),
                    aio_pika.ExchangeType.DIRECT,
                    durable=True,
                )
                queue = await channel.declare_queue(
                    experience_queue_name(self.prefix, partition),
                    durable=True,
                    arguments=experience_queue_arguments(self.prefix),
                )
                await queue.bind(exchange, routing_key=str(partition))
                await queue.consume(self._handle_experience_message)
                self.consumer_channels.append(channel)
        logger.info(
            "A2A worker %s consuming %s partitions",
            self.worker_id,
            self.partitions,
        )

    async def _handle_message(self, message: Any) -> None:
        try:
            payload = json.loads(message.body.decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("task command must be a JSON object")
            command = TaskCommand.from_payload(payload)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            logger.error("Discarding invalid task command: %s", exc)
            await message.reject(requeue=False)
            return

        try:
            claim_payload = {
                "agentId": command.agent_id,
                "taskId": command.task_id,
                "messageId": command.message_id,
                "contextId": command.context_id,
                "workerId": self.worker_id,
                "leaseSeconds": self.lease_seconds,
            }
            claim = await self.registry.claim_task_execution(claim_payload)
            decision = str(claim.get("decision", ""))
            while decision == "BUSY":
                delay = float(claim.get("retryAfterMs", 1000)) / 1000
                await asyncio.sleep(max(delay, 0.1))
                claim = await self.registry.claim_task_execution(claim_payload)
                decision = str(claim.get("decision", ""))
            if decision == "REPLAY":
                result = TaskExecutionResult.from_payload({
                    "messageId": claim.get("messageId"),
                    "taskId": claim.get("taskId"),
                    "agentId": claim.get("agentId"),
                    "state": claim.get("state"),
                    "text": claim.get("text", ""),
                    "error": claim.get("error", ""),
                })
            elif decision == "EXECUTE":
                try:
                    await self.registry.report_task_event(
                        agent_id=command.agent_id,
                        task_id=command.task_id,
                        context_id=command.context_id,
                        state="TASK_STATE_WORKING",
                        message=(
                            "Queue worker claimed the task for agent execution."
                        ),
                        event_id=(
                            f"task:{command.agent_id}:{command.task_id}:2"
                        ),
                        task_sequence=2,
                        metadata={
                            "phase": "agent-execution",
                            "taskSequence": 2,
                            "workerId": self.worker_id,
                        },
                        source="queue-worker",
                    )
                except Exception as exc:
                    logger.warning(
                        "Could not report working state task=%s: %s",
                        command.task_id,
                        exc,
                    )
                result = await self._execute(command)
                completed = await self.registry.complete_task_execution({
                    **result.to_payload(),
                    "claimToken": str(claim.get("claimToken", "")),
                })
                result = TaskExecutionResult.from_payload(completed)
            else:
                raise RuntimeError(f"unsupported Registry claim decision: {decision}")

            await self._publish_result(message, result)
            if self.experience_collector is not None and result.state == "COMPLETED":
                await self._publish_experience(command, result)
            await message.ack()
        except Exception as exc:
            logger.exception(
                "Task command failed before acknowledgement task=%s: %s",
                command.task_id,
                exc,
            )
            await message.nack(requeue=True)

    async def _handle_experience_message(self, message: Any) -> None:
        try:
            payload = json.loads(message.body.decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("experience command must be a JSON object")
            command = ExperienceCommand.from_payload(payload)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            logger.error("Discarding invalid experience command: %s", exc)
            await message.reject(requeue=False)
            return

        claim_token = ""
        try:
            claim_payload = {
                "agentId": command.agent_id,
                "taskId": command.task_id,
                "messageId": command.message_id,
                "workerId": self.worker_id,
                "leaseSeconds": self.lease_seconds,
            }
            claim = await self.registry.claim_task_experience(claim_payload)
            decision = str(claim.get("decision", ""))
            while decision == "BUSY":
                delay = float(claim.get("retryAfterMs", 1000)) / 1000
                await asyncio.sleep(max(delay, 0.1))
                claim = await self.registry.claim_task_experience(claim_payload)
                decision = str(claim.get("decision", ""))
            if decision == "REPLAY":
                await message.ack()
                return
            if decision != "EXECUTE":
                raise RuntimeError(
                    f"unsupported Registry experience decision: {decision}"
                )
            claim_token = str(claim.get("claimToken", ""))
            record = await self.registry.get(command.agent_id)
            if record is None:
                raise RuntimeError(
                    f"Agent '{command.agent_id}' is not registered or active"
                )
            if record.definition.integration_mode != "managed-adapter":
                raise RuntimeError(
                    "Native A2A agents require provider-side experience support"
                )
            backend = self.backend_factory.create(record.definition)
            if self.experience_collector is None:
                raise RuntimeError("experience collector is not configured")
            experience, memo_name = await self.experience_collector.collect(
                command,
                backend,
                record.definition.skills,
            )
            await self.registry.complete_task_experience({
                "agentId": command.agent_id,
                "taskId": command.task_id,
                "messageId": command.message_id,
                "claimToken": claim_token,
                "report": experience.to_payload(),
                "memoName": memo_name,
            })
            logger.info(
                "Collected task experience agent=%s task=%s memo=%s",
                command.agent_id,
                command.task_id,
                memo_name,
            )
            await message.ack()
        except Exception as exc:
            logger.exception(
                "Experience collection failed agent=%s task=%s: %s",
                command.agent_id,
                command.task_id,
                exc,
            )
            if claim_token:
                try:
                    await self.registry.release_task_experience({
                        "agentId": command.agent_id,
                        "taskId": command.task_id,
                        "messageId": command.message_id,
                        "claimToken": claim_token,
                        "error": str(exc),
                    })
                except Exception:
                    logger.exception(
                        "Could not release experience claim task=%s",
                        command.task_id,
                    )
            await message.nack(requeue=True)

    async def _execute(self, command: TaskCommand) -> TaskExecutionResult:
        try:
            record = await self.registry.get(command.agent_id)
            if record is None:
                raise RuntimeError(
                    f"Agent '{command.agent_id}' is not registered or active"
                )
            if record.definition.integration_mode != "managed-adapter":
                raise RuntimeError("Native A2A agents cannot use the managed worker")
            backend = self.backend_factory.create(record.definition)
            text = await backend.invoke(command.prompt, command.context_id)
            return TaskExecutionResult(
                message_id=command.message_id,
                task_id=command.task_id,
                agent_id=command.agent_id,
                state="COMPLETED",
                text=text,
            )
        except Exception as exc:
            return TaskExecutionResult(
                message_id=command.message_id,
                task_id=command.task_id,
                agent_id=command.agent_id,
                state="FAILED",
                error=str(exc),
            )

    async def _publish_result(
        self,
        source_message: Any,
        result: TaskExecutionResult,
    ) -> None:
        reply_to = str(source_message.reply_to or "").strip()
        if not reply_to:
            raise RuntimeError("task command has no reply queue")
        aio_pika = require_aio_pika()
        response = aio_pika.Message(
            body=json.dumps(
                result.to_payload(),
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8"),
            content_type="application/json",
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
            message_id=f"result:{result.message_id}",
            correlation_id=str(
                source_message.correlation_id or result.message_id
            ),
        )
        await self.publisher_channel.default_exchange.publish(
            response,
            routing_key=reply_to,
            mandatory=True,
        )

    async def _publish_experience(
        self,
        command: TaskCommand,
        result: TaskExecutionResult,
    ) -> None:
        if self.experience_exchange is None:
            return
        experience = ExperienceCommand.create(
            task_id=command.task_id,
            context_id=command.context_id,
            agent_id=command.agent_id,
            prompt=command.prompt,
            result_text=result.text,
        )
        aio_pika = require_aio_pika()
        message = aio_pika.Message(
            body=json.dumps(
                experience.to_payload(),
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8"),
            content_type="application/json",
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
            message_id=experience.message_id,
            correlation_id=experience.message_id,
        )
        await self.experience_exchange.publish(
            message,
            routing_key=str(task_partition(command.task_id, self.partitions)),
            mandatory=True,
        )

    async def close(self) -> None:
        await self.registry.aclose()
        if self.connection is not None and not self.connection.is_closed:
            await self.connection.close()


async def run_worker() -> None:
    broker_url = os.getenv(
        "A2A_BROKER_URL",
        "amqp://guest:guest@127.0.0.1:5672/",
    )
    registry = RegistryClient(
        os.getenv("A2A_REGISTRY_URL", "http://127.0.0.1:4200"),
        service_token=os.getenv("A2A_REGISTRY_SERVICE_TOKEN", ""),
    )
    experience_collector = None
    if os.getenv("AGENT_EXPERIENCE_ENABLED", "0").strip() == "1":
        experience_collector = ExperienceCollector(
            MemosExperienceSink(
                os.getenv(
                    "MEMOS_BASE_URL",
                    "https://memos.memtensor.cn/api/openmem/v1",
                ),
                os.getenv("MEMOS_API_KEY", "")
                or os.getenv("MEMOS_TOKEN", ""),
                os.getenv("MEMOS_USER_ID", "openclaw-user"),
                timeout_seconds=float(
                    os.getenv("MEMOS_TIMEOUT_SECONDS", "30")
                ),
                app_id=os.getenv("MEMOS_APP_ID", "a2a-coordination"),
                allow_public=os.getenv("MEMOS_ALLOW_PUBLIC", "0").strip().lower()
                in {"1", "true", "yes"},
                async_mode=os.getenv("MEMOS_ASYNC_MODE", "0").strip().lower()
                in {"1", "true", "yes"},
            )
        )
    worker = TaskQueueWorker(
        broker_url,
        registry,
        prefix=os.getenv("A2A_QUEUE_PREFIX", "a2a.task"),
        partitions=int(os.getenv("A2A_QUEUE_PARTITIONS", "8")),
        lease_seconds=float(os.getenv("A2A_WORKER_LEASE_SECONDS", "1200")),
        experience_collector=experience_collector,
    )
    try:
        await worker.start()
        await asyncio.Future()
    finally:
        await worker.close()


def main() -> None:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
    try:
        asyncio.run(run_worker())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
