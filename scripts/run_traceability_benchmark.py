from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import json
import os
import subprocess
import sys
import time


ROOT = Path(__file__).resolve().parents[1]
UTC = timezone.utc
CHINA = timezone(timedelta(hours=8))


def now_iso(zone: timezone) -> str:
    return datetime.now(zone).isoformat(timespec="milliseconds")


def json_line(value: dict) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def main() -> int:
    stamp = datetime.now(CHINA).strftime("%Y%m%d-%H%M%S")
    project_id = f"full-trace-benchmark-{stamp}"
    project_dir = ROOT / "data" / "projects" / project_id
    counter = 2
    while project_dir.exists():
        project_id = f"full-trace-benchmark-{stamp}-r{counter}"
        project_dir = ROOT / "data" / "projects" / project_id
        counter += 1

    prompt = (
        "依据 IM-AP-016-B 软件需求分析指导手册和 F38 软件安全需求规范模板，"
        "将已批准的 6 条 TSR 导出为可验证、可双向追溯的 SSR。必须逐条保留 "
        "ASIL，parent_tsr_ids 必须引用真实 TSR；不得猜测责任人、分配组件或未知"
        "运行模式，无法确定时填 TBD；输出完整 JSON。"
    )
    steps: list[tuple[str, list[str]]] = [
        (
            "project_init",
            [
                "run.py",
                "init",
                project_id,
                "--title",
                "需求追溯完整任务耗时基准",
            ],
        ),
        (
            "workplan_submit",
            [
                "run.py",
                "plan-submit",
                project_id,
                r"examples\workplan_tsr_to_ssr_traceability.json",
            ],
        ),
        (
            "workplan_approve",
            [
                "run.py",
                "plan-approve",
                project_id,
                "--reviewer",
                "benchmark-auto-review",
                "--comment",
                "耗时基准中的自动模拟审批；非正式业务批准",
            ],
        ),
        (
            "tsr_submit",
            [
                "run.py",
                "item-submit",
                project_id,
                "W01",
                r"examples\tsr_requirements_text3.json",
            ],
        ),
        (
            "tsr_approve",
            [
                "run.py",
                "item-approve",
                project_id,
                "W01",
                "--reviewer",
                "benchmark-auto-review",
                "--comment",
                "耗时基准中的自动模拟审批；输入为已结构化的6条TSR",
            ],
        ),
        (
            "ssr_model_run",
            [
                "run.py",
                "item-run",
                project_id,
                "W02",
                "--query",
                prompt,
                "--include-draft-knowledge",
                "--max-tool-rounds",
                "8",
            ],
        ),
        (
            "ssr_approve",
            [
                "run.py",
                "item-approve",
                project_id,
                "W02",
                "--reviewer",
                "benchmark-auto-review",
                "--comment",
                "耗时基准中的自动模拟审批；未进行真实领域专家审阅",
            ],
        ),
        (
            "traceability_run",
            ["run.py", "trace-run", project_id, "W03"],
        ),
        (
            "traceability_approve",
            [
                "run.py",
                "item-approve",
                project_id,
                "W03",
                "--reviewer",
                "benchmark-auto-review",
                "--comment",
                "耗时基准中的自动模拟审批；非正式业务批准",
            ],
        ),
        (
            "final_status",
            ["run.py", "plan-status", project_id],
        ),
    ]

    environment = os.environ.copy()
    environment["PYTHONUTF8"] = "1"
    environment["PYTHONIOENCODING"] = "utf-8"
    records: list[dict] = []
    benchmark_start = time.perf_counter()
    workflow_seconds: float | None = None
    log_dir: Path | None = None
    log_path: Path | None = None

    def persist_log() -> None:
        if log_path is None:
            return
        header = {
            "event": "benchmark_started",
            "project_id": project_id,
            "task": "6 TSR -> DeepSeek SSR -> deterministic traceability",
            "scope": {
                "manager_llm_planning": False,
                "excel_ingestion": False,
                "prebuilt_workplan": True,
                "structured_tsr_input": True,
                "human_reviews": "automatically simulated for timing",
            },
        }
        lines = [json_line(header), *(json_line(item) for item in records)]
        log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    failed_step: str | None = None
    for sequence, (step_id, arguments) in enumerate(steps, start=1):
        command = [sys.executable, *arguments]
        started_perf = time.perf_counter()
        started_utc = now_iso(UTC)
        started_china = now_iso(CHINA)
        completed = subprocess.run(
            command,
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        duration = time.perf_counter() - started_perf
        record = {
            "event": "command_completed",
            "sequence": sequence,
            "step_id": step_id,
            "command": ["python", *arguments],
            "started_at_utc": started_utc,
            "started_at_china": started_china,
            "completed_at_utc": now_iso(UTC),
            "completed_at_china": now_iso(CHINA),
            "duration_seconds": round(duration, 6),
            "exit_code": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
        }
        records.append(record)

        if step_id == "project_init" and completed.returncode == 0:
            log_dir = project_dir / "run_logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            log_path = log_dir / "full_run.jsonl"
        persist_log()
        print(f"{sequence:02d} {step_id}: {duration:.3f}s (exit={completed.returncode})")

        if completed.returncode != 0:
            failed_step = step_id
            break
        if step_id == "traceability_approve":
            workflow_seconds = time.perf_counter() - benchmark_start

    total_seconds = time.perf_counter() - benchmark_start
    if log_dir is None:
        print("Project initialization failed before a project log directory was available.")
        if records:
            print(records[-1]["stderr"] or records[-1]["stdout"])
        return 1

    record_by_step = {item["step_id"]: item for item in records}
    phase_steps = {
        "project_and_plan": [
            "project_init",
            "workplan_submit",
            "workplan_approve",
        ],
        "tsr_ingestion_and_gate": ["tsr_submit", "tsr_approve"],
        "deepseek_ssr_generation": ["ssr_model_run"],
        "ssr_gate": ["ssr_approve"],
        "deterministic_traceability": ["traceability_run"],
        "traceability_gate": ["traceability_approve"],
        "final_status_check": ["final_status"],
    }
    phase_seconds = {
        phase: round(
            sum(
                record_by_step[step]["duration_seconds"]
                for step in member_steps
                if step in record_by_step
            ),
            6,
        )
        for phase, member_steps in phase_steps.items()
    }

    summary: dict = {
        "project_id": project_id,
        "status": "failed" if failed_step else "completed",
        "failed_step": failed_step,
        "started_at_utc": records[0]["started_at_utc"],
        "started_at_china": records[0]["started_at_china"],
        "completed_at_utc": records[-1]["completed_at_utc"],
        "completed_at_china": records[-1]["completed_at_china"],
        "workflow_seconds_through_final_approval": (
            round(workflow_seconds, 6) if workflow_seconds is not None else None
        ),
        "total_seconds_including_status_check": round(total_seconds, 6),
        "phase_seconds": phase_seconds,
        "measurement_scope": {
            "includes": [
                "project initialization",
                "prebuilt workplan validation and persistence",
                "automatically simulated approval gates",
                "structured TSR submission",
                "DeepSeek model and knowledge-tool loop",
                "SSR schema and unique-ID validation",
                "deterministic traceability generation and validation",
                "final status read",
            ],
            "excludes": [
                "Manager LLM planning from natural-language task",
                "Excel parsing",
                "real human review waiting and review work",
                "F38/F41 Excel rendering",
            ],
        },
        "commands": [
            {
                "sequence": item["sequence"],
                "step_id": item["step_id"],
                "duration_seconds": item["duration_seconds"],
                "exit_code": item["exit_code"],
            }
            for item in records
        ],
        "log_file": str(log_path),
    }

    if not failed_step:
        ssr = json.loads(
            (project_dir / "artifacts" / "02_ssr_specification.json").read_text(
                encoding="utf-8"
            )
        )
        trace = json.loads(
            (project_dir / "artifacts" / "03_tsr_ssr_traceability.json").read_text(
                encoding="utf-8"
            )
        )
        summary["result_metrics"] = {
            "model": ssr.get("provenance", {}).get("model"),
            "ssr_count": len(ssr["data"]["requirements"]),
            "tool_call_count": len(ssr.get("provenance", {}).get("tool_calls", [])),
            "evidence_ref_count": len(
                ssr.get("provenance", {}).get("evidence_refs", [])
            ),
            "trace_link_count": len(trace["data"]["links"]),
            "trace_validation": trace["data"]["validation"],
        }

    summary_path = log_dir / "benchmark_summary.json"
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"FULL_LOG={log_path}")
    print(f"SUMMARY={summary_path}")
    return 1 if failed_step else 0


if __name__ == "__main__":
    raise SystemExit(main())
