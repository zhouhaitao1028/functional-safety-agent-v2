from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import json
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from fs_agent_v2.deterministic import (  # noqa: E402
    AsilCalculator,
    FaultTreeCalculator,
    TraceabilityValidator,
)
from fs_agent_v2.agents import AgentRegistry  # noqa: E402
from fs_agent_v2.errors import ContractError, GateError, ModelError  # noqa: E402
from fs_agent_v2.knowledge import KnowledgeService, WikiBuilder  # noqa: E402
from fs_agent_v2.orchestration import (  # noqa: E402
    AgentRuntime,
    DynamicWorkEngine,
    ManagerRuntime,
    StageEngine,
    WorkPlan,
)


def example(name: str) -> dict:
    return json.loads((ROOT / "examples" / name).read_text(encoding="utf-8"))


class FakeToolCallingClient:
    provider = "fake-provider"
    model = "fake-tool-model"

    def __init__(self, responses: list[dict]):
        self.responses = list(responses)
        self.requests: list[dict] = []

    def complete(self, messages: list[dict], tools: list[dict]) -> dict:
        self.requests.append({"messages": list(messages), "tools": tools})
        return self.responses.pop(0)


class DeterministicServicesTest(unittest.TestCase):
    def test_asil_is_computed_by_rule_file(self):
        calculator = AsilCalculator(ROOT / "knowledge" / "rules" / "asil_matrix.json")
        self.assertEqual("D", calculator.calculate("S3", "E4", "C3"))
        self.assertEqual("QM", calculator.calculate("S2", "E1", "C3"))

    def test_fault_tree_minimal_cut_sets(self):
        result = FaultTreeCalculator().calculate(example("fault_tree.json"))
        self.assertEqual(
            [["BE-AEB-001"], ["BE-AEB-002", "BE-AEB-003"]],
            result,
        )

    def test_traceability_reports_missing_and_dangling(self):
        artifact = {
            "links": [
                {"source_id": "A", "target_id": "B", "type": "derives"},
                {"source_id": "A", "target_id": "MISSING", "type": "derives"},
            ],
            "required_target_ids": ["B", "C"],
        }
        result = TraceabilityValidator().validate(artifact, {"A", "B", "C"})
        self.assertFalse(result["valid"])
        self.assertEqual(["C"], result["missing_coverage"])
        self.assertEqual("MISSING", result["dangling_references"][0]["id"])


class PipelineGateTest(unittest.TestCase):
    def setUp(self):
        local_temp = ROOT / "tests" / "_tmp"
        local_temp.mkdir(parents=True, exist_ok=True)
        self.temporary = TemporaryDirectory(dir=local_temp)
        self.engine = StageEngine(ROOT, Path(self.temporary.name) / "projects")
        self.engine.projects.init_project("demo", "Demo")

    def tearDown(self):
        self.temporary.cleanup()

    def test_downstream_is_blocked_until_human_approval(self):
        self.engine.submit_proposal("demo", "function_model", example("function_model.json"))
        with self.assertRaises(GateError):
            self.engine.submit_proposal(
                "demo", "malfunction_analysis", example("malfunction_analysis.json")
            )

        stage = self.engine.pipeline.stage("function_model")
        self.engine.projects.approve(
            "demo", stage.id, stage.output_artifact, "reviewer", "approved", "ok"
        )
        record = self.engine.submit_proposal(
            "demo", "malfunction_analysis", example("malfunction_analysis.json")
        )
        self.assertEqual("02_malfunction_analysis.json", record["artifact"])

    def test_artifact_change_invalidates_approval(self):
        original = example("function_model.json")
        self.engine.submit_proposal("demo", "function_model", original)
        stage = self.engine.pipeline.stage("function_model")
        self.engine.projects.approve(
            "demo", stage.id, stage.output_artifact, "reviewer", "approved", "ok"
        )
        changed = json.loads(json.dumps(original, ensure_ascii=False))
        changed["functions"][0]["purpose"] += "（已修改）"
        self.engine.submit_proposal("demo", "function_model", changed)
        status = self.engine.projects.approval_status(
            "demo", stage.id, stage.output_artifact
        )
        self.assertFalse(status["approved"])
        self.assertEqual("artifact_changed", status["reason"])

    def test_hara_asil_is_enriched_after_approved_dependencies(self):
        self.engine.submit_proposal("demo", "function_model", example("function_model.json"))
        self._approve("function_model")
        self.engine.submit_proposal(
            "demo", "malfunction_analysis", example("malfunction_analysis.json")
        )
        self._approve("malfunction_analysis")
        record = self.engine.submit_proposal(
            "demo", "hara_assessment", example("hara_assessment.json")
        )
        assessment = record["data"]["assessments"][0]
        self.assertEqual("D", assessment["asil"])
        self.assertEqual("0.1.0-draft", assessment["asil_rule_version"])

    def test_full_demo_pipeline_produces_deterministic_results(self):
        stages = [
            ("function_model", "function_model.json"),
            ("malfunction_analysis", "malfunction_analysis.json"),
            ("hara_assessment", "hara_assessment.json"),
            ("safety_goals", "safety_goals.json"),
            ("fault_tree", "fault_tree.json"),
            ("functional_safety_requirements", "fsr.json"),
        ]
        records = {}
        for stage_id, filename in stages:
            records[stage_id] = self.engine.submit_proposal(
                "demo", stage_id, example(filename)
            )
            self._approve(stage_id)

        self.assertEqual(
            [["BE-AEB-001"], ["BE-AEB-002", "BE-AEB-003"]],
            records["fault_tree"]["data"]["minimal_cut_sets"],
        )
        traceability = self.engine.submit_proposal(
            "demo", "traceability", example("traceability.json")
        )
        self.assertTrue(traceability["data"]["validation"]["valid"])

    def _approve(self, stage_id: str):
        stage = self.engine.pipeline.stage(stage_id)
        self.engine.projects.approve(
            "demo", stage.id, stage.output_artifact, "reviewer", "approved", "ok"
        )


