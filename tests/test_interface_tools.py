from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZipFile
import hashlib
import json
import sys
import unittest
from unittest.mock import patch

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from fs_agent_v2.agents import AgentRegistry  # noqa: E402
from fs_agent_v2.interface_tools import (  # noqa: E402
    InterfaceAnalysisService,
    InterfaceDeliveryService,
    LegacyInterfaceDeliveryService,
)
from fs_agent_v2.interface_tools.mcp_server import (  # noqa: E402
    AllowedPathPolicy,
    InterfaceMcpServer,
)
from fs_agent_v2.interface_tools.fast_generation import (  # noqa: E402
    FastInterfaceOverviewGenerator,
    HEADERS,
    _MatrixRow,
)
from fs_agent_v2.orchestration import (  # noqa: E402
    DynamicWorkEngine,
    WorkflowRuntime,
    WorkPlan,
)
from fs_agent_v2.schema import SchemaRegistry  # noqa: E402


class InterfaceToolsTest(unittest.TestCase):
    def setUp(self):
        local_temp = ROOT / "tests" / "_tmp"
        local_temp.mkdir(parents=True, exist_ok=True)
        self.temporary = TemporaryDirectory(dir=local_temp)
        self.temp_root = Path(self.temporary.name)
        self.package = self.temp_root / "interface-package"
        self.modules = self.package / "Inputs" / "01_PIE_400"
        self.matrix_dir = self.package / "Inputs" / "02_PIE_Matrix"
        self.config_dir = self.package / "Inputs" / "03_UnitConfig"
        self.modules.mkdir(parents=True)
        self.matrix_dir.mkdir(parents=True)
        self.config_dir.mkdir(parents=True)
        self._module(
            "Producer.xlsm",
            "SIG_SHARED_U8",
            "Output",
            "PpShared_DeShared",
        )
        self._module(
            "Consumer.xlsm",
            "SIG_SHARED_U8",
            "Input",
            "PpShared_DeShared",
        )
        self._unit_config(self.config_dir / "Unit2ComponentConfig.xlsx", True)
        self._unit_config(
            self.config_dir / "Unit2ComponentConfig(PIE100).xlsx", False
        )
        self._pie_matrix()

    def tearDown(self):
        self.temporary.cleanup()

    def test_native_package_analysis_writes_hashed_inventory(self):
        output = self.temp_root / "output"
        report = InterfaceAnalysisService().analyze_package(
            self.package, output_dir=output
        )
        self.assertEqual("native_basic_match", report["mode"])
        self.assertEqual(2, report["summary"]["module_files"])
        self.assertEqual(2, report["summary"]["parsed_module_files"])
        self.assertEqual(2, report["summary"]["extracted_interface_records"])
        self.assertEqual(1, report["summary"]["cross_application_links"])
        self.assertEqual(1, report["summary"]["valid_cross_application_links"])
        self.assertEqual(0, report["summary"]["missing_cross_application_links"])
        self.assertEqual(1, report["configuration_diff"]["internal"]["added"])
        inventory = Path(report["artifacts"][0]["path"])
        self.assertTrue(inventory.exists())
        self.assertEqual(64, len(report["artifacts"][0]["sha256"]))
        SchemaRegistry(ROOT / "schemas").validate(
            "software_interface_analysis.schema.json", report
        )

    def test_agent_registry_and_workplan_route_to_interface_engineer(self):
        registry = AgentRegistry(ROOT / "config" / "agents")
        agent = registry.get("software_interface_engineer")
        self.assertEqual("hybrid", agent.kind)
        self.assertIsNotNone(agent.api)
        self.assertIn("fusa-software-interface-traceability", agent.skills)
        plan = json.loads(
            (ROOT / "examples" / "workplan_software_interface_analysis.json").read_text(
                encoding="utf-8"
            )
        )
        plan["inputs"][0]["location"] = str(self.package)
        parsed = WorkPlan.from_dict(plan, registry)
        self.assertEqual("software_interface_engineer", parsed.item("W01").agent_id)
        self.assertEqual("analyze_package", parsed.item("W01").parameters["operation"])

    def test_dynamic_interface_agent_requires_plan_approval_and_saves_report(self):
        engine = DynamicWorkEngine(
            ROOT,
            self.temp_root / "projects",
            self.temp_root / "knowledge.db",
        )
        engine.projects.init_project("interface-demo", "Interface demo")
        plan = json.loads(
            (ROOT / "examples" / "workplan_software_interface_analysis.json").read_text(
                encoding="utf-8"
            )
        )
        plan["inputs"][0]["location"] = str(self.package)
        engine.submit_workplan("interface-demo", plan)
        engine.approve_workplan("interface-demo", "manager", "approved", "ok")
        result = WorkflowRuntime(engine).run_next("interface-demo")
        self.assertEqual("waiting_for_human_approval", result["state"])
        self.assertEqual("software_interface_engineer", result["agent_id"])
        record = engine.projects.load_artifact(
            "interface-demo", "01_software_interface_task.json"
        )
        self.assertEqual("software_interface_engineer", record["provenance"]["agent_id"])
        self.assertEqual("analyze_package", record["data"]["operation"])
        self.assertEqual(1, record["data"]["summary"]["valid_cross_application_links"])
        waiting = WorkflowRuntime(engine).run_next("interface-demo")
        self.assertEqual("waiting_for_human_approval", waiting["state"])

    def test_dynamic_interface_agent_routes_generation_to_fast_delivery_tool(self):
        engine = DynamicWorkEngine(
            ROOT,
            self.temp_root / "generation-projects",
            self.temp_root / "generation-knowledge.db",
        )
        engine.projects.init_project("generation-demo", "Generation demo")
        plan = json.loads(
            (ROOT / "examples" / "workplan_software_interface_generation.json").read_text(
                encoding="utf-8"
            )
        )
        plan["inputs"][0]["location"] = str(self.package)
        engine.submit_workplan("generation-demo", plan)
        engine.approve_workplan("generation-demo", "manager", "approved", "ok")
        produced = self.temp_root / "generated-overview.xlsx"
        produced.write_bytes(b"overview")
        report_file = self.temp_root / "generation-report.json"
        report_file.write_text("{}", encoding="utf-8")
        fake = {
            "schema_version": "1.0",
            "operation": "generate_interface_overview",
            "generated_at": "2026-11-06T00:00:00+00:00",
            "duration_seconds": 1.0,
            "engine": "fast",
            "source_sha256": "a" * 64,
            "validation": {"passed": True, "summary": {"data_rows": 12}},
            "artifacts": [
                {
                    "type": "software_interface_overview",
                    "path": str(produced),
                    "sha256": "b" * 64,
                }
            ],
            "report_artifact": {
                "type": "generation_report",
                "path": str(report_file),
                "sha256": "c" * 64,
            },
            "requires_human_confirmation": True,
        }
        with patch.object(
            engine.interface_delivery, "generate_overview", return_value=fake
        ) as generate:
            result = WorkflowRuntime(engine).run_next("generation-demo")
        self.assertEqual("waiting_for_human_approval", result["state"])
        self.assertEqual("fast", generate.call_args.kwargs["engine"])
        stored = engine.projects.load_artifact(
            "generation-demo", "01_software_interface_generation.json"
        )["data"]
        self.assertEqual("generate_overview", stored["operation"])
        self.assertEqual(12, stored["summary"]["data_rows"])

    def test_generation_uses_controlled_template_when_input_package_has_none(self):
        template = self.temp_root / "controlled-template.xlsx"
        workbook = Workbook()
        workbook.active.title = "Sheet1"
        workbook.save(template)
        service = InterfaceDeliveryService(
            self.temp_root,
            template_path=template,
        )

        report = service.generate_overview(
            self.package,
            output_dir=self.temp_root / "fallback-output",
            engine="fast",
        )

        self.assertEqual("controlled_default", report["template"]["source"])
        self.assertEqual(
            hashlib.sha256(template.read_bytes()).hexdigest(),
            report["template"]["sha256"],
        )
        self.assertTrue(Path(report["artifacts"][0]["path"]).is_file())

    def test_dynamic_interface_agent_routes_two_overviews_to_comparison_tool(self):
        engine = DynamicWorkEngine(
            ROOT,
            self.temp_root / "comparison-projects",
            self.temp_root / "comparison-knowledge.db",
        )
        engine.projects.init_project("comparison-demo", "Comparison demo")
        new_path = self.temp_root / "manager-new.xlsx"
        old_path = self.temp_root / "manager-old.xlsx"
        new_path.write_bytes(b"new")
        old_path.write_bytes(b"old")
        diff_path = self.temp_root / "manager-diff.xlsx"
        diff_path.write_bytes(b"diff")
        report_path = self.temp_root / "manager-comparison.json"
        report_path.write_text("{}", encoding="utf-8")
        plan = json.loads(
            (ROOT / "examples" / "workplan_software_interface_comparison.json").read_text(
                encoding="utf-8"
            )
        )
        plan["inputs"][0]["location"] = str(new_path)
        plan["inputs"][1]["location"] = str(old_path)
        engine.submit_workplan("comparison-demo", plan)
        engine.approve_workplan("comparison-demo", "manager", "approved", "ok")
        fake = {
            "schema_version": "1.0",
            "operation": "compare_interface_overviews",
            "generated_at": "2026-11-06T00:00:00+00:00",
            "new": {"sha256": "d" * 64},
            "old": {"sha256": "e" * 64},
            "summary": {
                "new_rows": 12,
                "old_rows": 11,
                "total_diffs": 1,
                "new_only": 1,
                "old_only": 0,
            },
            "artifacts": [
                {
                    "type": "interface_overview_diff_workbook",
                    "path": str(diff_path),
                    "sha256": "f" * 64,
                }
            ],
            "report_artifact": {
                "type": "comparison_report",
                "path": str(report_path),
                "sha256": "1" * 64,
            },
            "requires_human_confirmation": True,
        }
        with patch.object(
            engine.interface_delivery, "compare_overviews", return_value=fake
        ) as compare:
            result = WorkflowRuntime(engine).run_next("comparison-demo")
        self.assertEqual("waiting_for_human_approval", result["state"])
        self.assertEqual(new_path, compare.call_args.args[0])
        self.assertEqual(old_path, compare.call_args.args[1])
        stored = engine.projects.load_artifact(
            "comparison-demo", "01_software_interface_comparison.json"
        )["data"]
        self.assertEqual("compare_overviews", stored["operation"])
        self.assertEqual(1, stored["summary"]["new_only"])

    def test_mcp_lists_and_calls_tools_with_path_policy(self):
        server = InterfaceMcpServer(
            paths=AllowedPathPolicy([self.temp_root])
        )
        initialized = server.handle_message(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2025-11-25"},
            }
        )
        self.assertEqual(
            "fusa-software-interface",
            initialized["result"]["serverInfo"]["name"],
        )
        listed = server.handle_message(
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
        )
        self.assertEqual(5, len(listed["result"]["tools"]))
        names = {item["name"] for item in listed["result"]["tools"]}
        self.assertIn("fusa_interface_generate_overview", names)
        self.assertIn("fusa_interface_compare_overviews", names)
        generation_tool = next(
            item
            for item in listed["result"]["tools"]
            if item["name"] == "fusa_interface_generate_overview"
        )
        self.assertEqual(
            "fast",
            generation_tool["inputSchema"]["properties"]["engine"]["default"],
        )
        called = server.handle_message(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "fusa_interface_analyze_package",
                    "arguments": {"source_path": str(self.package), "max_findings": 10},
                },
            }
        )
        self.assertFalse(called["result"]["isError"])
        self.assertEqual(
            2, called["result"]["structuredContent"]["summary"]["module_files"]
        )

    def test_recovered_overview_comparison_emits_hashed_row_evidence(self):
        old_path = self.temp_root / "old.xlsx"
        new_path = self.temp_root / "new.xlsx"
        self._overview(old_path, changed=False, include_new=False)
        self._overview(new_path, changed=True, include_new=True)
        report = LegacyInterfaceDeliveryService(ROOT).compare_overviews(
            new_path,
            old_path,
            output_dir=self.temp_root / "comparison",
            max_evidence=20,
        )
        self.assertEqual(2, report["summary"]["total_diffs"])
        self.assertEqual(1, report["summary"]["new_only"])
        self.assertEqual(1, report["summary"]["content_diff_cells"])
        self.assertTrue(report["requires_human_confirmation"])
        self.assertEqual(
            2, sum(report["summary"]["status_counts"].values())
        )
        multiset = report["content_multiset_parity"]
        self.assertTrue(multiset["comparable"])
        self.assertEqual(2, multiset["new_only_rows"])
        self.assertEqual(1, multiset["old_only_rows"])
        evidence = Path(report["artifacts"][1]["path"])
        self.assertEqual(2, len(evidence.read_text(encoding="utf-8").splitlines()))
        report_file = Path(report["report_artifact"]["path"])
        self.assertEqual(
            report["report_artifact"]["sha256"],
            hashlib.sha256(report_file.read_bytes()).hexdigest(),
        )

    def test_independent_aa_ab_validator_enforces_non_participating_blanks(self):
        config = self.temp_root / "aa_ab_config.xlsx"
        workbook = Workbook()
        external = workbook.active
        external.title = "外部组件映射配置"
        external.append(["匹配字符串", "映射结果"])
        external.append(["ExternalRule", "剔除,不做任何处理"])
        workbook.save(config)

        overview = self.temp_root / "validated_overview.xlsx"
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "Sheet1"
        sheet.append(
            [
                "序号",
                "模块名字",
                "Signal方向",
                "Signal接口",
                "信号Mapping",
                "跨应用层SWC链路流",
                "跨应用层SWC信号流",
            ]
        )
        sheet.append(
            [
                "00001",
                "Producer",
                "Output",
                "SigOut",
                "ExternalRule_1",
                "Producer→Consumer",
                "SigOut→SigIn",
            ]
        )
        sheet.append(
            [
                "00002",
                "Consumer",
                "Input",
                "SigIn",
                "ExternalRule_1",
                "Producer→Consumer",
                "SigOut→SigIn",
            ]
        )
        sheet.append(
            ["00003", "Local", "Input", "LocalSig", "LocalRule", "", ""]
        )
        workbook.save(overview)
        service = LegacyInterfaceDeliveryService(ROOT)
        valid = service._validate_generated_overview(
            overview, config, max_evidence=20
        )
        self.assertTrue(valid["passed"])
        loaded = load_workbook(overview)
        loaded["Sheet1"].cell(4, 6, "unexpected")
        loaded.save(overview)
        loaded.close()
        invalid = service._validate_generated_overview(
            overview, config, max_evidence=20
        )
        self.assertFalse(invalid["passed"])
        self.assertEqual(
            1,
            invalid["summary"]["non_participating_aa_ab_mismatches"],
        )

    def test_fast_engine_flips_external_rte_direction_like_recovered_vba(self):
        values = [""] * 14
        values[0] = "InnerSignal"
        values[2] = "Input"
        values[10] = "Dt_RECORD_Demo"
        values[11] = "ExternalSignal"
        values[12] = "PpDemo"
        row = _MatrixRow(
            values=values,
            filenames="Demo.xlsm",
            components="COMP-A",
            adapter="FM-FMService",
            relation="FM-FMService→COMP-A",
            outer_type="MiddleWare",
            outer_component="(Mw)DemoService",
            outer_relation="(Mw)DemoService→FM-FMService",
        )
        overview, rte_rows = FastInterfaceOverviewGenerator()._unified_rows([], [row])
        self.assertEqual(2, rte_rows)
        external = overview[-1]
        self.assertEqual("(Mw)DemoService", external[0])
        self.assertEqual("Output", external[1])
        self.assertEqual("RTE", external[2])
        self.assertEqual("FM-FMService", external[3])

    def test_fast_writer_replaces_only_overview_sheet_data(self):
        template = self.temp_root / "overview-template.xlsx"
        workbook = Workbook()
        overview = workbook.active
        overview.title = "Sheet1"
        overview.append(["old header", "old value"])
        overview.append([1, "stale row"])
        overview["A1"].font = Font(bold=True)
        overview["A1"].fill = PatternFill("solid", fgColor="C8E6FF")
        overview["A1"].alignment = Alignment(horizontal="center")
        evidence = workbook.create_sheet("Evidence")
        evidence["A1"] = "must remain unchanged"
        evidence["B1"] = "=1+1"
        workbook.save(template)
        workbook.close()

        output = self.temp_root / "optimized-overview.xlsx"
        row = [""] * 27
        row[0] = "Module-A"
        row[1] = "Input"
        row[7] = "Signal-A"
        row[13] = "PpDemo|DtDemo|ElementA"
        row[25] = "Producer→Module-A"
        row[26] = "Signal-Out→Signal-A"

        with ZipFile(template, "r") as archive:
            worksheet_part = FastInterfaceOverviewGenerator._worksheet_part(
                archive, "Sheet1"
            )
            original_parts = {
                name: archive.read(name)
                for name in archive.namelist()
                if name != worksheet_part
            }

        FastInterfaceOverviewGenerator._write_overview(template, output, [row])

        with ZipFile(output, "r") as archive:
            self.assertEqual(set(original_parts) | {worksheet_part}, set(archive.namelist()))
            for name, payload in original_parts.items():
                self.assertEqual(payload, archive.read(name), name)

        generated = load_workbook(output, read_only=False, data_only=False)
        try:
            self.assertEqual(["Sheet1", "Evidence"], generated.sheetnames)
            sheet = generated["Sheet1"]
            self.assertEqual(
                [value or None for value in HEADERS],
                [cell.value for cell in sheet[1]],
            )
            self.assertEqual(1, sheet["A2"].value)
            self.assertEqual("Module-A", sheet["B2"].value)
            self.assertEqual("Input", sheet["C2"].value)
            self.assertEqual("Signal-A", sheet["I2"].value)
            self.assertEqual("Producer→Module-A", sheet["AA2"].value)
            self.assertEqual("Signal-Out→Signal-A", sheet["AB2"].value)
            self.assertTrue(sheet["AB1"].font.bold)
            self.assertEqual("solid", sheet["AB1"].fill.fill_type)
            self.assertEqual("center", sheet["AB1"].alignment.horizontal)
            self.assertEqual("must remain unchanged", generated["Evidence"]["A1"].value)
            self.assertEqual("=1+1", generated["Evidence"]["B1"].value)
        finally:
            generated.close()

    def _module(self, filename: str, signal: str, direction: str, port: str):
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "信号和参数定义"
        headers = [
            "Name",
            "Type",
            "Storage Class",
            "DataType",
            "Min",
            "Max",
            "Description",
            "Dimension",
            "Initial",
            "Value",
            "TopDataTypeName",
            "FullElementName",
            "InportName/SourceBLckName/OutportName",
            "coding",
        ]
        for column, value in enumerate(headers, 1):
            sheet.cell(8, column, value)
        values = [
            signal,
            "Signal",
            direction,
            "uint8",
            0,
            255,
            "shared signal",
            1,
            0,
            "",
            "Dt_SHARED",
            "Shared",
            port,
            "",
        ]
        for column, value in enumerate(values, 1):
            sheet.cell(9, column, value)
        workbook.save(self.modules / filename)

    def _unit_config(self, path: Path, include_consumer: bool):
        workbook = Workbook()
        internal = workbook.active
        internal.title = "内部单元和组件映射配置"
        internal.append(
            ["软件SWC名", "软件组件名", "软件单元名", "XLSM文件名", "变更历史", "是否与词典匹配"]
        )
        internal.append(
            ["SWC-A", "COMP-A", "Producer_", "Producer.xlsm", "", "已匹配"]
        )
        if include_consumer:
            internal.append(
                ["SWC-B", "COMP-B", "Consumer_", "Consumer.xlsm", "", "已匹配"]
            )
        external = workbook.create_sheet("外部组件映射配置")
        external.append(
            ["匹配字符串", "映射结果", "映射外部收发链路", "Outer表的外部收发关系", "Other SWC"]
        )
        external.append(["PpShared", "COMP-A", "ASW_SWC互相收发", "COMP-A→COMP-B", ""])
        workbook.save(path)

    def _pie_matrix(self):
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "CtApDemo"
        sheet.append(
            [
                "InportNum_OutportNum",
                "InportName_SourceBlockName_OutportName",
                "DataType_IsBus_",
                "TopDataTypeName",
                "FullElementName",
            ]
        )
        sheet.append(["Inport1", "PpShared_DeShared", "uint8", "Dt_SHARED", "Shared"])
        workbook.save(self.matrix_dir / "PIE_Matrix_Res_400_20261102.xlsx")

    def _overview(self, path: Path, *, changed: bool, include_new: bool):
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "Sheet1"
        headers = [
            "序号",
            "模块名字",
            "软件组件名",
            "软件单元名",
            "方向说明",
            "Signal方向",
            "DataType",
            "TopDataTypeName",
            "Signal接口",
            "Description",
            "列11",
            "列12",
            "列13",
            "列14",
            "信号Mapping",
        ]
        sheet.append(headers)
        first = [
            "00001",
            "Module-A",
            "Component-A",
            "Unit-A",
            "internal",
            "Output",
            "uint8",
            "DtA",
            "SignalA",
            "new description" if changed else "old description",
            "x11",
            "x12",
            "x13",
            "x14",
            "Mapping-A",
        ]
        sheet.append(first)
        if include_new:
            sheet.append(
                [
                    "00002",
                    "Module-A",
                    "Component-A",
                    "Unit-A",
                    "internal",
                    "Input",
                    "uint16",
                    "DtB",
                    "SignalB",
                    "new interface",
                    "x11",
                    "x12",
                    "x13",
                    "x14",
                    "Mapping-B",
                ]
            )
        workbook.save(path)


if __name__ == "__main__":
    unittest.main()
