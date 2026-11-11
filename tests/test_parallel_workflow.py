from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import json
import sys
import threading
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from fs_agent_v2.errors import ContractError  # noqa: E402
from fs_agent_v2.orchestration import DynamicWorkEngine, WorkflowRuntime  # noqa: E402
from fs_agent_v2.storage import ExecutionStore  # noqa: E402


def example(name: str) -> dict:
    return json.loads((ROOT / "examples" / name).read_text(encoding="utf-8"))


class ExecutionStoreConcurrencyTest(unittest.TestCase):
    def setUp(self):
        local_temp = ROOT / "tests" / "_tmp"
        local_temp.mkdir(parents=True, exist_ok=True)
        self.temporary = TemporaryDirectory(dir=local_temp)
        self.store = ExecutionStore(Path(self.temporary.name) / "execution.sqlite3")

    def tearDown(self):
        self.temporary.cleanup()

    @staticmethod
    def _item(item_id: str, dependencies: list[str] | None = None) -> dict:
        return {
            "id": item_id,
            "agent_id": "test-agent",
            "output_artifact": f"{item_id}.json",
            "approval_required": True,
            "depends_on": dependencies or [],
        }

    def test_two_workers_cannot_claim_the_same_item(self):
        self.store.register_plan("project", "plan-sha", [self._item("W01")])
        barrier = threading.Barrier(2)

        def claim(worker: str):
            barrier.wait(timeout=5)
            return self.store.claim_ready(
                "project",
                worker,
                limit=1,
                lease_seconds=60,
                eligible_item_ids={"W01"},
                idempotency_keys={"W01": "same-operation"},
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(claim, ["worker-a", "worker-b"]))

        claims = [item for group in results for item in group]
        self.assertEqual(1, len(claims))
        self.assertEqual("running", self.store.get_item("project", "W01")["state"])

    def test_dependency_requires_approved_not_just_completed(self):
        self.store.register_plan(
            "chain",
            "plan-sha",
            [self._item("W01"), self._item("W02", ["W01"])],
        )
        first = self.store.claim_ready(
            "chain",
            "worker",
            limit=2,
            lease_seconds=60,
            eligible_item_ids={"W01", "W02"},
            idempotency_keys={"W01": "one", "W02": "two"},
        )
        self.assertEqual(["W01"], [claim.item_id for claim in first])
        self.store.complete_claim(first[0], "artifact-one")

        blocked = self.store.claim_ready(
            "chain",
            "worker",
            limit=2,
            lease_seconds=60,
            eligible_item_ids={"W01", "W02"},
            idempotency_keys={"W01": "one", "W02": "two"},
        )
        self.assertEqual([], blocked)

        self.store.record_review(
            "chain", "W01", "approved", "artifact-one", "reviewer"
        )
        released = self.store.claim_ready(
            "chain",
            "worker",
            limit=2,
            lease_seconds=60,
            eligible_item_ids={"W01", "W02"},
            idempotency_keys={"W01": "one", "W02": "two"},
        )
        self.assertEqual(["W02"], [claim.item_id for claim in released])

    def test_expired_lease_is_retried_and_stale_commit_is_rejected(self):
        self.store.register_plan("lease", "plan-sha", [self._item("W01")])
        start = datetime(2026, 11, 3, tzinfo=timezone.utc)
        with patch(
            "fs_agent_v2.storage.execution_store._utc_now", return_value=start
        ):
            claim = self.store.claim_ready(
                "lease",
                "worker-a",
                limit=1,
                lease_seconds=5,
                eligible_item_ids={"W01"},
                idempotency_keys={"W01": "operation"},
            )[0]
        with patch(
            "fs_agent_v2.storage.execution_store._utc_now",
            return_value=start + timedelta(seconds=6),
        ):
            self.store.recover_expired()
            state = self.store.get_item("lease", "W01")
            self.assertEqual("retry_wait", state["state"])
            published: list[str] = []
            with self.assertRaises(ContractError):
                self.store.complete_claim(
                    claim,
                    "stale-output",
                    publish=lambda: published.append("stale-output"),
                )
            self.assertEqual([], published)


class ParallelWorkflowRuntimeTest(unittest.TestCase):
    def setUp(self):
        local_temp = ROOT / "tests" / "_tmp"
        local_temp.mkdir(parents=True, exist_ok=True)
        self.temporary = TemporaryDirectory(dir=local_temp)
        self.temp_root = Path(self.temporary.name)
        self.engine = DynamicWorkEngine(
            ROOT,
            self.temp_root / "projects",
            self.temp_root / "knowledge.db",
        )
        self.engine.projects.init_project("parallel", "Parallel")
        self.plan = {
            "id": "parallel-roots",
            "version": "1.0.0",
            "goal": "并行生成两个独立功能模型",
            "inputs": [
                {"id": "IN-SOURCE", "type": "source_material", "location": ""}
            ],
            "work_items": [
                {
                    "id": "W01",
                    "title": "功能模型 A",
                    "agent_id": "function_model_engineer",
                    "depends_on": [],
                    "input_refs": ["IN-SOURCE"],
                    "output": {
                        "id": "OUT-A",
                        "type": "function_model",
                        "artifact": "01_function_a.json",
                    },
                    "approval_required": True,
                },
                {
                    "id": "W02",
                    "title": "功能模型 B",
                    "agent_id": "function_model_engineer",
                    "depends_on": [],
                    "input_refs": ["IN-SOURCE"],
                    "output": {
                        "id": "OUT-B",
                        "type": "function_model",
                        "artifact": "02_function_b.json",
                    },
                    "approval_required": True,
                },
            ],
        }
        self.engine.submit_workplan("parallel", self.plan)
        self.engine.approve_workplan("parallel", "manager", "approved", "ok")

    def tearDown(self):
        self.temporary.cleanup()

    def test_independent_nodes_execute_in_parallel_and_wait_for_approval(self):
        runtime = WorkflowRuntime(self.engine)
        barrier = threading.Barrier(2)
        threads: list[str] = []

        def fake_run_item(project_id: str, item_id: str, **kwargs):
            threads.append(threading.current_thread().name)
            barrier.wait(timeout=5)
            return self.engine.submit_item(
                project_id,
                item_id,
                example("function_model.json"),
                provenance={"agent_id": "function_model_engineer", "model": "fake"},
                execution_claim=kwargs["execution_claim"],
            )

        runtime.agent_runtime.run_item = fake_run_item  # type: ignore[method-assign]
        result = runtime.run_ready("parallel", max_parallel=2, worker_id="test-pool")

        self.assertEqual(["W01", "W02"], result["claimed"])
        self.assertEqual(2, len(set(threads)))
        self.assertEqual("waiting_for_human_approval", result["state"])
        self.assertEqual(
            ["waiting_for_approval", "waiting_for_approval"],
            [row["execution_state"] for row in result["work_items"]],
        )

        repeated = runtime.run_ready("parallel", max_parallel=2)
        self.assertEqual([], repeated["claimed"])
        self.engine.approve_item("parallel", "W01", "reviewer", "approved", "ok")
        self.engine.approve_item("parallel", "W02", "reviewer", "approved", "ok")
        completed = runtime.run_ready("parallel", max_parallel=2)
        self.assertEqual("completed", completed["state"])


if __name__ == "__main__":
    unittest.main()
