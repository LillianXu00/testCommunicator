from a2a_server.queueing import (
    ExperienceCommand,
    TaskCommand,
    TaskExecutionResult,
    task_partition,
)


def test_task_commands_are_stable_and_round_trip() -> None:
    command = TaskCommand.create(
        task_id="task-123",
        context_id="context-123",
        agent_id="planner",
        prompt="write a report",
    )

    restored = TaskCommand.from_payload(command.to_payload())

    assert restored == command
    assert command.message_id == "execute:planner:task-123"
    assert task_partition(command.task_id, 8) == task_partition(
        command.task_id,
        8,
    )
    assert 0 <= task_partition(command.task_id, 8) < 8


def test_task_results_round_trip() -> None:
    result = TaskExecutionResult(
        message_id="execute:planner:task-123",
        task_id="task-123",
        agent_id="planner",
        state="COMPLETED",
        text="report",
    )

    restored = TaskExecutionResult.from_payload(result.to_payload())

    assert restored == result


def test_experience_commands_are_stable_and_round_trip() -> None:
    command = ExperienceCommand.create(
        task_id="task-123",
        context_id="context-123",
        agent_id="planner",
        prompt="write a report",
        result_text="completed report",
    )

    restored = ExperienceCommand.from_payload(command.to_payload())

    assert restored == command
    assert command.message_id == "experience:planner:task-123"