class DynamicManagerPlanTest(unittest.TestCase):
    def setUp(self):
        local_temp = ROOT / "tests" / "_tmp"
        local_temp.mkdir(parents=True, exist_ok=True)
        self.temporary = TemporaryDirectory(dir=local_temp)
        self.engine = DynamicWorkEngine(
            ROOT,
            Path(self.temporary.name) / "projects",
            Path(self.temporary.name) / "knowledge.db",
        )
        self.engine.knowledge_service.build(reset=True)
        self.engine.projects.init_project("dynamic", "Dynamic")
        self.plan = example("workplan_concept_to_fsr.json")

    def tearDown(self):
        self.temporary.cleanup()

    def test_registry_has_independent_api_contracts_without_plaintext_secrets(self):
        registry = AgentRegistry(ROOT / "config" / "agents")
        self.assertIsNotNone(registry.get("manager_agent").api)
        self.assertIsNone(registry.get("traceability_validator").api)
        resolved = registry.resolve_api(
            "hara_engineer",
            {
                "FS_AGENT_HARA_PROVIDER": "openai_compatible",
                "FS_AGENT_HARA_BASE_URL": "https://model.example/v1",
                "FS_AGENT_HARA_API_KEY": "test-only-key",
                "FS_AGENT_HARA_MODEL": "test-model",
            },
        )
        self.assertEqual("test-model", resolved.model)

    def test_workplan_is_validated_against_agent_capabilities(self):
        plan = WorkPlan.from_dict(self.plan, self.engine.registry)
        self.assertEqual("function_model_engineer", plan.item("W01").agent_id)
        invalid = json.loads(json.dumps(self.plan, ensure_ascii=False))
        invalid["work_items"][0]["output"]["type"] = "fault_tree"
        with self.assertRaises(ContractError):
            WorkPlan.from_dict(invalid, self.engine.registry)

        disconnected = json.loads(json.dumps(self.plan, ensure_ascii=False))
        disconnected["work_items"][1]["input_refs"] = ["IN-SOURCE"]
        with self.assertRaises(ContractError):
            WorkPlan.from_dict(disconnected, self.engine.registry)

        colliding_artifacts = json.loads(json.dumps(self.plan, ensure_ascii=False))
        colliding_artifacts["work_items"][1]["output"]["artifact"] = (
            colliding_artifacts["work_items"][0]["output"]["artifact"]
        )
        with self.assertRaisesRegex(ContractError, "duplicate output artifact"):
            WorkPlan.from_dict(colliding_artifacts, self.engine.registry)

    def test_plan_and_each_dependency_require_human_approval(self):
        self.engine.submit_workplan("dynamic", self.plan)
        with self.assertRaises(GateError):
            self.engine.submit_item("dynamic", "W01", example("function_model.json"))

        self.engine.approve_workplan("dynamic", "manager-reviewer", "approved", "ok")
        self.engine.submit_item("dynamic", "W01", example("function_model.json"))
        with self.assertRaises(GateError):
            self.engine.submit_item(
                "dynamic", "W02", example("malfunction_analysis.json")
            )

        self.engine.approve_item("dynamic", "W01", "domain-reviewer", "approved", "ok")
        record = self.engine.submit_item(
            "dynamic", "W02", example("malfunction_analysis.json")
        )
        self.assertEqual("02_malfunction_analysis.json", record["artifact"])
        context = self.engine.prepare_item_context(
            "dynamic", "W02", query="功能失效", include_draft_knowledge=True
        )
        self.assertIn("OUT-FUNCTION", context.inputs)
        self.assertTrue(context.skills)

    def test_model_automatically_calls_knowledge_tool_before_proposal(self):
        self._prepare_first_item()
        client = FakeToolCallingClient(
            [
                {
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call-knowledge-1",
                            "type": "function",
                            "function": {
                                "name": "knowledge_retrieve_evidence",
                                "arguments": json.dumps(
                                    {
                                        "query": "HARA scenario",
                                        "tags": ["hara_scenario"],
                                    }
                                ),
                            },
                        }
                    ],
                },
                {
                    "content": json.dumps(
                        example("malfunction_analysis.json"), ensure_ascii=False
                    )
                },
            ]
        )
        record = AgentRuntime(self.engine).run_item(
            "dynamic",
            "W02",
            include_draft_knowledge=True,
            client=client,
        )
        self.assertEqual("fake-tool-model", record["provenance"]["model"])
        self.assertEqual(
            "knowledge_retrieve_evidence",
            record["provenance"]["tool_calls"][0]["tool"],
        )
        self.assertTrue(record["provenance"]["evidence_refs"])
        self.assertEqual("tool", client.requests[1]["messages"][-1]["role"])
        knowledge_tool = next(
            tool
            for tool in client.requests[0]["tools"]
            if tool["function"]["name"] == "knowledge_retrieve_evidence"
        )
        self.assertEqual(
            ["company_template", "failure_mode", "hara_scenario"],
            knowledge_tool["function"]["parameters"]["properties"]["tags"][
                "items"
            ]["enum"],
        )

    def test_model_can_correct_an_invalid_knowledge_tag(self):
        self._prepare_first_item()
        client = FakeToolCallingClient(
            [
                {
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call-invalid-tag",
                            "type": "function",
                            "function": {
                                "name": "knowledge_retrieve_evidence",
                                "arguments": json.dumps(
                                    {"query": "HARA scenario", "tags": ["not_allowed"]}
                                ),
                            },
                        }
                    ],
                },
                {
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call-corrected-tag",
                            "type": "function",
                            "function": {
                                "name": "knowledge_retrieve_evidence",
                                "arguments": json.dumps(
                                    {
                                        "query": "HARA scenario",
                                        "tags": ["hara_scenario"],
                                    }
                                ),
                            },
                        }
                    ],
                },
                {
                    "content": json.dumps(
                        example("malfunction_analysis.json"), ensure_ascii=False
                    )
                },
            ]
        )
        record = AgentRuntime(self.engine).run_item(
            "dynamic",
            "W02",
            include_draft_knowledge=True,
            client=client,
        )
        calls = record["provenance"]["tool_calls"]
        self.assertIn("error", calls[0])
        self.assertTrue(calls[1]["evidence_refs"])
        retry_message = client.requests[1]["messages"][-1]
        self.assertEqual("tool", retry_message["role"])
        self.assertIn("retryable", retry_message["content"])

    def test_model_cannot_skip_required_knowledge_retrieval(self):
        self._prepare_first_item()
        client = FakeToolCallingClient(
            [
                {
                    "content": json.dumps(
                        example("malfunction_analysis.json"), ensure_ascii=False
                    )
                }
            ]
        )
        with self.assertRaises(ModelError):
            AgentRuntime(self.engine).run_item("dynamic", "W02", client=client)

    def test_manager_inspects_sources_and_registry_before_submitting_plan(self):
        source = Path(self.temporary.name) / "system-description.txt"
        source.write_text("AEB system description", encoding="utf-8")
        inputs = [
            {
                "id": "IN-SOURCE",
                "type": "source_material",
                "location": str(source),
            }
        ]
        plan = {
            "id": "manager-generated-plan",
            "version": "1.0.0",
            "goal": "建立功能模型",
            "inputs": inputs,
            "work_items": [
                {
                    "id": "W01",
                    "title": "建立功能模型",
                    "agent_id": "function_model_engineer",
                    "depends_on": [],
                    "input_refs": ["IN-SOURCE"],
                    "output": {
                        "id": "OUT-FUNCTION",
                        "type": "function_model",
                        "artifact": "01_function_model.json",
                    },
                    "approval_required": True,
                }
            ],
        }
        client = FakeToolCallingClient(
            [
                {
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call-registry",
                            "type": "function",
                            "function": {
                                "name": "agent_registry_list",
                                "arguments": "{}",
                            },
                        },
                        {
                            "id": "call-source",
                            "type": "function",
                            "function": {
                                "name": "source_inspect",
                                "arguments": json.dumps(
                                    {"reference_id": "IN-SOURCE"}
                                ),
                            },
                        },
                    ],
                },
                {"content": json.dumps(plan, ensure_ascii=False)},
            ]
        )
        record = ManagerRuntime(self.engine).plan(
            "dynamic", "读取系统描述并建立功能模型", inputs, client=client
        )
        self.assertEqual("manager_agent", record["provenance"]["agent_id"])
        self.assertEqual("function_model_engineer", record["data"]["work_items"][0]["agent_id"])
        self.assertEqual(
            ["agent_registry_list", "source_inspect"],
            [row["tool"] for row in record["provenance"]["tool_calls"]],
        )

    def _prepare_first_item(self):
        self.engine.submit_workplan("dynamic", self.plan)
        self.engine.approve_workplan("dynamic", "manager-reviewer", "approved", "ok")
        self.engine.submit_item("dynamic", "W01", example("function_model.json"))
        self.engine.approve_item("dynamic", "W01", "domain-reviewer", "approved", "ok")


