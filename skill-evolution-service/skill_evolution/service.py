from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import tempfile

from pathlib import Path

from skill_evolution.config import load_policies
from skill_evolution.manager import OpenClawManagerClient
from skill_evolution.memos import MemosClient, latest_checkpoint
from skill_evolution.models import EvolutionPolicy, SkillPackage, evidence_hash
from skill_evolution.package import ensure_skill_frontmatter, write_candidate_package
from skill_evolution.skillhub import SkillHubClient
from skill_evolution.store import EvolutionStore


logger = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parents[2]


def project_path(value: str, default: Path) -> Path:
    selected = Path(value) if value.strip() else default
    return selected if selected.is_absolute() else PROJECT_ROOT / selected


class SkillEvolutionService:
    def __init__(
        self,
        *,
        policy_path: Path,
        store: EvolutionStore,
        memos: MemosClient,
        manager: OpenClawManagerClient,
        skillhub: SkillHubClient,
        candidate_root: Path,
        lease_seconds: int = 3600,
    ) -> None:
        self.policy_path = policy_path
        self.store = store
        self.memos = memos
        self.manager = manager
        self.skillhub = skillhub
        self.candidate_root = candidate_root
        self.lease_seconds = lease_seconds

    @classmethod
    def from_environment(cls) -> SkillEvolutionService:
        memos_url = os.getenv(
            "MEMOS_BASE_URL",
            "https://memos.memtensor.cn/api/openmem/v1",
        )
        policy_path = project_path(
            os.getenv("SKILL_EVOLUTION_POLICY_FILE", ""),
            PROJECT_ROOT / "config" / "skill-evolution-policies.json",
        )
        return cls(
            policy_path=policy_path,
            store=EvolutionStore(
                project_path(
                    os.getenv("SKILL_EVOLUTION_DB", ""),
                    PROJECT_ROOT / ".runtime" / "skill-evolution.sqlite3",
                )
            ),
            memos=MemosClient(
                memos_url,
                os.getenv("MEMOS_API_KEY", "")
                or os.getenv("MEMOS_TOKEN", ""),
                os.getenv("MEMOS_USER_ID", "openclaw-user"),
                timeout_seconds=float(os.getenv("MEMOS_TIMEOUT_SECONDS", "30")),
            ),
            manager=OpenClawManagerClient.from_environment(),
            skillhub=SkillHubClient.from_environment(),
            candidate_root=project_path(
                os.getenv("SKILL_EVOLUTION_CANDIDATE_DIR", ""),
                PROJECT_ROOT / ".runtime" / "skill-candidates",
            ),
            lease_seconds=int(os.getenv("SKILL_EVOLUTION_LEASE_SECONDS", "3600")),
        )

    async def run_policy(self, policy: EvolutionPolicy, run_id: str) -> None:
        checkpoint = self.store.checkpoint(policy.policy_id)
        try:
            memories = await self.memos.list_since(
                checkpoint=checkpoint,
                evidence_query=policy.evidence_query,
                maximum_records=policy.max_memories,
            )
            if len(memories) < policy.minimum_new_memories:
                self.store.complete(
                    policy,
                    run_id,
                    outcome="WAITING_FOR_EVIDENCE",
                    evidence_count=len(memories),
                )
                logger.info(
                    "Skill evolution policy=%s waiting for evidence count=%s required=%s",
                    policy.policy_id,
                    len(memories),
                    policy.minimum_new_memories,
                )
                return
            digest = evidence_hash(memories)
            new_checkpoint = latest_checkpoint(memories)
            if digest == self.store.last_evidence_hash(policy.policy_id):
                self.store.complete(
                    policy,
                    run_id,
                    outcome="DUPLICATE_EVIDENCE",
                    evidence_count=len(memories),
                    evidence_digest=digest,
                    checkpoint=new_checkpoint,
                )
                logger.info(
                    "Skill evolution policy=%s outcome=DUPLICATE_EVIDENCE "
                    "evidence_count=%s",
                    policy.policy_id,
                    len(memories),
                )
                return
            current_package: SkillPackage | None = None
            if policy.fetch_current_skill:
                with tempfile.TemporaryDirectory(prefix="skill-evolution-current-") as temp:
                    current_package = await self.skillhub.fetch_current(
                        namespace=policy.namespace,
                        skill=policy.target_skill,
                        destination=Path(temp),
                    )
            current_skill = (
                current_package.prompt_files() if current_package is not None else {}
            )
            candidate = await self.manager.propose(
                policy,
                memories,
                current_skill,
                digest,
            )
            if candidate.action == "NO_CHANGE":
                self.store.complete(
                    policy,
                    run_id,
                    outcome="NO_CHANGE",
                    evidence_count=len(memories),
                    evidence_digest=digest,
                    checkpoint=new_checkpoint,
                )
                logger.info(
                    "Skill evolution policy=%s outcome=NO_CHANGE "
                    "evidence_count=%s",
                    policy.policy_id,
                    len(memories),
                )
                return
            if candidate.action == "REVIEW_REQUIRED" and not candidate.files:
                self.store.complete(
                    policy,
                    run_id,
                    outcome="REVIEW_REQUIRED",
                    evidence_count=len(memories),
                    evidence_digest=digest,
                    checkpoint=new_checkpoint,
                )
                logger.info(
                    "Skill evolution policy=%s outcome=REVIEW_REQUIRED "
                    "evidence_count=%s",
                    policy.policy_id,
                    len(memories),
                )
                return
            if candidate.action == "CREATE" and current_package is not None:
                raise RuntimeError(
                    "Manager tried to CREATE an existing Skill; use UPDATE so "
                    "the original package remains the baseline"
                )
            if candidate.action == "UPDATE" and current_package is None:
                raise RuntimeError(
                    "Manager tried to UPDATE without a complete original Skill package"
                )
            candidate = ensure_skill_frontmatter(
                candidate,
                target_skill=policy.target_skill,
                description=policy.task_description,
            )
            candidate.validate_files(
                target_skill=policy.target_skill,
                allowed_extensions=policy.allowed_extensions,
            )
            package_path = write_candidate_package(
                self.candidate_root,
                run_id=run_id,
                policy=policy,
                candidate=candidate,
                memories=memories,
                evidence_digest=digest,
                base_files=(
                    current_package.files
                    if candidate.action == "UPDATE" and current_package is not None
                    else None
                ),
            )
            outcome = "DRAFT_CREATED"
            published_version = ""
            if policy.auto_publish and candidate.action in {"CREATE", "UPDATE"}:
                await self.skillhub.publish(
                    package_path,
                    namespace=policy.namespace,
                    skill=policy.target_skill,
                    version=candidate.version,
                    visibility=policy.visibility,
                )
                outcome = "PUBLISHED"
                published_version = candidate.version
            elif candidate.action == "REVIEW_REQUIRED":
                outcome = "REVIEW_REQUIRED"
            self.store.complete(
                policy,
                run_id,
                outcome=outcome,
                evidence_count=len(memories),
                evidence_digest=digest,
                checkpoint=new_checkpoint,
                candidate_path=str(package_path),
                published_version=published_version,
            )
            logger.info(
                "Skill evolution policy=%s outcome=%s candidate=%s",
                policy.policy_id,
                outcome,
                package_path,
            )
        except Exception as exc:
            self.store.fail(policy, run_id, str(exc))
            logger.exception(
                "Skill evolution failed policy=%s run=%s",
                policy.policy_id,
                run_id,
            )

    async def tick(self, *, force_policy: str = "") -> int:
        policies = load_policies(self.policy_path)
        selected = [
            policy
            for policy in policies
            if policy.enabled and (not force_policy or policy.policy_id == force_policy)
        ]
        if force_policy and not selected:
            raise RuntimeError(f"enabled policy was not found: {force_policy}")
        count = 0
        for policy in selected:
            run_id = self.store.claim(
                policy,
                lease_seconds=self.lease_seconds,
                force=bool(force_policy),
            )
            if run_id is None:
                continue
            count += 1
            logger.info(
                "Skill evolution claimed policy=%s run=%s",
                policy.policy_id,
                run_id,
            )
            await self.run_policy(policy, run_id)
        return count

    async def run_forever(self) -> None:
        poll_seconds = float(os.getenv("SKILL_EVOLUTION_POLL_SECONDS", "15"))
        logger.info("Skill evolution scheduler started policy=%s", self.policy_path)
        while True:
            try:
                await self.tick()
            except Exception:
                logger.exception("Skill evolution scheduler tick failed")
            await asyncio.sleep(max(poll_seconds, 1))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run scheduled Manager skill evolution")
    parser.add_argument("--once", action="store_true", help="run one scheduler tick")
    parser.add_argument(
        "--policy",
        default="",
        help="force one enabled policy immediately (implies --once)",
    )
    parser.add_argument(
        "--show-state",
        action="store_true",
        help="print persisted policy state and exit",
    )
    parser.add_argument(
        "--recover-running",
        action="store_true",
        help=(
            "release RUNNING leases left by a stopped scheduler; stop the "
            "scheduler process before using this option"
        ),
    )
    return parser


async def async_main(args: argparse.Namespace) -> int:
    service = SkillEvolutionService.from_environment()
    if args.show_state:
        print(json.dumps(service.store.states(), ensure_ascii=False, indent=2))
        return 0
    if args.recover_running:
        recovered = service.store.recover_running()
        print(json.dumps({"recoveredRuns": recovered}, ensure_ascii=False))
        return 0
    if args.once or args.policy:
        count = await service.tick(force_policy=args.policy)
        logger.info("Skill evolution tick completed jobs=%s", count)
        return 0
    await service.run_forever()
    return 0


def main() -> None:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    args = build_parser().parse_args()
    try:
        raise SystemExit(asyncio.run(async_main(args)))
    except KeyboardInterrupt:
        raise SystemExit(130) from None
