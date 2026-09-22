from __future__ import annotations

import hashlib
import json
import re

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import Any


VALID_ACTIONS = {"CREATE", "UPDATE", "NO_CHANGE", "REVIEW_REQUIRED"}
VALID_VISIBILITIES = {"public", "namespace-only", "private"}
SKILL_FRONTMATTER_KEYS = {
    "name",
    "description",
    "license",
    "allowed-tools",
    "metadata",
}
SEMVER_PATTERN = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$"
)


def utc_now() -> datetime:
    return datetime.now(UTC)


def parse_timestamp(value: Any) -> datetime:
    text = str(value or "").strip()
    if not text:
        return datetime.fromtimestamp(0, UTC)
    if re.fullmatch(r"-?\d+(?:\.\d+)?", text):
        epoch = float(text)
        magnitude = abs(epoch)
        if magnitude >= 1_000_000_000_000_000_000:
            epoch /= 1_000_000_000
        elif magnitude >= 1_000_000_000_000_000:
            epoch /= 1_000_000
        elif magnitude >= 1_000_000_000_000:
            epoch /= 1_000
        try:
            return datetime.fromtimestamp(epoch, UTC)
        except (OSError, OverflowError, ValueError):
            return datetime.fromtimestamp(0, UTC)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return datetime.fromtimestamp(0, UTC)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


@dataclass(frozen=True)
class EvolutionPolicy:
    policy_id: str
    enabled: bool
    interval_seconds: int
    task_name: str
    task_description: str
    evidence_query: str
    relevance_criteria: tuple[str, ...]
    minimum_new_memories: int
    target_skill: str
    namespace: str
    visibility: str = "namespace-only"
    manager_agent_id: str = "main"
    max_memories: int = 50
    run_on_startup: bool = True
    auto_publish: bool = False
    fetch_current_skill: bool = True
    allowed_extensions: tuple[str, ...] = (
        ".md",
        ".json",
        ".txt",
        ".yaml",
        ".yml",
    )

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> EvolutionPolicy:
        task_profile = payload.get("taskProfile", {})
        if not isinstance(task_profile, dict):
            raise ValueError("taskProfile must be an object")
        policy = cls(
            policy_id=str(payload.get("id", "")).strip(),
            enabled=bool(payload.get("enabled", True)),
            interval_seconds=int(payload.get("intervalSeconds", 21600)),
            task_name=str(task_profile.get("name", "")).strip(),
            task_description=str(
                task_profile.get("description", "")
            ).strip(),
            evidence_query=str(
                task_profile.get("evidenceQuery", "")
            ).strip(),
            relevance_criteria=tuple(
                str(value).strip()
                for value in task_profile.get("relevanceCriteria", [])
                if str(value).strip()
            ),
            minimum_new_memories=int(payload.get("minimumNewMemories", 5)),
            target_skill=str(payload.get("targetSkill", "")).strip(),
            namespace=str(payload.get("namespace", "")).strip(),
            visibility=str(
                payload.get("visibility", "namespace-only")
            ).strip(),
            manager_agent_id=str(payload.get("managerAgentId", "main")).strip(),
            max_memories=int(payload.get("maxMemories", 50)),
            run_on_startup=bool(payload.get("runOnStartup", True)),
            auto_publish=bool(payload.get("autoPublish", False)),
            fetch_current_skill=bool(payload.get("fetchCurrentSkill", True)),
            allowed_extensions=tuple(
                str(value).strip().lower()
                for value in payload.get(
                    "allowedExtensions",
                    [".md", ".json", ".txt", ".yaml", ".yml"],
                )
                if str(value).strip()
            ),
        )
        policy.validate()
        return policy

    def validate(self) -> None:
        if not self.policy_id:
            raise ValueError("policy id is required")
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", self.policy_id):
            raise ValueError(f"invalid policy id: {self.policy_id}")
        if self.interval_seconds < 30:
            raise ValueError("intervalSeconds must be at least 30")
        if self.minimum_new_memories < 1:
            raise ValueError("minimumNewMemories must be at least 1")
        if self.max_memories < self.minimum_new_memories:
            raise ValueError(
                "maxMemories must be greater than or equal to minimumNewMemories"
            )
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", self.target_skill):
            raise ValueError(f"invalid targetSkill: {self.target_skill}")
        if not self.task_name:
            raise ValueError("taskProfile.name is required")
        if not self.task_description:
            raise ValueError("taskProfile.description is required")
        if not self.evidence_query:
            raise ValueError("taskProfile.evidenceQuery is required")
        if not self.namespace:
            raise ValueError("namespace is required")
        if self.visibility not in VALID_VISIBILITIES:
            raise ValueError(f"unsupported visibility: {self.visibility}")
        if not self.manager_agent_id:
            raise ValueError("managerAgentId is required")


