from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable
import json

from pydantic import ValidationError

from ..model_contracts import (
    INTENT_INTERACTIONS,
    INTENT_OPERATIONS,
    INTENT_VERSION_ORDERINGS,
    InterfaceIntentModel,
    format_validation_error,
)
from ..errors import ContractError, ModelError
from ..model import ChatModelClient, OpenAICompatibleChatClient
from .agent_runtime import AgentRuntime
from .dynamic_engine import DynamicWorkEngine


class SoftwareInterfaceIntentRuntime:
    """Continue an interface conversation and emit bounded execution parameters."""

    TOOL_NAME = "submit_software_interface_intent"
    OPERATIONS = INTENT_OPERATIONS
    INTERACTIONS = INTENT_INTERACTIONS
    VERSION_ORDERINGS = INTENT_VERSION_ORDERINGS

    def __init__(
        self,
        engine: DynamicWorkEngine,
        client_factory: Callable[[str], ChatModelClient] | None = None,
        max_rounds: int = 2,
    ):
        self.engine = engine
        self.client_factory = client_factory or self._default_client
        self.max_rounds = max(1, min(int(max_rounds), 3))

    def interpret(
        self,
        request: str,
        *,
        primary_input_supplied: bool,
        secondary_input_supplied: bool,
        conversation: list[dict] | tuple[dict, ...] = (),
        working_memory: dict | None = None,
        long_term_memory: list[dict] | tuple[dict, ...] = (),
        client: ChatModelClient | None = None,
    ) -> dict:
        if not isinstance(request, str) or not request.strip():
            raise ContractError("software interface request must be non-empty")
        agent = self.engine.registry.get("software_interface_engineer")
        if agent.api is None:
            raise ContractError(
                "software_interface_engineer requires an API config for intent understanding"
            )
        model = client or self.client_factory(agent.id)
        messages = self._messages(
            request,
            primary_input_supplied=primary_input_supplied,
            secondary_input_supplied=secondary_input_supplied,
            conversation=conversation,
            working_memory=working_memory,
            long_term_memory=long_term_memory,
        )
        tools = [self._tool_definition()]
        last_error = "model did not return an interface intent"

        for _ in range(self.max_rounds):
            message = model.complete(messages, tools)
            try:
                value = self._message_value(message)
                intent = self._validate(value)
                explicit_operation = self._explicit_operation(request)
                if explicit_operation:
                    intent = self._apply_explicit_operation(
                        intent, explicit_operation
                    )
                return {
                    **intent,
                    "provider": getattr(model, "provider", "injected-test-client"),
                    "model": getattr(model, "model", "unknown"),
                    "generated_at": datetime.now(timezone.utc).isoformat(
                        timespec="seconds"
                    ),
                    "method": (
                        "bounded_interface_intent_explicit_override"
                        if explicit_operation
                        else "bounded_interface_intent"
                    ),
                }
            except (ContractError, ModelError) as exc:
                last_error = str(exc)
                self._append_retry_feedback(messages, message, last_error)
        raise ModelError(
            "interface intent model exceeded its bounded retries: " + last_error
        )

    @staticmethod
    def _append_retry_feedback(
        messages: list[dict], message: dict, error: str
    ) -> None:
        """把契约校验失败回传给模型：有 tool_call 用 role=tool，纯文本回退 role=user。

        协议要求 role=tool 消息必须挂在真实 tool_call_id 上，因此两条路径都保留。
        """
        instruction = (
            "Call submit_software_interface_intent once with corrected arguments."
        )
        tool_calls = message.get("tool_calls")
        if isinstance(tool_calls, list) and tool_calls:
            messages.append(
                {
                    "role": "assistant",
                    "content": message.get("content"),
                    "tool_calls": tool_calls,
                }
            )
            first = tool_calls[0]
            tool_id = first.get("id") if isinstance(first, dict) else None
            if isinstance(tool_id, str) and tool_id:
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_id,
                        "content": json.dumps(
                            {
                                "error": error,
                                "retryable": True,
                                "instruction": instruction,
                            },
                            ensure_ascii=False,
                            separators=(",", ":"),
                        ),
                    }
                )
                return
        messages.append(
            {
                "role": "user",
                "content": "The intent was rejected: " + error + ". " + instruction,
            }
        )

    @staticmethod
    def _explicit_operation(request: str) -> str | None:
        """Recognize only unambiguous current-turn operation switches.

        Conversation memory helps with short follow-ups, but a pending comparison
        must never override a fresh instruction such as ``生成接口总览表``.
        """
        compact = "".join(request.casefold().split())
        compare_words = ("对比", "比较", "差异", "变化")
        generate_words = ("生成", "新建", "制作", "产出")
        overview_words = ("总览", "总表", "overview")
        analyze_words = ("分析接口包", "检查接口包", "查看接口包", "解析接口包")

        wants_compare = any(word in compact for word in compare_words)
        wants_generate = any(word in compact for word in generate_words) and any(
            word in compact for word in overview_words
        )
        if wants_generate and not wants_compare:
            return "generate_overview"
        if wants_compare and any(
            word in compact
            for word in ("版本", "总览", "总表", "新版", "旧版", "两个")
        ):
            return "compare_overviews"
        if any(word in compact for word in analyze_words):
            return "analyze_package"
        return None

    @staticmethod
    def _apply_explicit_operation(intent: dict, operation: str) -> dict:
        summaries = {
            "generate_overview": "基于当前接口包生成接口总览表",
            "compare_overviews": "比较两个接口总览版本的差异",
            "analyze_package": "只读分析当前软件接口包",
        }
        replies = {
            "generate_overview": "好的，我会基于当前接口包生成新的接口总览表。",
            "compare_overviews": "好的，我会比较两个接口总览版本。",
            "analyze_package": "好的，我会只读分析当前软件接口包。",
        }
        version_ordering = (
            intent["version_ordering"]
            if operation == "compare_overviews"
            and intent["operation"] == "compare_overviews"
            else "input_order"
        )
        return {
            **intent,
            "interaction": "execute",
            "operation": operation,
            "compare_to_existing": False,
            "version_ordering": version_ordering,
            "summary": summaries[operation],
            "confidence": max(intent["confidence"], 0.99),
            "clarification_needed": False,
            "clarification_question": "",
            "assistant_reply": replies[operation],
        }

    def _default_client(self, agent_id: str) -> ChatModelClient:
        return OpenAICompatibleChatClient(self.engine.registry.resolve_api(agent_id))

    @classmethod
    def _message_value(cls, message: dict) -> dict:
        calls = message.get("tool_calls") or []
        if calls:
            if not isinstance(calls, list) or len(calls) != 1:
                raise ModelError("intent model must make exactly one tool call")
            _, name, arguments = AgentRuntime._parse_tool_call(calls[0])
            if name != cls.TOOL_NAME:
                raise ModelError(f"intent model called unsupported tool: {name}")
            return arguments
        return AgentRuntime._parse_proposal(message.get("content"))

    @classmethod
    def _validate(cls, value: dict) -> dict:
        """结构化输出 schema 强校验：字段与跨字段规则收编在 InterfaceIntentModel。"""
        try:
            return InterfaceIntentModel.model_validate(value).model_dump()
        except ValidationError as exc:
            raise ContractError(
                "interface intent contract rejected: "
                + format_validation_error(exc)
            ) from exc

    @classmethod
    def _tool_definition(cls) -> dict:
        return {
            "type": "function",
            "function": {
                "name": cls.TOOL_NAME,
                "description": (
                    "Submit one bounded software-interface task intent. This does not "
                    "execute files or choose another Agent."
                ),
                "parameters": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": [
                        "interaction",
                        "operation",
                        "compare_to_existing",
                        "version_ordering",
                        "summary",
                        "confidence",
                        "clarification_needed",
                        "clarification_question",
                        "assistant_reply",
                    ],
                    "properties": {
                        "interaction": {
                            "type": "string",
                            "enum": sorted(cls.INTERACTIONS),
                            "description": (
                                "execute runs one operation; answer replies without a tool; "
                                "clarify asks one blocking question."
                            ),
                        },
                        "operation": {
                            "type": "string",
                            "enum": sorted(cls.OPERATIONS),
                        },
                        "compare_to_existing": {"type": "boolean"},
                        "version_ordering": {
                            "type": "string",
                            "enum": sorted(cls.VERSION_ORDERINGS),
                        },
                        "summary": {
                            "type": "string",
                            "description": "One short Chinese description of the understood task.",
                        },
                        "confidence": {
                            "type": "number",
                            "minimum": 0,
                            "maximum": 1,
                        },
                        "clarification_needed": {"type": "boolean"},
                        "clarification_question": {"type": "string"},
                        "assistant_reply": {
                            "type": "string",
                            "description": (
                                "A concise Chinese answer for answer interactions, or a short "
                                "acknowledgement for execute interactions."
                            ),
                        },
                    },
                },
            },
        }

    @staticmethod
    def _messages(
        request: str,
        *,
        primary_input_supplied: bool,
        secondary_input_supplied: bool,
        conversation: list[dict] | tuple[dict, ...] = (),
        working_memory: dict | None = None,
        long_term_memory: list[dict] | tuple[dict, ...] = (),
    ) -> list[dict]:
        system = (
            "You are the conversational brain inside software_interface_engineer, not a "
            "one-shot classifier. Resolve pronouns, corrections and short follow-up answers "
            "from the supplied conversation history. The current user_request has priority "
            "over conversation history and working memory. If it explicitly requests a new "
            "operation, treat it as a task switch and abandon any older pending clarification "
            "or missing-input state. "
            "You do not inspect or edit files and must not invent file paths. "
            "Use interaction=answer for questions about how the interface workflow works, "
            "terminology, version ordering or other requests that do not ask you to execute a "
            "file operation; answer them directly in Chinese. Use interaction=execute only "
            "when the conversation contains an actionable request. Use interaction=clarify "
            "only when one blocking ambiguity remains after considering the full history. "
            "Choose generate_overview when the user wants a formal interface overview "
            "generated from an interface package; choose compare_overviews when the user "
            "wants differences between two versions or two completed overview workbooks; choose "
            "analyze_package for read-only diagnosis or package inspection; otherwise "
            "choose unclear. If the user says the file with the later or more recent modified "
            "time is the new version, set version_ordering=modification_time; the backend will "
            "compare timestamps deterministically. Otherwise use input_order. "
            "compare_to_existing is only for generation when the user "
            "explicitly asks to compare the generated workbook with the baseline already "
            "inside the same package. Do not ask the same question twice if the user has "
            "answered it in history. Call submit_software_interface_intent exactly once."
        )
        payload = {
            "user_request": request.strip(),
            "provided_inputs": {
                "primary_input_supplied": bool(primary_input_supplied),
                "secondary_input_supplied": bool(secondary_input_supplied),
            },
            "security_boundary": (
                "Input paths are backend-owned structured fields and are intentionally "
                "not shown to the model. Never infer or reproduce them."
            ),
            "working_memory": working_memory or {},
            "confirmed_project_memory": [
                {
                    "kind": item.get("kind"),
                    "content": item.get("content"),
                }
                for item in list(long_term_memory)[-10:]
                if isinstance(item, dict) and isinstance(item.get("content"), str)
            ],
        }
        messages: list[dict] = [{"role": "system", "content": system}]
        for turn in list(conversation)[-12:]:
            if not isinstance(turn, dict):
                continue
            role = turn.get("role")
            content = turn.get("content")
            if role in {"user", "assistant"} and isinstance(content, str) and content.strip():
                messages.append({"role": role, "content": content.strip()[:4000]})
        messages.append(
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}
        )
        return messages
