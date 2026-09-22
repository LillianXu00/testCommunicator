from __future__ import annotations

import logging

from a2a.helpers import get_message_text, new_task_from_user_message, new_text_message, new_text_part
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.tasks import TaskUpdater
from a2a.types import TaskState

from a2a_server.backends.factory import BackendFactory
from a2a_server.queueing import TaskCommand, TaskDispatcher
from a2a_server.registry_client import (
    RegistryClient,
)


logger = logging.getLogger(__name__)

class RegistryGatewayExecutor(AgentExecutor):
    def __init__(
        self,
        registry: RegistryClient,
        backend_factory: BackendFactory | None = None,
        task_dispatcher: TaskDispatcher | None = None,
    ) -> None:
        self.registry = registry
        self.backend_factory = backend_factory or BackendFactory()
        self.task_dispatcher = task_dispatcher

    async def _report(
        self,
        *,
        agent_id: str,
        task_id: str,
        context_id: str,
        state: TaskState,
        message: str,
        phase: str,
        sequence_no: int,
    ) -> None:
        state_name = TaskState.Name(state)
        try:
            await self.registry.report_task_event(
                agent_id=agent_id,
                task_id=task_id,
                context_id=context_id,
                state=state_name,
                message=message,
                event_id=f"task:{agent_id}:{task_id}:{sequence_no}",
                task_sequence=sequence_no,
                metadata={"phase": phase, "taskSequence": sequence_no},
            )
        except Exception as exc:
            logger.warning(
                "Could not report task event agent=%s task=%s state=%s: %s",
                agent_id,
                task_id,
                state_name,
                exc,
            )

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        message = context.message
        if message is None:
            raise ValueError("A2A request does not contain a message")

        task = context.current_task or new_task_from_user_message(message)
        if context.current_task is None:
            await event_queue.enqueue_event(task)
        updater = TaskUpdater(event_queue, task.id, task.context_id)

        agent_id = str(context.call_context.state.get("agent_id", "")).strip()
        if agent_id:
            await self._report(
                agent_id=agent_id,
                task_id=task.id,
                context_id=task.context_id,
                state=TaskState.TASK_STATE_SUBMITTED,
                message="Task accepted by the managed A2A Gateway.",
                phase="accepted",
                sequence_no=1,
            )
        record = await self.registry.get(agent_id) if agent_id else None
        if record is None:
            if agent_id:
                await self._report(
                    agent_id=agent_id,
                    task_id=task.id,
                    context_id=task.context_id,
                    state=TaskState.TASK_STATE_REJECTED,
                    message=f"No active managed agent is registered at '{agent_id}'.",
                    phase="rejected",
                    sequence_no=2,
                )
            await updater.update_status(
                TaskState.TASK_STATE_REJECTED,
                new_text_message(
                    f"No active managed agent is registered at '{agent_id}'."
                ),
            )
            return
        if record.definition.integration_mode != "managed-adapter":
            await self._report(
                agent_id=agent_id,
                task_id=task.id,
                context_id=task.context_id,
                state=TaskState.TASK_STATE_REJECTED,
                message="Native A2A agents must be called through their provider interface.",
                phase="rejected",
                sequence_no=2,
            )
            await updater.update_status(
                TaskState.TASK_STATE_REJECTED,
                new_text_message(
                    "Native A2A agents must be called through the interface in "
                    "their provider Agent Card."
                ),
            )
            return

        prompt = get_message_text(message).strip()
        if not prompt:
            await self._report(
                agent_id=agent_id,
                task_id=task.id,
                context_id=task.context_id,
                state=TaskState.TASK_STATE_INPUT_REQUIRED,
                message="Please provide a task.",
                phase="waiting-for-input",
                sequence_no=2,
            )
            await updater.update_status(
                TaskState.TASK_STATE_INPUT_REQUIRED,
                new_text_message("Please provide a task."),
            )
            return

        working_message = (
            f"{record.definition.name} is processing the task."
            if self.task_dispatcher is None
            else f"{record.definition.name} task is queued for execution."
        )
        if self.task_dispatcher is None:
            await self._report(
                agent_id=agent_id,
                task_id=task.id,
                context_id=task.context_id,
                state=TaskState.TASK_STATE_WORKING,
                message=working_message,
                phase="agent-execution",
                sequence_no=2,
            )
        await updater.update_status(
            TaskState.TASK_STATE_WORKING,
            new_text_message(working_message),
        )
        try:
            if self.task_dispatcher is None:
                backend = self.backend_factory.create(record.definition)
                result = await backend.invoke(prompt, task.context_id)
            else:
                execution = await self.task_dispatcher.dispatch(
                    TaskCommand.create(
                        task_id=task.id,
                        context_id=task.context_id,
                        agent_id=agent_id,
                        prompt=prompt,
                    )
                )
                if execution.state != "COMPLETED":
                    raise RuntimeError(
                        execution.error or "Queued agent execution failed"
                    )
                result = execution.text
            await updater.add_artifact(parts=[new_text_part(text=result, media_type="text/plain")])
            completed_message = f"{record.definition.name} completed the task."
            await self._report(
                agent_id=agent_id,
                task_id=task.id,
                context_id=task.context_id,
                state=TaskState.TASK_STATE_COMPLETED,
                message=completed_message,
                phase="completed",
                sequence_no=3,
            )
            await updater.update_status(
                TaskState.TASK_STATE_COMPLETED,
                new_text_message(completed_message),
            )
        except Exception as exc:
            failed_message = f"Agent backend failed: {exc}"
            await self._report(
                agent_id=agent_id,
                task_id=task.id,
                context_id=task.context_id,
                state=TaskState.TASK_STATE_FAILED,
                message=failed_message,
                phase="failed",
                sequence_no=3,
            )
            await updater.update_status(
                TaskState.TASK_STATE_FAILED,
                new_text_message(failed_message),
            )

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        if context.current_task is None:
            return
        updater = TaskUpdater(event_queue, context.current_task.id, context.current_task.context_id)
        await updater.cancel()
        agent_id = str(context.call_context.state.get("agent_id", "")).strip()
        if agent_id:
            await self._report(
                agent_id=agent_id,
                task_id=context.current_task.id,
                context_id=context.current_task.context_id,
                state=TaskState.TASK_STATE_CANCELED,
                message="Task canceled.",
                phase="canceled",
                sequence_no=4,
            )
