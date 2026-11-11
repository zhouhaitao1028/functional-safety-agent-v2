from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class AgentContext:
    project_id: str
    stage_id: str
    inputs: dict[str, dict]
    knowledge: tuple[dict, ...]
    instructions: str
    skills: tuple[str, ...] = ()
    graph_context: tuple[dict, ...] = ()


@dataclass(frozen=True)
class AgentResult:
    proposal: dict
    rationale: str = ""
    evidence_refs: tuple[str, ...] = ()


class StageAgent(Protocol):
    """Provider-neutral interface: an agent only proposes structured data."""

    name: str

    def propose(self, context: AgentContext) -> AgentResult:
        ...