class KnowledgePlatformTest(unittest.TestCase):
    def setUp(self):
        local_temp = ROOT / "tests" / "_tmp"
        local_temp.mkdir(parents=True, exist_ok=True)
        self.temporary = TemporaryDirectory(dir=local_temp)
        self.temp_root = Path(self.temporary.name)
        self.service = KnowledgeService(ROOT, self.temp_root / "knowledge.db")

    def tearDown(self):
        self.temporary.cleanup()

    def test_hybrid_rag_returns_versioned_evidence_and_graph_context(self):
        result = self.service.build(reset=True)
        self.assertGreaterEqual(result["documents"], 4)
        pack = self.service.retrieve("ASIL matrix", approved_only=False, graph_depth=1)
        self.assertTrue(pack["evidence"])
        self.assertIn("knowledge_id", pack["evidence"][0])
        self.assertTrue(pack["graph_context"])

    def test_wiki_is_built_from_the_same_index(self):
        self.service.build(reset=True)
        result = WikiBuilder(self.service.index).build(self.temp_root / "wiki")
        output = Path(result["output"])
        self.assertEqual("功能安全知识库.html", output.name)
        self.assertTrue(output.exists())
        self.assertIn("这个 Wiki 怎么用", output.read_text(encoding="utf-8"))
        detail_names = [path.name for path in output.parent.glob("*.html")]
        self.assertTrue(any("asil-matrix-initial" in name for name in detail_names))

    def test_llm_agents_reference_the_knowledge_skill(self):
        registry = AgentRegistry(ROOT / "config" / "agents")
        model_agents = [agent for agent in registry.list() if agent.kind == "llm"]
        self.assertTrue(model_agents)
        for agent in model_agents:
            self.assertIn("functional-safety-knowledge", agent.skills)
            stored = registry.skill_documents(agent.id)
            self.assertEqual("functional-safety-knowledge", stored[0].id)
            self.assertIn("Retrieval policy", stored[0].instructions)


if __name__ == "__main__":
    unittest.main()

