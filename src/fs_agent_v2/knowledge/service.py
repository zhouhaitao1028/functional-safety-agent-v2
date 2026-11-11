from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import json
import os

from ..errors import KnowledgeError
from .index import KnowledgeIndex
from .models import IndexedDocument, TextSection
from .parsers import SUPPORTED_SUFFIXES, chunk_sections, parse_asset
from .repository import KnowledgeEntry, KnowledgeRepository
from .vector import HashingEmbeddingProvider, OpenAICompatibleEmbeddingProvider


class KnowledgeService:
    def __init__(self, project_root: Path, index_path: Path | None = None):
        self.project_root = project_root
        self.repository = KnowledgeRepository(project_root / "knowledge")
        config = self._load_config(project_root)
        retrieval = config.get("retrieval", {})
        self.index = KnowledgeIndex(
            index_path or project_root / "data" / "knowledge" / "knowledge.db",
            self._embedding_provider(config),
            float(retrieval.get("lexical_weight", 0.6)),
            float(retrieval.get("vector_weight", 0.4)),
        )

    def build(self, *, reset: bool = False, include_external: bool = False) -> dict:
        if reset:
            self.index.reset()
        warnings: list[str] = []
        internal_documents = 0
        external_documents = 0
        entries = self.repository.list()
        for entry in entries:
            self._index_entry(entry)
            internal_documents += 1
        if include_external:
            external_documents, external_warnings = self._index_external(entries)
            warnings.extend(external_warnings)
        return {
            **self.index.statistics(),
            "internal_documents_indexed": internal_documents,
            "external_documents_indexed": external_documents,
            "warnings": warnings,
        }

    def retrieve(
        self,
        query: str,
        *,
        tags: set[str] | None = None,
        approved_only: bool = True,
        limit: int = 8,
        graph_depth: int = 1,
    ) -> dict:
        evidence = self.index.search(
            query,
            limit=limit,
            tags=tags,
            approved_only=approved_only,
        )
        graph_context: list[dict] = []
        seen: set[tuple[str, str, str]] = set()
        for item in evidence:
            for edge in self.index.graph_neighbors(item.knowledge_id, graph_depth):
                key = (edge["source_id"], edge["relation"], edge["target_id"])
                if key not in seen:
                    seen.add(key)
                    graph_context.append(edge)
        return {
            "query": query,
            "policy": {
                "approved_only": approved_only,
                "tags": sorted(tags or []),
                "retrieval": "hybrid_lexical_vector_with_graph_expansion",
            },
            "evidence": [item.to_dict() for item in evidence],
            "graph_context": graph_context,
        }

    def _index_entry(self, entry: KnowledgeEntry) -> None:
        relative = entry.path.relative_to(self.project_root)
        body = json.dumps(entry.content, ensure_ascii=False, indent=2)
        kind = "knowledge_entry"
        if "rules" in entry.path.parts:
            kind = "deterministic_rule"
        elif "domains" in entry.path.parts:
            kind = "domain_knowledge"
        elif "sources" in entry.path.parts:
            kind = "source_catalog"
        document = IndexedDocument(
            id=entry.id,
            title=entry.id,
            source_path=str(relative),
            version=entry.version,
            status=entry.status,
            kind=kind,
            tags=entry.tags,
            body=body,
        )
        self.index.upsert_document(
            document, chunk_sections([TextSection("json:content", body)])
        )

    def _index_external(
        self, entries: tuple[KnowledgeEntry, ...]
    ) -> tuple[int, list[str]]:
        catalogs = [
            entry
            for entry in entries
            if isinstance(entry.content, dict) and entry.content.get("relative_root")
        ]
        indexed = 0
        warnings: list[str] = []
        for catalog in catalogs:
            source_root = (self.project_root / catalog.content["relative_root"]).resolve()
            if not source_root.exists():
                warnings.append(f"external source does not exist: {source_root}")
                continue
            assets: list[tuple[str, str, str, str, str]] = []
            for path in sorted(source_root.rglob("*")):
                if not path.is_file() or path.suffix.lower() not in SUPPORTED_SUFFIXES:
                    continue
                if path.name.upper() == "AGENTS.MD":
                    continue
                relative = path.relative_to(source_root)
                folder = self._knowledge_folder(relative)
                if folder is None:
                    continue
                try:
                    sections = parse_asset(path)
                    chunks = chunk_sections(sections)
                    if not chunks:
                        warnings.append(f"no extractable content: {relative}")
                        continue
                    document_id = "legacy-" + sha256(
                        str(relative).encode("utf-8")
                    ).hexdigest()[:20]
                    kind = "company_guide" if folder == "01_指导手册" else "company_template"
                    business_area = relative.parts[0] if relative.parts else "unknown"
                    tags = (
                        "company_process" if kind == "company_guide" else "company_template",
                        f"business_area:{business_area}",
                        "legacy_source",
                    )
                    body = "\n".join(section.text for section in sections)
                    document = IndexedDocument(
                        id=document_id,
                        title=path.name,
                        source_path=str(path),
                        version="legacy-unverified",
                        status="draft",
                        kind=kind,
                        tags=tags,
                        body=body,
                    )
                    self.index.upsert_document(document, chunks)
                    self.index.upsert_edge(catalog.id, "catalogs", document_id)
                    domain_id = f"business-area:{business_area}"
                    self.index.upsert_node(domain_id, "business_area", business_area)
                    self.index.upsert_edge(document_id, "belongs_to", domain_id)
                    assets.append((document_id, kind, business_area, path.name, str(relative)))
                    indexed += 1
                except KnowledgeError as exc:
                    warnings.append(str(exc))
            self._relate_guides_and_templates(assets)
        return indexed, warnings

    def _relate_guides_and_templates(
        self, assets: list[tuple[str, str, str, str, str]]
    ) -> None:
        guides = [item for item in assets if item[1] == "company_guide"]
        templates = [item for item in assets if item[1] == "company_template"]
        keywords = ("FTA", "FMEA", "DFA", "需求", "架构", "单元测试", "集成测试", "配置", "工具")
        for guide_id, _, guide_area, guide_name, _ in guides:
            for template_id, _, template_area, template_name, _ in templates:
                if guide_area != template_area:
                    continue
                matches = [
                    keyword
                    for keyword in keywords
                    if keyword.lower() in guide_name.lower()
                    and keyword.lower() in template_name.lower()
                ]
                if matches:
                    self.index.upsert_edge(
                        guide_id,
                        "guides_template",
                        template_id,
                        {"matched_terms": matches},
                    )

    @staticmethod
    def _knowledge_folder(relative: Path) -> str | None:
        for part in relative.parts:
            if part in {"01_指导手册", "02_模板"}:
                return part
        return None

    @staticmethod
    def _load_config(project_root: Path) -> dict:
        config_path = project_root / "config" / "knowledge.json"
        if not config_path.exists():
            return {"embedding": {"provider": "hashing_development"}}
        return json.loads(config_path.read_text(encoding="utf-8"))

    @staticmethod
    def _embedding_provider(full_config: dict):
        config = full_config["embedding"]
        provider = config.get("provider", "auto")
        base_url = os.environ.get(config.get("base_url_env", ""), "")
        api_key = os.environ.get(config.get("api_key_env", ""), "")
        model = os.environ.get(config.get("model_env", ""), "")
        if provider == "hashing_development":
            return HashingEmbeddingProvider(int(config.get("fallback_dimensions", 256)))
        if provider in {"auto", "openai_compatible"} and base_url and model:
            return OpenAICompatibleEmbeddingProvider(
                base_url,
                api_key,
                model,
                int(config.get("timeout_seconds", 60)),
            )
        if provider == "openai_compatible":
            raise KnowledgeError(
                "embedding API is selected but base URL or model environment is missing"
            )
        return HashingEmbeddingProvider(int(config.get("fallback_dimensions", 256)))
