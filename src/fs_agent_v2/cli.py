from __future__ import annotations

from pathlib import Path
import argparse
import json
import os
import sys

from .errors import FunctionalSafetyError
from .knowledge import KnowledgeService, WikiBuilder
from .interface_tools import (
    InterfaceDeliveryService,
    InterfaceAnalysisService,
)
from .interface_tools.mcp_server import run_stdio_server
from .orchestration import (
    AgentRuntime,
    DynamicWorkEngine,
    ManagerRuntime,
    StageEngine,
    WorkflowRuntime,
)


def _review_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--reviewer", required=True)
    parser.add_argument("--decision", choices=("approved", "rejected"), default="approved")
    parser.add_argument("--comment", default="")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Functional Safety Agent V2")
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="create a dynamic-workplan project")
    init.add_argument("project_id")
    init.add_argument("--title", default="")

    web = sub.add_parser("web", help="run the local FastAPI test console")
    web.add_argument("--host", default="127.0.0.1")
    web.add_argument("--port", type=int, default=8000)

    sub.add_parser("agents", help="list registered business agents")
    api = sub.add_parser("agent-api", help="show API variable names and readiness")
    api.add_argument("agent_id")

    manager_plan = sub.add_parser(
        "manager-plan",
        help="let the Manager inspect inputs, select Agents and submit a workplan",
    )
    manager_plan.add_argument("project_id")
    manager_plan.add_argument("--request", required=True)
    manager_plan.add_argument(
        "--input",
        action="append",
        nargs=3,
        required=True,
        metavar=("ID", "TYPE", "LOCATION"),
        help="structured source input; repeat for multiple inputs",
    )
    manager_plan.add_argument("--include-draft-knowledge", action="store_true")
    manager_plan.add_argument("--max-tool-rounds", type=int, default=8)

    plan_submit = sub.add_parser("plan-submit", help="validate and save a Manager workplan")
    plan_submit.add_argument("project_id")
    plan_submit.add_argument("workplan", type=Path)
    plan_approve = sub.add_parser("plan-approve", help="approve or reject the Manager workplan")
    plan_approve.add_argument("project_id")
    _review_arguments(plan_approve)
    plan_status = sub.add_parser("plan-status", help="show dynamic work-item state")
    plan_status.add_argument("project_id")

    item_submit = sub.add_parser("item-submit", help="validate and save a work-item proposal")
    item_submit.add_argument("project_id")
    item_submit.add_argument("item_id")
    item_submit.add_argument("proposal", type=Path)
    item_approve = sub.add_parser("item-approve", help="approve or reject a work-item artifact")
    item_approve.add_argument("project_id")
    item_approve.add_argument("item_id")
    _review_arguments(item_approve)

    item_run = sub.add_parser(
        "item-run", help="run a business Agent with automatic read-only tool calling"
    )
    item_run.add_argument("project_id")
    item_run.add_argument("item_id")
    item_run.add_argument("--query", default="")
    item_run.add_argument("--include-draft-knowledge", action="store_true")
    item_run.add_argument("--max-tool-rounds", type=int, default=8)

    trace_run = sub.add_parser(
        "trace-run", help="deterministically build and validate TSR-to-SSR traceability"
    )
    trace_run.add_argument("project_id")
    trace_run.add_argument("item_id")

    interface_run = sub.add_parser(
        "interface-run",
        help="run the deterministic software-interface Engineer work item",
    )
    interface_run.add_argument("project_id")
    interface_run.add_argument("item_id")
    interface_run.add_argument("--max-findings", type=int, default=500)

    workflow_run = sub.add_parser(
        "workflow-run",
        help="run the next ready business Agent and yield at its human approval gate",
    )
    workflow_run.add_argument("project_id")
    workflow_run.add_argument("--query", default="")
    workflow_run.add_argument("--include-draft-knowledge", action="store_true")
    workflow_run.add_argument("--max-tool-rounds", type=int, default=8)
    workflow_run.add_argument("--max-findings", type=int, default=500)
    workflow_run.add_argument(
        "--max-parallel",
        type=int,
        default=1,
        help="claim and run up to this many independent ready DAG nodes",
    )
    workflow_run.add_argument("--lease-seconds", type=int, default=120)
    workflow_run.add_argument("--max-attempts", type=int, default=3)

    interface_analyze = sub.add_parser(
        "interface-analyze",
        help="analyze a legacy interface ZIP or unpacked package",
    )
    interface_analyze.add_argument("source", type=Path)
    interface_analyze.add_argument("--output", type=Path)
    interface_analyze.add_argument("--max-findings", type=int, default=500)

    interface_compare = sub.add_parser(
        "interface-compare", help="compare current and baseline Unit configuration"
    )
    interface_compare.add_argument("current", type=Path)
    interface_compare.add_argument("baseline", type=Path)
    interface_compare.add_argument("--output", type=Path)

    interface_generate = sub.add_parser(
        "interface-generate",
        help="generate and independently validate an interface overview",
    )
    interface_generate.add_argument("source", type=Path)
    interface_generate.add_argument("--output", type=Path, required=True)
    interface_generate.add_argument(
        "--engine",
        choices=("fast", "auto"),
        default="fast",
        help="fast is the pure-Python engine (auto is an alias of fast)",
    )
    interface_generate.add_argument("--visible", action="store_true")
    comparison_mode = interface_generate.add_mutually_exclusive_group()
    comparison_mode.add_argument(
        "--compare",
        action="store_true",
        help="also run the full old/new overview comparison",
    )
    comparison_mode.add_argument(
        "--no-compare",
        action="store_true",
        help="deprecated compatibility flag; generation is already standalone",
    )
    interface_generate.add_argument("--timeout-seconds", type=int, default=1200)
    interface_generate.add_argument("--max-evidence", type=int, default=1000)

    overview_compare = sub.add_parser(
        "interface-overview-compare",
        help="compare new and old interface overviews with row evidence",
    )
    overview_compare.add_argument("new", type=Path)
    overview_compare.add_argument("old", type=Path)
    overview_compare.add_argument("--output", type=Path, required=True)
    overview_compare.add_argument("--new-sheet", default="Sheet1")
    overview_compare.add_argument("--old-sheet", default="Sheet1")
    overview_compare.add_argument("--module", action="append", default=[])
    overview_compare.add_argument("--timeout-seconds", type=int, default=600)
    overview_compare.add_argument("--max-evidence", type=int, default=1000)

    sub.add_parser(
        "interface-mcp", help="serve software-interface tools over MCP stdio"
    )

    sub.add_parser("recipe-show", help="show the optional concept-to-FSR example recipe")
    recipe_status = sub.add_parser("recipe-status", help="show optional recipe stage state")
    recipe_status.add_argument("project_id")
    recipe_submit = sub.add_parser("recipe-submit", help="submit to the optional recipe runner")
    recipe_submit.add_argument("project_id")
    recipe_submit.add_argument("stage_id")
    recipe_submit.add_argument("proposal", type=Path)
    recipe_approve = sub.add_parser("recipe-approve", help="review optional recipe output")
    recipe_approve.add_argument("project_id")
    recipe_approve.add_argument("stage_id")
    _review_arguments(recipe_approve)

    knowledge = sub.add_parser("knowledge", help="list current knowledge entries")
    knowledge.add_argument("--tag", action="append", default=[])
    knowledge.add_argument("--approved-only", action="store_true")

    knowledge_build = sub.add_parser("knowledge-build", help="build the local hybrid RAG and graph index")
    knowledge_build.add_argument("--reset", action="store_true")
    knowledge_build.add_argument("--include-external", action="store_true")

    knowledge_search = sub.add_parser("knowledge-search", help="retrieve an evidence pack")
    knowledge_search.add_argument("query")
    knowledge_search.add_argument("--tag", action="append", default=[])
    knowledge_search.add_argument("--include-draft", action="store_true")
    knowledge_search.add_argument("--limit", type=int, default=8)
    knowledge_search.add_argument("--graph-depth", type=int, default=1)

    knowledge_graph = sub.add_parser("knowledge-graph", help="traverse knowledge relationships")
    knowledge_graph.add_argument("node_id")
    knowledge_graph.add_argument("--depth", type=int, default=1)

    wiki = sub.add_parser("wiki-build", help="build a static human-readable knowledge Wiki")
    wiki.add_argument("--output", type=Path, default=Path("data/wiki"))
    return parser


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    root = Path(__file__).resolve().parents[2]
    args = build_parser().parse_args(argv)
    if args.command == "web":
        import uvicorn

        from .web import create_app

        uvicorn.run(create_app(root), host=args.host, port=args.port, log_level="info")
        return 0
    if args.command == "interface-mcp":
        run_stdio_server()
        return 0
    if args.command == "interface-analyze":
        service = InterfaceAnalysisService()
        output = args.output
        if output is not None and not output.is_absolute():
            output = Path.cwd() / output
        value = service.analyze_package(
            args.source,
            output_dir=output,
            max_findings=args.max_findings,
        )
        print(json.dumps(value, ensure_ascii=False, indent=2))
        return 0
    if args.command == "interface-compare":
        service = InterfaceAnalysisService()
        output = args.output
        if output is not None and not output.is_absolute():
            output = Path.cwd() / output
        value = service.compare_config_files(
            args.current, args.baseline, output=output
        )
        print(json.dumps(value, ensure_ascii=False, indent=2))
        return 0
    if args.command == "interface-generate":
        service = InterfaceDeliveryService(root)
        output = args.output if args.output.is_absolute() else Path.cwd() / args.output
        value = service.generate_overview(
            args.source,
            output_dir=output,
            engine=args.engine,
            visible=args.visible,
            compare_to_existing=bool(args.compare),
            timeout_seconds=args.timeout_seconds,
            max_evidence=args.max_evidence,
        )
        print(json.dumps(value, ensure_ascii=False, indent=2))
        return 0
    if args.command == "interface-overview-compare":
        service = InterfaceDeliveryService(root)
        output = args.output if args.output.is_absolute() else Path.cwd() / args.output
        value = service.compare_overviews(
            args.new,
            args.old,
            output_dir=output,
            new_sheet=args.new_sheet,
            old_sheet=args.old_sheet,
            included_modules=args.module or None,
            timeout_seconds=args.timeout_seconds,
            max_evidence=args.max_evidence,
        )
        print(json.dumps(value, ensure_ascii=False, indent=2))
        return 0
    dynamic = DynamicWorkEngine(root)
    try:
        if args.command == "init":
            value = dynamic.projects.init_project(args.project_id, args.title or args.project_id)
            print(json.dumps(value, ensure_ascii=False, indent=2))
        elif args.command == "agents":
            rows = [
                {
                    "id": item.id,
                    "title": item.title,
                    "kind": item.kind,
                    "consumes": item.consumes,
                    "produces": item.produces,
                    "skills": item.skills,
                    "api": item.api is not None,
                }
                for item in dynamic.registry.list()
            ]
            print(json.dumps(rows, ensure_ascii=False, indent=2))
        elif args.command == "agent-api":
            agent = dynamic.registry.get(args.agent_id)
            if agent.api is None:
                value = {"agent_id": agent.id, "kind": agent.kind, "api_required": False}
            else:
                api = agent.api
                names = {
                    "provider": api.provider_env,
                    "base_url": api.base_url_env,
                    "api_key": api.api_key_env,
                    "model": api.model_env,
                }
                value = {
                    "agent_id": agent.id,
                    "api_required": True,
                    "config_file": str(root / "config" / "agents" / f"{agent.id}.json"),
                    "environment_variables": names,
                    **dynamic.registry.api_status(agent.id),
                }
            print(json.dumps(value, ensure_ascii=False, indent=2))
        elif args.command == "manager-plan":
            inputs = [
                {"id": row[0], "type": row[1], "location": row[2]}
                for row in args.input
            ]
            runtime = ManagerRuntime(dynamic, max_tool_rounds=args.max_tool_rounds)
            record = runtime.plan(
                args.project_id,
                args.request,
                inputs,
                include_draft_knowledge=args.include_draft_knowledge,
            )
            print(json.dumps(record, ensure_ascii=False, indent=2))
        elif args.command == "plan-submit":
            value = json.loads(args.workplan.read_text(encoding="utf-8"))
            print(json.dumps(dynamic.submit_workplan(args.project_id, value), ensure_ascii=False, indent=2))
        elif args.command == "plan-approve":
            print(json.dumps(dynamic.approve_workplan(args.project_id, args.reviewer, args.decision, args.comment), ensure_ascii=False, indent=2))
        elif args.command == "plan-status":
            print(json.dumps(dynamic.status(args.project_id), ensure_ascii=False, indent=2))
        elif args.command == "item-submit":
            proposal = json.loads(args.proposal.read_text(encoding="utf-8"))
            print(json.dumps(dynamic.submit_item(args.project_id, args.item_id, proposal), ensure_ascii=False, indent=2))
        elif args.command == "item-approve":
            print(json.dumps(dynamic.approve_item(args.project_id, args.item_id, args.reviewer, args.decision, args.comment), ensure_ascii=False, indent=2))
        elif args.command == "item-run":
            runtime = AgentRuntime(dynamic, max_tool_rounds=args.max_tool_rounds)
            record = runtime.run_item(
                args.project_id,
                args.item_id,
                query=args.query,
                include_draft_knowledge=args.include_draft_knowledge,
            )
            print(json.dumps(record, ensure_ascii=False, indent=2))
        elif args.command == "trace-run":
            record = dynamic.run_requirement_traceability(
                args.project_id, args.item_id
            )
            print(json.dumps(record, ensure_ascii=False, indent=2))
        elif args.command == "interface-run":
            record = dynamic.run_software_interface_analysis(
                args.project_id,
                args.item_id,
                max_findings=args.max_findings,
            )
            print(json.dumps(record, ensure_ascii=False, indent=2))
        elif args.command == "workflow-run":
            runtime = WorkflowRuntime(dynamic, max_tool_rounds=args.max_tool_rounds)
            runner = runtime.run_ready if args.max_parallel > 1 else runtime.run_next
            keywords = {
                "query": args.query,
                "include_draft_knowledge": args.include_draft_knowledge,
                "max_findings": args.max_findings,
                "lease_seconds": args.lease_seconds,
                "max_attempts": args.max_attempts,
            }
            if args.max_parallel > 1:
                keywords["max_parallel"] = args.max_parallel
            result = runner(args.project_id, **keywords)
            print(json.dumps(result, ensure_ascii=False, indent=2))
        elif args.command.startswith("recipe-"):
            recipe = StageEngine(root)
            if args.command == "recipe-show":
                rows = [
                    {"id": stage.id, "title": stage.title, "agent": stage.agent}
                    for stage in recipe.pipeline.stages
                ]
                print(json.dumps(rows, ensure_ascii=False, indent=2))
            elif args.command == "recipe-status":
                print(json.dumps(recipe.stage_status(args.project_id), ensure_ascii=False, indent=2))
            elif args.command == "recipe-submit":
                proposal = json.loads(args.proposal.read_text(encoding="utf-8"))
                print(json.dumps(recipe.submit_proposal(args.project_id, args.stage_id, proposal), ensure_ascii=False, indent=2))
            elif args.command == "recipe-approve":
                stage = recipe.pipeline.stage(args.stage_id)
                print(json.dumps(recipe.projects.approve(args.project_id, stage.id, stage.output_artifact, args.reviewer, args.decision, args.comment), ensure_ascii=False, indent=2))
        elif args.command == "knowledge":
            entries = dynamic.knowledge.search(set(args.tag), args.approved_only)
            rows = [
                {"id": item.id, "version": item.version, "status": item.status, "source": item.source, "tags": item.tags}
                for item in entries
            ]
            print(json.dumps(rows, ensure_ascii=False, indent=2))
        elif args.command == "knowledge-build":
            service = KnowledgeService(root)
            value = service.build(
                reset=args.reset, include_external=args.include_external
            )
            print(json.dumps(value, ensure_ascii=False, indent=2))
        elif args.command == "knowledge-search":
            service = KnowledgeService(root)
            value = service.retrieve(
                args.query,
                tags=set(args.tag),
                approved_only=not args.include_draft,
                limit=args.limit,
                graph_depth=args.graph_depth,
            )
            print(json.dumps(value, ensure_ascii=False, indent=2))
        elif args.command == "knowledge-graph":
            service = KnowledgeService(root)
            print(
                json.dumps(
                    service.index.graph_neighbors(args.node_id, args.depth),
                    ensure_ascii=False,
                    indent=2,
                )
            )
        elif args.command == "wiki-build":
            service = KnowledgeService(root)
            output = args.output if args.output.is_absolute() else root / args.output
            print(
                json.dumps(
                    WikiBuilder(service.index).build(output),
                    ensure_ascii=False,
                    indent=2,
                )
            )
        return 0
    except (FunctionalSafetyError, FileNotFoundError, KeyError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
