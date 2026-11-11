from __future__ import annotations

from pathlib import Path
import argparse
import json
import os
import subprocess
import sys


def main() -> int:
    parser = argparse.ArgumentParser(description="Call the interface MCP Server over stdio")
    parser.add_argument("source", type=Path)
    parser.add_argument("--max-findings", type=int, default=5)
    args = parser.parse_args()
    source = args.source.resolve(strict=True)
    root = Path(__file__).resolve().parents[1]
    environment = dict(os.environ)
    environment["FS_INTERFACE_MCP_ALLOWED_ROOTS"] = str(source.parent)
    process = subprocess.Popen(
        [sys.executable, str(root / "run.py"), "interface-mcp"],
        cwd=root,
        env=environment,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    if process.stdin is None or process.stdout is None:
        raise RuntimeError("failed to open MCP stdio pipes")

    def request(value: dict, expect_response: bool = True) -> dict | None:
        process.stdin.write(json.dumps(value, ensure_ascii=False) + "\n")
        process.stdin.flush()
        if not expect_response:
            return None
        line = process.stdout.readline()
        if not line:
            error = process.stderr.read() if process.stderr is not None else ""
            raise RuntimeError(f"MCP server closed the stream: {error}")
        return json.loads(line)

    try:
        initialized = request(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {},
                    "clientInfo": {"name": "fusa-mcp-smoke", "version": "1.0"},
                },
            }
        )
        request(
            {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
            expect_response=False,
        )
        called = request(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "fusa_interface_analyze_package",
                    "arguments": {
                        "source_path": str(source),
                        "max_findings": max(1, min(args.max_findings, 50)),
                    },
                },
            }
        )
        if initialized is None or called is None:
            raise RuntimeError("missing MCP response")
        result = called.get("result", {})
        structured = result.get("structuredContent", {})
        output = {
            "server": initialized.get("result", {}).get("serverInfo"),
            "protocol_version": initialized.get("result", {}).get("protocolVersion"),
            "tool": "fusa_interface_analyze_package",
            "is_error": result.get("isError"),
            "mode": structured.get("mode"),
            "source_fingerprint": structured.get("source_fingerprint"),
            "summary": structured.get("summary"),
        }
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return 0 if result.get("isError") is False else 2
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


if __name__ == "__main__":
    raise SystemExit(main())
