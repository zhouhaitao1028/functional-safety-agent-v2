from __future__ import annotations

from dataclasses import replace
from functools import partial
from hashlib import sha256
from pathlib import Path
import asyncio
import json
import re
import sqlite3
import threading
from typing import Literal
import uuid

from fastapi import FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from .. import __version__
from ..errors import ContractError, FunctionalSafetyError
from ..knowledge import StageCatalog
from ..knowledge.index import KnowledgeIndex
from ..model import OpenAICompatibleChatClient
from ..orchestration import (
    DynamicWorkEngine,
    ManagerRuntime,
    SoftwareInterfaceIntentRuntime,
    WorkflowRuntime,
)


DEEPSEEK_MODELS = (
    {
        "id": "deepseek-flash",
        "title": "DeepSeek Flash",
        "description": "速度优先，适合常规路由与工作计划。",
        "recommended": True,
    },
    {
        "id": "deepseek-v4-pro",
        "title": "DeepSeek V4 Pro",
        "description": "能力优先，适合复杂多阶段任务规划。",
        "recommended": False,
    },
)
DEEPSEEK_MODEL_IDS = {item["id"] for item in DEEPSEEK_MODELS}


class ProjectCreate(BaseModel):
    project_id: str = Field(default="", max_length=80)
    title: str = Field(default="", max_length=200)


class SourceInput(BaseModel):
    id: str = Field(min_length=1, max_length=80)
    type: str = Field(default="", max_length=120)
    location: str = Field(min_length=1)


class ManagerPlanRequest(BaseModel):
    request: str = Field(min_length=1)
    inputs: list[SourceInput] = Field(default_factory=list)
    include_draft_knowledge: bool = False
    max_tool_rounds: int = Field(default=8, ge=2, le=16)
    model: Literal["deepseek-flash", "deepseek-v4-pro"] = "deepseek-flash"


class SoftwareInterfacePlanRequest(BaseModel):
    """Deterministic web-console template for one interface Engineer task."""

    operation: Literal[
        "analyze_package", "generate_overview", "compare_overviews"
    ]
    primary_path: str = Field(min_length=1)
    secondary_path: str = ""
    engine: Literal["fast", "auto"] = "fast"
    compare_to_existing: bool = False
    max_evidence: int = Field(default=1000, ge=1, le=10000)
    max_findings: int = Field(default=500, ge=1, le=5000)
    timeout_seconds: int = Field(default=1200, ge=30, le=7200)


class ApprovalRequest(BaseModel):
    reviewer: str = Field(min_length=1, max_length=160)
    decision: str = Field(default="approved", pattern="^(approved|rejected)$")
    comment: str = Field(default="", max_length=2000)


class WorkflowRunRequest(BaseModel):
    query: str = Field(default="", max_length=1000)
    include_draft_knowledge: bool = False
    max_tool_rounds: int = Field(default=8, ge=1, le=16)
    max_findings: int = Field(default=100, ge=1, le=5000)
    max_parallel: int = Field(default=1, ge=1, le=32)
    lease_seconds: int = Field(default=120, ge=5, le=3600)
    max_attempts: int = Field(default=3, ge=1, le=20)
    model: Literal["deepseek-flash", "deepseek-v4-pro"] = "deepseek-flash"


class QuickTaskRunRequest(BaseModel):
    request: str = Field(default="生成软件接口总览表", max_length=2000)
    # 路径可选：为空时由「消息文本提取 > 工作记忆」智能解析链补齐
    input_path: str = ""
    secondary_input_path: str = ""
    max_evidence: int = Field(default=1000, ge=1, le=10000)
    model: Literal["deepseek-flash", "deepseek-v4-pro"] = "deepseek-flash"


QUICK_TASKS = {
    "interface_generation": {
        "id": "interface_generation",
        "title": "软件接口任务",
        "description": "理解生成、对比或分析意图，再由确定性工具执行并校验。",
        "agent_id": "software_interface_engineer",
        "input_label": "软件接口包路径",
        "input_type": "software_interface_package",
        "plan_approval": "automatic",
        "result_approval": "human",
    }
}

# 每个项目一把执行锁：快速任务已改为后台线程异步执行，防止并发跑同一项目
_project_locks: dict[str, threading.Lock] = {}


def _software_interface_workplan(
    body: SoftwareInterfacePlanRequest, *, run_key: str = ""
) -> dict:
    """Build the same strict contract that Manager should produce for this Agent."""
    if run_key and not all(
        character.isalnum() or character in "-_" for character in run_key
    ):
        raise ContractError("software interface run key contains unsafe characters")
    if body.operation == "compare_overviews":
        if not body.secondary_path.strip():
            raise FunctionalSafetyError(
                "compare_overviews requires both new and old overview paths"
            )
        inputs = [
            {
                "id": "IN-NEW-OVERVIEW",
                "type": "interface_overview",
                "location": _clean_input_path(body.primary_path),
            },
            {
                "id": "IN-OLD-OVERVIEW",
                "type": "interface_overview",
                "location": body.secondary_path.strip(),
            },
        ]
        title = "比较新旧软件接口总览"
        goal = "比较新旧软件接口总览并输出带哈希和行级证据的结构化差异"
        artifact = "01_software_interface_comparison.json"
        parameters = {
            "operation": body.operation,
            "max_evidence": body.max_evidence,
            "timeout_seconds": body.timeout_seconds,
        }
    else:
        inputs = [
            {
                "id": "IN-INTERFACE-PACKAGE",
                "type": "software_interface_package",
                "location": _clean_input_path(body.primary_path),
            }
        ]
        if body.operation == "generate_overview":
            title = "生成并校验软件接口总览表"
            goal = "使用确定性引擎生成软件接口总览，并执行字段及 AA/AB 独立校验"
            artifact = "01_software_interface_generation.json"
            parameters = {
                "operation": body.operation,
                "engine": body.engine,
                "compare_to_existing": body.compare_to_existing,
                "max_evidence": body.max_evidence,
                "timeout_seconds": body.timeout_seconds,
            }
        else:
            title = "分析软件接口包"
            goal = "分析接口字典、跨应用层收发链路和 Unit2ComponentConfig"
            artifact = "01_software_interface_analysis.json"
            parameters = {
                "operation": body.operation,
                "max_findings": body.max_findings,
            }

    plan_suffix = f"-{run_key}" if run_key else ""
    artifact_path = Path(artifact)
    if run_key:
        artifact = f"{artifact_path.stem}_{run_key}{artifact_path.suffix}"
    work_item_id = f"W01-{run_key}" if run_key else "W01"
    output_id = (
        f"OUT-SOFTWARE-INTERFACE-TASK-{run_key}"
        if run_key
        else "OUT-SOFTWARE-INTERFACE-TASK"
    )

    return {
        "id": f"software-interface-{body.operation.replace('_', '-')}{plan_suffix}",
        "version": "1.0.0",
        "goal": goal,
        "inputs": inputs,
        "work_items": [
            {
                "id": work_item_id,
                "title": title,
                "agent_id": "software_interface_engineer",
                "depends_on": [],
                "input_refs": [item["id"] for item in inputs],
                "parameters": parameters,
                "output": {
                    "id": output_id,
                    "type": "software_interface_task_result",
                    "artifact": artifact,
                },
                "approval_required": True,
            }
        ],
    }


