"""Process-stage knowledge graph: stages, guides/templates, and engineer skills.

设计思路（对应报告撰写任务线的知识供给）：
- config/stages.json 以流程环节为节点（功能→HARA→安全目标→TSR→FSR→架构→测试），
  每个环节挂载指南/模板资产与上下游边；
- 每个环节一个 skill（skills/<skill_id>/SKILL.md），由一线工程师维护，
  回答三问：用到哪些指南模板、模板易错点、何时跳转参考另一份指南；
- context_pack(stage_id) 是 Agent harness 的检索入口：沿图谱把该环节的
  skill、指南模板全文、上游依据与下游去向一次性拉齐，注入子 Agent 上下文。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json
import re

from ..errors import KnowledgeError

_SKILL_TEMPLATE_MARK = "【待补充"


@dataclass(frozen=True)
class GuideAsset:
    id: str
    title: str
    kind: str  # guide = 指南, template = 模板
    file: str

    def to_summary(self) -> dict:
        return {"id": self.id, "title": self.title, "kind": self.kind}


@dataclass(frozen=True)
class StageDefinition:
    id: str
    name: str
    order: int
    description: str
    agent_id: str
    skill_id: str
    produces: str
    upstream: tuple[str, ...]
    downstream: tuple[str, ...]
    guides: tuple[GuideAsset, ...]
    # 关联公司文档（RAG 库 document id）：draft 只作引用展示，
    # approved 后可由 harness 升级为全文注入。
    rag_documents: tuple[str, ...] = ()


@dataclass(frozen=True)
class StageSkill:
    skill_id: str
    name: str
    description: str
    uses: tuple[str, ...]      # 用到的指南模板（guide id 列表）
    pitfalls: tuple[str, ...]  # 模板/环节易错点
    jumps: tuple[str, ...]     # 跨指南跳转条件
    body: str
    source_path: str
    is_template: bool          # True = 还是样式模板，等待工程师补全


class StageCatalog:
    """Loads the stage knowledge graph and engineer skills from the repo."""

    def __init__(self, project_root: Path):
        self.root = project_root
        config_path = project_root / "config" / "stages.json"
        if not config_path.is_file():
            raise KnowledgeError(f"stage catalog config is missing: {config_path}")
        try:
            value = json.loads(config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise KnowledgeError(f"invalid stage catalog config: {exc}") from exc
        rows = value.get("stages")
        if not isinstance(rows, list) or not rows:
            raise KnowledgeError("stage catalog config contains no stages")
        stages: dict[str, StageDefinition] = {}
        guide_index: dict[str, tuple[str, GuideAsset]] = {}
        for row in rows:
            stage = self._parse_stage(row, config_path)
            if stage.id in stages:
                raise KnowledgeError(f"duplicate stage id: {stage.id}")
            stages[stage.id] = stage
            for guide in stage.guides:
                if guide.id in guide_index:
                    raise KnowledgeError(f"duplicate guide id: {guide.id}")
                guide_index[guide.id] = (stage.id, guide)
        for stage in stages.values():
            unknown = [
                neighbor
                for neighbor in (*stage.upstream, *stage.downstream)
                if neighbor not in stages
            ]
            if unknown:
                raise KnowledgeError(
                    f"stage {stage.id} references unknown neighbor stages: {unknown}"
                )
        self._stages = stages
        self._guide_index = guide_index
        self._skills: dict[str, StageSkill] = {}
        for stage in stages.values():
            self._skills[stage.skill_id] = self._load_skill(stage)

    # ------------------------------------------------------------------ 查询

    def stages(self) -> list[dict]:
        ordered = sorted(self._stages.values(), key=lambda item: item.order)
        return [
            {
                "id": stage.id,
                "name": stage.name,
                "order": stage.order,
                "description": stage.description,
                "agent_id": stage.agent_id,
                "skill_id": stage.skill_id,
                "produces": stage.produces,
                "upstream": list(stage.upstream),
                "downstream": list(stage.downstream),
                "guide_count": len(stage.guides),
                "guide_ids": [guide.id for guide in stage.guides],
                "rag_document_count": len(stage.rag_documents),
                "skill_status": (
                    "template" if self._skills[stage.skill_id].is_template else "authored"
                ),
            }
            for stage in ordered
        ]

    def stage(self, stage_id: str) -> dict:
        stage = self._require_stage(stage_id)
        skill = self._skills[stage.skill_id]
        return {
            **self._stage_summary(stage),
            "skill": self._skill_dict(skill),
            "guides": [guide.to_summary() for guide in stage.guides],
            "rag_document_ids": list(stage.rag_documents),
        }

    def guide(self, guide_id: str) -> dict:
        found = self._guide_index.get(guide_id)
        if found is None:
            raise KnowledgeError(f"unknown knowledge guide: {guide_id}")
        stage_id, guide = found
        path = self.root / guide.file
        if not path.is_file():
            raise KnowledgeError(f"guide asset file is missing: {guide.file}")
        content = path.read_text(encoding="utf-8")
        return {
            **guide.to_summary(),
            "file": guide.file,
            "stage_id": stage_id,
            "content": content,
        }

    def context_pack(self, stage_id: str) -> dict:
        """harness 检索入口：一次性拉齐该环节执行所需的完整知识上下文。"""
        stage = self._require_stage(stage_id)
        skill = self._skills[stage.skill_id]
        guides = [self.guide(guide.id) for guide in stage.guides]
        upstream = [
            self._stage_neighbor_pack(neighbor_id, direction="upstream")
            for neighbor_id in stage.upstream
        ]
        downstream = [
            self._stage_neighbor_pack(neighbor_id, direction="downstream")
            for neighbor_id in stage.downstream
        ]
        declared = {item for item in skill.uses}
        missing = [guide.id for guide in stage.guides if guide.id not in declared]
        return {
            "stage": self._stage_summary(stage),
            "skill": self._skill_dict(skill),
            "guides": guides,
            "upstream": upstream,
            "downstream": downstream,
            # 关联公司文档引用（id 列表）：由 web 层用知识库元数据补全
            # title/status/wiki_url；draft 只作引用，不注入正文。
            "rag_document_ids": list(stage.rag_documents),
            "skill_coverage_note": (
                "skill 声明的指南清单未覆盖环节全部资产: " + ", ".join(missing)
                if missing
                else "skill 声明覆盖全部环节资产"
            ),
        }

    # ------------------------------------------------------------------ 内部

    def _stage_neighbor_pack(self, stage_id: str, *, direction: str) -> dict:
        stage = self._stages[stage_id]
        return {
            "direction": direction,
            "id": stage.id,
            "name": stage.name,
            "produces": stage.produces,
            "guide_ids": [guide.id for guide in stage.guides],
        }

    def _require_stage(self, stage_id: str) -> StageDefinition:
        stage = self._stages.get(stage_id)
        if stage is None:
            raise KnowledgeError(f"unknown process stage: {stage_id}")
        return stage

    @staticmethod
    def _stage_summary(stage: StageDefinition) -> dict:
        return {
            "id": stage.id,
            "name": stage.name,
            "order": stage.order,
            "description": stage.description,
            "agent_id": stage.agent_id,
            "skill_id": stage.skill_id,
            "produces": stage.produces,
            "upstream": list(stage.upstream),
            "downstream": list(stage.downstream),
        }

    @staticmethod
    def _skill_dict(skill: StageSkill) -> dict:
        return {
            "skill_id": skill.skill_id,
            "name": skill.name,
            "description": skill.description,
            "uses": list(skill.uses),
            "pitfalls": list(skill.pitfalls),
            "jumps": list(skill.jumps),
            "body": skill.body,
            "source_path": skill.source_path,
            "is_template": skill.is_template,
        }

    def _parse_stage(self, row: object, config_path: Path) -> StageDefinition:
        if not isinstance(row, dict):
            raise KnowledgeError(f"{config_path}: each stage must be an object")
        required = {"id", "name", "order", "agent_id", "skill_id", "guides"}
        missing = required - row.keys()
        if missing:
            raise KnowledgeError(f"{config_path}: stage missing fields {sorted(missing)}")
        guides = []
        for guide_row in row["guides"]:
            guide_required = {"id", "title", "kind", "file"}
            guide_missing = guide_required - guide_row.keys()
            if guide_missing:
                raise KnowledgeError(
                    f"{config_path}: guide in stage {row['id']} missing {sorted(guide_missing)}"
                )
            asset_path = self.root / guide_row["file"]
            if not asset_path.is_file():
                raise KnowledgeError(
                    f"{config_path}: guide asset file is missing: {guide_row['file']}"
                )
            guides.append(
                GuideAsset(
                    id=guide_row["id"],
                    title=guide_row["title"],
                    kind=guide_row["kind"],
                    file=guide_row["file"],
                )
            )
        return StageDefinition(
            id=row["id"],
            name=row["name"],
            order=int(row["order"]),
            description=str(row.get("description", "")),
            agent_id=row["agent_id"],
            skill_id=row["skill_id"],
            produces=str(row.get("produces", "")),
            upstream=tuple(row.get("upstream", [])),
            downstream=tuple(row.get("downstream", [])),
            guides=tuple(guides),
            rag_documents=tuple(dict.fromkeys(row.get("rag_documents", []))),
        )

    def _load_skill(self, stage: StageDefinition) -> StageSkill:
        path = self.root / "skills" / stage.skill_id / "SKILL.md"
        if not path.is_file():
            raise KnowledgeError(
                f"stage {stage.id} references missing skill: {stage.skill_id}"
            )
        raw = path.read_text(encoding="utf-8")
        frontmatter, body = self._split_frontmatter(raw)
        sections = self._split_sections(body)
        uses = self._extract_guide_ids(sections.get("用到的指南模板", ""))
        return StageSkill(
            skill_id=stage.skill_id,
            name=frontmatter.get("name", stage.skill_id),
            description=frontmatter.get("description", ""),
            uses=tuple(uses),
            pitfalls=self._bullet_items(sections.get("易错点", "")),
            jumps=self._bullet_items(sections.get("跳转参考", "")),
            body=body,
            source_path=str(path.relative_to(self.root)),
            is_template=_SKILL_TEMPLATE_MARK in raw,
        )

    @staticmethod
    def _split_frontmatter(raw: str) -> tuple[dict, str]:
        match = re.match(r"\A---\s*\n(.*?)\n---\s*\n?", raw, re.DOTALL)
        if match is None:
            return {}, raw
        metadata: dict[str, str] = {}
        for line in match.group(1).splitlines():
            if ":" in line:
                key, _, value = line.partition(":")
                metadata[key.strip()] = value.strip()
        return metadata, raw[match.end():]

    @staticmethod
    def _split_sections(body: str) -> dict[str, str]:
        sections: dict[str, str] = {}
        current = ""
        for line in body.splitlines():
            heading = re.match(r"^##\s+(.+?)\s*$", line)
            if heading:
                current = heading.group(1)
                sections.setdefault(current, "")
                continue
            if current:
                sections[current] += line + "\n"
        return sections

    @staticmethod
    def _extract_guide_ids(section_text: str) -> list[str]:
        return list(dict.fromkeys(re.findall(r"`([a-zA-Z0-9-]+)`", section_text)))

    @staticmethod
    def _bullet_items(section_text: str) -> tuple[str, ...]:
        items = []
        for line in section_text.splitlines():
            stripped = line.strip()
            if stripped.startswith(("- ", "* ")):
                text = stripped[2:].strip()
                if text:
                    items.append(text)
        return tuple(items)