@dataclass(frozen=True)
class MemoRecord:
    name: str
    content: str
    creator: str
    create_time: str
    update_time: str
    tags: tuple[str, ...]

    def evidence_payload(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "creator": self.creator,
            "createTime": self.create_time,
            "updateTime": self.update_time,
            "tags": list(self.tags),
            "content": self.content,
        }


@dataclass(frozen=True)
class SkillPackage:
    files: dict[str, bytes]
    version: str = ""

    def prompt_files(self, *, maximum_text_bytes: int = 100_000) -> dict[str, str]:
        visible: dict[str, str] = {}
        for path, content in sorted(self.files.items()):
            if len(content) > maximum_text_bytes:
                visible[path] = (
                    f"[PRESERVED RESOURCE: {len(content)} bytes; content omitted]"
                )
                continue
            try:
                visible[path] = content.decode("utf-8")
            except UnicodeDecodeError:
                visible[path] = (
                    f"[PRESERVED BINARY RESOURCE: {len(content)} bytes]"
                )
        return visible


def evidence_hash(records: list[MemoRecord]) -> str:
    payload = [record.evidence_payload() for record in records]
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class SkillCandidate:
    action: str
    reason: str
    version: str
    files: dict[str, str]

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> SkillCandidate:
        raw_files = payload.get("files", {})
        if not isinstance(raw_files, dict):
            raise ValueError("candidate files must be an object")
        candidate = cls(
            action=str(payload.get("action", "")).strip().upper(),
            reason=str(payload.get("reason", "")).strip(),
            version=str(payload.get("version", "")).strip(),
            files={str(path): str(content) for path, content in raw_files.items()},
        )
        if candidate.action not in VALID_ACTIONS:
            raise ValueError(f"unsupported candidate action: {candidate.action}")
        if not candidate.reason:
            raise ValueError("candidate reason is required")
        if candidate.action in {"CREATE", "UPDATE"}:
            if not SEMVER_PATTERN.fullmatch(candidate.version):
                raise ValueError("CREATE or UPDATE requires a semantic version")
            if "SKILL.md" not in candidate.files:
                raise ValueError("CREATE or UPDATE requires SKILL.md")
        return candidate

    def validate_files(
        self,
        *,
        target_skill: str,
        allowed_extensions: tuple[str, ...],
        maximum_files: int = 32,
        maximum_total_bytes: int = 1_000_000,
    ) -> None:
        if len(self.files) > maximum_files:
            raise ValueError(f"candidate contains more than {maximum_files} files")
        total_bytes = 0
        for raw_path, content in self.files.items():
            normalized = raw_path.replace("\\", "/")
            path = PurePosixPath(normalized)
            if path.is_absolute() or ".." in path.parts or not path.parts:
                raise ValueError(f"unsafe candidate path: {raw_path}")
            suffix = path.suffix.lower()
            if raw_path != "SKILL.md" and suffix not in allowed_extensions:
                raise ValueError(f"candidate file type is not allowed: {raw_path}")
            total_bytes += len(content.encode("utf-8"))
        if total_bytes > maximum_total_bytes:
            raise ValueError("candidate package is too large")
        if self.action not in {"CREATE", "UPDATE"}:
            return
        skill_markdown = self.files["SKILL.md"]
        if not skill_markdown.startswith("---\n"):
            raise ValueError("SKILL.md must start with YAML frontmatter")
        try:
            frontmatter = skill_markdown.split("---", 2)[1]
        except IndexError as exc:
            raise ValueError("SKILL.md frontmatter is incomplete") from exc
        name_match = re.search(r"(?m)^name:\s*['\"]?([^'\"\n]+)", frontmatter)
        description_match = re.search(
            r"(?m)^description:\s*['\"]?([^'\"\n]+)",
            frontmatter,
        )
        if not name_match or name_match.group(1).strip() != target_skill:
            raise ValueError(f"SKILL.md name must be {target_skill}")
        if not description_match or not description_match.group(1).strip():
            raise ValueError("SKILL.md description is required")
        top_level_keys = set(
            re.findall(r"(?m)^([A-Za-z][A-Za-z0-9-]*):", frontmatter)
        )
        unexpected = top_level_keys - SKILL_FRONTMATTER_KEYS
        if unexpected:
            raise ValueError(
                "SKILL.md contains unsupported frontmatter fields: "
                + ", ".join(sorted(unexpected))
            )
        skill_name = name_match.group(1).strip()
        if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", skill_name):
            raise ValueError("SKILL.md name must use lowercase hyphen-case")
        description = description_match.group(1).strip()
        if len(description) > 1024 or "<" in description or ">" in description:
            raise ValueError("SKILL.md description is not valid")
        if re.search(r"(?m)^[ ]{0,3}\[TODO:[^\n]*\][ \t]*$", skill_markdown):
            raise ValueError("SKILL.md contains an unfinished TODO placeholder")