def _clean_input_path(raw: str) -> str:
    """Normalize pasted paths: strip wrapping quotes added by Windows “复制文件地址”."""
    value = raw.strip()
    while value and value[0] in {'"', "'"}:
        value = value[1:].lstrip()
    while value and value[-1] in {'"', "'"}:
        value = value[:-1].rstrip()
    return value


_PATH_PATTERN = re.compile(r"[A-Za-z]:[\\/][^\s\"'，。；；！？]+")


def _extract_paths_from_text(text: str) -> list[str]:
    """Extract plausible file paths mentioned in free-form user text."""
    paths: list[str] = []
    for match in _PATH_PATTERN.findall(text or ""):
        cleaned = _clean_input_path(match)
        if cleaned and cleaned not in paths and Path(cleaned).exists():
            paths.append(cleaned)
    return paths


_OVERVIEW_EXTS = {".xlsx", ".xlsm", ".xltx", ".xltm"}


def _is_overview_workbook(path: str) -> bool:
    """compare_overviews 的输入必须是 Excel 总览表，压缩包等接口包不可比较。"""
    return Path(path).suffix.lower() in _OVERVIEW_EXTS


def _suggested_old_overview(primary: str, memory: dict) -> str:
    """Pick a previous overview as the old-version candidate for comparisons."""
    last_inputs = memory.get("last_inputs") or {}
    candidates = [
        memory.get("last_overview") or "",
        last_inputs.get("primary_input") or "",
        last_inputs.get("secondary_input") or "",
    ]
    for candidate in candidates:
        if (
            candidate
            and candidate != primary
            and Path(candidate).exists()
        ):
            return candidate
    return ""


def _infer_input_type(path: str) -> str:
    """Guess the structured input type from the path's name/extension."""
    name = Path(path).name
    lowered = name.lower()
    if ("overview" in lowered or "总览" in name) and lowered.endswith(
        (".json", ".xlsx", ".csv")
    ):
        return "interface_overview"
    return "software_interface_package"


def _ordered_interface_overviews(
    primary_path: str, secondary_path: str, ordering: str
) -> tuple[str, str]:
    """Return deterministic new/old paths without letting the model inspect files."""
    primary = _clean_input_path(primary_path)
    secondary = _clean_input_path(secondary_path)
    if ordering == "input_order":
        return primary, secondary
    if ordering != "modification_time":
        raise ContractError(f"unsupported interface version ordering: {ordering}")
    try:
        first = Path(primary).resolve(strict=True)
        second = Path(secondary).resolve(strict=True)
    except OSError as exc:
        raise ContractError(f"cannot inspect interface overview timestamp: {exc}") from exc
    if not first.is_file() or not second.is_file():
        raise ContractError("version timestamp ordering requires two overview files")
    if first.stat().st_mtime_ns >= second.stat().st_mtime_ns:
        return str(first), str(second)
    return str(second), str(first)


def _explicit_modification_time_rule(request: str) -> bool:
    normalized = request.casefold().replace(" ", "")
    return any(
        marker in normalized
        for marker in (
            "修改时间",
            "更新时间",
            "时间较晚",
            "时间更晚",
            "时间更新",
            "时间更接近",
            "最近修改",
            "mtime",
        )
    )


def _deepseek_client(
    engine: DynamicWorkEngine, agent_id: str, model: str
) -> OpenAICompatibleChatClient:
    if model not in DEEPSEEK_MODEL_IDS:
        raise ContractError(f"unsupported DeepSeek model: {model}")
    config = engine.registry.resolve_api(agent_id)
    if "api.deepseek.com" not in config.base_url:
        raise ContractError(
            f"agent {agent_id} is not configured for the DeepSeek API"
        )
    return OpenAICompatibleChatClient(replace(config, model=model))


