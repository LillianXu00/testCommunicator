from __future__ import annotations

import json

import httpx
import pytest

from a2a_server.experience import (
    ExperienceCollector,
    MemosExperienceSink,
    TaskExperience,
    build_reflection_prompt,
)
from a2a_server.queueing import ExperienceCommand


def make_command() -> ExperienceCommand:
    return ExperienceCommand.create(
        task_id="task-report-1",
        context_id="context-report-1",
        agent_id="planner",
        prompt="Write an insight report for the risk committee.",
        result_text="A report with facts, analysis and recommendations.",
    )


def experience_json() -> str:
    return json.dumps({
        "summary": "The report was completed but source handling can improve.",
        "taskType": "insight report writing",
        "skillId": "made-up-id",
        "executionProcess": {
            "problems": [{
                "stage": "source collection",
                "problem": "One supplied source had no publication date.",
                "impact": "Chronology required an explicit unknown marker.",
                "resolution": "The report marked the date as unavailable.",
            }],
            "effectiveActions": ["Separated facts from analysis."],
            "unresolvedIssues": [],
        },
        "taskImprovement": {
            "strengths": ["Recommendations were tied to identified risks."],
            "improvements": [{
                "area": "evidence coverage",
                "currentLimitation": "One claim used only one source.",
                "recommendation": "Cross-check material claims with two sources.",
                "expectedBenefit": "The report will be easier to audit.",
            }],
            "nextTimePlan": ["Build a source matrix before drafting."],
        },
        "confidence": 0.8,
    })


def test_experience_has_process_and_task_improvement_sections() -> None:
    experience = TaskExperience.from_text(
        experience_json(),
        allowed_skill_ids=("forward-report",),
    )

    assert experience.skill_id == "forward-report"
    assert experience.process_problems[0]["stage"] == "source collection"
    assert experience.improvements[0]["area"] == "evidence coverage"
    assert experience.next_time_plan == ("Build a source matrix before drafting.",)


def test_reflection_prompt_forbids_reexecution_and_requires_two_sections() -> None:
    prompt = build_reflection_prompt(
        make_command(),
        ({"id": "forward-report", "name": "Report", "description": "Writes reports."},),
    )

    assert "Do not execute the task again" in prompt
    assert "executionProcess" in prompt
    assert "taskImprovement" in prompt
    assert "Never invent hidden tool calls or failures" in prompt


class FakeBackend:
    def __init__(self) -> None:
        self.context_id = ""

    async def invoke(self, _prompt: str, context_id: str) -> str:
        self.context_id = context_id
        return experience_json()


class FakeSink:
    def __init__(self) -> None:
        self.experience: TaskExperience | None = None

    async def write(self, _command, experience, _skills) -> str:
        self.experience = experience
        return "memos/experience-1"


@pytest.mark.asyncio
async def test_collector_uses_isolated_context_and_writes_structured_memo() -> None:
    backend = FakeBackend()
    sink = FakeSink()
    collector = ExperienceCollector(sink)  # type: ignore[arg-type]

    experience, memo_name = await collector.collect(
        make_command(),
        backend,
        ({"id": "forward-report", "tags": ["insight-report"]},),
    )

    assert backend.context_id == "experience:task-report-1"
    assert sink.experience == experience
    assert memo_name == "memos/experience-1"


@pytest.mark.asyncio
async def test_memos_cloud_sink_uses_token_auth_and_add_message_contract() -> None:
    captured: dict = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["authorization"] = request.headers.get("authorization")
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "code": 0,
                "data": {"message_id": "cloud-message-1"},
                "message": "ok",
            },
        )

    sink = MemosExperienceSink(
        "https://memos.example/api/openmem/v1",
        "mpg-test-token",
        "a2a-user",
        app_id="a2a-app",
        transport=httpx.MockTransport(handler),
    )
    experience = TaskExperience.from_text(
        experience_json(),
        allowed_skill_ids=("forward-report",),
    )

    name = await sink.write(
        make_command(),
        experience,
        ({"id": "forward-report", "tags": ["insight-report"]},),
    )

    assert name == "cloud-message-1"
    assert captured["url"].endswith("/api/openmem/v1/add/message")
    assert captured["authorization"] == "Token mpg-test-token"
    assert captured["body"]["user_id"] == "a2a-user"
    assert captured["body"]["conversation_id"] == (
        "experience:planner:task-report-1"
    )
    assert captured["body"]["async_mode"] is False
    assert "insight-report" in captured["body"]["tags"]
