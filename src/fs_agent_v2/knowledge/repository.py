from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json

from ..errors import KnowledgeError


@dataclass(frozen=True)
class KnowledgeEntry:
    id: str
    version: str
    status: str
    source: str
    tags: tuple[str, ...]
    content: object
    path: Path

    @classmethod
    def load(cls, path: Path) -> "KnowledgeEntry":
        value = json.loads(path.read_text(encoding="utf-8"))
        metadata = value.get("metadata", {})
        required = {"id", "version", "status", "source", "tags"}
        missing = required - set(metadata)
        if missing:
            raise KnowledgeError(f"{path}: missing knowledge metadata {sorted(missing)}")
        return cls(
            id=metadata["id"],
            version=metadata["version"],
            status=metadata["status"],
            source=metadata["source"],
            tags=tuple(metadata["tags"]),
            content=value.get("content", value.get("matrix")),
            path=path,
        )


class KnowledgeRepository:
    def __init__(self, root: Path):
        self.root = root

    def list(self) -> tuple[KnowledgeEntry, ...]:
        entries: list[KnowledgeEntry] = []
        for path in sorted(self.root.rglob("*.json")):
            entries.append(KnowledgeEntry.load(path))
        ids = [entry.id for entry in entries]
        if len(ids) != len(set(ids)):
            raise KnowledgeError("knowledge base contains duplicate ids")
        return tuple(entries)

    def search(self, tags: set[str], approved_only: bool = False) -> tuple[KnowledgeEntry, ...]:
        result = []
        for entry in self.list():
            if tags and not tags.intersection(entry.tags):
                continue
            if approved_only and entry.status != "approved":
                continue
            result.append(entry)
        return tuple(result)

