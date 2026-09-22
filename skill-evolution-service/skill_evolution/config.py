from __future__ import annotations

import json

from pathlib import Path

from skill_evolution.models import EvolutionPolicy


def load_policies(path: str | Path) -> list[EvolutionPolicy]:
    source = Path(path)
    payload = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("policies"), list):
        raise ValueError("policy file must contain a policies array")
    policies = [
        EvolutionPolicy.from_payload(item)
        for item in payload["policies"]
        if isinstance(item, dict)
    ]
    policy_ids = [policy.policy_id for policy in policies]
    if len(policy_ids) != len(set(policy_ids)):
        raise ValueError("policy ids must be unique")
    return policies