def create_app(
    root: Path | None = None,
    project_root: Path | None = None,
) -> FastAPI:
    project_dir = (root or Path(__file__).resolve().parents[3]).resolve()
    engine = DynamicWorkEngine(project_dir, project_root=project_root)
    static_dir = Path(__file__).resolve().parent / "static"
    dist_dir = static_dir / "dist"
    dist_index = dist_dir / "index.html"
    legacy_index = static_dir / "legacy.html"

    def _console_response() -> FileResponse:
        if dist_index.is_file():
            return FileResponse(
                dist_index,
                headers={"Cache-Control": "no-store, max-age=0"},
            )
        return FileResponse(
            legacy_index,
            headers={"Cache-Control": "no-store, max-age=0"},
        )

    app = FastAPI(
        title="Functional Safety Agent V2",
        version=__version__,
        description="Manager-routed functional-safety Agent test API",
    )
    app.state.engine = engine
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

    @app.exception_handler(FunctionalSafetyError)
    async def functional_safety_error(_, exc: FunctionalSafetyError):
        return JSONResponse(
            status_code=409,
            content={"error": exc.__class__.__name__, "detail": str(exc)},
        )

    @app.exception_handler(FileNotFoundError)
    async def file_not_found(_, exc: FileNotFoundError):
        return JSONResponse(
            status_code=404,
            content={"error": "FileNotFoundError", "detail": str(exc)},
        )

    @app.get("/", include_in_schema=False)
    async def home():
        return _console_response()

    @app.get("/legacy", include_in_schema=False)
    async def legacy_console():
        return FileResponse(
            legacy_index,
            headers={"Cache-Control": "no-store, max-age=0"},
        )

    @app.get("/api/health")
    async def health():
        return {
            "status": "ok",
            "version": __version__,
            "project_root": str(engine.projects.root),
        }

    @app.get("/api/agents")
    async def agents():
        return [
            {
                "id": agent.id,
                "title": agent.title,
                "role": agent.role,
                "kind": agent.kind,
                "business": agent.business,
                "consumes": agent.consumes,
                "optional_consumes": agent.optional_consumes,
                "produces": agent.produces,
                "approval_required": agent.approval_required,
                "automatic_executor": engine.automatic_executor(agent.id),
            }
            for agent in engine.registry.list()
        ]

    @app.get("/api/models")
    async def models():
        """Return the safe model allowlist; never expose provider credentials."""
        return list(DEEPSEEK_MODELS)

    # 流程环节知识图谱：环节 → 指南/模板 → 工程师 skill → harness 注入包
    stage_catalog = StageCatalog(project_dir)

    # RAG 知识库（公司模板文档引用层的元数据 + Wiki 页服务）
    knowledge_db = project_dir / "data" / "knowledge" / "knowledge.db"
    rag_index = KnowledgeIndex(knowledge_db) if knowledge_db.is_file() else None
    wiki_dir = project_dir / "data" / "wiki"

    def _rag_doc_meta(doc_id: str) -> dict | None:
        """按 id 取知识库文档元数据；未知 id 返回 None（引用悬空时优雅降级）。"""
        if rag_index is None:
            return None
        try:
            doc = rag_index.get_document(doc_id)
        except sqlite3.Error:
            return None
        if not doc:
            return None
        return {
            "id": doc["id"],
            "title": doc.get("title") or doc["id"],
            "status": doc.get("status", "draft"),
            "kind": doc.get("kind", ""),
            "wiki_url": f"/api/knowledge/rag-docs/{doc['id']}/wiki",
        }

    def _attach_rag_docs(payload: dict) -> dict:
        """把 stage 返回的 rag_document_ids 展开为完整文档引用清单。"""
        ids = payload.pop("rag_document_ids", [])
        payload["rag_documents"] = [
            meta for doc_id in ids if (meta := _rag_doc_meta(doc_id)) is not None
        ]
        return payload

    @app.get("/api/knowledge/stages")
    async def knowledge_stages():
        """流程环节链与各环节的知识资产、skill 完成度。"""
        return stage_catalog.stages()

    @app.get("/api/knowledge/rag-docs/{doc_id}/wiki")
    async def knowledge_rag_doc_wiki(doc_id: str):
        """返回公司文档的静态 Wiki 页（文件名可由 doc_id 确定性推导）。"""
        meta = _rag_doc_meta(doc_id)
        if meta is None:
            raise HTTPException(status_code=404, detail=f"unknown knowledge document: {doc_id}")
        suffix = sha256(doc_id.encode("utf-8")).hexdigest()[:8]
        matches = sorted(wiki_dir.glob(f"*-{suffix}.html"))
        if not matches:
            raise HTTPException(
                status_code=404,
                detail="wiki page not built yet; run `python run.py wiki-build` first",
            )
        return FileResponse(matches[0], media_type="text/html")

    @app.get("/api/knowledge/stages/{stage_id}/context-pack")
    async def knowledge_stage_context_pack(stage_id: str):
        """harness 检索预览：该环节 skill + 指南模板全文 + 上下游依据 + 公司文档引用。"""
        return _attach_rag_docs(stage_catalog.context_pack(stage_id))

    @app.get("/api/knowledge/stages/{stage_id}")
    async def knowledge_stage(stage_id: str):
        return _attach_rag_docs(stage_catalog.stage(stage_id))

    @app.get("/api/knowledge/traceability")
    async def knowledge_traceability():
        """追溯确认线的知识供给：契约 skill + 确定性工具清单（零臆想层）。"""
        skill_path = project_dir / "skills" / "fusa-software-interface-traceability" / "SKILL.md"
        skill: dict = {"name": "fusa-software-interface-traceability", "description": "", "source_path": "", "body": ""}
        if skill_path.is_file():
            raw = skill_path.read_text(encoding="utf-8")
            front, body = "", raw
            if raw.startswith("---"):
                _, front, body = raw.split("---", 2)
            meta: dict[str, str] = {}
            for line in front.splitlines():
                if ":" in line:
                    key, _, value = line.partition(":")
                    meta[key.strip()] = value.strip()
            skill = {
                "name": meta.get("name", "fusa-software-interface-traceability"),
                "description": meta.get("description", ""),
                "source_path": str(skill_path.relative_to(project_dir)),
                "body": body.strip(),
            }
        from ..interface_tools.mcp_server import InterfaceMcpServer

        tool_defs = {
            tool["name"].removeprefix("fusa_interface_"): tool
            for tool in InterfaceMcpServer.tool_definitions()
        }
        mcp_tools = [
            {"name": tool["name"], "description": tool.get("description", "")}
            for tool in InterfaceMcpServer.tool_definitions()
        ]
        algorithms = [
            {
                "id": "asil-lookup",
                "name": "ASIL 等级查表",
                "description": "S/E/C 参数按 ISO 26262 Part 3 附表确定性映射 ASIL 等级，纯查表无模型参与",
            },
            {
                "id": "fta-minimal-cutsets",
                "name": "FTA 最小割集",
                "description": "故障树布尔化后用确定性算法求解最小割集，输出可复核的割集清单",
            },
            {
                "id": "traceability-coverage",
                "name": "追溯覆盖率校验",
                "description": "需求-架构-测试追溯链按 ID 关系做覆盖率核对，缺口即告警，禁止模型补全",
            },
        ]
        # 追溯线环节链：按 config/traceability_stages.json 组织，每环节挂工具与 skill 契约锚点
        trace_config_path = project_dir / "config" / "traceability_stages.json"
        trace_stages: list[dict] = []
        if trace_config_path.is_file():
            trace_cfg = json.loads(trace_config_path.read_text(encoding="utf-8"))
            for row in trace_cfg.get("stages", []):
                tools = []
                for tool_id in row.get("tools", []):
                    definition = tool_defs.get(tool_id, {})
                    tools.append(
                        {
                            "id": tool_id,
                            "mcp_name": f"fusa_interface_{tool_id}",
                            "description": definition.get("description", ""),
                        }
                    )
                trace_stages.append(
                    {
                        "id": row["id"],
                        "name": row["name"],
                        "order": int(row.get("order", 0)),
                        "description": row.get("description", ""),
                        "tools": tools,
                        "skill_refs": row.get("skill_refs", []),
                    }
                )
        return {
            "task": trace_cfg.get("task", "") if trace_config_path.is_file() else "",
            "skill": skill,
            "trace_stages": trace_stages,
            "mcp_tools": mcp_tools,
            "algorithms": algorithms,
            "principle": "所有专业结论由确定性工具产出，模型只负责任务理解与结果解释；每个输出携带输入/输出 SHA-256 与证据锚点，关键节点人工确认。",
        }

    @app.get("/api/knowledge/guides/{guide_id}")
    async def knowledge_guide(guide_id: str):
        return stage_catalog.guide(guide_id)

    @app.get("/api/quick-tasks")
    async def quick_tasks():
        """Discover fixed Agent routes exposed as low-friction task channels."""
        rows = [dict(item) for item in QUICK_TASKS.values()]
        interface_package = project_dir.parent / "外部接口生成与对比工具.zip"
        if interface_package.is_file():
            for item in rows:
                if item["id"] == "interface_generation":
                    item["default_input_path"] = str(interface_package)
        return rows

    @app.get("/api/projects")
    async def projects():
        rows = []
        if engine.projects.root.exists():
            for manifest in engine.projects.root.glob("*/project.json"):
                try:
                    value = json.loads(manifest.read_text(encoding="utf-8"))
                    if not value.get("deleted_at"):
                        rows.append(value)
                except (OSError, json.JSONDecodeError):
                    continue
        return sorted(rows, key=lambda row: row.get("created_at", ""), reverse=True)

    @app.post("/api/projects", status_code=201)
    async def create_project(body: ProjectCreate):
        title = body.title.strip() or body.project_id.strip()
        return engine.projects.init_named_project(title)

    @app.get("/api/projects/{project_id}")
    async def project(project_id: str):
        return engine.projects.load_project(project_id)

    @app.delete("/api/projects/{project_id}")
    async def archive_project(project_id: str):
        # 硬删除：项目目录（含消息、产物、接口输出、上传材料）+ 执行库中的全部行
        manifest = engine.projects.delete_project(project_id)
        removed_rows = engine.execution.purge_project(project_id)
        return {
            "state": "deleted",
            "project": manifest,
            "purged_rows": removed_rows,
        }

    @app.post("/api/projects/{project_id}/restore")
    async def restore_project(project_id: str):
        return {
            "state": "active",
            "project": engine.projects.restore_project(project_id, "web-user"),
        }

    @app.get("/api/projects/{project_id}/messages")
    async def project_messages(
        project_id: str, limit: int = Query(default=40, ge=1, le=200)
    ):
        return engine.load_messages(project_id, limit=limit)

    @app.get("/api/projects/{project_id}/memory")
    async def project_memory(project_id: str):
        engine.projects.load_project(project_id)
        return {
            "working": {
                "manager": engine.execution.get_working_memory(
                    project_id, "manager"
                ),
                "software_interface": engine.execution.get_working_memory(
                    project_id, "software_interface"
                ),
            },
            "long_term": engine.execution.memories(
                project_id,
                agent_id="software_interface_engineer",
                status="confirmed",
                limit=20,
            ),
        }

    @app.get("/api/projects/{project_id}/workplan")
    async def workplan(project_id: str):
        return engine.projects.load_workplan(project_id)

    @app.post("/api/projects/{project_id}/uploads")
    async def upload_project_file(project_id: str, file: UploadFile = File(...)):
        """Composer 弹框选择的本地文件落盘到项目目录，回填服务端绝对路径。"""
        engine.projects.load_project(project_id)
        uploads_dir = engine.projects.project_dir(project_id) / "uploads"
        uploads_dir.mkdir(parents=True, exist_ok=True)
        safe_name = Path(file.filename or "upload.bin").name
        target = uploads_dir / f"{uuid.uuid4().hex[:8]}_{safe_name}"
        with target.open("wb") as sink:
            while chunk := await file.read(1 << 20):
                sink.write(chunk)
        return {"path": str(target.resolve()), "name": safe_name}

    @app.post("/api/projects/{project_id}/manager-plan")
    async def manager_plan(project_id: str, body: ManagerPlanRequest):
        runtime = ManagerRuntime(engine, max_tool_rounds=body.max_tool_rounds)
        inputs = []
        for item in body.inputs:
            data = item.model_dump()
            data["location"] = _clean_input_path(data["location"])
            if not data["type"].strip():
                # 前端不再要求用户理解输入类型，按路径特征自动推断
                data["type"] = _infer_input_type(data["location"])
            inputs.append(data)
        if not inputs:
            # 结构化输入缺省时，从对话文本智能提取路径并推断类型
            inputs = [
                {
                    "id": f"IN-USER-SOURCE-{index + 1}",
                    "type": _infer_input_type(path),
                    "location": path,
                }
                for index, path in enumerate(_extract_paths_from_text(body.request))
            ]
        if not inputs:
            # 消息文本也没有路径时，回退到本对话的产物历史：
            # 工作记忆登记的 overview_history + 历史完成消息里的产物清单，
            # 让“对比这两个表”这类指代能解析到对话中最近生成的总览表
            memory_value = (
                engine.execution.get_working_memory(project_id, "software_interface")
                or {}
            ).get("value", {}) or {}
            history_paths = [
                _clean_input_path(str(row.get("path", "")))
                for row in (memory_value.get("overview_history") or [])
                if isinstance(row, dict) and row.get("path")
            ]
            for message in engine.load_messages(project_id, limit=24):
                if message.get("role") != "assistant":
                    continue
                meta = message.get("metadata") or {}
                if meta.get("state") != "completed":
                    continue
                for row in meta.get("result_artifacts") or []:
                    history_paths.append(_clean_input_path(str(row.get("path", ""))))
            deduped: list[str] = []
            for path in history_paths:
                if (
                    path
                    and _is_overview_workbook(path)
                    and Path(path).exists()
                    and path not in deduped
                ):
                    deduped.append(path)
            inputs = [
                {
                    "id": f"IN-USER-SOURCE-{index + 1}",
                    "type": "interface_overview",
                    "location": path,
                }
                for index, path in enumerate(deduped[-2:])
            ]
        if not inputs:
            # 无任何路径线索：提示用户直接在对话里粘贴路径
            engine.append_message(
                project_id,
                "assistant",
                "还没有可用的输入来源。请把输入文件或目录的路径直接粘贴到对话里再发送，"
                "例如：请基于 C:\\path\\to\\接口包 生成分析计划。",
                {"channel": "manager", "state": "needs_input"},
            )
            return {
                "state": "needs_input",
                "required_fields": ["inputs"],
                "message": "请在对话中粘贴输入文件或目录的路径",
            }
        client = _deepseek_client(engine, "manager_agent", body.model)
        def execute() -> dict:
            conversation = engine.load_messages(project_id, limit=12)
            user_message = engine.append_message(
                project_id,
                "user",
                body.request,
                {"channel": "manager", "model": body.model},
            )
            record = runtime.plan(
                project_id,
                body.request,
                inputs,
                include_draft_knowledge=body.include_draft_knowledge,
                conversation=conversation,
                client=client,
            )
            assistant_message = engine.append_message(
                project_id,
                "assistant",
                "已结合当前项目对话生成工作计划，请确认 Agent 选择和执行步骤。",
                {
                    "channel": "manager",
                    "state": "plan_created",
                    "model": body.model,
                    "workplan_sha256": record["sha256"],
                },
            )
            engine.execution.set_working_memory(
                project_id,
                "manager",
                {
                    "state": "waiting_for_plan_approval",
                    "goal": record["data"]["goal"],
                    "user_message_id": user_message["id"],
                    "assistant_message_id": assistant_message["id"],
                    "workplan_sha256": record["sha256"],
                },
            )
            return record

        return await run_in_threadpool(execute)

    @app.post("/api/projects/{project_id}/software-interface-plan")
    async def software_interface_plan(
        project_id: str, body: SoftwareInterfacePlanRequest
    ):
        run_key = uuid.uuid4().hex[:12]
        plan = _software_interface_workplan(body, run_key=run_key)
        return engine.submit_workplan(
            project_id,
            plan,
            provenance={
                "source": "web_console_deterministic_template",
                "operation": body.operation,
                "run_key": run_key,
                "note": "Bypasses Manager model planning; execution and human gates are unchanged.",
            },
        )

    @app.post("/api/projects/{project_id}/quick-tasks/{task_id}/run")
    async def run_quick_task(
        project_id: str, task_id: str, body: QuickTaskRunRequest
    ):
        definition = QUICK_TASKS.get(task_id)
        if definition is None:
            raise ContractError(f"unknown quick task: {task_id}")

        def execute() -> dict:
            try:
                return run_quick_task_flow()
            except FunctionalSafetyError as exc:
                message = f"执行失败：{exc}"
                try:
                    append_flow_message(
                        "assistant",
                        message,
                        {
                            "channel": "software_interface",
                            "state": "failed",
                            "error": str(exc),
                        },
                    )
                except FunctionalSafetyError:
                    pass
                return {
                    "state": "failed",
                    "quick_task": definition,
                    "message": message,
                    "error": str(exc),
                }

        def append_flow_message(role: str, content: str, metadata: dict) -> dict:
            """落库流程消息并记录 SSE 可见事件，确保前端自动刷新看到它。"""
            row = engine.append_message(project_id, role, content, metadata)
            engine.execution.record_event(
                project_id,
                "quick_task_message",
                {"message_id": row["id"], "state": metadata.get("state", "")},
            )
            return row

        def run_quick_task_flow() -> dict:
            if task_id != "interface_generation":
                raise ContractError(f"quick task has no executor: {task_id}")
            conversation = engine.load_messages(project_id, limit=12)
            working_memory = engine.execution.get_working_memory(
                project_id, "software_interface"
            )
            prior_memory = (working_memory or {}).get("value", {}) or {}
            last_inputs = prior_memory.get("last_inputs") or {}
            text_paths = _extract_paths_from_text(body.request)

            def resolve_input(supplied: str, *fallbacks: str) -> str:
                cleaned = _clean_input_path(supplied)
                if cleaned:
                    return cleaned
                for fallback in fallbacks:
                    if fallback:
                        return fallback
                return ""

            input_path = resolve_input(
                body.input_path,
                text_paths[0] if text_paths else "",
                last_inputs.get("primary_input") or "",
            )
            secondary_input_path = resolve_input(
                body.secondary_input_path,
                text_paths[1] if len(text_paths) > 1 else "",
                last_inputs.get("secondary_input") or "",
            )
            long_term_memory = engine.execution.memories(
                project_id,
                agent_id=definition["agent_id"],
                status="confirmed",
                limit=10,
            )
            client = _deepseek_client(engine, definition["agent_id"], body.model)
            intent = SoftwareInterfaceIntentRuntime(engine).interpret(
                body.request,
                primary_input_supplied=bool(input_path),
                secondary_input_supplied=bool(secondary_input_path),
                conversation=conversation,
                working_memory=(working_memory or {}).get("value", {}),
                long_term_memory=long_term_memory,
                client=client,
            )
            # 意图守护：用户明确表达“对比/比较”时，不允许模型降级为生成——
            # 找不齐两份总览表应走 needs_input 询问，而不是静默改道再生成一遍
            if intent.get("operation") == "generate_overview" and any(
                key in (body.request or "")
                for key in ("对比", "比较", "差异", "比对", "compare", "diff")
            ):
                intent = {**intent, "operation": "compare_overviews"}
            effective_secondary_input = (
                secondary_input_path if intent["operation"] == "compare_overviews" else ""
            )
            if intent["operation"] == "compare_overviews":
                # 产物历史：每次完成的生成任务都登记总览表路径，
                # 让“比较第一次和第二次生成的输出”这类指代可被确定性解析
                history_paths = [
                    _clean_input_path(str(row.get("path", "")))
                    for row in (prior_memory.get("overview_history") or [])
                    if isinstance(row, dict) and row.get("path")
                ]
                history_paths = [
                    path
                    for path in history_paths
                    if path and _is_overview_workbook(path) and Path(path).exists()
                ]
                if history_paths:
                    if (
                        not input_path or not _is_overview_workbook(input_path)
                    ) and history_paths[-1] != effective_secondary_input:
                        input_path = history_paths[-1]
                    if (
                        (
                            not effective_secondary_input
                            or not _is_overview_workbook(effective_secondary_input)
                        )
                        and len(history_paths) >= 2
                        and history_paths[-2] != input_path
                    ):
                        effective_secondary_input = history_paths[-2]
                # 输入仍是接口包/压缩包等非 Excel 文件时，兜底用最近一次总览表替换；
                # 无法补救时给出可读提示，而不是让 openpyxl 在执行期报压缩包错误
                last_overview = _clean_input_path(
                    str(prior_memory.get("last_overview") or "")
                )
                if last_overview and _is_overview_workbook(last_overview):
                    if (
                        input_path
                        and not _is_overview_workbook(input_path)
                        and last_overview != effective_secondary_input
                    ):
                        input_path = last_overview
                    if (
                        effective_secondary_input
                        and not _is_overview_workbook(effective_secondary_input)
                        and last_overview != input_path
                    ):
                        effective_secondary_input = last_overview
                invalid_inputs = [
                    (role, path)
                    for role, path in (
                        ("新版", input_path),
                        ("旧版", effective_secondary_input),
                    )
                    if path and not _is_overview_workbook(path)
                ]
                if invalid_inputs or not effective_secondary_input:
                    found = len(
                        {
                            path
                            for path in (input_path, effective_secondary_input, *history_paths)
                            if path and _is_overview_workbook(path) and Path(path).exists()
                        }
                    )
                    if found >= 2:
                        # 两个输入框相同或其一缺失但历史可补：让用户明确新旧关系
                        message = (
                            f"本对话共找到 {found} 份总览表，但无法确定哪份是新版、哪份是旧版。"
                            "请在消息中说明，例如：比较《A.xlsx》（新）和《B.xlsx》（旧）。"
                        )
                    else:
                        message = (
                            f"对比任务需要两个 Excel 总览表（.xlsx/.xlsm），"
                            f"本对话只找到 {found} 份。请先对接口包执行“生成总览表”，"
                            "再将其与旧版总览表对比；如已有旧版总览表，请提供其路径。"
                        )
                    append_flow_message(
                        "assistant",
                        message,
                        {
                            "channel": "software_interface",
                            "state": "needs_input",
                            "intent": intent,
                        },
                    )
                    engine.execution.set_working_memory(
                        project_id,
                        "software_interface",
                        {"state": "needs_input", "intent": intent},
                    )
                    return {
                        "state": "needs_input",
                        "quick_task": definition,
                        "interpretation": intent,
                        "required_fields": ["secondary_input_path"],
                        "message": message,
                    }
            memory_state = {
                "state": "understood",
                "intent": intent,
                "primary_input_supplied": bool(input_path),
                "secondary_input_supplied": bool(effective_secondary_input),
                "last_user_message_id": user_message["id"],
            }
            engine.execution.set_working_memory(
                project_id, "software_interface", memory_state
            )
            if intent["version_ordering"] == "modification_time":
                explicit_rule = _explicit_modification_time_rule(body.request)
                engine.execution.remember(
                    project_id,
                    agent_id=definition["agent_id"],
                    kind="version_ordering_rule",
                    content="软件接口版本对比时，文件修改时间较晚的总览表作为新版。",
                    source_refs=[user_message["id"]],
                    confidence=1.0 if explicit_rule else intent["confidence"],
                    status="confirmed" if explicit_rule else "draft",
                )
            if intent["interaction"] == "answer":
                message = intent["assistant_reply"]
                append_flow_message(
                    "assistant",
                    message,
                    {
                        "channel": "software_interface",
                        "state": "answered",
                        "intent": intent,
                    },
                )
                engine.execution.set_working_memory(
                    project_id,
                    "software_interface",
                    {**memory_state, "state": "answered"},
                )
                return {
                    "state": "answered",
                    "quick_task": definition,
                    "interpretation": intent,
                    "required_fields": [],
                    "message": message,
                }
            if intent["operation"] == "compare_overviews" and not effective_secondary_input:
                prefix = (
                    "明白，将按文件修改时间自动判定新版和旧版；"
                    if intent["version_ordering"] == "modification_time"
                    else "已识别为两个接口总览版本的对比任务；"
                )
                suggestion = _suggested_old_overview(input_path, prior_memory)
                if suggestion:
                    message = (
                        prefix
                        + f"检测到最近的接口总览表“{Path(suggestion).name}”，"
                        "将用它作为旧版；如需更换，请在输入框填写其他旧版路径后重新发送。"
                    )
                    append_flow_message(
                        "assistant",
                        message,
                        {
                            "channel": "software_interface",
                            "state": "needs_input",
                            "intent": intent,
                            "suggested_secondary": suggestion,
                        },
                    )
                    engine.execution.set_working_memory(
                        project_id,
                        "software_interface",
                        {**memory_state, "state": "needs_input"},
                    )
                    return {
                        "state": "needs_input",
                        "quick_task": definition,
                        "interpretation": intent,
                        "required_fields": ["secondary_input_path"],
                        "suggested_secondary": suggestion,
                        "message": message,
                    }
                message = prefix + "请补充另一个接口总览表路径。"
                append_flow_message(
                    "assistant",
                    message,
                    {
                        "channel": "software_interface",
                        "state": "needs_input",
                        "intent": intent,
                    },
                )
                engine.execution.set_working_memory(
                    project_id,
                    "software_interface",
                    {**memory_state, "state": "needs_input"},
                )
                return {
                    "state": "needs_input",
                    "quick_task": definition,
                    "interpretation": intent,
                    "required_fields": ["secondary_input_path"],
                    "message": message,
                }
            if intent["interaction"] == "clarify" or intent["clarification_needed"]:
                message = intent["clarification_question"]
                append_flow_message(
                    "assistant",
                    message,
                    {
                        "channel": "software_interface",
                        "state": "needs_clarification",
                        "intent": intent,
                    },
                )
                engine.execution.set_working_memory(
                    project_id,
                    "software_interface",
                    {**memory_state, "state": "needs_clarification"},
                )
                return {
                    "state": "needs_clarification",
                    "quick_task": definition,
                    "interpretation": intent,
                    "required_fields": [],
                    "message": message,
                }
            progress = (
                f"已理解为“{intent['summary']}”，确定性工具执行中；"
                "接口包较大时约需 1–2 分钟，执行期间也可以继续跟我对话。"
            )
            if (
                intent["operation"] == "compare_overviews"
                and input_path
                and effective_secondary_input
            ):
                progress = (
                    f"已理解为“{intent['summary']}”，确定性工具执行中；"
                    f"本次对比：新版《{Path(input_path).name}》，"
                    f"旧版《{Path(effective_secondary_input).name}》。"
                )
            progress_message = append_flow_message(
                "assistant",
                progress,
                {
                    "channel": "software_interface",
                    "state": "executing",
                    "intent": intent,
                },
            )
            engine.execution.record_event(
                project_id,
                "quick_task_progress",
                {
                    "stage": "tool_running",
                    "operation": intent["operation"],
                    "message_id": progress_message["id"],
                },
            )
            primary_path, secondary_path = _ordered_interface_overviews(
                input_path,
                effective_secondary_input,
                intent["version_ordering"],
            )
            template = SoftwareInterfacePlanRequest(
                operation=intent["operation"],
                primary_path=primary_path,
                secondary_path=secondary_path,
                engine="fast",
                compare_to_existing=intent["compare_to_existing"],
                max_evidence=body.max_evidence,
            )
            run_key = user_message["id"].removeprefix("msg-")
            plan = _software_interface_workplan(template, run_key=run_key)
            plan_record = engine.submit_workplan(
                project_id,
                plan,
                provenance={
                    "source": "quick_task_router",
                    "quick_task_id": task_id,
                    "agent_id": definition["agent_id"],
                    "user_request": body.request.strip(),
                    "run_key": run_key,
                    "user_message_id": user_message["id"],
                    "method": "model_intent_then_fixed_agent_route",
                    "model": intent["model"],
                    "intent": intent,
                },
            )
            approval = engine.approve_workplan(
                project_id,
                "quick-task-router",
                "approved",
                f"Fixed route {task_id} -> {definition['agent_id']}; no human plan review required.",
            )
            run_result = WorkflowRuntime(engine).run_next(
                project_id,
                query=body.request.strip(),
                max_findings=body.max_evidence,
                model=body.model,
            )
            if run_result.get("state") not in {
                "waiting_for_human_approval",
                "work_item_completed",
                "completed",
            }:
                error = run_result.get("error") or "确定性工具未生成结果"
                message = f"执行失败：{error}"
                append_flow_message(
                    "assistant",
                    message,
                    {
                        "channel": "software_interface",
                        "state": "failed",
                        "intent": intent,
                        "run_key": run_key,
                        "error": error,
                    },
                )
                engine.execution.set_working_memory(
                    project_id,
                    "software_interface",
                    {
                        **memory_state,
                        "state": "failed",
                        "operation": intent["operation"],
                        "active_run_key": run_key,
                        "error": error,
                    },
                )
                return {
                    "state": "failed",
                    "quick_task": definition,
                    "interpretation": intent,
                    "message": message,
                    "error": error,
                    "workplan": plan_record,
                    "automatic_plan_approval": approval,
                    "run": run_result,
                    "status": engine.status(project_id),
                }
            message = (
                f"已理解为“{intent['summary']}”，并完成确定性工具执行，"
                "请检查结果和证据后确认。"
            )
            # 收集本次运行的摘要与产物，写入完成消息元数据，
            # 让历史对话中的旧结果仍然可见（结果卡只展示最新一份）
            result_summary: dict = {}
            result_artifacts: list[dict] = []
            overview_artifact = ""
            plan_model = engine.load_workplan(project_id)
            for item in engine.status(project_id).get("work_items", []):
                if not item.get("artifact_exists"):
                    continue
                try:
                    loaded = engine.projects.load_artifact(
                        project_id, plan_model.item(item["id"]).output.artifact
                    )
                except FunctionalSafetyError:
                    continue
                data = loaded.get("data") or {}
                if not result_summary:
                    result_summary = data.get("summary") or {}
                for row in data.get("artifacts") or []:
                    result_artifacts.append(
                        {
                            "type": str(row.get("type", "")),
                            "path": str(row.get("path", "")),
                        }
                    )
                    if "overview" in str(row.get("type", "")) and not overview_artifact:
                        overview_artifact = str(row.get("path", ""))
            append_flow_message(
                "assistant",
                message,
                {
                    "channel": "software_interface",
                    "state": "completed",
                    "intent": intent,
                    "result_summary": result_summary,
                    "result_artifacts": result_artifacts,
                },
            )
            # 登记本次生成的总览表到产物历史，供后续“对比两次输出”解析指代
            overview_history = [
                row
                for row in (prior_memory.get("overview_history") or [])
                if isinstance(row, dict) and row.get("path")
            ]
            generated_overview = overview_artifact or (
                primary_path if intent["operation"] == "generate_overview" else ""
            )
            if generated_overview:
                overview_history = [
                    row
                    for row in overview_history
                    if row.get("path") != generated_overview
                ]
                overview_history.append(
                    {"path": generated_overview, "run_key": run_key}
                )
            engine.execution.set_working_memory(
                project_id,
                "software_interface",
                {
                    **memory_state,
                    "state": "completed",
                    "operation": intent["operation"],
                    "version_ordering": intent["version_ordering"],
                    "active_run_key": run_key,
                    "last_inputs": {
                        "primary_input": primary_path,
                        "secondary_input": effective_secondary_input,
                    },
                    "last_overview": overview_artifact or primary_path,
                    "overview_history": overview_history,
                },
            )
            return {
                "state": "completed",
                "quick_task": definition,
                "interpretation": intent,
                "message": message,
                "workplan": plan_record,
                "automatic_plan_approval": approval,
                "run": run_result,
                "status": engine.status(project_id),
            }

        # 异步执行：立即返回，后续进度经消息+SSE 推送；对话在执行期间保持可用
        lock = _project_locks.setdefault(project_id, threading.Lock())
        if not lock.acquire(blocking=False):
            return {
                "state": "busy",
                "quick_task": definition,
                "message": "上一个任务仍在执行中，等它完成后再发下一个任务。",
            }
        # 用户消息必须同步落库：POST 返回后前端立即 refetch，
        # 后台线程落库会与它竞态，气泡会“消失”到下一次发送才出现
        user_message = engine.append_message(
            project_id,
            "user",
            body.request,
            {
                "channel": "software_interface",
                "model": body.model,
            },
        )

        def background() -> None:
            try:
                execute()
            finally:
                lock.release()

        threading.Thread(target=background, daemon=True).start()
        return {
            "state": "executing",
            "quick_task": definition,
            "message": "任务已开始执行，执行期间可以继续对话。",
        }

    @app.post("/api/projects/{project_id}/approvals/workplan")
    async def approve_workplan(project_id: str, body: ApprovalRequest):
        return engine.approve_workplan(
            project_id,
            body.reviewer,
            body.decision,
            body.comment,
        )

    @app.get("/api/projects/{project_id}/status")
    async def status(project_id: str):
        return engine.status(project_id)

    @app.post("/api/projects/{project_id}/workflow/run")
    async def run_workflow(project_id: str, body: WorkflowRunRequest):
        runtime = WorkflowRuntime(engine, max_tool_rounds=body.max_tool_rounds)
        runner = runtime.run_ready if body.max_parallel > 1 else runtime.run_next
        keywords = {
            "query": body.query,
            "include_draft_knowledge": body.include_draft_knowledge,
            "max_findings": body.max_findings,
            "lease_seconds": body.lease_seconds,
            "max_attempts": body.max_attempts,
            "model": body.model,
        }
        if body.max_parallel > 1:
            keywords["max_parallel"] = body.max_parallel
        return await run_in_threadpool(
            partial(
                runner,
                project_id,
                **keywords,
            )
        )

    @app.get("/api/projects/{project_id}/items/{item_id}/artifact")
    async def item_artifact(project_id: str, item_id: str):
        item = engine.load_workplan(project_id).item(item_id)
        return engine.projects.load_artifact(project_id, item.output.artifact)

    @app.post("/api/projects/{project_id}/items/{item_id}/approval")
    async def approve_item(
        project_id: str, item_id: str, body: ApprovalRequest
    ):
        return engine.approve_item(
            project_id,
            item_id,
            body.reviewer,
            body.decision,
            body.comment,
        )

    @app.get("/api/projects/{project_id}/events")
    async def events(
        project_id: str,
        limit: int = Query(default=100, ge=1, le=1000),
    ):
        engine.projects.load_project(project_id)
        path = engine.projects.project_dir(project_id) / "events" / "events.jsonl"
        if not path.exists():
            return []
        rows = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return rows[-limit:]

    @app.get("/api/projects/{project_id}/execution-events")
    async def execution_events(
        project_id: str,
        limit: int = Query(default=100, ge=1, le=1000),
    ):
        return engine.execution_events(project_id, limit=limit)

    @app.get("/api/projects/{project_id}/download")
    async def download_artifact(project_id: str, path: str):
        """下载项目内产物；路径必须位于项目目录内，防止任意文件读取。"""
        project_root = engine.projects.project_dir(project_id).resolve()
        target = Path(_clean_input_path(path)).resolve()
        if target != project_root and project_root not in target.parents:
            raise ContractError("download path is outside the project directory")
        if not target.is_file():
            raise ContractError(f"file not found: {target.name}")
        return FileResponse(target, filename=target.name)

    @app.get("/api/projects/{project_id}/events/stream")
    async def event_stream(project_id: str, request: Request):
        engine.projects.load_project(project_id)
        raw_cursor = (
            request.headers.get("last-event-id")
            or request.query_params.get("last_event_id")
            or "0"
        )
        try:
            cursor = max(0, int(raw_cursor))
        except ValueError:
            cursor = 0

        async def generator():
            nonlocal cursor
            yield ": connected\n\n"
            idle_turns = 0
            while True:
                if await request.is_disconnected():
                    return
                try:
                    rows = await run_in_threadpool(
                        engine.execution.events_since, project_id, cursor
                    )
                except sqlite3.Error:
                    return
                if rows:
                    idle_turns = 0
                    for row in rows:
                        cursor = row["sequence"]
                        data = json.dumps(row, ensure_ascii=False)
                        yield (
                            f"id: {row['sequence']}\n"
                            f"event: {row['type']}\n"
                            f"data: {data}\n\n"
                        )
                    continue
                idle_turns += 1
                if idle_turns % 15 == 0:
                    yield ": ping\n\n"
                await asyncio.sleep(1)

        return StreamingResponse(
            generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-store",
                "X-Accel-Buffering": "no",
            },
        )

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa_fallback(full_path: str):
        if full_path.startswith(("api/", "static/")):
            return JSONResponse(
                status_code=404,
                content={"error": "NotFound", "detail": full_path},
            )
        if not dist_dir.is_dir():
            return _console_response()
        candidate = (dist_dir / full_path).resolve()
        try:
            candidate.relative_to(dist_dir.resolve())
        except ValueError:
            return _console_response()
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        return _console_response()

    return app


app = create_app()
