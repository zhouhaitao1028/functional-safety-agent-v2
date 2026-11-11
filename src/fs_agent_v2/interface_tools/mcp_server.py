from __future__ import annotations

from pathlib import Path
import json
import os
import sys

from ..errors import ContractError
from .delivery import InterfaceDeliveryService
from .service import InterfaceAnalysisService


class AllowedPathPolicy:
    """Confine model-selected paths to explicit local workspace roots."""

    def __init__(self, roots: list[Path] | None = None):
        if roots is None:
            configured = os.environ.get("FS_INTERFACE_MCP_ALLOWED_ROOTS", "")
            roots = (
                [Path(item) for item in configured.split(os.pathsep) if item.strip()]
                if configured
                else [Path.cwd()]
            )
        self.roots = tuple(path.expanduser().resolve(strict=True) for path in roots)
        if not self.roots:
            raise ContractError("MCP path policy requires at least one allowed root")

    def input_path(self, raw: object) -> Path:
        if not isinstance(raw, str) or not raw.strip():
            raise ContractError("input path must be a non-empty string")
        path = Path(raw).expanduser().resolve(strict=True)
        self._require_allowed(path)
        return path

    def output_path(self, raw: object) -> Path:
        if not isinstance(raw, str) or not raw.strip():
            raise ContractError("output path must be a non-empty string")
        path = Path(raw).expanduser().resolve(strict=False)
        self._require_allowed(path)
        return path

    def _require_allowed(self, path: Path) -> None:
        if not any(path == root or root in path.parents for root in self.roots):
            raise ContractError(
                f"path is outside FS_INTERFACE_MCP_ALLOWED_ROOTS: {path}"
            )


