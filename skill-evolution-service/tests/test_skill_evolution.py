from __future__ import annotations

import json
import io
import re
import zipfile

from datetime import UTC, datetime
from pathlib import Path

import pytest
import httpx

from skill_evolution.config import load_policies
from skill_evolution.manager import build_manager_prompt, parse_candidate_response
from skill_evolution.memos import MemosClient, latest_checkpoint, parse_checkpoint
from skill_evolution.models import (
    EvolutionPolicy,
    MemoRecord,
    SkillCandidate,
    SkillPackage,
    parse_timestamp,
)
from skill_evolution.package import ensure_skill_frontmatter, write_candidate_package
from skill_evolution.service import SkillEvolutionService
from skill_evolution.skillhub import SkillHubClient
from skill_evolution.store import EvolutionStore


def make_policy(**overrides) -> EvolutionPolicy:
    payload = {
        "id": "report-evolution",
        "enabled": True,
        "intervalSeconds": 60,
        "taskProfile": {
            "name": "Evidence-based report writing",
            "description": "Create a report from supplied source evidence.",
            "evidenceQuery": (
                "Find user feedback and execution experience about creating "
                "evidence-based reports, including research coverage, "
                "analysis quality, structure, and conclusions."
            ),
            "relevanceCriteria": [
                "The experience concerns evidence-based report creation."
            ],
        },
        "minimumNewMemories": 1,
        "maxMemories": 10,
        "targetSkill": "report-writer",
        "namespace": "company",
        "autoPublish": True,
        "fetchCurrentSkill": True,
    }
    payload.update(overrides)
    return EvolutionPolicy.from_payload(payload)


def make_memory() -> MemoRecord:
    return MemoRecord(
        name="memos/1",
        content="#skill-learning #report Repeated evidence.",
        creator="users/1",
        create_time="2026-08-20T00:00:00Z",
        update_time="2026-08-20T01:00:00Z",
        tags=("report", "skill-learning"),
    )


def test_memos_tags_and_compound_checkpoint() -> None:
    record = MemosClient._record({
        "name": "memos/2",
        "content": "#skill-learning #report reusable lesson",
        "creator": "users/1",
        "createTime": "2026-08-20T00:00:00Z",
        "updateTime": "2026-08-20T01:00:00Z",
    })
    checkpoint = latest_checkpoint([record])
    timestamp, name = parse_checkpoint(checkpoint)
    assert timestamp == datetime(2026, 8, 20, 1, tzinfo=UTC)
    assert name == "memos/2"
    assert record.tags == ("report", "skill-learning")


def test_parse_timestamp_accepts_memos_epoch_milliseconds() -> None:
    parsed = parse_timestamp(1787214973247)

    assert parsed == datetime(2026, 8, 20, 8, 36, 13, 247000, tzinfo=UTC)
    assert parse_timestamp("1787214973247") == parsed
    assert parse_timestamp("invalid") == datetime.fromtimestamp(0, UTC)


@pytest.mark.asyncio
async def test_memos_cloud_search_uses_task_query_without_tag_filter() -> None:
    captured: dict = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["authorization"] = request.headers.get("authorization")
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={
            "code": 0,
            "data": {
                "memory_detail_list": [
                    {
                        "memory_key": "memory-1",
                        "memory_value": (
                            "#skill-learning #report Improve source coverage."
                        ),
                        "tags": ["skill-learning", "report"],
                        "user_id": "a2a-user",
                    },
                    {
                        "memory_key": "memory-2",
                        "memory_value": "Unrelated memory.",
                        "tags": [],
                    },
                ]
            },
            "message": "ok",
        })

    client = MemosClient(
        "https://memos.example/api/openmem/v1",
        "mpg-test-token",
        "a2a-user",
        transport=httpx.MockTransport(handler),
    )

    records = await client.list_since(
        checkpoint="ignored-by-cloud-search",
        evidence_query="Find experience about evidence-based report writing.",
        maximum_records=10,
    )

    assert [record.name for record in records] == ["memory-1", "memory-2"]
    assert captured["url"].endswith("/api/openmem/v1/search/memory")
    assert captured["authorization"] == "Token mpg-test-token"
    assert captured["body"]["user_id"] == "a2a-user"
    assert captured["body"]["query"] == (
        "Find experience about evidence-based report writing."
    )
    assert captured["body"]["memory_limit_number"] == 10


