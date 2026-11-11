from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json
import re

from ..agents import AgentRegistry
from ..errors import ContractError


SAFE_ID = re.compile(r"[A-Za-z0-9_-]{1,80}")


@dataclass(frozen=True)
class WorkInput:
    id: str
    type: str
    location: str = ""


@dataclass(frozen=True)
class WorkOutput:
    id: str
    type: str
    artifact: str


@dataclass(frozen=True)
class WorkItem:
    id: str
    title: str
    agent_id: str
    depends_on: tuple[str, ...]
    input_refs: tuple[str, ...]
    output: WorkOutput
    parameters: dict
    approval_required: bool


@dataclass(frozen=True)
class WorkPlan:
    id: str
    version: str
    goal: str
    inputs: tuple[WorkInput, ...]
    work_items: tuple[WorkItem, ...]
    raw: dict

    @classmethod
    def load(cls, path: Path, registry: AgentRegistry) -> "WorkPlan":
        return cls.from_dict(json.loads(path.read_text(encoding="utf-8")), registry)

    @classmethod
    def from_dict(cls, value: dict, registry: AgentRegistry) -> "WorkPlan":
        inputs = tuple(
            WorkInput(item["id"], item["type"], item.get("location", ""))
            for item in value["inputs"]
        )
        work_items = tuple(
            WorkItem(
                id=item["id"],
                title=item["title"],
                agent_id=item["agent_id"],
                depends_on=tuple(item["depends_on"]),
                input_refs=tuple(item["input_refs"]),
                output=WorkOutput(
                    item["output"]["id"],
                    item["output"]["type"],
                    item["output"]["artifact"],
                ),
                parameters=dict(item.get("parameters", {})),
                approval_required=bool(item["approval_required"]),
            )
            for item in value["work_items"]
        )
        plan = cls(value["id"], value["version"], value["goal"], inputs, work_items, value)
        plan.validate(registry)
        return plan

    def item(self, item_id: str) -> WorkItem:
        for item in self.work_items:
            if item.id == item_id:
                return item
        raise ContractError(f"unknown work item: {item_id}")

    def validate(self, registry: AgentRegistry) -> None:
        self._validate_unique_ids()
        item_map = {item.id: item for item in self.work_items}
        for item in self.work_items:
            if not SAFE_ID.fullmatch(item.id):
                raise ContractError(f"unsafe work item id: {item.id}")
            missing = set(item.depends_on) - item_map.keys()
            if missing:
                raise ContractError(f"work item {item.id} has unknown dependencies: {sorted(missing)}")
            if item.id in item.depends_on:
                raise ContractError(f"work item {item.id} cannot depend on itself")
            if Path(item.output.artifact).name != item.output.artifact or "\\" in item.output.artifact:
                raise ContractError(f"work item {item.id} has unsafe artifact filename")
        self._topological_order(item_map)

        reference_types = {item.id: item.type for item in self.inputs}
        reference_producers: dict[str, str] = {}
        for item in self.work_items:
            if item.output.id in reference_types:
                raise ContractError(f"duplicate input/output reference id: {item.output.id}")
            reference_types[item.output.id] = item.output.type
            reference_producers[item.output.id] = item.id

        for item in self.work_items:
            agent = registry.get(item.agent_id)
            if agent.id == "software_interface_engineer":
                operation = item.parameters.get("operation", "analyze_package")
                if operation not in {
                    "analyze_package",
                    "generate_overview",
                    "compare_overviews",
                }:
                    raise ContractError(
                        f"software interface work item {item.id} has unsupported operation: {operation}"
                    )
            if agent.output_schema and not item.output.artifact.lower().endswith(".json"):
                raise ContractError(
                    f"work item {item.id} structured artifact must use a .json filename"
                )
            if item.output.type not in agent.produces:
                raise ContractError(
                    f"agent {agent.id} cannot produce {item.output.type} for {item.id}"
                )
            if agent.approval_required and not item.approval_required:
                raise ContractError(f"work item {item.id} cannot disable its required human gate")
            ancestors = self._ancestors(item.id, item_map)
            dependency_outputs = {
                item_map[dependency_id].output.id
                for dependency_id in item.depends_on
            }
            missing_dependency_inputs = dependency_outputs - set(item.input_refs)
            if missing_dependency_inputs:
                raise ContractError(
                    f"work item {item.id} depends on upstream work but does not consume "
                    f"its outputs: {sorted(missing_dependency_inputs)}"
                )
            actual_input_types: set[str] = set()
            actual_input_type_list: list[str] = []
            for reference in item.input_refs:
                if reference not in reference_types:
                    raise ContractError(f"work item {item.id} has unknown input ref: {reference}")
                producer = reference_producers.get(reference)
                if producer and producer not in ancestors:
                    raise ContractError(
                        f"work item {item.id} uses {reference} without depending on {producer}"
                    )
                input_type = reference_types[reference]
                actual_input_types.add(input_type)
                actual_input_type_list.append(input_type)
                allowed_input_types = set(agent.consumes) | set(
                    agent.optional_consumes
                )
                if "*" not in agent.consumes and input_type not in allowed_input_types:
                    raise ContractError(
                        f"agent {agent.id} cannot consume {input_type} for {item.id}"
                    )
            required_input_types = set(agent.consumes) - {"*"}
            missing_input_types = required_input_types - actual_input_types
            if missing_input_types:
                raise ContractError(
                    f"work item {item.id} is missing agent inputs: {sorted(missing_input_types)}"
                )
            if agent.id == "software_interface_engineer":
                if operation in {"analyze_package", "generate_overview"}:
                    if (
                        actual_input_type_list.count("software_interface_package") != 1
                        or len(actual_input_type_list) != 1
                    ):
                        raise ContractError(
                            f"software interface {operation} item {item.id} requires exactly "
                            "one software_interface_package input"
                        )
                elif operation == "compare_overviews":
                    comparable = sum(
                        input_type
                        in {"interface_overview", "software_interface_task_result"}
                        for input_type in actual_input_type_list
                    )
                    if comparable != 2 or len(actual_input_type_list) != 2:
                        raise ContractError(
                            f"software interface compare item {item.id} requires exactly two "
                            "interface_overview/software_interface_task_result inputs"
                        )

    def _validate_unique_ids(self) -> None:
        input_ids = [item.id for item in self.inputs]
        item_ids = [item.id for item in self.work_items]
        output_ids = [item.output.id for item in self.work_items]
        artifact_names = [item.output.artifact for item in self.work_items]
        for label, values in (
            ("input ids", input_ids),
            ("work item ids", item_ids),
            ("output ids", output_ids),
            ("output artifact names", artifact_names),
        ):
            if len(values) != len(set(values)):
                raise ContractError(f"workplan contains duplicate {label}")

    @staticmethod
    def _topological_order(item_map: dict[str, WorkItem]) -> list[str]:
        pending = {item_id: set(item.depends_on) for item_id, item in item_map.items()}
        completed: list[str] = []
        while pending:
            ready = sorted(item_id for item_id, deps in pending.items() if not deps)
            if not ready:
                raise ContractError("workplan contains a dependency cycle")
            for item_id in ready:
                pending.pop(item_id)
                completed.append(item_id)
            for dependencies in pending.values():
                dependencies.difference_update(ready)
        return completed

    @classmethod
    def _ancestors(cls, item_id: str, item_map: dict[str, WorkItem]) -> set[str]:
        output: set[str] = set()
        pending = list(item_map[item_id].depends_on)
        while pending:
            dependency = pending.pop()
            if dependency in output:
                continue
            output.add(dependency)
            pending.extend(item_map[dependency].depends_on)
        return output
