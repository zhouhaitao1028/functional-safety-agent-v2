from __future__ import annotations

from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from fs_agent_v2.errors import ContractError  # noqa: E402
from fs_agent_v2.interface_tools.openai_adapter import (  # noqa: E402
    interface_tool_definition,
    mcp_to_openai_tool,
    mcp_to_openai_tools,
)
from fs_agent_v2.interface_tools.mcp_server import InterfaceMcpServer  # noqa: E402


class McpToOpenAiAdapterTest(unittest.TestCase):
    def test_tool_shape_wraps_function_envelope(self):
        definition = {
            "name": "fusa_interface_generate_overview",
            "description": "生成接口总览表",
            "inputSchema": {"type": "object", "properties": {"p": {"type": "string"}}},
            "outputSchema": {"type": "object"},
        }
        converted = mcp_to_openai_tool(definition)
        self.assertEqual("function", converted["type"])
        self.assertEqual(
            "fusa_interface_generate_overview", converted["function"]["name"]
        )
        self.assertEqual("生成接口总览表", converted["function"]["description"])
        self.assertEqual(
            {"type": "object", "properties": {"p": {"type": "string"}}},
            converted["function"]["parameters"],
        )
        self.assertNotIn("outputSchema", converted["function"])

    def test_missing_input_schema_falls_back_to_object(self):
        converted = mcp_to_openai_tool({"name": "fusa_interface_x"})
        self.assertEqual(
            {"type": "object", "properties": {}}, converted["function"]["parameters"]
        )

    def test_batch_conversion(self):
        definitions = InterfaceMcpServer.tool_definitions()
        converted = mcp_to_openai_tools(definitions)
        self.assertEqual(len(definitions), len(converted))
        for item in converted:
            self.assertEqual("function", item["type"])
            self.assertTrue(item["function"]["name"].startswith("fusa_interface_"))

    def test_canonical_name_maps_to_mcp_tool(self):
        canonical_to_mcp = {
            "interface.generate_overview": "fusa_interface_generate_overview",
            "interface.compare_overviews": "fusa_interface_compare_overviews",
            "interface.analyze_package": "fusa_interface_analyze_package",
            "interface.compare_config": "fusa_interface_compare_configs",
            "interface.inspect_overview": "fusa_interface_inspect_overview",
        }
        for canonical, mcp_name in canonical_to_mcp.items():
            converted = interface_tool_definition(canonical)
            self.assertEqual(mcp_name, converted["function"]["name"])
            self.assertIn("properties", converted["function"]["parameters"])

    def test_unknown_canonical_rejected(self):
        with self.assertRaises(ContractError):
            interface_tool_definition("interface.not_a_tool")

    def test_definition_without_name_rejected(self):
        with self.assertRaises(ContractError):
            mcp_to_openai_tool({"description": "no name"})


if __name__ == "__main__":
    unittest.main()