def test_load_policies_and_validate_unique_ids(tmp_path: Path) -> None:
    policy_path = tmp_path / "policies.json"
    policy_path.write_text(
        json.dumps({"policies": [
            {
                "id": "report-evolution",
                "enabled": True,
                "intervalSeconds": 60,
                "taskProfile": {
                    "name": "Report writing",
                    "description": "Create an evidence-based report.",
                    "evidenceQuery": "Find report-writing experience.",
                },
                "minimumNewMemories": 1,
                "targetSkill": "report-writer",
                "namespace": "company",
            }
        ]}),
        encoding="utf-8",
    )
    policies = load_policies(policy_path)
    assert policies[0].target_skill == "report-writer"


def test_store_lease_checkpoint_and_completion(tmp_path: Path) -> None:
    store = EvolutionStore(tmp_path / "state.sqlite3")
    policy = make_policy()
    now = datetime(2026, 8, 20, tzinfo=UTC)
    run_id = store.claim(policy, now=now)
    assert run_id
    assert store.claim(policy, now=now) is None
    store.complete(
        policy,
        run_id,
        outcome="NO_CHANGE",
        evidence_count=1,
        evidence_digest="abc",
        checkpoint="2026-08-20T01:00:00Z",
        now=now,
    )
    assert store.checkpoint(policy.policy_id) == "2026-08-20T01:00:00Z"
    assert store.last_evidence_hash(policy.policy_id) == "abc"
    assert store.states()[0]["last_outcome"] == "NO_CHANGE"


def test_store_recovers_running_lease_after_scheduler_stops(tmp_path: Path) -> None:
    store = EvolutionStore(tmp_path / "state.sqlite3")
    policy = make_policy()
    now = datetime(2026, 8, 20, tzinfo=UTC)
    assert store.claim(policy, now=now)

    recovered = store.recover_running(reason="operator restart", now=now)

    assert recovered == 1
    state = store.states()[0]
    assert state["status"] == "IDLE"
    assert state["lease_until"] is None
    assert state["last_error"] == "operator restart"
    assert store.claim(policy, now=now, force=True)


def test_manager_candidate_json_and_package_validation() -> None:
    candidate = parse_candidate_response(
        """```json
        {
          "action": "UPDATE",
          "reason": "Repeated evidence improves the workflow.",
          "version": "1.1.0",
          "files": {
            "SKILL.md": "---\\nname: report-writer\\ndescription: Write reports from supplied evidence.\\n---\\n\\n# Workflow\\n"
          }
        }
        ```"""
    )
    candidate.validate_files(
        target_skill="report-writer",
        allowed_extensions=(".md", ".json"),
    )
    with pytest.raises(ValueError, match="unsafe candidate path"):
        SkillCandidate(
            action="UPDATE",
            reason="bad path",
            version="1.1.0",
            files={
                "SKILL.md": candidate.files["SKILL.md"],
                "../secret.md": "bad",
            },
        ).validate_files(
            target_skill="report-writer",
            allowed_extensions=(".md",),
        )


def test_missing_skill_frontmatter_is_supplied_from_policy() -> None:
    candidate = SkillCandidate(
        action="CREATE",
        reason="Create a reusable workflow.",
        version="1.0.0",
        files={"SKILL.md": "# Workflow\n\nUse verified evidence.\n"},
    )

    normalized = ensure_skill_frontmatter(
        candidate,
        target_skill="report-writer",
        description="Create evidence-based reports.",
    )

    assert normalized.files["SKILL.md"].startswith(
        "---\n"
        "name: report-writer\n"
        'description: "Create evidence-based reports."\n'
        "---\n\n"
    )
    normalized.validate_files(
        target_skill="report-writer",
        allowed_extensions=(".md",),
    )


def test_existing_skill_frontmatter_preserves_description_and_optional_fields() -> None:
    candidate = SkillCandidate(
        action="UPDATE",
        reason="Apply repeated improvements.",
        version="1.2.0",
        files={
            "SKILL.md": (
                "---\n"
                "name: stale-name\n"
                "description: Updated report workflow.\n"
                "version: 0.9.0\n"
                "license: MIT\n"
                "---\n\n"
                "# Workflow\n"
            )
        },
    )

    normalized = ensure_skill_frontmatter(
        candidate,
        target_skill="report-writer",
        description="Create evidence-based reports.",
    )

    assert normalized.files["SKILL.md"].startswith(
        "---\n"
        "name: report-writer\n"
        "description: Updated report workflow.\n"
        "license: MIT\n"
        "---\n"
    )
    assert "version:" not in normalized.files["SKILL.md"]


