from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import json
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from fs_agent_v2.orchestration import (  # noqa: E402
    DynamicWorkEngine,
    SoftwareInterfaceIntentRuntime,
)


class FakeIntentClient:
    provider = "test-provider"
    model = "deepseek-flash"

    def __init__(self, responses: list[dict]):
        self.responses = list(responses)
        self.calls: list[dict] = []

    def complete(self, messages: list[dict], tools: list[dict]) -> dict:
        self.calls.append({"messages": messages, "tools": tools})
        return self.responses.pop(0)


def tool_response(**arguments) -> dict:
    return {
        "content": None,
        "tool_calls": [
            {
                "id": "intent-1",
                "type": "function",
                "function": {
                    "name": "submit_software_interface_intent",
                    "arguments": json.dumps(arguments, ensure_ascii=False),
                },
            }
        ],
    }


class SoftwareInterfaceIntentRuntimeTest(unittest.TestCase):
    def setUp(self):
        local_temp = ROOT / "tests" / "_tmp"
        local_temp.mkdir(parents=True, exist_ok=True)
        self.temporary = TemporaryDirectory(dir=local_temp)
        self.engine = DynamicWorkEngine(
            ROOT, project_root=Path(self.temporary.name) / "projects"
        )

    def tearDown(self):
        self.temporary.cleanup()

    def test_model_tool_call_selects_comparison_without_receiving_paths(self):
        client = FakeIntentClient(
            [
                tool_response(
                    interaction="execute",
                    operation="compare_overviews",
                    compare_to_existing=False,
                    version_ordering="input_order",
                    summary="比较新版和旧版接口总览表",
                    confidence=0.98,
                    clarification_needed=False,
                    clarification_question="",
                    assistant_reply="我会比较两个版本。",
                )
            ]
        )
        intent = SoftwareInterfaceIntentRuntime(self.engine).interpret(
            "比较两个版本发生了哪些变化",
            primary_input_supplied=True,
            secondary_input_supplied=True,
            client=client,
        )
        self.assertEqual("compare_overviews", intent["operation"])
        self.assertEqual("deepseek-flash", intent["model"])
        wire_payload = client.calls[0]["messages"][1]["content"]
        self.assertIn('"primary_input_supplied": true', wire_payload)
        self.assertNotIn("C:\\", wire_payload)

    def test_invalid_intent_is_retried_with_bounded_contract_feedback(self):
        client = FakeIntentClient(
            [
                {"content": '{"operation":"invent_operation"}'},
                tool_response(
                    interaction="execute",
                    operation="generate_overview",
                    compare_to_existing=False,
                    version_ordering="input_order",
                    summary="从接口包生成并校验总览表",
                    confidence=0.92,
                    clarification_needed=False,
                    clarification_question="",
                    assistant_reply="我会生成并校验接口总览。",
                ),
            ]
        )
        intent = SoftwareInterfaceIntentRuntime(self.engine).interpret(
            "生成接口总览表",
            primary_input_supplied=True,
            secondary_input_supplied=False,
            client=client,
        )
        self.assertEqual("generate_overview", intent["operation"])
        self.assertEqual(2, len(client.calls))
        self.assertIn(
            "intent was rejected",
            client.calls[1]["messages"][-1]["content"].lower(),
        )

    def test_unclear_intent_must_return_one_clarification_question(self):
        client = FakeIntentClient(
            [
                tool_response(
                    interaction="clarify",
                    operation="unclear",
                    compare_to_existing=False,
                    version_ordering="input_order",
                    summary="无法判断需要生成、对比还是分析",
                    confidence=0.35,
                    clarification_needed=True,
                    clarification_question="你希望生成总览、对比版本，还是只分析接口包？",
                    assistant_reply="",
                )
            ]
        )
        intent = SoftwareInterfaceIntentRuntime(self.engine).interpret(
            "帮我处理一下",
            primary_input_supplied=True,
            secondary_input_supplied=False,
            client=client,
        )
        self.assertTrue(intent["clarification_needed"])
        self.assertEqual("unclear", intent["operation"])

    def test_recent_conversation_is_injected_for_follow_up_version_rule(self):
        client = FakeIntentClient(
            [
                tool_response(
                    interaction="execute",
                    operation="compare_overviews",
                    compare_to_existing=False,
                    version_ordering="modification_time",
                    summary="比较两个版本并按修改时间判断新旧",
                    confidence=0.96,
                    clarification_needed=False,
                    clarification_question="",
                    assistant_reply="明白，将修改时间更晚的文件作为新版。",
                )
            ]
        )
        history = [
            {"role": "user", "content": "比较两个版本"},
            {
                "role": "assistant",
                "content": "请说明如何判断哪个文件是新版。",
            },
        ]
        intent = SoftwareInterfaceIntentRuntime(self.engine).interpret(
            "哪个时间更接近现在就是哪个",
            primary_input_supplied=True,
            secondary_input_supplied=True,
            conversation=history,
            client=client,
        )
        self.assertEqual("modification_time", intent["version_ordering"])
        messages = client.calls[0]["messages"]
        self.assertEqual("比较两个版本", messages[1]["content"])
        self.assertIn("如何判断", messages[2]["content"])
        self.assertIn("哪个时间更接近", messages[3]["content"])

    def test_explicit_generation_switch_overrides_pending_comparison_memory(self):
        client = FakeIntentClient(
            [
                tool_response(
                    interaction="clarify",
                    operation="compare_overviews",
                    compare_to_existing=False,
                    version_ordering="input_order",
                    summary="继续等待旧版接口总览路径",
                    confidence=0.75,
                    clarification_needed=True,
                    clarification_question="请补充旧版接口总览表路径。",
                    assistant_reply="",
                )
            ]
        )
        intent = SoftwareInterfaceIntentRuntime(self.engine).interpret(
            "没有旧版本，先生成接口总览表",
            primary_input_supplied=True,
            secondary_input_supplied=False,
            conversation=[
                {"role": "user", "content": "比较两个版本"},
                {"role": "assistant", "content": "请补充另一个总览表路径。"},
            ],
            working_memory={
                "state": "needs_input",
                "intent": {"operation": "compare_overviews"},
            },
            client=client,
        )

        self.assertEqual("generate_overview", intent["operation"])
        self.assertEqual("execute", intent["interaction"])
        self.assertFalse(intent["clarification_needed"])
        self.assertFalse(intent["compare_to_existing"])
        self.assertEqual("", intent["clarification_question"])
        self.assertEqual(
            "bounded_interface_intent_explicit_override", intent["method"]
        )


if __name__ == "__main__":
    unittest.main()
