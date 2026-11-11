from __future__ import annotations

from ..errors import ContractError


class FaultTreeCalculator:
    """Calculate minimal cut sets from a finite AND/OR fault tree."""

    def calculate(self, artifact: dict) -> list[list[str]]:
        nodes = {node["id"]: node for node in artifact["nodes"]}
        top = artifact["top_event_id"]
        if top not in nodes:
            raise ContractError(f"fault tree top event does not exist: {top}")
        visiting: set[str] = set()
        result = self._expand(top, nodes, visiting)
        minimized = self._minimize(result)
        return [sorted(item) for item in sorted(minimized, key=lambda item: (len(item), sorted(item)))]

    def enrich(self, artifact: dict) -> dict:
        output = dict(artifact)
        output["minimal_cut_sets"] = self.calculate(artifact)
        return output

    def _expand(self, node_id: str, nodes: dict[str, dict], visiting: set[str]) -> list[frozenset[str]]:
        if node_id in visiting:
            raise ContractError(f"fault tree contains a cycle at {node_id}")
        try:
            node = nodes[node_id]
        except KeyError as exc:
            raise ContractError(f"fault tree references missing node {node_id}") from exc
        node_type = node["type"]
        if node_type == "BASIC":
            return [frozenset({node_id})]
        children = node.get("children", [])
        if not children:
            raise ContractError(f"gate {node_id} has no children")
        visiting.add(node_id)
        child_sets = [self._expand(child, nodes, visiting) for child in children]
        visiting.remove(node_id)
        if node_type == "OR":
            return [item for group in child_sets for item in group]
        if node_type == "AND":
            combined = [frozenset()]
            for group in child_sets:
                combined = [left | right for left in combined for right in group]
            return combined
        raise ContractError(f"unsupported fault tree node type: {node_type}")

    @staticmethod
    def _minimize(cut_sets: list[frozenset[str]]) -> list[frozenset[str]]:
        unique = sorted(set(cut_sets), key=len)
        result: list[frozenset[str]] = []
        for candidate in unique:
            if not any(existing <= candidate for existing in result):
                result.append(candidate)
        return result

