from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json

from .errors import ContractError


@dataclass(frozen=True)
class StageDefinition:
    id: str
    title: str
    agent: str
    schema: str
    output_artifact: str
    depends_on: tuple[str, ...]
    knowledge_tags: tuple[str, ...]
    deterministic_checks: tuple[str, ...]
    approval_required: bool = True

    @classmethod
    def from_dict(cls, value: dict) -> "StageDefinition":
        return cls(
            id=value["id"],
            title=value["title"],
            agent=value["agent"],
            schema=value["schema"],
            output_artifact=value["output_artifact"],
            depends_on=tuple(value.get("depends_on", [])),
            knowledge_tags=tuple(value.get("knowledge_tags", [])),
            deterministic_checks=tuple(value.get("deterministic_checks", [])),
            approval_required=bool(value.get("approval_required", True)),
        )


@dataclass(frozen=True)
class PipelineDefinition:
    id: str
    version: str
    stages: tuple[StageDefinition, ...]

    @classmethod
    def load(cls, path: Path) -> "PipelineDefinition":
        value = json.loads(path.read_text(encoding="utf-8"))
        stages = tuple(StageDefinition.from_dict(item) for item in value["stages"])
        ids = [item.id for item in stages]
        if len(ids) != len(set(ids)):
            raise ContractError("pipeline contains duplicate stage ids")
        known: set[str] = set()
        for stage in stages:
            missing = set(stage.depends_on) - known
            if missing:
                raise ContractError(
                    f"stage {stage.id} depends on unknown or later stages: {sorted(missing)}"
                )
            known.add(stage.id)
        return cls(value["id"], value["version"], stages)

    def stage(self, stage_id: str) -> StageDefinition:
        for stage in self.stages:
            if stage.id == stage_id:
                return stage
        raise ContractError(f"unknown stage: {stage_id}")

