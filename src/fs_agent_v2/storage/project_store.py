from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import os
import re
import shutil
import threading
import uuid

from ..errors import ContractError, GateError


PROJECT_ID_MAX_LENGTH = 80
INVALID_PROJECT_ID_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
WINDOWS_RESERVED_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{index}" for index in range(1, 10)}
    | {f"LPT{index}" for index in range(1, 10)}
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class ProjectStore:
    WORKPLAN_ARTIFACT = "00_workplan.json"
    CONVERSATION_LOG = "conversation.jsonl"
    _event_lock = threading.Lock()

    def __init__(self, root: Path):
        self.root = root

    def project_dir(self, project_id: str) -> Path:
        if (
            not project_id
            or project_id != project_id.strip()
            or len(project_id) > PROJECT_ID_MAX_LENGTH
            or project_id in {".", ".."}
            or INVALID_PROJECT_ID_RE.search(project_id)
            or project_id.endswith((" ", "."))
            or project_id.split(".", 1)[0].upper() in WINDOWS_RESERVED_NAMES
        ):
            raise ContractError(
                "project id must be a safe Windows folder name of at most "
                f"{PROJECT_ID_MAX_LENGTH} characters"
            )
        return self.root / project_id

    @staticmethod
    def project_id_from_title(title: str) -> str:
        value = INVALID_PROJECT_ID_RE.sub("_", title.strip()).rstrip(" .")
        if value in {"", ".", ".."}:
            value = "未命名项目"
        if value.split(".", 1)[0].upper() in WINDOWS_RESERVED_NAMES:
            value += "_项目"
        return value[:PROJECT_ID_MAX_LENGTH].rstrip(" .") or "未命名项目"

    def init_named_project(self, title: str) -> dict:
        """Create a project whose directory follows its user-visible title."""
        clean_title = title.strip()
        if not clean_title:
            raise ContractError("project title is required")
        base = self.project_id_from_title(clean_title)
        for index in range(1, 10000):
            suffix = "" if index == 1 else f"_{index}"
            stem = base[: PROJECT_ID_MAX_LENGTH - len(suffix)].rstrip(" .")
            project_id = f"{stem}{suffix}"
            if not self.project_dir(project_id).exists():
                return self.init_project(project_id, clean_title)
        raise ContractError(f"too many projects share the same title: {clean_title}")

    def init_project(self, project_id: str, title: str) -> dict:
        project = self.project_dir(project_id)
        if project.exists():
            raise ContractError(f"project already exists: {project_id}")
        for name in ("inputs", "artifacts", "approvals", "inbox", "events"):
            (project / name).mkdir(parents=True, exist_ok=False)
        manifest = {
            "project_id": project_id,
            "title": title,
            "created_at": utc_now(),
            "orchestration_mode": "manager_dynamic_workplan",
        }
        self._write_json(project / "project.json", manifest)
        self.append_event(project_id, "project_created", {"title": title})
        return manifest

    def load_project(self, project_id: str) -> dict:
        path = self.project_dir(project_id) / "project.json"
        if not path.exists():
            raise ContractError(f"project does not exist: {project_id}")
        return json.loads(path.read_text(encoding="utf-8"))

    def delete_project(self, project_id: str) -> dict:
        """Hard-delete one project: remove the whole directory and return its manifest.

        companion execution-store rows (events/messages/memory) are purged by the caller.
        """
        manifest = self.load_project(project_id)
        project = self.project_dir(project_id)
        if project.exists():
            shutil.rmtree(project)
        return manifest

    def archive_project(self, project_id: str, actor: str = "web-user") -> dict:
        """Soft-delete one project so history removal remains recoverable."""
        project = self.project_dir(project_id)
        manifest = self.load_project(project_id)
        if manifest.get("deleted_at"):
            return manifest
        manifest["deleted_at"] = utc_now()
        manifest["deleted_by"] = actor
        self._write_json(project / "project.json", manifest)
        self.append_event(
            project_id,
            "project_archived",
            {"actor": actor, "deleted_at": manifest["deleted_at"]},
        )
        return manifest

    def restore_project(self, project_id: str, actor: str = "web-user") -> dict:
        project = self.project_dir(project_id)
        manifest = self.load_project(project_id)
        if not manifest.get("deleted_at"):
            return manifest
        previous_deleted_at = manifest.pop("deleted_at")
        manifest.pop("deleted_by", None)
        manifest["restored_at"] = utc_now()
        self._write_json(project / "project.json", manifest)
        self.append_event(
            project_id,
            "project_restored",
            {"actor": actor, "previous_deleted_at": previous_deleted_at},
        )
        return manifest

    def save_workplan(
        self, project_id: str, value: dict, provenance: dict | None = None
    ) -> dict:
        return self.save_artifact(
            project_id, "workplan", self.WORKPLAN_ARTIFACT, value, provenance
        )

    def load_workplan(self, project_id: str) -> dict:
        return self.load_artifact(project_id, self.WORKPLAN_ARTIFACT)

    def save_artifact(
        self,
        project_id: str,
        stage_id: str,
        filename: str,
        value: dict,
        provenance: dict | None = None,
    ) -> dict:
        self.project_dir(project_id)
        self.load_project(project_id)
        record = self.build_artifact_record(stage_id, filename, value, provenance)
        self.publish_artifact(project_id, record)
        return record

    @staticmethod
    def build_artifact_record(
        stage_id: str,
        filename: str,
        value: dict,
        provenance: dict | None = None,
    ) -> dict:
        canonical = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        record = {
            "stage_id": stage_id,
            "artifact": filename,
            "sha256": digest,
            "updated_at": utc_now(),
            "data": value,
        }
        if provenance:
            record["provenance"] = provenance
        return record

    def publish_artifact(self, project_id: str, record: dict) -> None:
        project = self.project_dir(project_id)
        self.load_project(project_id)
        self._write_json(project / "artifacts" / record["artifact"], record)
        self.append_event(
            project_id,
            "artifact_saved",
            {
                "stage_id": record["stage_id"],
                "sha256": record["sha256"],
                "generated_by": record.get("provenance", {}).get("agent_id"),
            },
        )

    def load_artifact(self, project_id: str, filename: str) -> dict:
        path = self.project_dir(project_id) / "artifacts" / filename
        if not path.exists():
            raise ContractError(f"artifact does not exist: {filename}")
        return json.loads(path.read_text(encoding="utf-8"))

    def approve(
        self,
        project_id: str,
        stage_id: str,
        artifact_filename: str,
        reviewer: str,
        decision: str,
        comment: str,
    ) -> dict:
        if decision not in {"approved", "rejected"}:
            raise ContractError("approval decision must be approved or rejected")
        artifact = self.load_artifact(project_id, artifact_filename)
        record = {
            "approval_id": f"approval-{uuid.uuid4().hex[:12]}",
            "stage_id": stage_id,
            "artifact": artifact_filename,
            "artifact_sha256": artifact["sha256"],
            "reviewer": reviewer,
            "decision": decision,
            "comment": comment,
            "decided_at": utc_now(),
        }
        self._write_json(self.project_dir(project_id) / "approvals" / f"{stage_id}.json", record)
        self.append_event(project_id, "stage_reviewed", record)
        return record

    def approval_status(self, project_id: str, stage_id: str, artifact_filename: str) -> dict:
        approval_path = self.project_dir(project_id) / "approvals" / f"{stage_id}.json"
        if not approval_path.exists():
            return {"approved": False, "reason": "missing"}
        approval = json.loads(approval_path.read_text(encoding="utf-8"))
        artifact = self.load_artifact(project_id, artifact_filename)
        if approval["artifact_sha256"] != artifact["sha256"]:
            return {"approved": False, "reason": "artifact_changed", "record": approval}
        if approval["decision"] != "approved":
            return {"approved": False, "reason": "rejected", "record": approval}
        return {"approved": True, "reason": "approved", "record": approval}

    def require_approved(self, project_id: str, stage_id: str, artifact_filename: str) -> None:
        status = self.approval_status(project_id, stage_id, artifact_filename)
        if not status["approved"]:
            raise GateError(f"stage {stage_id} is not approved: {status['reason']}")

    def append_event(self, project_id: str, event_type: str, payload: dict) -> None:
        path = self.project_dir(project_id) / "events" / "events.jsonl"
        row = {"timestamp": utc_now(), "type": event_type, "payload": payload}
        encoded = (json.dumps(row, ensure_ascii=False) + "\n").encode("utf-8")
        # O_APPEND plus one os.write keeps each event contiguous for the local
        # multi-thread Worker pool. SQLite workflow_events is the canonical
        # concurrent execution log; this JSONL remains a readable projection.
        with self._event_lock:
            descriptor = os.open(
                path,
                os.O_APPEND | os.O_CREAT | os.O_WRONLY,
                0o666,
            )
            try:
                os.write(descriptor, encoded)
            finally:
                os.close(descriptor)

    def append_message(
        self,
        project_id: str,
        role: str,
        content: str,
        metadata: dict | None = None,
        *,
        message_id: str | None = None,
        timestamp: str | None = None,
    ) -> dict:
        """Append one durable project conversation turn without mixing it into artifacts."""
        self.load_project(project_id)
        if role not in {"user", "assistant"}:
            raise ContractError("conversation role must be user or assistant")
        if not isinstance(content, str) or not content.strip():
            raise ContractError("conversation content must be non-empty")
        if len(content) > 8000:
            raise ContractError("conversation content exceeds 8000 characters")
        if metadata is not None and not isinstance(metadata, dict):
            raise ContractError("conversation metadata must be an object")
        row = {
            "id": message_id or f"msg-{uuid.uuid4().hex[:12]}",
            "timestamp": timestamp or utc_now(),
            "role": role,
            "content": content.strip(),
            "metadata": metadata or {},
        }
        path = self.project_dir(project_id) / "events" / self.CONVERSATION_LOG
        encoded = (json.dumps(row, ensure_ascii=False) + "\n").encode("utf-8")
        with self._event_lock:
            descriptor = os.open(
                path,
                os.O_APPEND | os.O_CREAT | os.O_WRONLY,
                0o666,
            )
            try:
                os.write(descriptor, encoded)
            finally:
                os.close(descriptor)
        return row

    def load_messages(self, project_id: str, *, limit: int = 40) -> list[dict]:
        self.load_project(project_id)
        limit = max(1, min(int(limit), 200))
        path = self.project_dir(project_id) / "events" / self.CONVERSATION_LOG
        if not path.exists():
            return []
        rows: list[dict] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if (
                isinstance(value, dict)
                and value.get("role") in {"user", "assistant"}
                and isinstance(value.get("content"), str)
            ):
                rows.append(value)
        return rows[-limit:]

    @staticmethod
    def _write_json(path: Path, value: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + f".tmp.{uuid.uuid4().hex}")
        try:
            temporary.write_text(
                json.dumps(value, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            temporary.replace(path)
        finally:
            if temporary.exists():
                temporary.unlink()