def test_update_package_overlays_changes_without_deleting_resources(
    tmp_path: Path,
) -> None:
    policy = make_policy()
    candidate = ensure_skill_frontmatter(
        SkillCandidate(
            action="UPDATE",
            reason="Apply a supported improvement.",
            version="1.1.0",
            files={
                "SKILL.md": (
                    "---\n"
                    "name: report-writer\n"
                    "description: Updated reports.\n"
                    "license: MIT\n"
                    "---\n\n"
                    "# Updated workflow\n"
                )
            },
        ),
        target_skill=policy.target_skill,
        description=policy.task_description,
    )

    package = write_candidate_package(
        tmp_path,
        run_id="evo-preserve",
        policy=policy,
        candidate=candidate,
        memories=[make_memory()],
        evidence_digest="abc123",
        base_files={
            "SKILL.md": b"old instructions",
            "agents/openai.yaml": b"interface:\n  display_name: Report Writer\n",
            "scripts/render.py": b"print('render')\n",
            "references/style.md": b"# Style\n",
            "assets/logo.png": b"\x89PNG\r\n\x1a\n\xff",
        },
    )

    assert "# Updated workflow" in (package / "SKILL.md").read_text("utf-8")
    assert (package / "agents" / "openai.yaml").read_bytes().startswith(b"interface:")
    assert (package / "scripts" / "render.py").read_bytes() == b"print('render')\n"
    assert (package / "references" / "style.md").read_bytes() == b"# Style\n"
    assert (package / "assets" / "logo.png").read_bytes().endswith(b"\xff")


def test_manager_prompt_separates_process_and_task_improvement_evidence() -> None:
    prompt = build_manager_prompt(make_policy(), [make_memory()], {})

    assert "executionProcess" in prompt
    assert "taskImprovement" in prompt
    assert "operational evidence" in prompt
    assert "Agent self-reflection is evidence rather than authority" in prompt
    assert "skillId fields, and tags are provenance only" in prompt
    assert "Evidence-based report writing" in prompt
    assert "creating evidence-based reports" in prompt
    assert "immutable baseline" in prompt
    assert "omitted files are preserved automatically" in prompt
    assert "Deletion is unsupported" in prompt
    assert "Do not put version in SKILL.md frontmatter" in prompt


def zip_package(files: dict[str, bytes]) -> bytes:
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        for path, content in files.items():
            archive.writestr(path, content)
    return stream.getvalue()


@pytest.mark.asyncio
async def test_skillhub_api_fetch_preserves_complete_package(
    tmp_path: Path,
) -> None:
    package = {
        "SKILL.md": (
            b"---\nname: report-writer\ndescription: Reports.\n"
            b"version: 1.1.0\n---\n"
        ),
        "references/details.md": b"# Details\n",
        "assets/icon.png": b"\x89PNG\r\n\x1a\n\xff",
    }
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/resolve"):
            return httpx.Response(200, json={"data": {"version": "1.1.0"}})
        return httpx.Response(
            200,
            content=zip_package(package),
            headers={"content-type": "application/zip"},
        )

    client = SkillHubClient(
        "http://skillhub.example",
        transport=httpx.MockTransport(handler),
    )

    files = await client.fetch_current(
        namespace="docker-admin",
        skill="report-writer",
        destination=tmp_path,
    )

    assert files == SkillPackage(package, version="1.1.0")
    assert files.prompt_files()["assets/icon.png"].startswith(
        "[PRESERVED BINARY RESOURCE:"
    )
    assert [request.url.path for request in requests] == [
        "/api/v1/skills/docker-admin/report-writer/resolve",
        "/api/v1/skills/docker-admin/report-writer/versions/1.1.0/download",
    ]


@pytest.mark.asyncio
async def test_skillhub_api_fetch_treats_registry_400_not_found_as_missing(
    tmp_path: Path,
) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"code": 400, "msg": "Skill not found: x"})

    client = SkillHubClient(
        "http://skillhub.example",
        transport=httpx.MockTransport(handler),
    )

    result = await client.fetch_current(
        namespace="global",
        skill="missing-skill",
        destination=tmp_path,
    )

    assert result is None


