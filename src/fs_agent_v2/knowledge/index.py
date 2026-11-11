from __future__ import annotations

from collections import deque
from contextlib import contextmanager
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Iterator
import json
import sqlite3

from .models import Evidence, IndexedDocument, TextSection
from ..errors import KnowledgeError
from .vector import EmbeddingProvider, HashingEmbeddingProvider, cosine, lexical_score


class KnowledgeIndex:
    def __init__(
        self,
        path: Path,
        embedding: EmbeddingProvider | None = None,
        lexical_weight: float = 0.6,
        vector_weight: float = 0.4,
    ):
        self.path = path
        self.embedding = embedding or HashingEmbeddingProvider()
        total_weight = lexical_weight + vector_weight
        if total_weight <= 0:
            raise KnowledgeError("retrieval weights must have a positive sum")
        self.lexical_weight = lexical_weight / total_weight
        self.vector_weight = vector_weight / total_weight
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def reset(self) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM edges")
            connection.execute("DELETE FROM nodes")
            connection.execute("DELETE FROM chunks")
            connection.execute("DELETE FROM documents")
            connection.execute("DELETE FROM index_metadata")

    def upsert_document(self, document: IndexedDocument, sections: list[TextSection]) -> int:
        digest = sha256(document.body.encode("utf-8")).hexdigest()
        with self._connect() as connection:
            self._ensure_embedding_compatible(connection, allow_initialize=True)
            connection.execute(
                """
                INSERT INTO documents(
                    id, title, source_path, version, status, kind, tags_json,
                    body, content_hash, indexed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    title=excluded.title,
                    source_path=excluded.source_path,
                    version=excluded.version,
                    status=excluded.status,
                    kind=excluded.kind,
                    tags_json=excluded.tags_json,
                    body=excluded.body,
                    content_hash=excluded.content_hash,
                    indexed_at=excluded.indexed_at
                """,
                (
                    document.id,
                    document.title,
                    document.source_path,
                    document.version,
                    document.status,
                    document.kind,
                    json.dumps(document.tags, ensure_ascii=False),
                    document.body,
                    digest,
                    datetime.now(timezone.utc).isoformat(timespec="seconds"),
                ),
            )
            connection.execute("DELETE FROM chunks WHERE document_id = ?", (document.id,))
            for ordinal, section in enumerate(sections, 1):
                chunk_id = f"{document.id}#chunk-{ordinal}"
                connection.execute(
                    """
                    INSERT INTO chunks(id, document_id, ordinal, locator, content, vector_json)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        chunk_id,
                        document.id,
                        ordinal,
                        section.locator,
                        section.text,
                        json.dumps(self.embedding.embed(section.text)),
                    ),
                )
            self._upsert_node_connection(
                connection,
                document.id,
                "knowledge_document",
                document.title,
                {"kind": document.kind, "status": document.status, "version": document.version},
            )
            for tag in document.tags:
                tag_id = f"tag:{tag}"
                self._upsert_node_connection(connection, tag_id, "tag", tag, {})
                self._upsert_edge_connection(
                    connection, document.id, "has_tag", tag_id, {}
                )
        return len(sections)

    def upsert_node(self, node_id: str, node_type: str, label: str, metadata: dict | None = None) -> None:
        with self._connect() as connection:
            self._upsert_node_connection(connection, node_id, node_type, label, metadata or {})

    def upsert_edge(
        self,
        source_id: str,
        relation: str,
        target_id: str,
        metadata: dict | None = None,
    ) -> None:
        with self._connect() as connection:
            self._upsert_edge_connection(
                connection, source_id, relation, target_id, metadata or {}
            )

    def search(
        self,
        query: str,
        *,
        limit: int = 8,
        tags: set[str] | None = None,
        approved_only: bool = True,
        kinds: set[str] | None = None,
    ) -> list[Evidence]:
        query_vector = self.embedding.embed(query)
        with self._connect() as connection:
            self._ensure_embedding_compatible(connection, allow_initialize=False)
            rows = connection.execute(
                """
                SELECT
                    c.id AS chunk_id, c.locator, c.content, c.vector_json,
                    d.id AS document_id, d.title, d.source_path, d.version,
                    d.status, d.kind, d.tags_json
                FROM chunks c JOIN documents d ON d.id = c.document_id
                """
            ).fetchall()
        evidence: list[Evidence] = []
        for row in rows:
            document_tags = tuple(json.loads(row["tags_json"]))
            if approved_only and row["status"] != "approved":
                continue
            if tags and not tags.intersection(document_tags):
                continue
            if kinds and row["kind"] not in kinds:
                continue
            lexical = lexical_score(query, f"{row['title']} {row['content']}")
            vector = cosine(query_vector, json.loads(row["vector_json"]))
            score = self.lexical_weight * lexical + self.vector_weight * vector
            if score <= 0:
                continue
            evidence.append(
                Evidence(
                    knowledge_id=row["document_id"],
                    chunk_id=row["chunk_id"],
                    title=row["title"],
                    source_path=row["source_path"],
                    locator=row["locator"],
                    version=row["version"],
                    status=row["status"],
                    kind=row["kind"],
                    tags=document_tags,
                    content=row["content"],
                    score=score,
                    lexical_score=lexical,
                    vector_score=vector,
                )
            )
        evidence.sort(key=lambda item: (-item.score, item.knowledge_id, item.chunk_id))
        return evidence[:limit]

    def graph_neighbors(self, node_id: str, depth: int = 1) -> list[dict]:
        depth = max(0, min(depth, 5))
        output: list[dict] = []
        seen_edges: set[tuple[str, str, str]] = set()
        seen_nodes = {node_id}
        pending: deque[tuple[str, int]] = deque([(node_id, 0)])
        with self._connect() as connection:
            while pending:
                current, level = pending.popleft()
                if level >= depth:
                    continue
                rows = connection.execute(
                    """
                    SELECT e.source_id, e.relation, e.target_id, e.metadata_json,
                           s.label AS source_label, t.label AS target_label
                    FROM edges e
                    LEFT JOIN nodes s ON s.id = e.source_id
                    LEFT JOIN nodes t ON t.id = e.target_id
                    WHERE e.source_id = ? OR e.target_id = ?
                    """,
                    (current, current),
                ).fetchall()
                for row in rows:
                    key = (row["source_id"], row["relation"], row["target_id"])
                    if key in seen_edges:
                        continue
                    seen_edges.add(key)
                    output.append(
                        {
                            "source_id": row["source_id"],
                            "source_label": row["source_label"],
                            "relation": row["relation"],
                            "target_id": row["target_id"],
                            "target_label": row["target_label"],
                            "metadata": json.loads(row["metadata_json"]),
                            "depth": level + 1,
                        }
                    )
                    neighbor = (
                        row["target_id"] if row["source_id"] == current else row["source_id"]
                    )
                    if neighbor not in seen_nodes:
                        seen_nodes.add(neighbor)
                        pending.append((neighbor, level + 1))
        return output

    def list_documents(self) -> list[dict]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT id, title, source_path, version, status, kind, tags_json,
                       content_hash, indexed_at,
                       (SELECT COUNT(*) FROM chunks c WHERE c.document_id = d.id) AS chunk_count
                FROM documents d ORDER BY kind, title
                """
            ).fetchall()
        return [
            {
                **{key: row[key] for key in row.keys() if key != "tags_json"},
                "tags": json.loads(row["tags_json"]),
            }
            for row in rows
        ]

    def get_document(self, document_id: str) -> dict | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM documents WHERE id = ?", (document_id,)
            ).fetchone()
        if row is None:
            return None
        value = dict(row)
        value["tags"] = json.loads(value.pop("tags_json"))
        return value

    def statistics(self) -> dict:
        with self._connect() as connection:
            return {
                "documents": connection.execute("SELECT COUNT(*) FROM documents").fetchone()[0],
                "chunks": connection.execute("SELECT COUNT(*) FROM chunks").fetchone()[0],
                "nodes": connection.execute("SELECT COUNT(*) FROM nodes").fetchone()[0],
                "edges": connection.execute("SELECT COUNT(*) FROM edges").fetchone()[0],
                "embedding_provider": self.embedding.name,
            }

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                PRAGMA foreign_keys = ON;
                CREATE TABLE IF NOT EXISTS documents(
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    source_path TEXT NOT NULL,
                    version TEXT NOT NULL,
                    status TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    tags_json TEXT NOT NULL,
                    body TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    indexed_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS chunks(
                    id TEXT PRIMARY KEY,
                    document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                    ordinal INTEGER NOT NULL,
                    locator TEXT NOT NULL,
                    content TEXT NOT NULL,
                    vector_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_chunks_document ON chunks(document_id);
                CREATE TABLE IF NOT EXISTS nodes(
                    id TEXT PRIMARY KEY,
                    type TEXT NOT NULL,
                    label TEXT NOT NULL,
                    metadata_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS edges(
                    source_id TEXT NOT NULL,
                    relation TEXT NOT NULL,
                    target_id TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    PRIMARY KEY(source_id, relation, target_id)
                );
                CREATE INDEX IF NOT EXISTS idx_edges_source ON edges(source_id);
                CREATE INDEX IF NOT EXISTS idx_edges_target ON edges(target_id);
                CREATE TABLE IF NOT EXISTS index_metadata(
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                """
            )

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    @staticmethod
    def _upsert_node_connection(
        connection: sqlite3.Connection,
        node_id: str,
        node_type: str,
        label: str,
        metadata: dict,
    ) -> None:
        connection.execute(
            """
            INSERT INTO nodes(id, type, label, metadata_json) VALUES (?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                type=excluded.type, label=excluded.label, metadata_json=excluded.metadata_json
            """,
            (node_id, node_type, label, json.dumps(metadata, ensure_ascii=False)),
        )

    @staticmethod
    def _upsert_edge_connection(
        connection: sqlite3.Connection,
        source_id: str,
        relation: str,
        target_id: str,
        metadata: dict,
    ) -> None:
        connection.execute(
            """
            INSERT INTO edges(source_id, relation, target_id, metadata_json)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(source_id, relation, target_id) DO UPDATE SET
                metadata_json=excluded.metadata_json
            """,
            (source_id, relation, target_id, json.dumps(metadata, ensure_ascii=False)),
        )

    def _ensure_embedding_compatible(
        self, connection: sqlite3.Connection, *, allow_initialize: bool
    ) -> None:
        row = connection.execute(
            "SELECT value FROM index_metadata WHERE key = 'embedding_provider'"
        ).fetchone()
        if row is None:
            chunk_count = connection.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
            if chunk_count and not allow_initialize:
                raise KnowledgeError(
                    "knowledge index has no embedding metadata; rebuild with knowledge-build --reset"
                )
            if allow_initialize:
                connection.execute(
                    "INSERT INTO index_metadata(key, value) VALUES ('embedding_provider', ?)",
                    (self.embedding.name,),
                )
            return
        if row["value"] != self.embedding.name:
            raise KnowledgeError(
                "embedding provider changed; rebuild the index with knowledge-build --reset"
            )
