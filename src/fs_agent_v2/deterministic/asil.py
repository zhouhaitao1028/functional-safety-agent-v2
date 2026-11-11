from __future__ import annotations

from pathlib import Path
import json

from ..errors import ContractError


class AsilCalculator:
    """Deterministic ASIL lookup backed by an independently versioned rule file."""

    def __init__(self, matrix_path: Path):
        document = json.loads(matrix_path.read_text(encoding="utf-8"))
        self.metadata = document["metadata"]
        self.matrix = document["matrix"]

    def calculate(self, severity: str, exposure: str, controllability: str) -> str:
        key = f"{severity}/{exposure}/{controllability}"
        try:
            return self.matrix[key]
        except KeyError as exc:
            raise ContractError(f"ASIL matrix has no entry for {key}") from exc

    def enrich(self, artifact: dict) -> dict:
        output = json.loads(json.dumps(artifact, ensure_ascii=False))
        for assessment in output.get("assessments", []):
            calculated = self.calculate(
                assessment["severity"],
                assessment["exposure"],
                assessment["controllability"],
            )
            proposed = assessment.get("asil")
            if proposed and proposed != calculated:
                raise ContractError(
                    f"{assessment['id']}: proposed ASIL {proposed} conflicts with "
                    f"deterministic result {calculated}"
                )
            assessment["asil"] = calculated
            assessment["asil_rule_version"] = self.metadata["version"]
        return output

