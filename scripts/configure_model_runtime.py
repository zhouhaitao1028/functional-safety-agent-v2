from __future__ import annotations

from getpass import getpass
from pathlib import Path
import argparse
import json
import os


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Write a git-ignored local model runtime config without echoing the API key."
    )
    parser.add_argument("--provider", required=True)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--agent", help="optional agent id; omit to configure defaults")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    project_root = Path(__file__).resolve().parents[1]
    output = project_root / "config" / "local" / "model_runtime.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    existing = {}
    if output.exists():
        existing = json.loads(output.read_text(encoding="utf-8"))
    api_key = getpass("API key (input hidden): ").strip()
    if not api_key:
        raise SystemExit("API key cannot be empty")
    target = {
        "provider": args.provider,
        "base_url": args.base_url.rstrip("/"),
        "api_key": api_key,
        "model": args.model,
    }
    existing.setdefault("defaults", {})
    existing.setdefault("agents", {})
    if args.agent:
        existing["agents"][args.agent] = target
    else:
        existing["defaults"] = target
    temporary = output.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    os.replace(temporary, output)
    print(f"Saved local model runtime configuration to {output}")
    print("The API key was not printed. Environment variables still take precedence.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
