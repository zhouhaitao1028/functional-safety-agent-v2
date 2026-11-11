from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import replace
from pathlib import Path
import hashlib
import json
import os
import socket
import threading
import time
import uuid

from ..errors import ContractError, GateError
from ..model import OpenAICompatibleChatClient
from ..storage import WorkClaim
from .agent_runtime import AgentRuntime
from .dynamic_engine import DynamicWorkEngine


class WorkflowRuntime:
    """Claims ready DAG nodes and executes one or more isolated Workers."""

    def __init__(self, engine: DynamicWorkEngine, max_tool_rounds: int = 8):
        self.engine = engine
        self.agent_runtime = AgentRuntime(engine, max_tool_rounds=max_tool_rounds)

    def run_next(
        self,
        project_id: str,
        *,
        query: str = "",
        include_draft_knowledge: bool = False,
        max_findings: int = 500,
        lease_seconds: int = 120,
        max_attempts: int = 3,
        model: str = "",
    ) -> dict:
        """Backward-compatible single-node execution with human-gate yielding."""
        plan = self.engine.load_workplan(project_id)
        self.engine.ensure_execution_plan(project_id, plan)
        self.engine.projects.require_approved(
            project_id, "workplan", self.engine.projects.WORKPLAN_ARTIFACT
        )
        self.engine.execution.recover_expired()
        status = self.engine.status(project_id)
        by_id = {row["id"]: row for row in status["work_items"]}
        for item in plan.work_items:
            row = by_id[item.id]
            if row["execution_state"] == "waiting_for_approval":
                return {
                    "state": "waiting_for_human_approval",
                    "work_item": item.id,
                    "agent_id": item.agent_id,
                    "artifact": str(
                        self.engine.projects.project_dir(project_id)
                        / "artifacts"
                        / item.output.artifact
                    ),
                    "approval": row["approval"],
                    "attempt": row["attempt"],
                }
            if row["execution_state"] == "rejected":
                return {
                    "state": "rejected",
                    "work_item": item.id,
                    "agent_id": item.agent_id,
                    "artifact": str(
                        self.engine.projects.project_dir(project_id)
                        / "artifacts"
                        / item.output.artifact
                    ),
                    "approval": "rejected",
                    "attempt": row["attempt"],
                }
            if row["execution_state"] == "running":
                return {
                    "state": "running",
                    "work_item": item.id,
                    "agent_id": item.agent_id,
                    "worker_id": row["worker_id"],
                    "lease_expires_at": row["lease_expires_at"],
                    "attempt": row["attempt"],
                }

        batch = self.run_ready(
            project_id,
            query=query,
            include_draft_knowledge=include_draft_knowledge,
            max_findings=max_findings,
            max_parallel=1,
            lease_seconds=lease_seconds,
            max_attempts=max_attempts,
            model=model,
        )
        if batch["results"]:
            return batch["results"][0]
        if batch["state"] == "completed":
            return {"state": "completed", "work_items": batch["work_items"]}
        return {"state": batch["state"], "work_items": batch["work_items"]}

    def run_ready(
        self,
        project_id: str,
        *,
        query: str = "",
        include_draft_knowledge: bool = False,
        max_findings: int = 500,
        max_parallel: int = 4,
        lease_seconds: int = 120,
        max_attempts: int = 3,
        worker_id: str | None = None,
        model: str = "",
    ) -> dict:
        """Atomically claim and concurrently execute the current ready DAG frontier."""
        parallelism = max(1, min(int(max_parallel), 32))
        lease = max(5, int(lease_seconds))
        attempts = max(1, int(max_attempts))
        plan = self.engine.load_workplan(project_id)
        self.engine.ensure_execution_plan(project_id, plan)
        self.engine.projects.require_approved(
            project_id, "workplan", self.engine.projects.WORKPLAN_ARTIFACT
        )
        self.engine.execution.recover_expired()
        ready = {
            row["item_id"]
            for row in self.engine.execution.list_items(project_id)
            if row["ready"]
        }
        eligible = {
            item.id
            for item in plan.work_items
            if item.id in ready
            and self.engine.automatic_executor(item.agent_id) is not None
        }
        keys = {
            item.id: self._idempotency_key(
                project_id,
                item.id,
                query=query,
                include_draft_knowledge=include_draft_knowledge,
                max_findings=max_findings,
                model=model,
            )
            for item in plan.work_items
            if item.id in eligible
        }
        owner = worker_id or (
            f"{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:8]}"
        )
        claims = self.engine.execution.claim_ready(
            project_id,
            owner,
            limit=parallelism,
            lease_seconds=lease,
            eligible_item_ids=eligible,
            idempotency_keys=keys,
            input_fingerprints=keys,
            max_attempts=attempts,
        )

        results: list[dict] = []
        if claims:
            with ThreadPoolExecutor(
                max_workers=len(claims),
                thread_name_prefix="fs-agent-worker",
            ) as pool:
                futures = {
                    pool.submit(
                        self._execute_claim,
                        claim,
                        query=query,
                        include_draft_knowledge=include_draft_knowledge,
                        max_findings=max_findings,
                        lease_seconds=lease,
                        model=model,
                    ): claim
                    for claim in claims
                }
                for future in as_completed(futures):
                    results.append(future.result())
            position = {item.id: index for index, item in enumerate(plan.work_items)}
            results.sort(key=lambda row: position[row["work_item"]])

        status = self.engine.status(project_id)
        work_items = status["work_items"]
        if all(row["execution_state"] == "approved" for row in work_items):
            state = "completed"
        elif any(row["execution_state"] == "waiting_for_approval" for row in work_items):
            state = "waiting_for_human_approval"
        elif any(row["execution_state"] == "running" for row in work_items):
            state = "running"
        elif any(row["execution_state"] == "failed" for row in work_items):
            state = "failed"
        elif claims:
            state = "batch_completed"
        else:
            state = "blocked"
        return {
            "state": state,
            "worker_id": owner,
            "max_parallel": parallelism,
            "claimed": [claim.item_id for claim in claims],
            "results": results,
            "work_items": work_items,
        }

    def _execute_claim(
        self,
        claim: WorkClaim,
        *,
        query: str,
        include_draft_knowledge: bool,
        max_findings: int,
        lease_seconds: int,
        model: str,
    ) -> dict:
        started = time.perf_counter()
        stop = threading.Event()
        heartbeat = threading.Thread(
            target=self._heartbeat_loop,
            args=(claim, stop, lease_seconds),
            name=f"lease-{claim.item_id}",
            daemon=True,
        )
        heartbeat.start()
        try:
            agent = self.engine.registry.get(claim.agent_id)
            if agent.id == "software_interface_engineer":
                record = self.engine.run_software_interface_task(
                    claim.project_id,
                    claim.item_id,
                    max_findings=max_findings,
                    execution_claim=claim,
                )
            elif agent.id == "traceability_validator":
                record = self.engine.run_requirement_traceability(
                    claim.project_id,
                    claim.item_id,
                    execution_claim=claim,
                )
            elif agent.api is not None:
                client = None
                if model:
                    config = replace(
                        self.engine.registry.resolve_api(agent.id), model=model
                    )
                    client = OpenAICompatibleChatClient(config)
                record = self.agent_runtime.run_item(
                    claim.project_id,
                    claim.item_id,
                    query=query,
                    include_draft_knowledge=include_draft_knowledge,
                    client=client,
                    execution_claim=claim,
                )
            else:
                raise ContractError(
                    f"no automatic executor is registered for {agent.id}"
                )
            current = self.engine.execution.get_item(
                claim.project_id, claim.item_id
            )
            waiting = current["state"] == "waiting_for_approval"
            return {
                "state": (
                    "waiting_for_human_approval" if waiting else "work_item_completed"
                ),
                "work_item": claim.item_id,
                "agent_id": claim.agent_id,
                "artifact": str(
                    self.engine.projects.project_dir(claim.project_id)
                    / "artifacts"
                    / claim.output_artifact
                ),
                "sha256": record["sha256"],
                "approval": "missing" if waiting else "not_required",
                "attempt": claim.attempt,
                "idempotency_key": claim.idempotency_key,
                "duration_seconds": round(time.perf_counter() - started, 6),
                "provenance": record.get("provenance", {}),
            }
        except Exception as exc:
            failed = self.engine.execution.fail_claim(
                claim,
                str(exc),
                retryable=not isinstance(
                    exc, (ContractError, GateError, FileNotFoundError)
                ),
            )
            return {
                "state": failed.get("state", "failed"),
                "work_item": claim.item_id,
                "agent_id": claim.agent_id,
                "attempt": claim.attempt,
                "idempotency_key": claim.idempotency_key,
                "duration_seconds": round(time.perf_counter() - started, 6),
                "error": str(exc),
            }
        finally:
            stop.set()
            heartbeat.join(timeout=2.0)

    def _heartbeat_loop(
        self,
        claim: WorkClaim,
        stop: threading.Event,
        lease_seconds: int,
    ) -> None:
        interval = max(1.0, min(30.0, lease_seconds / 3.0))
        while not stop.wait(interval):
            if not self.engine.execution.heartbeat(
                claim, lease_seconds=lease_seconds
            ):
                return

    def _idempotency_key(
        self,
        project_id: str,
        item_id: str,
        *,
        query: str,
        include_draft_knowledge: bool,
        max_findings: int,
        model: str,
    ) -> str:
        plan_record = self.engine.projects.load_workplan(project_id)
        plan = self.engine.load_workplan(project_id)
        item = plan.item(item_id)
        producers = {work.output.id: work for work in plan.work_items}
        initial = {source.id: source for source in plan.inputs}
        references: list[dict] = []
        for reference in item.input_refs:
            producer = producers.get(reference)
            if producer:
                path = (
                    self.engine.projects.project_dir(project_id)
                    / "artifacts"
                    / producer.output.artifact
                )
                if path.exists():
                    artifact = self.engine.projects.load_artifact(
                        project_id, producer.output.artifact
                    )
                    references.append(
                        {"id": reference, "artifact_sha256": artifact["sha256"]}
                    )
                else:
                    references.append({"id": reference, "artifact_sha256": None})
                continue
            source = initial[reference]
            location = Path(source.location) if source.location else None
            if location is not None and not location.is_absolute():
                location = self.engine.root / location
            stat = None
            if location is not None and location.exists():
                resolved = location.resolve()
                value = resolved.stat()
                stat = {
                    "path": str(resolved),
                    "size": value.st_size,
                    "mtime_ns": value.st_mtime_ns,
                }
            references.append(
                {
                    "id": source.id,
                    "type": source.type,
                    "location": source.location,
                    "stat": stat,
                }
            )

        agent_config = self.engine.root / "config" / "agents" / f"{item.agent_id}.json"
        agent_hash = (
            hashlib.sha256(agent_config.read_bytes()).hexdigest()
            if agent_config.exists()
            else None
        )
        model_config = None
        agent = self.engine.registry.get(item.agent_id)
        if agent.api is not None:
            resolved = self.engine.registry.resolve_api(agent.id)
            model_config = {
                "provider": resolved.provider,
                "base_url": resolved.base_url,
                "model": model or resolved.model,
            }
        knowledge_path = self.engine.knowledge_service.index.path
        knowledge_stat = None
        if knowledge_path.exists():
            value = knowledge_path.stat()
            knowledge_stat = {"size": value.st_size, "mtime_ns": value.st_mtime_ns}
        payload = {
            "project_id": project_id,
            "plan_sha256": plan_record["sha256"],
            "item_id": item_id,
            "agent_id": item.agent_id,
            "agent_config_sha256": agent_hash,
            "references": references,
            "query": query,
            "include_draft_knowledge": include_draft_knowledge,
            "max_findings": max_findings,
            "model": model_config,
            "knowledge_index": knowledge_stat,
        }
        canonical = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
