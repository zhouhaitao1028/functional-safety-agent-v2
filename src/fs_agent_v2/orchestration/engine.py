from __future__ import annotations

from pathlib import Path
import json

from ..agents.base import AgentContext, StageAgent
from ..contracts import PipelineDefinition, StageDefinition
from ..deterministic import AsilCalculator, FaultTreeCalculator, TraceabilityValidator
from ..errors import ContractError
from ..knowledge import KnowledgeRepository
from ..schema import SchemaRegistry
from ..storage import ProjectStore


class StageEngine:
    """Optional fixed-recipe runner kept for demos and repeatable standard jobs."""

    def __init__(self, root: Path, project_root: Path | None = None):
        self.root = root
        self.pipeline = PipelineDefinition.load(
            root / "recipes" / "concept_to_fsr.example.json"
        )
        self.schemas = SchemaRegistry(root / "schemas")
        self.knowledge = KnowledgeRepository(root / "knowledge")
        self.projects = ProjectStore(project_root or root / "data" / "projects")
        self.asil = AsilCalculator(root / "knowledge" / "rules" / "asil_matrix.json")
        self.fta = FaultTreeCalculator()
        self.traceability = TraceabilityValidator()

    def submit_proposal(self, project_id: str, stage_id: str, proposal: dict) -> dict:
        stage = self.pipeline.stage(stage_id)
        self.projects.load_project(project_id)
        self._require_dependencies(project_id, stage)
        self.schemas.validate(stage.schema, proposal)
        enriched = self._run_deterministic_checks(project_id, stage, proposal)
        self.schemas.validate(stage.schema, enriched)
        return self.projects.save_artifact(
            project_id, stage.id, stage.output_artifact, enriched
        )

    def run_agent(self, project_id: str, stage_id: str, agent: StageAgent) -> dict:
        """Run any provider adapter, then force its output through the same gates."""
        stage = self.pipeline.stage(stage_id)
        if agent.name != stage.agent:
            raise ContractError(
                f"stage {stage_id} requires agent {stage.agent}, got {agent.name}"
            )
        self._require_dependencies(project_id, stage)
        inputs = {
            dependency: self.projects.load_artifact(
                project_id, self.pipeline.stage(dependency).output_artifact
            )["data"]
            for dependency in stage.depends_on
        }
        knowledge = tuple(
            {
                "id": entry.id,
                "version": entry.version,
                "status": entry.status,
                "source": entry.source,
                "tags": entry.tags,
                "content": entry.content,
            }
            for entry in self.knowledge.search(set(stage.knowledge_tags))
        )
        context = AgentContext(
            project_id=project_id,
            stage_id=stage_id,
            inputs=inputs,
            knowledge=knowledge,
            instructions=(
                "Return only a proposal matching the stage JSON contract. "
                "Do not calculate deterministic fields and do not write files."
            ),
        )
        result = agent.propose(context)
        return self.submit_proposal(project_id, stage_id, result.proposal)

    def stage_status(self, project_id: str) -> list[dict]:
        result = []
        for stage in self.pipeline.stages:
            artifact_path = (
                self.projects.project_dir(project_id) / "artifacts" / stage.output_artifact
            )
            artifact_exists = artifact_path.exists()
            approval = {"approved": False, "reason": "artifact_missing"}
            if artifact_exists:
                approval = self.projects.approval_status(
                    project_id, stage.id, stage.output_artifact
                )
            result.append(
                {
                    "id": stage.id,
                    "title": stage.title,
                    "agent": stage.agent,
                    "artifact_exists": artifact_exists,
                    "approval": approval["reason"],
                    "ready": self._is_ready(project_id, stage),
                }
            )
        return result

    def collect_known_ids(self, project_id: str) -> set[str]:
        known: set[str] = set()
        for stage in self.pipeline.stages:
            path = self.projects.project_dir(project_id) / "artifacts" / stage.output_artifact
            if not path.exists():
                continue
            data = json.loads(path.read_text(encoding="utf-8"))["data"]
            self._collect_ids(data, known)
        return known

    def _require_dependencies(self, project_id: str, stage: StageDefinition) -> None:
        for dependency_id in stage.depends_on:
            dependency = self.pipeline.stage(dependency_id)
            if dependency.approval_required:
                self.projects.require_approved(
                    project_id, dependency.id, dependency.output_artifact
                )
            else:
                self.projects.load_artifact(project_id, dependency.output_artifact)

    def _is_ready(self, project_id: str, stage: StageDefinition) -> bool:
        try:
            self._require_dependencies(project_id, stage)
            return True
        except Exception:
            return False

    def _run_deterministic_checks(
        self, project_id: str, stage: StageDefinition, proposal: dict
    ) -> dict:
        enriched = json.loads(json.dumps(proposal, ensure_ascii=False))
        for check in stage.deterministic_checks:
            if check == "asil_matrix":
                enriched = self.asil.enrich(enriched)
            elif check == "minimal_cut_sets":
                enriched = self.fta.enrich(enriched)
            elif check == "traceability":
                known = self.collect_known_ids(project_id)
                enriched["validation"] = self.traceability.validate(enriched, known)
            elif check == "unique_ids":
                ids: set[str] = set()
                duplicates: set[str] = set()
                self._collect_duplicate_ids(enriched, ids, duplicates)
                if duplicates:
                    raise ContractError(f"duplicate business ids: {sorted(duplicates)}")
            else:
                raise ContractError(f"unknown deterministic check: {check}")
        return enriched

    @classmethod
    def _collect_ids(cls, value: object, output: set[str]) -> None:
        if isinstance(value, dict):
            if isinstance(value.get("id"), str):
                output.add(value["id"])
            for child in value.values():
                cls._collect_ids(child, output)
        elif isinstance(value, list):
            for child in value:
                cls._collect_ids(child, output)

    @classmethod
    def _collect_duplicate_ids(
        cls, value: object, known: set[str], duplicates: set[str]
    ) -> None:
        if isinstance(value, dict):
            item_id = value.get("id")
            if isinstance(item_id, str):
                if item_id in known:
                    duplicates.add(item_id)
                known.add(item_id)
            for child in value.values():
                cls._collect_duplicate_ids(child, known, duplicates)
        elif isinstance(value, list):
            for child in value:
                cls._collect_duplicate_ids(child, known, duplicates)

