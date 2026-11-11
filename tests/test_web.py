from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import json
import os
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from fs_agent_v2.web import create_app  # noqa: E402
from fs_agent_v2.errors import ContractError  # noqa: E402
from fs_agent_v2.web.app import (  # noqa: E402
    DEEPSEEK_MODELS,
    QUICK_TASKS,
    SoftwareInterfacePlanRequest,
    _explicit_modification_time_rule,
    _infer_input_type,
    _ordered_interface_overviews,
    _software_interface_workplan,
)


class WebApiTest(unittest.TestCase):
    def setUp(self):
        local_temp = ROOT / "tests" / "_tmp"
        local_temp.mkdir(parents=True, exist_ok=True)
        self.temporary = TemporaryDirectory(dir=local_temp)
        self.project_root = Path(self.temporary.name) / "projects"
        self.app = create_app(ROOT, self.project_root)

    def tearDown(self):
        self.temporary.cleanup()

    def test_api_routes_and_console_are_registered(self):
        routes: dict[str, set[str]] = {}
        for route in self.app.routes:
            if hasattr(route, "methods"):
                routes.setdefault(route.path, set()).update(route.methods or [])
        self.assertIn("GET", routes["/api/health"])
        self.assertIn("POST", routes["/api/projects"])
        self.assertIn("DELETE", routes["/api/projects/{project_id}"])
        self.assertIn("POST", routes["/api/projects/{project_id}/restore"])
        self.assertIn("GET", routes["/api/projects/{project_id}/messages"])
        self.assertIn("GET", routes["/api/projects/{project_id}/memory"])
        self.assertIn("POST", routes["/api/projects/{project_id}/manager-plan"])
        self.assertIn("GET", routes["/api/quick-tasks"])
        self.assertIn("GET", routes["/api/models"])
        self.assertIn(
            "POST", routes["/api/projects/{project_id}/quick-tasks/{task_id}/run"]
        )
        self.assertIn(
            "POST",
            routes["/api/projects/{project_id}/software-interface-plan"],
        )
        self.assertIn("POST", routes["/api/projects/{project_id}/workflow/run"])
        self.assertIn("GET", routes["/api/projects/{project_id}/events/stream"])
        self.assertIn("GET", routes["/legacy"])
        self.assertIn("GET", routes["/{full_path:path}"])
        self.assertIn(
            "GET", routes["/api/projects/{project_id}/execution-events"]
        )
        self.assertIn(
            "POST", routes["/api/projects/{project_id}/items/{item_id}/approval"]
        )
        self.assertIn("GET", routes["/api/knowledge/stages"])
        self.assertIn("GET", routes["/api/knowledge/stages/{stage_id}"])
        self.assertIn(
            "GET", routes["/api/knowledge/stages/{stage_id}/context-pack"]
        )
        self.assertIn("GET", routes["/api/knowledge/guides/{guide_id}"])

    def test_knowledge_stage_catalog_endpoints(self):
        from fastapi.testclient import TestClient

        client = TestClient(self.app)
        stages = client.get("/api/knowledge/stages").json()
        self.assertEqual(7, len(stages))
        self.assertEqual(
            ["function-definition", "hara-analysis", "safety-goal", "tsr-authoring",
             "fsr-authoring", "software-architecture", "verification-testing"],
            [row["id"] for row in stages],
        )
        self.assertIn("hara-analysis", [row["id"] for row in stages])

        hara = client.get("/api/knowledge/stages/hara-analysis").json()
        self.assertEqual("hara_engineer", hara["agent_id"])
        self.assertEqual(
            ["gd-hara-analysis", "tp-hara-worksheet", "tp-function-model"],
            hara["skill"]["uses"],
        )
        self.assertTrue(hara["skill"]["pitfalls"])
        self.assertTrue(hara["skill"]["jumps"])
        self.assertFalse(hara["skill"]["is_template"])

        template_stage = client.get("/api/knowledge/stages/tsr-authoring").json()
        self.assertTrue(template_stage["skill"]["is_template"])

        pack = client.get("/api/knowledge/stages/fsr-authoring/context-pack").json()
        self.assertEqual(2, len(pack["guides"]))
        self.assertTrue(all(guide["content"] for guide in pack["guides"]))
        self.assertEqual("tsr-authoring", pack["upstream"][0]["id"])
        self.assertIn("覆盖", pack["skill_coverage_note"])

        guide = client.get("/api/knowledge/guides/gd-hara-analysis").json()
        self.assertIn("HARA", guide["title"])
        self.assertIn("ASIL", guide["content"])

        missing = client.get("/api/knowledge/stages/not-a-stage")
        self.assertEqual(409, missing.status_code)

    def test_upload_saves_file_into_project_and_returns_path(self):
        from fastapi.testclient import TestClient

        client = TestClient(self.app)
        created = client.post("/api/projects", json={"title": "upload-demo"}).json()
        project_id = created["project_id"]
        response = client.post(
            f"/api/projects/{project_id}/uploads",
            files={"file": ("Inputs.zip", b"stub-bytes")},
        )
        self.assertEqual(200, response.status_code)
        payload = response.json()
        self.assertEqual("Inputs.zip", payload["name"])
        stored = Path(payload["path"])
        self.assertTrue(stored.is_file())
        self.assertTrue(stored.read_bytes().startswith(b"stub-bytes"))
        self.assertIn("uploads", str(stored.parent))

    def test_manager_plan_without_path_clues_asks_for_input(self):
        from fastapi.testclient import TestClient

        self.assertEqual(
            "software_interface_package", _infer_input_type("C:\\data\\接口包.zip")
        )
        self.assertEqual(
            "interface_overview",
            _infer_input_type("C:\\out\\software_interface_overview.json"),
        )

        client = TestClient(self.app)
        created = client.post("/api/projects", json={"title": "smart-inputs"}).json()
        project_id = created["project_id"]
        response = client.post(
            f"/api/projects/{project_id}/manager-plan",
            json={"request": "帮我规划一个接口分析任务", "inputs": []},
        )
        self.assertEqual(200, response.status_code)
        payload = response.json()
        self.assertEqual("needs_input", payload["state"])
        self.assertIn("inputs", payload["required_fields"])
        messages = client.get(
            f"/api/projects/{project_id}/messages"
        ).json()
        self.assertTrue(
            any(
                message.get("metadata", {}).get("state") == "needs_input"
                for message in messages
            )
        )

    def test_events_since_returns_rows_after_cursor(self):
        engine = self.app.state.engine
        engine.projects.init_project("sse-demo", "SSE demo")
        plan = json.loads(
            (ROOT / "examples" / "workplan_software_interface_analysis.json").read_text(
                encoding="utf-8"
            )
        )
        engine.submit_workplan("sse-demo", plan)
        all_rows = engine.execution.events_since("sse-demo", 0)
        self.assertTrue(all_rows)
        cursor = all_rows[0]["sequence"]
        remaining = engine.execution.events_since("sse-demo", cursor)
        self.assertEqual(
            [row["sequence"] for row in all_rows[1:]],
            [row["sequence"] for row in remaining],
        )
        self.assertEqual([], engine.execution.events_since("sse-demo", 10**9))

    def test_sse_stream_emits_connected_frame_and_events(self):
        import httpx
        import socket
        import threading
        import time
        import uvicorn

        engine = self.app.state.engine
        engine.projects.init_project("sse-stream-demo", "SSE stream demo")
        plan = json.loads(
            (ROOT / "examples" / "workplan_software_interface_analysis.json").read_text(
                encoding="utf-8"
            )
        )
        engine.submit_workplan("sse-stream-demo", plan)

        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        server = uvicorn.Server(
            uvicorn.Config(self.app, host="127.0.0.1", port=port, log_level="warning")
        )
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        for _ in range(100):
            if server.started:
                break
            time.sleep(0.05)
        self.assertTrue(server.started, "uvicorn test server did not start")
        try:
            with httpx.Client(base_url=f"http://127.0.0.1:{port}") as client:
                with client.stream(
                    "GET", "/api/projects/sse-stream-demo/events/stream", timeout=10
                ) as response:
                    self.assertEqual(200, response.status_code)
                    self.assertTrue(
                        response.headers["content-type"].startswith(
                            "text/event-stream"
                        )
                    )
                    chunks = []
                    for chunk in response.iter_text():
                        chunks.append(chunk)
                        if "event: " in "".join(chunks):
                            break
                stream = "".join(chunks)
                self.assertIn(": connected", stream)
                self.assertIn("event: ", stream)
                self.assertIn("id: ", stream)
                self.assertIn("data: ", stream)
        finally:
            server.should_exit = True
            thread.join(timeout=5)

    def test_console_pages_are_served(self):
        from fastapi.testclient import TestClient

        self.assertTrue(
            (ROOT / "src" / "fs_agent_v2" / "web" / "static" / "legacy.html").is_file()
        )
        dist_index = ROOT / "src" / "fs_agent_v2" / "web" / "static" / "dist" / "index.html"
        if dist_index.is_file():
            home_html = TestClient(self.app).get("/").text
            self.assertIn('id="root"', home_html)
        legacy = TestClient(self.app).get("/legacy")
        self.assertEqual(200, legacy.status_code)
        self.assertIn("FuSa Agent", legacy.text)

    def test_quick_task_registry_exposes_fixed_interface_route(self):
        task = QUICK_TASKS["interface_generation"]
        self.assertEqual("software_interface_engineer", task["agent_id"])
        self.assertEqual("automatic", task["plan_approval"])
        self.assertEqual("human", task["result_approval"])

    def test_deepseek_model_catalog_uses_current_supported_ids(self):
        self.assertEqual(
            ["deepseek-flash", "deepseek-v4-pro"],
            [item["id"] for item in DEEPSEEK_MODELS],
        )

    def test_interface_versions_can_be_ordered_by_file_modification_time(self):
        first = Path(self.temporary.name) / "first.xlsx"
        second = Path(self.temporary.name) / "second.xlsx"
        first.write_bytes(b"first")
        second.write_bytes(b"second")
        os.utime(first, (100, 100))
        os.utime(second, (200, 200))
        newer, older = _ordered_interface_overviews(
            str(first), str(second), "modification_time"
        )
        self.assertEqual(second.resolve(), Path(newer))
        self.assertEqual(first.resolve(), Path(older))
        self.assertTrue(
            _explicit_modification_time_rule("哪个时间更接近现在就是哪个")
        )
        self.assertFalse(_explicit_modification_time_rule("比较两个版本"))

    def test_deterministic_interface_template_builds_strict_generation_and_compare_plans(self):
        generation = _software_interface_workplan(
            SoftwareInterfacePlanRequest(
                operation="generate_overview",
                primary_path="input-package.zip",
                engine="fast",
            )
        )
        compare = _software_interface_workplan(
            SoftwareInterfacePlanRequest(
                operation="compare_overviews",
                primary_path="new.xlsx",
                secondary_path="old.xlsx",
            )
        )
        self.assertEqual(
            "generate_overview", generation["work_items"][0]["parameters"]["operation"]
        )
        self.assertEqual(2, len(compare["inputs"]))
        self.assertEqual(
            ["IN-NEW-OVERVIEW", "IN-OLD-OVERVIEW"],
            compare["work_items"][0]["input_refs"],
        )
        engine = self.app.state.engine
        engine.projects.init_project("template-demo", "Template demo")
        record = engine.submit_workplan("template-demo", compare)
        self.assertEqual("compare_overviews", record["data"]["work_items"][0]["parameters"]["operation"])

    def test_each_interface_run_has_independent_execution_identity(self):
        body = SoftwareInterfacePlanRequest(
            operation="generate_overview",
            primary_path="input-package.zip",
            engine="fast",
        )
        first = _software_interface_workplan(body, run_key="first123")
        second = _software_interface_workplan(body, run_key="second456")
        first_item = first["work_items"][0]
        second_item = second["work_items"][0]

        self.assertNotEqual(first["id"], second["id"])
        self.assertNotEqual(first_item["id"], second_item["id"])
        self.assertNotEqual(
            first_item["output"]["artifact"],
            second_item["output"]["artifact"],
        )

        engine = self.app.state.engine
        engine.projects.init_project("repeat-run-demo", "Repeat run demo")
        engine.submit_workplan("repeat-run-demo", first)
        engine.projects.save_artifact(
            "repeat-run-demo",
            first_item["id"],
            first_item["output"]["artifact"],
            {"run": "first"},
        )
        engine.projects.approve(
            "repeat-run-demo",
            first_item["id"],
            first_item["output"]["artifact"],
            "tester",
            "approved",
            "first run accepted",
        )

        engine.submit_workplan("repeat-run-demo", second)
        rows = engine.execution.list_items("repeat-run-demo")
        self.assertEqual([second_item["id"]], [row["item_id"] for row in rows])
        self.assertEqual("pending", rows[0]["state"])
        self.assertFalse(
            (
                engine.projects.project_dir("repeat-run-demo")
                / "artifacts"
                / second_item["output"]["artifact"]
            ).exists()
        )

    def test_web_engine_uses_isolated_project_root_and_existing_contracts(self):
        engine = self.app.state.engine
        created = engine.projects.init_project("web-demo", "Web Demo")
        self.assertEqual("web-demo", created["project_id"])
        archived = engine.projects.archive_project("web-demo", "test")
        self.assertIn("deleted_at", archived)
        restored = engine.projects.restore_project("web-demo", "test")
        self.assertNotIn("deleted_at", restored)
        engine.append_message("web-demo", "user", "比较两个接口版本")
        engine.append_message(
            "web-demo",
            "assistant",
            "请补充第二个文件。",
            {"state": "needs_input"},
        )
        messages = engine.load_messages("web-demo")
        self.assertEqual(["user", "assistant"], [row["role"] for row in messages])
        self.assertEqual("needs_input", messages[-1]["metadata"]["state"])
        engine.execution.set_working_memory(
            "web-demo", "software_interface", {"state": "needs_input"}
        )
        engine.execution.remember(
            "web-demo",
            agent_id="software_interface_engineer",
            kind="version_ordering_rule",
            content="修改时间较晚的是新版。",
            source_refs=[messages[0]["id"]],
        )
        self.assertEqual(
            "needs_input",
            engine.execution.get_working_memory(
                "web-demo", "software_interface"
            )["value"]["state"],
        )
        self.assertEqual(
            "version_ordering_rule",
            engine.execution.memories(
                "web-demo", agent_id="software_interface_engineer"
            )[0]["kind"],
        )
        plan = json.loads(
            (ROOT / "examples" / "workplan_software_interface_analysis.json").read_text(
                encoding="utf-8"
            )
        )
        engine.submit_workplan("web-demo", plan)
        engine.approve_workplan("web-demo", "web-reviewer", "approved", "ok")
        status = engine.status("web-demo")
        self.assertEqual("approved", status["workplan_approval"])
        self.assertTrue(status["work_items"][0]["ready"])
        self.assertTrue((self.project_root / "web-demo" / "project.json").exists())

    def test_delete_endpoint_hard_deletes_project_and_purges_execution_rows(self):
        engine = self.app.state.engine
        engine.projects.init_project("hard-delete-demo", "Hard Delete Demo")
        engine.append_message("hard-delete-demo", "user", "生成接口总览表")
        engine.execution.record_event("hard-delete-demo", "quick_task_progress", {})

        from fastapi.testclient import TestClient

        client = TestClient(self.app)
        response = client.delete("/api/projects/hard-delete-demo")
        self.assertEqual(200, response.status_code)
        payload = response.json()
        self.assertEqual("deleted", payload["state"])
        # 项目目录（消息、产物、上传材料）整体移除
        self.assertFalse((self.project_root / "hard-delete-demo").exists())
        # 执行库中的事件全部清除；项目目录已删则加载消息直接报项目不存在
        self.assertEqual([], engine.execution.events_since("hard-delete-demo", 0))
        with self.assertRaises(ContractError):
            engine.load_messages("hard-delete-demo")
        # 再次删除已不存在的项目返回冲突错误
        again = client.delete("/api/projects/hard-delete-demo")
        self.assertEqual(409, again.status_code)

    def test_quick_task_run_returns_busy_when_project_lock_held(self):
        import threading

        from fs_agent_v2.web.app import _project_locks

        self.app.state.engine.projects.init_project("lock-demo", "Lock Demo")
        lock = _project_locks.setdefault("lock-demo", threading.Lock())
        self.assertTrue(lock.acquire(blocking=False))
        try:
            from fastapi.testclient import TestClient

            client = TestClient(self.app)
            response = client.post(
                "/api/projects/lock-demo/quick-tasks/interface_generation/run",
                json={"request": "生成总览表", "input_path": "whatever"},
            )
            self.assertEqual(200, response.status_code)
            self.assertEqual("busy", response.json()["state"])
        finally:
            lock.release()

    def test_download_endpoint_serves_project_files_and_blocks_outside_paths(self):
        from fastapi.testclient import TestClient

        engine = self.app.state.engine
        engine.projects.init_project("dl-demo", "DL Demo")
        target = self.project_root / "dl-demo" / "artifacts"
        target.mkdir(parents=True, exist_ok=True)
        (target / "result.json").write_text('{"ok": true}', encoding="utf-8")

        client = TestClient(self.app)
        ok = client.get(
            "/api/projects/dl-demo/download",
            params={"path": str(target / "result.json")},
        )
        self.assertEqual(200, ok.status_code)
        self.assertEqual('{"ok": true}', ok.text)

        outside = client.get(
            "/api/projects/dl-demo/download",
            params={"path": str(self.project_root.parent / "project_memory.md")},
        )
        self.assertEqual(409, outside.status_code)

    def test_named_projects_use_safe_user_titles_as_folder_names(self):
        store = self.app.state.engine.projects
        first = store.init_named_project("软件接口 项目")
        second = store.init_named_project("软件接口 项目")
        sanitized = store.init_named_project("接口:项目/测试")

        self.assertEqual("软件接口 项目", first["project_id"])
        self.assertEqual("软件接口 项目_2", second["project_id"])
        self.assertEqual("接口_项目_测试", sanitized["project_id"])
        self.assertTrue((self.project_root / first["project_id"]).is_dir())
        self.assertTrue((self.project_root / second["project_id"]).is_dir())
        self.assertEqual("接口:项目/测试", sanitized["title"])
        with self.assertRaises(ContractError):
            store.project_dir("../outside")

    def test_interface_agent_is_exposed_as_automatically_executable(self):
        engine = self.app.state.engine
        self.assertEqual(
            "deterministic_interface_service",
            engine.automatic_executor("software_interface_engineer"),
        )
        self.assertIsNone(engine.automatic_executor("document_engineer"))


if __name__ == "__main__":
    unittest.main()
