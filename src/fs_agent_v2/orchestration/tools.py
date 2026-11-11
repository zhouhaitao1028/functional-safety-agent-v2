from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING
from collections import Counter
import zipfile

from ..agents import AgentContext, AgentDefinition
from ..errors import ContractError

if TYPE_CHECKING:
    from .dynamic_engine import DynamicWorkEngine


WIRE_NAMES = {
    "knowledge.retrieve_evidence": "knowledge_retrieve_evidence",
    "knowledge.graph": "knowledge_graph",
    "artifact.read": "artifact_read",
    "artifact.list": "artifact_list",
    "agent_registry.list": "agent_registry_list",
    "source.inspect": "source_inspect",
}
CANONICAL_NAMES = {wire: canonical for canonical, wire in WIRE_NAMES.items()}


@dataclass(frozen=True)
class ToolExecution:
    result: object
    evidence_refs: tuple[str, ...] = ()


class AgentToolbox:
    """Allowlisted, read-only tools exposed to a model tool-calling loop."""

    def __init__(
        self,
        engine: "DynamicWorkEngine",
        project_id: str,
        agent: AgentDefinition,
        context: AgentContext,
        *,
        allow_draft_knowledge: bool = False,
    ):
        self.engine = engine
        self.project_id = project_id
        self.agent = agent
        self.context = context
        self.allow_draft_knowledge = allow_draft_knowledge
        self.allowed = set(agent.tools)
        self.graph_nodes: set[str] = set()

    def definitions(self) -> list[dict]:
        definitions = []
        for canonical in self.agent.tools:
            if canonical not in WIRE_NAMES:
                raise ContractError(
                    f"agent {self.agent.id} requests an unavailable runtime tool: {canonical}"
                )
            definition = self._definition(canonical)
            if canonical == "knowledge.retrieve_evidence":
                allowed_tags = sorted(self.agent.knowledge_scopes)
                if allowed_tags:
                    function = definition["function"]
                    function["parameters"]["properties"]["tags"]["items"][
                        "enum"
                    ] = allowed_tags
                    function["description"] += (
                        " The tags argument may contain only these values: "
                        + ", ".join(allowed_tags)
                        + ". Omit tags to search all knowledge permitted to this Agent."
                    )
            definitions.append(definition)
        return definitions

    def execute(self, wire_name: str, arguments: dict) -> ToolExecution:
        canonical = CANONICAL_NAMES.get(wire_name)
        if canonical is None or canonical not in self.allowed:
            raise ContractError(f"model attempted unavailable tool: {wire_name}")
        if canonical == "knowledge.retrieve_evidence":
            return self._retrieve(arguments)
        if canonical == "knowledge.graph":
            return self._graph(arguments)
        if canonical == "artifact.read":
            return self._artifact_read(arguments)
        if canonical == "artifact.list":
            return ToolExecution(
                {
                    "available_input_refs": sorted(self.context.inputs),
                    "project_id": self.project_id,
                }
            )
        if canonical == "agent_registry.list":
            return ToolExecution(
                [
                    {
                        "id": item.id,
                        "title": item.title,
                        "kind": item.kind,
                        "business": item.business,
                        "consumes": item.consumes,
                        "optional_consumes": item.optional_consumes,
                        "produces": item.produces,
                        "tools": item.tools,
                        "approval_required": item.approval_required,
                        "automatic_executor": self.engine.automatic_executor(item.id),
                    }
                    for item in self.engine.registry.list()
                    if item.role != "manager"
                ]
            )
        if canonical == "source.inspect":
            return self._source_inspect(arguments)
        raise ContractError(f"tool is declared but not implemented: {canonical}")

    def _source_inspect(self, arguments: dict) -> ToolExecution:
        reference_id = arguments.get("reference_id")
        if not isinstance(reference_id, str) or reference_id not in self.context.inputs:
            raise ContractError(
                f"source reference is not an allowed Manager input: {reference_id}"
            )
        source = self.context.inputs[reference_id]
        location = source.get("location", "")
        if not isinstance(location, str) or not location.strip():
            raise ContractError(f"source input {reference_id} has no location")
        path = Path(location)
        if not path.is_absolute():
            path = self.engine.root / path
        try:
            path = path.resolve(strict=True)
        except FileNotFoundError as exc:
            raise ContractError(f"source input does not exist: {location}") from exc
        result: dict = {
            "reference_id": reference_id,
            "declared_type": source.get("type"),
            "resolved_path": str(path),
            "kind": "directory" if path.is_dir() else "file",
        }
        if path.is_file():
            result.update({"suffix": path.suffix.lower(), "size_bytes": path.stat().st_size})
            if path.suffix.lower() == ".zip":
                try:
                    with zipfile.ZipFile(path) as archive:
                        members = [item for item in archive.infolist() if not item.is_dir()]
                        suffixes = Counter(Path(item.filename).suffix.lower() for item in members)
                        result["zip_summary"] = {
                            "files": len(members),
                            "uncompressed_bytes": sum(item.file_size for item in members),
                            "suffix_counts": dict(sorted(suffixes.items())),
                            "sample_members": [item.filename for item in members[:20]],
                        }
                except zipfile.BadZipFile as exc:
                    raise ContractError(f"source input is not a valid ZIP: {location}") from exc
        else:
            children = sorted(path.iterdir(), key=lambda item: item.name.lower())
            result["directory_summary"] = {
                "direct_children": len(children),
                "sample_names": [item.name for item in children[:20]],
            }
        return ToolExecution(result)

    def _retrieve(self, arguments: dict) -> ToolExecution:
        query = arguments.get("query")
        if not isinstance(query, str) or not query.strip():
            raise ContractError("knowledge query must be a non-empty string")
        requested_tags = arguments.get("tags", [])
        if not isinstance(requested_tags, list) or not all(
            isinstance(tag, str) for tag in requested_tags
        ):
            raise ContractError("knowledge tags must be an array of strings")
        allowed_tags = set(self.agent.knowledge_scopes)
        tags = set(requested_tags) & allowed_tags if requested_tags else allowed_tags
        if requested_tags and not tags:
            raise ContractError(
                "model requested knowledge tags outside the Agent's allowed scopes"
            )
        # Knowledge approval policy is a runtime/user decision, not a model
        # decision. The model may narrow the query and allowed tags, but it
        # cannot elevate or accidentally suppress the draft-access policy
        # selected for this run.
        approved_only = not self.allow_draft_knowledge
        limit = arguments.get("limit", 8)
        if not isinstance(limit, int):
            raise ContractError("knowledge limit must be an integer")
        pack = self.engine.knowledge_service.retrieve(
            query.strip(),
            tags=tags,
            approved_only=approved_only,
            limit=max(1, min(limit, 12)),
            graph_depth=1,
        )
        refs = tuple(
            f"{item['knowledge_id']}::{item['chunk_id']}" for item in pack["evidence"]
        )
        self.graph_nodes.update(item["knowledge_id"] for item in pack["evidence"])
        for edge in pack["graph_context"]:
            self.graph_nodes.add(edge["source_id"])
            self.graph_nodes.add(edge["target_id"])
        return ToolExecution(pack, refs)

    def _graph(self, arguments: dict) -> ToolExecution:
        node_id = arguments.get("node_id")
        if not isinstance(node_id, str) or not node_id.strip():
            raise ContractError("graph node_id must be a non-empty string")
        if node_id.strip() not in self.graph_nodes:
            raise ContractError(
                "graph traversal must start from a node returned by knowledge retrieval"
            )
        depth = arguments.get("depth", 1)
        if not isinstance(depth, int):
            raise ContractError("graph depth must be an integer")
        result = self.engine.knowledge_service.index.graph_neighbors(
            node_id.strip(), max(1, min(depth, 3))
        )
        for edge in result:
            self.graph_nodes.add(edge["source_id"])
            self.graph_nodes.add(edge["target_id"])
        return ToolExecution(result)

    def _artifact_read(self, arguments: dict) -> ToolExecution:
        reference_id = arguments.get("reference_id")
        if not isinstance(reference_id, str) or reference_id not in self.context.inputs:
            raise ContractError(
                f"artifact reference is not an allowed work-item input: {reference_id}"
            )
        return ToolExecution(
            {"reference_id": reference_id, "data": self.context.inputs[reference_id]}
        )

    @staticmethod
    def _definition(canonical: str) -> dict:
        definitions = {
            "knowledge.retrieve_evidence": {
                "description": "Search functional-safety knowledge and return a versioned Evidence Pack. Whether draft evidence is eligible is fixed by runtime policy and cannot be changed by the model.",
                "parameters": {
                    "type": "object",
                    "required": ["query"],
                    "properties": {
                        "query": {"type": "string"},
                        "tags": {"type": "array", "items": {"type": "string"}},
                        "limit": {"type": "integer"},
                    },
                },
            },
            "knowledge.graph": {
                "description": "Traverse guide, template, business-area and knowledge relationships from a known node ID.",
                "parameters": {
                    "type": "object",
                    "required": ["node_id"],
                    "properties": {
                        "node_id": {"type": "string"},
                        "depth": {"type": "integer"},
                    },
                },
            },
            "artifact.read": {
                "description": "Read one allowlisted input artifact of the current work item.",
                "parameters": {
                    "type": "object",
                    "required": ["reference_id"],
                    "properties": {"reference_id": {"type": "string"}},
                },
            },
            "artifact.list": {
                "description": "List the input artifact references available to this work item.",
                "parameters": {"type": "object", "properties": {}},
            },
            "agent_registry.list": {
                "description": "List registered business agents and their structured input/output capabilities.",
                "parameters": {"type": "object", "properties": {}},
            },
            "source.inspect": {
                "description": "Inspect metadata and a bounded directory/ZIP manifest for one allowlisted source input. This tool is read-only and never executes files or macros.",
                "parameters": {
                    "type": "object",
                    "required": ["reference_id"],
                    "properties": {"reference_id": {"type": "string"}},
                },
            },
        }
        value = definitions[canonical]
        return {
            "type": "function",
            "function": {
                "name": WIRE_NAMES[canonical],
                "description": value["description"],
                "parameters": value["parameters"],
            },
        }
