from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TextSection:
    locator: str
    text: str


@dataclass(frozen=True)
class IndexedDocument:
    id: str
    title: str
    source_path: str
    version: str
    status: str
    kind: str
    tags: tuple[str, ...]
    body: str


@dataclass(frozen=True)
class Evidence:
    knowledge_id: str
    chunk_id: str
    title: str
    source_path: str
    locator: str
    version: str
    status: str
    kind: str
    tags: tuple[str, ...]
    content: str
    score: float
    lexical_score: float
    vector_score: float

    def to_dict(self) -> dict:
        return {
            "knowledge_id": self.knowledge_id,
            "chunk_id": self.chunk_id,
            "title": self.title,
            "source_path": self.source_path,
            "locator": self.locator,
            "version": self.version,
            "status": self.status,
            "kind": self.kind,
            "tags": self.tags,
            "content": self.content,
            "score": round(self.score, 6),
            "lexical_score": round(self.lexical_score, 6),
            "vector_score": round(self.vector_score, 6),
        }
