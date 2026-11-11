from __future__ import annotations

from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from fs_agent_v2.errors import ContractError  # noqa: E402
from fs_agent_v2.model_contracts import (  # noqa: E402
    AGENT_PROPOSAL_MODELS,
    InterfaceIntentModel,
    WorkplanProposalModel,
    format_validation_error,
    validate_agent_proposal,
    validate_interface_task_result,
    validate_workplan_proposal,
)
from pydantic import ValidationError  # noqa: E402


def valid_intent() -> dict:
    return {
        "interaction": "execute",
        "operation": "compare_overviews",
        "compare_to_existing": False,
        "version_ordering": "modification_time",
        "summary": "按修改时间对比新旧总览表",
        "confidence": 0.9,
        "clarification_needed": False,
        "clarification_question": "",
        "assistant_reply": "",
    }


def valid_workplan() -> dict:
    return {
        "id": "plan-1",
        "version": "1.0.0",
        "goal": "生成接口总览表",
        "inputs": [
            {"id": "IN-1", "type": "interface_overview", "location": "C:/a.xlsx"}
        ],
        "work_items": [
            {
                "id": "W1",
                "title": "生成",
                "agent_id": "software_interface_engineer",
                "depends_on": [],
                "input_refs": ["IN-1"],
                "output": {"id": "OUT-1", "type": "overview", "artifact": "a.xlsx"},
                "approval_required": True,
            }
        ],
    }


class InterfaceIntentContractTest(unittest.TestCase):
    def test_valid_intent_round_trips_with_cleaned_fields(self):
        payload = valid_intent()
        payload["summary"] = "  摘要  "
        intent = InterfaceIntentModel.model_validate(payload).model_dump()
        self.assertEqual("摘要", intent["summary"])
        self.assertEqual(0.9, intent["confidence"])

    def test_extra_fields_are_rejected(self):
        payload = valid_intent()
        payload["input_path"] = "C:/leak.xlsx"
        with self.assertRaises(ValidationError) as ctx:
            InterfaceIntentModel.model_validate(payload)
        message = format_validation_error(ctx.exception)
        self.assertIn("input_path", message)

    def test_compare_to_existing_only_valid_for_generate(self):
        payload = valid_intent()
        payload["compare_to_existing"] = True
        with self.assertRaises(ValidationError) as ctx:
            InterfaceIntentModel.model_validate(payload)
        self.assertIn(
            "compare_to_existing is only valid",
            format_validation_error(ctx.exception),
        )

    def test_modification_time_only_valid_for_compare(self):
        payload = valid_intent()
        payload["operation"] = "generate_overview"
        with self.assertRaises(ValidationError) as ctx:
            InterfaceIntentModel.model_validate(payload)
        self.assertIn(
            "modification_time ordering is only valid",
            format_validation_error(ctx.exception),
        )

    def test_execute_requires_concrete_operation(self):
        payload = valid_intent()
        payload["operation"] = "unclear"
        with self.assertRaises(ValidationError):
            InterfaceIntentModel.model_validate(payload)

    def test_answer_requires_assistant_reply(self):
        payload = valid_intent()
        payload.update(
            {
                "interaction": "answer",
                "operation": "unclear",
                "version_ordering": "input_order",
                "assistant_reply": "  ",
            }
        )
        with self.assertRaises(ValidationError) as ctx:
            InterfaceIntentModel.model_validate(payload)
        self.assertIn(
            "answer interaction requires",
            format_validation_error(ctx.exception),
        )

    def test_clarify_requires_question(self):
        payload = valid_intent()
        payload.update(
            {
                "interaction": "clarify",
                "operation": "unclear",
                "version_ordering": "input_order",
                "clarification_needed": True,
                "clarification_question": "",
            }
        )
        with self.assertRaises(ValidationError) as ctx:
            InterfaceIntentModel.model_validate(payload)
        self.assertIn(
            "clarification request must include",
            format_validation_error(ctx.exception),
        )

    def test_confidence_out_of_range_rejected(self):
        payload = valid_intent()
        payload["confidence"] = 1.5
        with self.assertRaises(ValidationError) as ctx:
            InterfaceIntentModel.model_validate(payload)
        self.assertIn("between 0 and 1", format_validation_error(ctx.exception))

    def test_empty_summary_rejected(self):
        payload = valid_intent()
        payload["summary"] = "   "
        with self.assertRaises(ValidationError) as ctx:
            InterfaceIntentModel.model_validate(payload)
        self.assertIn("summary", format_validation_error(ctx.exception))