class InterfaceMcpServer:
    """Small MCP stdio server exposing deterministic interface tools."""

    SERVER_NAME = "fusa-software-interface"
    SERVER_VERSION = "0.3.0"
    SUPPORTED_PROTOCOLS = ("2026-07-28", "2025-11-25", "2025-06-18")

    def __init__(
        self,
        service: InterfaceAnalysisService | None = None,
        delivery: InterfaceDeliveryService | None = None,
        paths: AllowedPathPolicy | None = None,
    ):
        self.service = service or InterfaceAnalysisService()
        self.delivery = delivery or InterfaceDeliveryService()
        self.paths = paths or AllowedPathPolicy()

    def handle_message(self, message: object) -> dict | None:
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
            return self._error(None, -32600, "Invalid Request")
        request_id = message.get("id")
        method = message.get("method")
        params = message.get("params", {})
        if method == "notifications/initialized":
            return None
        if not isinstance(params, dict):
            return self._error(request_id, -32602, "params must be an object")
        try:
            if method == "initialize":
                requested = params.get("protocolVersion")
                protocol = (
                    requested
                    if requested in self.SUPPORTED_PROTOCOLS
                    else self.SUPPORTED_PROTOCOLS[1]
                )
                result = {
                    "protocolVersion": protocol,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {
                        "name": self.SERVER_NAME,
                        "version": self.SERVER_VERSION,
                    },
                    "instructions": (
                        "Deterministic software-interface overview generation, "
                        "version comparison, inventory and trace review. Every "
                        "delivery includes hashes and row-level evidence; confirm "
                        "changes, warnings and missing links with a human reviewer."
                    ),
                }
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {"tools": self.tool_definitions()}
            elif method == "tools/call":
                name = params.get("name")
                arguments = params.get("arguments", {})
                if not isinstance(name, str) or not isinstance(arguments, dict):
                    return self._error(
                        request_id, -32602, "tool name/arguments are invalid"
                    )
                return self._response(request_id, self.call_tool(name, arguments))
            else:
                return self._error(request_id, -32601, f"Method not found: {method}")
            return self._response(request_id, result)
        except (ContractError, FileNotFoundError, ValueError, OSError) as exc:
            if method == "tools/call":
                result = {
                    "content": [{"type": "text", "text": str(exc)}],
                    "isError": True,
                }
                return self._response(request_id, result)
            return self._error(request_id, -32602, str(exc))
        except Exception as exc:  # keep the stdio server alive after tool failure
            return self._error(request_id, -32603, f"Internal error: {exc}")

    def call_tool(self, name: str, arguments: dict) -> dict:
        if name == "fusa_interface_analyze_package":
            source = self.paths.input_path(arguments.get("source_path"))
            output_raw = arguments.get("output_dir")
            output = self.paths.output_path(output_raw) if output_raw else None
            result = self.service.analyze_package(
                source,
                output_dir=output,
                max_findings=arguments.get("max_findings", 500),
            )
        elif name == "fusa_interface_compare_configs":
            current = self.paths.input_path(arguments.get("current_path"))
            baseline = self.paths.input_path(arguments.get("baseline_path"))
            output_raw = arguments.get("output_path")
            output = self.paths.output_path(output_raw) if output_raw else None
            result = self.service.compare_config_files(
                current, baseline, output=output
            )
        elif name == "fusa_interface_inspect_overview":
            overview = self.paths.input_path(arguments.get("overview_path"))
            result = self.service.inspect_overview_file(
                overview, max_findings=arguments.get("max_findings", 500)
            )
        elif name == "fusa_interface_generate_overview":
            source = self.paths.input_path(arguments.get("source_path"))
            output = self.paths.output_path(arguments.get("output_dir"))
            result = self.delivery.generate_overview(
                source,
                output_dir=output,
                engine=arguments.get("engine", "fast"),
                visible=bool(arguments.get("visible", False)),
                compare_to_existing=bool(
                    arguments.get("compare_to_existing", False)
                ),
                timeout_seconds=arguments.get("timeout_seconds", 1200),
                max_evidence=arguments.get("max_evidence", 1000),
            )
        elif name == "fusa_interface_compare_overviews":
            new_overview = self.paths.input_path(arguments.get("new_path"))
            old_overview = self.paths.input_path(arguments.get("old_path"))
            output = self.paths.output_path(arguments.get("output_dir"))
            modules = arguments.get("included_modules")
            if modules is not None and not (
                isinstance(modules, list)
                and all(isinstance(item, str) for item in modules)
            ):
                raise ContractError("included_modules must be a string array")
            result = self.delivery.compare_overviews(
                new_overview,
                old_overview,
                output_dir=output,
                new_sheet=arguments.get("new_sheet", "Sheet1"),
                old_sheet=arguments.get("old_sheet", "Sheet1"),
                included_modules=modules,
                timeout_seconds=arguments.get("timeout_seconds", 600),
                max_evidence=arguments.get("max_evidence", 1000),
            )
        else:
            raise ContractError(f"unknown MCP tool: {name}")
        return {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(result, ensure_ascii=False, separators=(",", ":")),
                }
            ],
            "structuredContent": result,
            "isError": False,
        }

    @staticmethod
    def tool_definitions() -> list[dict]:
        return [
            {
                "name": "fusa_interface_generate_overview",
                "description": (
                    "从标准接口包或 ZIP 生成《软件组件接口收发总览表》。"
                    "纯 Python 快速引擎（复刻自生产 VBA 宏），在隔离副本上执行，"
                    "产出独立校验证据、输入清单哈希、引擎日志，可选附带新旧基线对比。"
                    "大接口包（60+ 模块）约需 10~40 秒。"
                ),
                "inputSchema": {
                    "type": "object",
                    "required": ["source_path", "output_dir"],
                    "properties": {
                        "source_path": {"type": "string"},
                        "output_dir": {"type": "string"},
                        "engine": {
                            "type": "string",
                            "enum": ["fast", "auto"],
                            "default": "fast",
                        },
                        "visible": {"type": "boolean", "default": False},
                        "compare_to_existing": {
                            "type": "boolean",
                            "default": False,
                        },
                        "timeout_seconds": {
                            "type": "integer",
                            "minimum": 60,
                            "maximum": 7200,
                            "default": 1200,
                        },
                        "max_evidence": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 5000,
                            "default": 1000,
                        },
                    },
                },
                "outputSchema": {"type": "object"},
            },
            {
                "name": "fusa_interface_compare_overviews",
                "description": (
                    "对比新生成的接口总览表与旧版本，按业务键列（非行号）对齐，"
                    "含疑似重命名检测。产出 Excel 对比工作簿、JSON 报告和 JSONL 行级证据。"
                    "第一本工作簿为新版本，第二本为旧版本。"
                ),
                "inputSchema": {
                    "type": "object",
                    "required": ["new_path", "old_path", "output_dir"],
                    "properties": {
                        "new_path": {"type": "string"},
                        "old_path": {"type": "string"},
                        "output_dir": {"type": "string"},
                        "new_sheet": {"type": "string", "default": "Sheet1"},
                        "old_sheet": {"type": "string", "default": "Sheet1"},
                        "included_modules": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "timeout_seconds": {
                            "type": "integer",
                            "minimum": 30,
                            "maximum": 3600,
                            "default": 600,
                        },
                        "max_evidence": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 5000,
                            "default": 1000,
                        },
                    },
                },
                "outputSchema": {"type": "object"},
            },
            {
                "name": "fusa_interface_analyze_package",
                "description": (
                    "解析旧版软件接口 ZIP 或已解压包：扫描模块词典、审计跨应用收发链路、"
                    "对比当前/基线 Unit2ComponentConfig。仅作包结构审计，"
                    "不能替代正式的总览表生成。"
                ),
                "inputSchema": {
                    "type": "object",
                    "required": ["source_path"],
                    "properties": {
                        "source_path": {"type": "string"},
                        "output_dir": {"type": "string"},
                        "max_findings": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 5000,
                            "default": 500,
                        },
                    },
                },
                "outputSchema": {"type": "object"},
            },
            {
                "name": "fusa_interface_compare_configs",
                "description": (
                    "对比当前与基线 Unit2ComponentConfig 配置工作簿，"
                    "按稳定业务键匹配行，重复键单独提示不静默取舍。"
                ),
                "inputSchema": {
                    "type": "object",
                    "required": ["current_path", "baseline_path"],
                    "properties": {
                        "current_path": {"type": "string"},
                        "baseline_path": {"type": "string"},
                        "output_path": {"type": "string"},
                    },
                },
                "outputSchema": {"type": "object"},
            },
            {
                "name": "fusa_interface_inspect_overview",
                "description": (
                    "审查已有的软件组件接口总览表：返回 AA/AB 跨应用链路覆盖率，"
                    "并为每条缺失链路附证据行。总览表已存在且只需复核覆盖率时使用。"
                ),
                "inputSchema": {
                    "type": "object",
                    "required": ["overview_path"],
                    "properties": {
                        "overview_path": {"type": "string"},
                        "max_findings": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 5000,
                            "default": 500,
                        },
                    },
                },
                "outputSchema": {"type": "object"},
            },
        ]

    @staticmethod
    def _response(request_id: object, result: dict) -> dict:
        return {"jsonrpc": "2.0", "id": request_id, "result": result}

    @staticmethod
    def _error(request_id: object, code: int, message: str) -> dict:
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": code, "message": message},
        }

    def serve_stdio(self) -> None:
        for line in sys.stdin:
            if not line.strip():
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError as exc:
                response = self._error(None, -32700, f"Parse error: {exc}")
            else:
                response = self.handle_message(message)
            if response is not None:
                sys.stdout.write(
                    json.dumps(response, ensure_ascii=False, separators=(",", ":"))
                    + "\n"
                )
                sys.stdout.flush()


def run_stdio_server() -> None:
    if hasattr(sys.stdin, "reconfigure"):
        sys.stdin.reconfigure(encoding="utf-8", errors="strict")
        sys.stdout.reconfigure(encoding="utf-8", errors="strict")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    InterfaceMcpServer().serve_stdio()


if __name__ == "__main__":
    run_stdio_server()
