"""Pydantic contracts for model-facing structured outputs and deterministic results.

本模块是「结构化输出 schema 校验」的单一收口（与既有 contracts.py 的流程
管线契约相互独立）：
- 模型最终提案（接口意图、Manager 工作计划、Agent 提案）在提交前先过 pydantic 强校验；
- 校验失败的信息经 format_validation_error 压缩成模型可读懂的修正指令，
  由调用方的有界工具循环以 role=tool / role=user 消息回传实现自愈重试；
- 确定性引擎结果契约（software_interface_contract）的结构部分也在此收编，
  SHA-256、产物存在性、证据锚点等领域校验仍由 dynamic_engine 代码执行。
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)
from pydantic.types import StringConstraints

from .errors import ContractError

NonEmptyStr = Annotated[str, StringConstraints(min_length=1)]


def format_validation_error(exc: ValidationError, *, limit: int = 5) -> str:
    """把 pydantic ValidationError 压缩为「字段路径 + 期望 + 实际值」的可读消息。

    截断到前 limit 条，避免自愈回传时 token 膨胀。
    """
    errors = exc.errors()
    parts: list[str] = []
    for item in errors[:limit]:
        loc = ".".join(
            str(piece) for piece in item.get("loc", ()) if piece != "__root__"
        )
        loc = loc or "<root>"
        msg = str(item.get("msg", "invalid value"))
        rendered = repr(item.get("input"))
        if len(rendered) > 80:
            rendered = rendered[:77] + "..."
        parts.append(f"{loc}: {msg}（当前值: {rendered}）")
    if len(errors) > limit:
        parts.append(f"（另有 {len(errors) - limit} 条问题未列出）")
    return "；".join(parts)


# --------------------------------------------------------------------------
# 接口任务意图（SoftwareInterfaceIntentRuntime 的最终提案）
# --------------------------------------------------------------------------

INTENT_INTERACTIONS = frozenset({"execute", "answer", "clarify"})
INTENT_OPERATIONS = frozenset(
    {"generate_overview", "compare_overviews", "analyze_package", "unclear"}
)
INTENT_VERSION_ORDERINGS = frozenset({"input_order", "modification_time"})


class InterfaceIntentModel(BaseModel):
    """submit_software_interface_intent 工具参数的强校验契约。

    字段与跨字段规则 1:1 迁移自 SoftwareInterfaceIntentRuntime._validate，
    枚举常量反向提供给它做单一事实源。
    """

    model_config = ConfigDict(extra="forbid")

    interaction: str
    operation: str
    compare_to_existing: bool
    version_ordering: str
    summary: str
    confidence: float
    clarification_needed: bool
    clarification_question: str = ""
    assistant_reply: str = ""

    @field_validator("interaction")
    @classmethod
    def _check_interaction(cls, value: str) -> str:
        if value not in INTENT_INTERACTIONS:
            raise ValueError(f"unsupported interface interaction: {value}")
        return value

    @field_validator("operation")
    @classmethod
    def _check_operation(cls, value: str) -> str:
        if value not in INTENT_OPERATIONS:
            raise ValueError(f"unsupported interface operation: {value}")
        return value

    @field_validator("version_ordering")
    @classmethod
    def _check_version_ordering(cls, value: str) -> str:
        if value not in INTENT_VERSION_ORDERINGS:
            raise ValueError(f"unsupported version ordering: {value}")
        return value

    @field_validator("summary")
    @classmethod
    def _clean_summary(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("interface intent summary must be non-empty")
        return cleaned[:500]

    @field_validator("confidence")
    @classmethod
    def _check_confidence(cls, value: float) -> float:
        if not 0 <= value <= 1:
            raise ValueError("interface intent confidence must be between 0 and 1")
        return float(value)

    @field_validator("clarification_question", "assistant_reply")
    @classmethod
    def _clean_text(cls, value: str) -> str:
        return value.strip()

    @field_validator("clarification_question")
    @classmethod
    def _truncate_question(cls, value: str) -> str:
        return value[:500]

    @field_validator("assistant_reply")
    @classmethod
    def _truncate_reply(cls, value: str) -> str:
        return value[:1000]

    @model_validator(mode="after")
    def _cross_field_rules(self) -> "InterfaceIntentModel":
        if (
            self.operation != "generate_overview"
            and self.compare_to_existing
        ):
            raise ValueError(
                "compare_to_existing is only valid for generate_overview"
            )
        if (
            self.version_ordering == "modification_time"
            and self.operation != "compare_overviews"
        ):
            raise ValueError(
                "modification_time ordering is only valid for compare_overviews"
            )
        if self.interaction == "execute":
            if self.operation == "unclear" or self.clarification_needed:
                raise ValueError(
                    "execute interaction requires one concrete operation without clarification"
                )
        elif self.interaction == "answer":
            if self.operation != "unclear" or self.clarification_needed:
                raise ValueError(
                    "answer interaction must not schedule an operation or clarification"
                )
            if not self.assistant_reply.strip():
                raise ValueError("answer interaction requires assistant_reply")
        elif not self.clarification_needed:
            raise ValueError("clarify interaction must request clarification")
        if self.clarification_needed and not self.clarification_question.strip():
            raise ValueError("clarification request must include one question")
        return self


# --------------------------------------------------------------------------
# Manager 工作计划提案（结构部分；inputs 逐字复制校验仍在 ManagerRuntime）
# --------------------------------------------------------------------------


class WorkplanInputModel(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: NonEmptyStr
    type: NonEmptyStr
    location: str = ""


class WorkplanItemOutputModel(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: NonEmptyStr
    type: NonEmptyStr
    artifact: NonEmptyStr


class WorkplanItemModel(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: NonEmptyStr
    title: NonEmptyStr
    agent_id: NonEmptyStr
    depends_on: list[NonEmptyStr] = Field(default_factory=list)
    input_refs: list[NonEmptyStr] = Field(default_factory=list)
    output: WorkplanItemOutputModel
    approval_required: bool
    parameters: dict = Field(default_factory=dict)


class WorkplanProposalModel(BaseModel):
    """Manager 最终提案的结构契约，镜像 schemas/workplan.schema.json 的必需字段。"""

    model_config = ConfigDict(extra="allow")

    id: NonEmptyStr
    version: NonEmptyStr
    goal: NonEmptyStr
    inputs: list[WorkplanInputModel]
    work_items: list[WorkplanItemModel] = Field(min_length=1)


def validate_workplan_proposal(proposal: dict) -> None:
    try:
        WorkplanProposalModel.model_validate(proposal)
    except ValidationError as exc:
        raise ContractError(
            "Manager workplan rejected: " + format_validation_error(exc)
        ) from exc


# --------------------------------------------------------------------------
# 软件接口确定性任务结果契约（结构部分；领域校验仍在 dynamic_engine）
# --------------------------------------------------------------------------


class InterfaceGenerationValidationSummary(BaseModel):
    model_config = ConfigDict(extra="allow")

    data_rows: int


class InterfaceGenerationValidation(BaseModel):
    model_config = ConfigDict(extra="allow")

    passed: bool
    summary: InterfaceGenerationValidationSummary

    @field_validator("passed")
    @classmethod
    def _must_pass(cls, value: bool) -> bool:
        if not value:
            raise ValueError(
                "generated software interface overview failed independent "
                "validation"
            )
        return value


class InterfaceGenerationResult(BaseModel):
    model_config = ConfigDict(extra="allow")

    validation: InterfaceGenerationValidation


class InterfaceGenerationTask(BaseModel):
    model_config = ConfigDict(extra="allow")

    summary: dict
    result: InterfaceGenerationResult

    @model_validator(mode="after")
    def _reconcile_rows(self) -> "InterfaceGenerationTask":
        if self.result.validation.summary.data_rows != self.summary.get(
            "data_rows"
        ):
            raise ValueError(
                "software interface generation row counts do not reconcile"
            )
        return self


class InterfaceComparisonSummary(BaseModel):
    model_config = ConfigDict(extra="allow")

    new_rows: int = Field(ge=0)
    old_rows: int = Field(ge=0)
    total_diffs: int = Field(ge=0)
    new_only: int = Field(ge=0)
    old_only: int = Field(ge=0)


class InterfaceComparisonResult(BaseModel):
    model_config = ConfigDict(extra="allow")

    summary: InterfaceComparisonSummary


class InterfaceComparisonTask(BaseModel):
    model_config = ConfigDict(extra="allow")

    result: InterfaceComparisonResult


class InterfaceAnalysisSummary(BaseModel):
    model_config = ConfigDict(extra="allow")

    parsed_module_files: int = 0
    skipped_module_files: int = 0
    module_files: int = -1
    valid_cross_application_links: int = 0
    missing_cross_application_links: int = 0
    cross_application_links: int = -1
    findings: int = 0

    @model_validator(mode="after")
    def _reconcile_counts(self) -> "InterfaceAnalysisSummary":
        if (
            self.parsed_module_files + self.skipped_module_files
            != self.module_files
        ):
            raise ValueError(
                "software interface report module counts do not reconcile"
            )
        if (
            self.valid_cross_application_links
            + self.missing_cross_application_links
            != self.cross_application_links
        ):
            raise ValueError(
                "software interface report link counts do not reconcile"
            )
        return self


class InterfaceAnalysisResult(BaseModel):
    model_config = ConfigDict(extra="allow")

    summary: InterfaceAnalysisSummary
    findings: list[object] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_findings(self) -> "InterfaceAnalysisResult":
        if self.summary.findings < len(self.findings):
            raise ValueError(
                "software interface report finding count is smaller than its "
                "evidence list"
            )
        return self


class InterfaceAnalysisTask(BaseModel):
    model_config = ConfigDict(extra="allow")

    result: InterfaceAnalysisResult


_INTERFACE_TASK_MODELS = {
    "generate_overview": InterfaceGenerationTask,
    "compare_overviews": InterfaceComparisonTask,
    "analyze_package": InterfaceAnalysisTask,
}


def validate_interface_task_result(value: dict) -> None:
    """收编自 dynamic_engine._validate_software_interface_contract 的结构部分。

    result 字段缺省时以 value 自身充当 result（与旧逻辑 result = value.get("result", value) 一致）。
    """
    operation = value.get("operation")
    model = _INTERFACE_TASK_MODELS.get(operation)
    if model is None:
        raise ContractError(
            f"unsupported software interface operation: {operation!r}"
        )
    normalized = dict(value)
    result = normalized.get("result")
    if not isinstance(result, dict):
        normalized["result"] = value
    try:
        model.model_validate(normalized)
    except ValidationError as exc:
        raise ContractError(
            "software interface task result rejected: "
            + format_validation_error(exc)
        ) from exc


# --------------------------------------------------------------------------
# Agent 提案注册表（agent_id -> pydantic 模型；未注册的 Agent 回退不阻断）
# --------------------------------------------------------------------------


class InterfaceArtifactModel(BaseModel):
    model_config = ConfigDict(extra="allow")

    type: NonEmptyStr
    path: NonEmptyStr
    sha256: str = Field(min_length=64)


class InterfaceTaskProposalModel(BaseModel):
    """software_interface_engineer 的输出契约，镜像
    schemas/software_interface_task_result.schema.json 的必需字段。"""

    model_config = ConfigDict(extra="allow")

    schema_version: NonEmptyStr
    generated_at: NonEmptyStr
    operation: Literal["analyze_package", "generate_overview", "compare_overviews"]
    status: Literal["completed"]
    source: dict
    source_fingerprint: str = Field(min_length=64)
    summary: dict
    artifacts: list[InterfaceArtifactModel]
    result: dict
    requires_human_confirmation: bool
    limitations: list[NonEmptyStr]


AGENT_PROPOSAL_MODELS: dict[str, type[BaseModel]] = {
    "software_interface_engineer": InterfaceTaskProposalModel,
}


def validate_agent_proposal(agent_id: str, proposal: dict) -> None:
    """按注册表校验 Agent 最终提案；未注册的 Agent 直接放行（语义不变）。"""
    model = AGENT_PROPOSAL_MODELS.get(agent_id)
    if model is None:
        return
    try:
        model.model_validate(proposal)
    except ValidationError as exc:
        raise ContractError(
            f"agent {agent_id} proposal rejected: " + format_validation_error(exc)
        ) from exc