class WorkplanProposalContractTest(unittest.TestCase):
    def test_valid_plan_passes(self):
        validate_workplan_proposal(valid_workplan())

    def test_missing_version_rejected(self):
        plan = valid_workplan()
        plan.pop("version")
        with self.assertRaises(ContractError) as ctx:
            validate_workplan_proposal(plan)
        self.assertIn("version", str(ctx.exception))

    def test_empty_work_items_rejected(self):
        plan = valid_workplan()
        plan["work_items"] = []
        with self.assertRaises(ContractError) as ctx:
            validate_workplan_proposal(plan)
        self.assertIn("work_items", str(ctx.exception))

    def test_missing_input_type_rejected(self):
        plan = valid_workplan()
        plan["inputs"] = [{"id": "IN-1", "location": "C:/a.xlsx"}]
        with self.assertRaises(ContractError) as ctx:
            validate_workplan_proposal(plan)
        self.assertIn("type", str(ctx.exception))


class InterfaceTaskResultContractTest(unittest.TestCase):
    def test_generate_reconciles_rows(self):
        validate_interface_task_result(
            {
                "operation": "generate_overview",
                "summary": {"data_rows": 42},
                "result": {
                    "validation": {
                        "passed": True,
                        "summary": {"data_rows": 42},
                    }
                },
            }
        )

    def test_generate_row_mismatch_rejected(self):
        with self.assertRaises(ContractError) as ctx:
            validate_interface_task_result(
                {
                    "operation": "generate_overview",
                    "summary": {"data_rows": 41},
                    "result": {
                        "validation": {
                            "passed": True,
                            "summary": {"data_rows": 42},
                        }
                    },
                }
            )
        self.assertIn("do not reconcile", str(ctx.exception))

    def test_generate_failed_validation_rejected(self):
        with self.assertRaises(ContractError) as ctx:
            validate_interface_task_result(
                {
                    "operation": "generate_overview",
                    "summary": {"data_rows": 42},
                    "result": {
                        "validation": {
                            "passed": False,
                            "summary": {"data_rows": 42},
                        }
                    },
                }
            )
        self.assertIn("failed independent validation", str(ctx.exception))

    def test_compare_summary_contract(self):
        validate_interface_task_result(
            {
                "operation": "compare_overviews",
                "result": {
                    "summary": {
                        "new_rows": 10,
                        "old_rows": 9,
                        "total_diffs": 1,
                        "new_only": 1,
                        "old_only": 0,
                    }
                },
            }
        )
        with self.assertRaises(ContractError):
            validate_interface_task_result(
                {
                    "operation": "compare_overviews",
                    "result": {
                        "summary": {
                            "new_rows": -1,
                            "old_rows": 9,
                            "total_diffs": 1,
                            "new_only": 1,
                            "old_only": 0,
                        }
                    },
                }
            )

    def test_analyze_counts_reconcile_and_findings_covered(self):
        validate_interface_task_result(
            {
                "operation": "analyze_package",
                "result": {
                    "summary": {
                        "parsed_module_files": 61,
                        "skipped_module_files": 0,
                        "module_files": 61,
                        "valid_cross_application_links": 2,
                        "missing_cross_application_links": 1,
                        "cross_application_links": 3,
                        "findings": 2,
                    },
                    "findings": [{"id": "F1"}, {"id": "F2"}],
                },
            }
        )
        with self.assertRaises(ContractError) as ctx:
            validate_interface_task_result(
                {
                    "operation": "analyze_package",
                    "result": {
                        "summary": {
                            "parsed_module_files": 60,
                            "skipped_module_files": 0,
                            "module_files": 61,
                            "cross_application_links": 3,
                            "findings": 1,
                        },
                        "findings": [{"id": "F1"}, {"id": "F2"}],
                    },
                }
            )
        self.assertIn("do not reconcile", str(ctx.exception))

    def test_result_absent_falls_back_to_value_itself(self):
        validate_interface_task_result(
            {
                "operation": "compare_overviews",
                "summary": {
                    "new_rows": 1,
                    "old_rows": 1,
                    "total_diffs": 0,
                    "new_only": 0,
                    "old_only": 0,
                },
            }
        )

    def test_unknown_operation_rejected(self):
        with self.assertRaises(ContractError) as ctx:
            validate_interface_task_result({"operation": "magic"})
        self.assertIn("unsupported software interface operation", str(ctx.exception))


class AgentProposalRegistryTest(unittest.TestCase):
    def test_unregistered_agent_passes_through(self):
        validate_agent_proposal("malfunction_analysis", {"anything": True})

    def test_registered_interface_proposal_enforced(self):
        model = AGENT_PROPOSAL_MODELS["software_interface_engineer"]
        with self.assertRaises(ValidationError):
            model.model_validate({"schema_version": "1.0.0"})


if __name__ == "__main__":
    unittest.main()
