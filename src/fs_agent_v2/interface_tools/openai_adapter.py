"""MCP 工具定义 → OpenAI function-calling tools 格式适配器。

InterfaceMcpServer.tool_definitions() 返回 MCP tools/list 形状
{name, description, inputSchema, outputSchema}，其中 inputSchema 已是完整
JSON Schema。本适配器剥掉 outputSchema（模型通道不需要）并包一层
function 信封，供 AgentToolbox 把接口确定性工具声明给模型。
工具执行仍由确定性后端完成，模型只能引用、不能直接调用。
"""

from __future__ import annotations

from ..errors import ContractError

# canonical 名（config/agents/*.json 的 tools 字段）→ MCP 工具名
INTERFACE_TOOL_NAMES = {
    "interface.generate_overview": "fusa_interface_generate_overview",
    "interface.compare_overviews": "fusa_interface_compare_overviews",
    "interface.analyze_package": "fusa_interface_analyze_package",
    "interface.compare_config": "fusa_interface_compare_configs",
    "interface.inspect_overview": "fusa_interface_inspect_overview",
}


def mcp_to_openai_tool(definition: dict) -> dict:
    """单个 MCP 工具定义转 OpenAI tools 元素；丢弃 outputSchema。"""
    name = definition.get("name")
    if not isinstance(name, str) or not name:
        raise ContractError("MCP tool definition has no name")
    parameters = definition.get("inputSchema")
    if not isinstance(parameters, dict):
        parameters = {"type": "object", "properties": {}}
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": str(definition.get("description", "")),
            "parameters": parameters,
        },
    }


def mcp_to_openai_tools(definitions: list[dict]) -> list[dict]:
    return [mcp_to_openai_tool(definition) for definition in definitions]


def interface_tool_definition(canonical: str) -> dict:
    """按 canonical 工具名取接口 MCP 工具的 OpenAI 声明（懒加载避免重依赖）。"""
    mcp_name = INTERFACE_TOOL_NAMES.get(canonical)
    if mcp_name is None:
        raise ContractError(f"unknown interface tool: {canonical}")
    from .mcp_server import InterfaceMcpServer

    definition = next(
        (
            item
            for item in InterfaceMcpServer.tool_definitions()
            if item["name"] == mcp_name
        ),
        None,
    )
    if definition is None:
        raise ContractError(
            f"interface tool {canonical} is missing from InterfaceMcpServer"
        )
    return mcp_to_openai_tool(definition)
