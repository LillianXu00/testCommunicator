from __future__ import annotations

from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.tasks import TaskUpdater
from a2a.helpers import get_message_text, new_task_from_user_message, new_text_message, new_text_part
from a2a.types import TaskState

from a2a_server.backends.base import AgentBackend


class PlannerAgentExecutor(AgentExecutor):
    def __init__(self, backend: AgentBackend) -> None:
        self.backend = backend

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        message = context.message
        if message is None:
            raise ValueError("A2A request does not contain a message")

        task = context.current_task or new_task_from_user_message(message)
        if context.current_task is None:
            await event_queue.enqueue_event(task)
        updater = TaskUpdater(event_queue, task.id, task.context_id)

        prompt = get_message_text(message).strip()
        if not prompt:
            await updater.update_status(
                TaskState.TASK_STATE_INPUT_REQUIRED,
                new_text_message("Please provide a report-generation task."),
            )
            return

        await updater.update_status(
            TaskState.TASK_STATE_WORKING,
            new_text_message("Planner is processing the task."),
        )
        try:
            result = await self.backend.invoke(prompt, task.context_id)
            await updater.add_artifact(parts=[new_text_part(text=result, media_type="text/plain")])
            await updater.update_status(
                TaskState.TASK_STATE_COMPLETED,
                new_text_message("Planner completed the task."),
            )
        except Exception as exc:
            await updater.update_status(
                TaskState.TASK_STATE_FAILED,
                new_text_message(f"Planner backend failed: {exc}"),
            )

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        if context.current_task is None:
            return
        updater = TaskUpdater(event_queue, context.current_task.id, context.current_task.context_id)
        await updater.cancel()
