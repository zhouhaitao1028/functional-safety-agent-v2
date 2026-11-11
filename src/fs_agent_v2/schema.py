from __future__ import annotations

from pathlib import Path
import json

from .errors import ContractError


class SchemaRegistry:
    """Small, dependency-free JSON Schema subset for pipeline contracts."""

    def __init__(self, root: Path):
        self.root = root

    def load(self, schema_name: str) -> dict:
        return json.loads((self.root / schema_name).read_text(encoding="utf-8"))

    def validate(self, schema_name: str, value: object) -> None:
        schema = self.load(schema_name)
        errors: list[str] = []
        self._validate_node(schema, value, "$", errors)
        if errors:
            raise ContractError("structured artifact validation failed:\n- " + "\n- ".join(errors))

    def _validate_node(self, schema: dict, value: object, path: str, errors: list[str]) -> None:
        expected = schema.get("type")
        type_map = {
            "object": dict,
            "array": list,
            "string": str,
            "integer": int,
            "number": (int, float),
            "boolean": bool,
        }
        if expected in type_map and not isinstance(value, type_map[expected]):
            errors.append(f"{path}: expected {expected}, got {type(value).__name__}")
            return
        if "enum" in schema and value not in schema["enum"]:
            errors.append(f"{path}: {value!r} is not one of {schema['enum']}")
        if isinstance(value, str) and len(value) < int(schema.get("minLength", 0)):
            errors.append(f"{path}: string is shorter than {schema['minLength']}")
        if isinstance(value, list):
            if len(value) < int(schema.get("minItems", 0)):
                errors.append(f"{path}: requires at least {schema['minItems']} items")
            item_schema = schema.get("items")
            if item_schema:
                for index, item in enumerate(value):
                    self._validate_node(item_schema, item, f"{path}[{index}]", errors)
        if isinstance(value, dict):
            for key in schema.get("required", []):
                if key not in value:
                    errors.append(f"{path}: missing required property {key!r}")
            for key, child_schema in schema.get("properties", {}).items():
                if key in value:
                    self._validate_node(child_schema, value[key], f"{path}.{key}", errors)

