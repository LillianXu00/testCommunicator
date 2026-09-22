from __future__ import annotations

import json

from datetime import UTC, datetime
from pathlib import Path

from skill_evolution.models import EvolutionPolicy, MemoRecord, SkillCandidate


def ensure_skill_frontmatter(
    candidate: SkillCandidate,
    *,
    target_skill: str,
    description: str,
) -> SkillCandidate:
    if candidate.action not in {"CREATE", "UPDATE"}:
        return candidate
    files = dict(candidate.files)
    body = files.get("SKILL.md", "").replace("\r\n", "\n")
    body = body.lstrip("\ufeff\n\r ")
    if body.startswith("---\n"):
        closing = body.find("\n---\n", 4)
        if closing < 0:
            raise ValueError("SKILL.md has an unterminated YAML frontmatter block")
        existing = body[4:closing].splitlines()
        existing_description = next(
            (
                line
                for line in existing
                if line.split(":", 1)[0].strip() == "description"
            ),
            "",
        )
        metadata = [
            f"name: {target_skill}",
            existing_description
            or f"description: {json.dumps(description, ensure_ascii=False)}",
        ]
        # Release versions are carried by provenance and the registry, not by
        # the Skill frontmatter. Remove legacy generated version fields.
        controlled = {"name", "description", "version"}
        preserved = [
            line
            for line in existing
            if line.split(":", 1)[0].strip() not in controlled
        ]
        frontmatter = "\n".join([*metadata, *preserved])
        body = f"---\n{frontmatter}\n---\n" + body[closing + 5 :].lstrip("\n")
    else:
        metadata = [
            f"name: {target_skill}",
            f"description: {json.dumps(description, ensure_ascii=False)}",
        ]
        body = "---\n" + "\n".join(metadata) + "\n---\n\n" + body
    files["SKILL.md"] = body
    return SkillCandidate(
        action=candidate.action,
        reason=candidate.reason,
        version=candidate.version,
        files=files,
    )


def write_candidate_package(
    root: Path,
    *,
    run_id: str,
    policy: EvolutionPolicy,
    candidate: SkillCandidate,
    memories: list[MemoRecord],
    evidence_digest: str,
    base_files: dict[str, bytes] | None = None,
) -> Path:
    package_path = root / policy.policy_id / run_id / policy.target_skill
    package_path.mkdir(parents=True, exist_ok=False)
    for relative, content in (base_files or {}).items():
        target = package_path.joinpath(*relative.replace("\\", "/").split("/"))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    for relative, content in candidate.files.items():
        target = package_path.joinpath(*relative.replace("\\", "/").split("/"))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8", newline="\n")
    provenance = {
        "schemaVersion": 1,
        "policyId": policy.policy_id,
        "targetSkill": policy.target_skill,
        "namespace": policy.namespace,
        "action": candidate.action,
        "version": candidate.version,
        "generatedBy": f"openclaw/{policy.manager_agent_id}",
        "generatedAt": datetime.now(UTC).isoformat(),
        "evidenceHash": evidence_digest,
        "sourceMemos": [record.name for record in memories],
        "reason": candidate.reason,
    }
    (package_path / "provenance.json").write_text(
        json.dumps(provenance, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return package_path