@pytest.mark.asyncio
async def test_skillhub_api_publish_uploads_zip_and_verifies_result(
    tmp_path: Path,
) -> None:
    (tmp_path / "SKILL.md").write_text(
        "---\nname: report-writer\ndescription: Reports.\n---\n\n# Reports\n",
        encoding="utf-8",
    )
    (tmp_path / "references").mkdir()
    (tmp_path / "references" / "details.md").write_bytes(b"# Details\n")
    published: dict[str, bytes] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer test-token"
        if request.method == "POST":
            body = request.content
            match = re.search(
                rb"PK\x03\x04.*",
                body,
                flags=re.DOTALL,
            )
            assert match is not None
            # The multipart trailer follows the ZIP end marker, which ZipFile ignores.
            published.update(SkillHubClient._unpack_archive(match.group(0)))
            assert b'NAMESPACE_ONLY' in body
            return httpx.Response(200, json={"data": {
                "namespace": "docker-admin",
                "slug": "report-writer",
                "version": "1.2.0",
                "visibility": "NAMESPACE_ONLY",
            }})
        return httpx.Response(200, content=zip_package(published))

    client = SkillHubClient(
        "http://skillhub.example",
        token="test-token",
        transport=httpx.MockTransport(handler),
    )

    response = await client.publish(
        tmp_path,
        namespace="docker-admin",
        skill="report-writer",
        version="1.2.0",
        visibility="namespace-only",
    )

    assert response["version"] == "1.2.0"
    assert b"version: 1.2.0" in published["SKILL.md"]
    assert published["references/details.md"] == b"# Details\n"
    assert "version: 1.2.0" not in (tmp_path / "SKILL.md").read_text(
        encoding="utf-8"
    )


@pytest.mark.asyncio
async def test_skillhub_api_publish_requires_token(
    tmp_path: Path,
) -> None:
    client = SkillHubClient("http://skillhub.example")

    with pytest.raises(RuntimeError, match="SKILLHUB_API_TOKEN is required"):
        await client.publish(
            tmp_path,
            namespace="docker-admin",
            skill="report-writer",
            version="1.2.0",
            visibility="private",
        )
class FakeMemos:
    async def list_since(self, **_kwargs):
        return [make_memory()]


class FakeManager:
    async def propose(self, *_args, **_kwargs):
        return SkillCandidate(
            action="CREATE",
            reason="The evidence supports a reusable report workflow.",
            version="1.0.0",
            files={
                "SKILL.md": (
                    "---\n"
                    "name: report-writer\n"
                    "description: Write reports from structured evidence.\n"
                    "---\n\n"
                    "# Report Writer\n\nUse the supplied evidence.\n"
                )
            },
        )


class FakeSkillHub:
    def __init__(self) -> None:
        self.published: list[Path] = []

    async def fetch_current(self, **_kwargs):
        return None

    async def publish(self, package_path: Path, **_kwargs):
        self.published.append(package_path)
        return {"ok": True}


@pytest.mark.asyncio
async def test_service_calls_manager_and_publishes_candidate(tmp_path: Path) -> None:
    policy = make_policy()
    policy_path = tmp_path / "policies.json"
    policy_path.write_text(
        json.dumps({"policies": [{
            "id": policy.policy_id,
            "enabled": True,
            "intervalSeconds": policy.interval_seconds,
            "taskProfile": {
                "name": policy.task_name,
                "description": policy.task_description,
                "evidenceQuery": policy.evidence_query,
                "relevanceCriteria": list(policy.relevance_criteria),
            },
            "minimumNewMemories": policy.minimum_new_memories,
            "maxMemories": policy.max_memories,
            "targetSkill": policy.target_skill,
            "namespace": policy.namespace,
            "autoPublish": True,
        }]}),
        encoding="utf-8",
    )
    store = EvolutionStore(tmp_path / "state.sqlite3")
    skillhub = FakeSkillHub()
    service = SkillEvolutionService(
        policy_path=policy_path,
        store=store,
        memos=FakeMemos(),
        manager=FakeManager(),
        skillhub=skillhub,
        candidate_root=tmp_path / "candidates",
    )
    assert await service.tick(force_policy=policy.policy_id) == 1
    state = store.states()[0]
    assert state["last_outcome"] == "PUBLISHED"
    assert state["last_published_version"] == "1.0.0"
    assert len(skillhub.published) == 1
    assert (skillhub.published[0] / "SKILL.md").exists()
    assert (skillhub.published[0] / "provenance.json").exists()
