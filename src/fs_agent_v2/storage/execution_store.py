from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Iterable
import hashlib
import json
import sqlite3
import threading
import uuid

from ..errors import ContractError


ITEM_STATES = {
    "pending",
    "running",
    "waiting_for_approval",
    "approved",
    "rejected",
    "retry_wait",
    "failed",
    "cancelled",
    "stale",
}


class _ClosingConnection(sqlite3.Connection):
    """Commit or roll back like sqlite3.Connection, then release Windows handles."""

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None = None) -> str:
    return (value or _utc_now()).isoformat(timespec="milliseconds")


@dataclass(frozen=True)
class WorkClaim:
    project_id: str
    item_id: str
    agent_id: str
    output_artifact: str
    attempt: int
    row_version: int
    worker_id: str
    lease_token: str
    lease_expires_at: str
    idempotency_key: str
    input_fingerprint: str

    def to_dict(self) -> dict:
        return asdict(self)


class ExecutionStore:
    """SQLite-backed work-item state, leases and compare-and-swap commits."""

    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_lock = threading.Lock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.path,
            timeout=5.0,
            isolation_level=None,
            factory=_ClosingConnection,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=5000")
        return connection

    def _initialize(self) -> None:
        with self._init_lock:
            with self._connect() as connection:
                connection.execute("PRAGMA journal_mode=WAL")
                connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS workflow_items (
                        project_id TEXT NOT NULL,
                        item_id TEXT NOT NULL,
                        position INTEGER NOT NULL,
                        plan_sha256 TEXT NOT NULL,
                        agent_id TEXT NOT NULL,
                        output_artifact TEXT NOT NULL,
                        approval_required INTEGER NOT NULL DEFAULT 1,
                        state TEXT NOT NULL,
                        attempt INTEGER NOT NULL DEFAULT 0,
                        max_attempts INTEGER NOT NULL DEFAULT 3,
                        row_version INTEGER NOT NULL DEFAULT 0,
                        worker_id TEXT,
                        lease_token TEXT,
                        lease_expires_at TEXT,
                        heartbeat_at TEXT,
                        idempotency_key TEXT,
                        input_fingerprint TEXT,
                        output_sha256 TEXT,
                        last_error TEXT,
                        next_attempt_at TEXT,
                        started_at TEXT,
                        finished_at TEXT,
                        approved_at TEXT,
                        updated_at TEXT NOT NULL,
                        PRIMARY KEY (project_id, item_id),
                        CHECK (state IN (
                            'pending', 'running', 'waiting_for_approval',
                            'approved', 'rejected', 'retry_wait', 'failed',
                            'cancelled', 'stale'
                        ))
                    );

                    CREATE TABLE IF NOT EXISTS workflow_dependencies (
                        project_id TEXT NOT NULL,
                        item_id TEXT NOT NULL,
                        dependency_item_id TEXT NOT NULL,
                        PRIMARY KEY (project_id, item_id, dependency_item_id),
                        FOREIGN KEY (project_id, item_id)
                            REFERENCES workflow_items(project_id, item_id)
                            ON DELETE CASCADE,
                        FOREIGN KEY (project_id, dependency_item_id)
                            REFERENCES workflow_items(project_id, item_id)
                            ON DELETE CASCADE
                    );

                    CREATE TABLE IF NOT EXISTS workflow_attempts (
                        attempt_id TEXT PRIMARY KEY,
                        project_id TEXT NOT NULL,
                        item_id TEXT NOT NULL,
                        attempt INTEGER NOT NULL,
                        worker_id TEXT NOT NULL,
                        lease_token TEXT NOT NULL UNIQUE,
                        idempotency_key TEXT NOT NULL,
                        state TEXT NOT NULL,
                        started_at TEXT NOT NULL,
                        finished_at TEXT,
                        output_sha256 TEXT,
                        error TEXT,
                        FOREIGN KEY (project_id, item_id)
                            REFERENCES workflow_items(project_id, item_id)
                            ON DELETE CASCADE
                    );

                    CREATE TABLE IF NOT EXISTS workflow_events (
                        sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                        project_id TEXT NOT NULL,
                        item_id TEXT,
                        event_type TEXT NOT NULL,
                        timestamp TEXT NOT NULL,
                        payload_json TEXT NOT NULL
                    );

                    CREATE INDEX IF NOT EXISTS idx_workflow_ready
                        ON workflow_items(project_id, state, position);
                    CREATE INDEX IF NOT EXISTS idx_workflow_lease
                        ON workflow_items(state, lease_expires_at);
                    CREATE INDEX IF NOT EXISTS idx_workflow_events
                        ON workflow_events(project_id, sequence);

                    CREATE TABLE IF NOT EXISTS conversation_messages (
                        sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                        message_id TEXT NOT NULL UNIQUE,
                        project_id TEXT NOT NULL,
                        role TEXT NOT NULL,
                        content TEXT NOT NULL,
                        metadata_json TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        CHECK (role IN ('user', 'assistant'))
                    );

                    CREATE INDEX IF NOT EXISTS idx_conversation_project
                        ON conversation_messages(project_id, sequence);

                    CREATE TABLE IF NOT EXISTS working_memory (
                        project_id TEXT NOT NULL,
                        namespace TEXT NOT NULL,
                        value_json TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        PRIMARY KEY (project_id, namespace)
                    );

                    CREATE TABLE IF NOT EXISTS long_term_memory (
                        memory_id TEXT PRIMARY KEY,
                        fingerprint TEXT NOT NULL UNIQUE,
                        project_id TEXT NOT NULL,
                        agent_id TEXT,
                        kind TEXT NOT NULL,
                        content TEXT NOT NULL,
                        source_refs_json TEXT NOT NULL,
                        confidence REAL NOT NULL,
                        status TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        CHECK (status IN ('draft', 'confirmed'))
                    );

                    CREATE INDEX IF NOT EXISTS idx_long_term_memory_scope
                        ON long_term_memory(project_id, agent_id, status, updated_at);
                    """
                )
                columns = {
                    row["name"]
                    for row in connection.execute(
                        "PRAGMA table_info(workflow_items)"
                    ).fetchall()
                }
                if "approval_required" not in columns:
                    connection.execute(
                        "ALTER TABLE workflow_items "
                        "ADD COLUMN approval_required INTEGER NOT NULL DEFAULT 1"
                    )

    @staticmethod
    def _event(
        connection: sqlite3.Connection,
        project_id: str,
        item_id: str | None,
        event_type: str,
        payload: dict,
        *,
        timestamp: str | None = None,
    ) -> None:
        connection.execute(
            """
            INSERT INTO workflow_events(
                project_id, item_id, event_type, timestamp, payload_json
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                project_id,
                item_id,
                event_type,
                timestamp or _iso(),
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            ),
        )

    def append_conversation_message(
        self,
        project_id: str,
        role: str,
        content: str,
        metadata: dict | None = None,
    ) -> dict:
        if role not in {"user", "assistant"}:
            raise ContractError("conversation role must be user or assistant")
        if not isinstance(content, str) or not content.strip():
            raise ContractError("conversation content must be non-empty")
        if len(content) > 8000:
            raise ContractError("conversation content exceeds 8000 characters")
        if metadata is not None and not isinstance(metadata, dict):
            raise ContractError("conversation metadata must be an object")
        row = {
            "id": f"msg-{uuid.uuid4().hex[:12]}",
            "timestamp": _iso(),
            "role": role,
            "content": content.strip(),
            "metadata": metadata or {},
        }
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO conversation_messages(
                    message_id, project_id, role, content, metadata_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    row["id"],
                    project_id,
                    row["role"],
                    row["content"],
                    json.dumps(row["metadata"], ensure_ascii=False, separators=(",", ":")),
                    row["timestamp"],
                ),
            )
        return row

    def conversation_messages(self, project_id: str, *, limit: int = 40) -> list[dict]:
        limit = max(1, min(int(limit), 200))
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT message_id, role, content, metadata_json, created_at
                FROM conversation_messages
                WHERE project_id=?
                ORDER BY sequence DESC
                LIMIT ?
                """,
                (project_id, limit),
            ).fetchall()
        return [
            {
                "id": row["message_id"],
                "timestamp": row["created_at"],
                "role": row["role"],
                "content": row["content"],
                "metadata": json.loads(row["metadata_json"]),
            }
            for row in reversed(rows)
        ]

    def set_working_memory(self, project_id: str, namespace: str, value: dict) -> dict:
        if not namespace or not isinstance(value, dict):
            raise ContractError("working memory requires a namespace and object value")
        updated_at = _iso()
        encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO working_memory(project_id, namespace, value_json, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(project_id, namespace) DO UPDATE SET
                    value_json=excluded.value_json,
                    updated_at=excluded.updated_at
                """,
                (project_id, namespace, encoded, updated_at),
            )
        return {"namespace": namespace, "value": value, "updated_at": updated_at}

    def get_working_memory(self, project_id: str, namespace: str) -> dict | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT value_json, updated_at FROM working_memory
                WHERE project_id=? AND namespace=?
                """,
                (project_id, namespace),
            ).fetchone()
        if row is None:
            return None
        return {
            "namespace": namespace,
            "value": json.loads(row["value_json"]),
            "updated_at": row["updated_at"],
        }

    def remember(
        self,
        project_id: str,
        *,
        agent_id: str | None,
        kind: str,
        content: str,
        source_refs: list[str] | tuple[str, ...] = (),
        confidence: float = 1.0,
        status: str = "confirmed",
    ) -> dict:
        if status not in {"draft", "confirmed"}:
            raise ContractError("long-term memory status must be draft or confirmed")
        if not kind or not isinstance(content, str) or not content.strip():
            raise ContractError("long-term memory requires kind and content")
        confidence = float(confidence)
        if not 0 <= confidence <= 1:
            raise ContractError("long-term memory confidence must be between 0 and 1")
        normalized = content.strip()
        fingerprint = hashlib.sha256(
            f"{project_id}\0{agent_id or ''}\0{kind}\0{normalized}".encode("utf-8")
        ).hexdigest()
        now = _iso()
        memory_id = f"mem-{fingerprint[:12]}"
        encoded_refs = json.dumps(list(source_refs), ensure_ascii=False, separators=(",", ":"))
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO long_term_memory(
                    memory_id, fingerprint, project_id, agent_id, kind, content,
                    source_refs_json, confidence, status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(fingerprint) DO UPDATE SET
                    source_refs_json=excluded.source_refs_json,
                    confidence=excluded.confidence,
                    status=excluded.status,
                    updated_at=excluded.updated_at
                """,
                (
                    memory_id,
                    fingerprint,
                    project_id,
                    agent_id,
                    kind,
                    normalized,
                    encoded_refs,
                    confidence,
                    status,
                    now,
                    now,
                ),
            )
        return {
            "id": memory_id,
            "project_id": project_id,
            "agent_id": agent_id,
            "kind": kind,
            "content": normalized,
            "source_refs": list(source_refs),
            "confidence": confidence,
            "status": status,
            "updated_at": now,
        }

    def memories(
        self,
        project_id: str,
        *,
        agent_id: str | None = None,
        status: str = "confirmed",
        limit: int = 20,
    ) -> list[dict]:
        limit = max(1, min(int(limit), 100))
        query = (
            "SELECT * FROM long_term_memory WHERE project_id=? AND status=?"
            + (" AND (agent_id=? OR agent_id IS NULL)" if agent_id else "")
            + " ORDER BY updated_at DESC LIMIT ?"
        )
        parameters: list[object] = [project_id, status]
        if agent_id:
            parameters.append(agent_id)
        parameters.append(limit)
        with self._connect() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [
            {
                "id": row["memory_id"],
                "project_id": row["project_id"],
                "agent_id": row["agent_id"],
                "kind": row["kind"],
                "content": row["content"],
                "source_refs": json.loads(row["source_refs_json"]),
                "confidence": row["confidence"],
                "status": row["status"],
                "updated_at": row["updated_at"],
            }
            for row in rows
        ]

    def register_plan(
        self,
        project_id: str,
        plan_sha256: str,
        items: Iterable[dict],
        *,
        initial_states: dict[str, dict] | None = None,
        max_attempts: int = 3,
    ) -> None:
        rows = list(items)
        states = initial_states or {}
        now = _iso()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT DISTINCT plan_sha256 FROM workflow_items WHERE project_id=?",
                (project_id,),
            ).fetchall()
            if existing and any(row["plan_sha256"] != plan_sha256 for row in existing):
                running = connection.execute(
                    """
                    SELECT item_id FROM workflow_items
                    WHERE project_id=? AND state='running'
                    """,
                    (project_id,),
                ).fetchall()
                if running:
                    connection.rollback()
                    raise ContractError(
                        "cannot replace a workplan while work items are running: "
                        + ", ".join(row["item_id"] for row in running)
                    )
                connection.execute(
                    "DELETE FROM workflow_items WHERE project_id=?", (project_id,)
                )

            for position, item in enumerate(rows):
                initial = states.get(item["id"], {})
                state = initial.get("state", "pending")
                if state not in ITEM_STATES:
                    connection.rollback()
                    raise ContractError(f"invalid initial execution state: {state}")
                connection.execute(
                    """
                    INSERT INTO workflow_items(
                        project_id, item_id, position, plan_sha256, agent_id,
                        output_artifact, approval_required, state, max_attempts, output_sha256,
                        approved_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(project_id, item_id) DO UPDATE SET
                        position=excluded.position,
                        plan_sha256=excluded.plan_sha256,
                        agent_id=excluded.agent_id,
                        output_artifact=excluded.output_artifact,
                        approval_required=excluded.approval_required,
                        max_attempts=excluded.max_attempts,
                        updated_at=excluded.updated_at
                    """,
                    (
                        project_id,
                        item["id"],
                        position,
                        plan_sha256,
                        item["agent_id"],
                        item["output_artifact"],
                        1 if item.get("approval_required", True) else 0,
                        state,
                        max(1, max_attempts),
                        initial.get("output_sha256"),
                        initial.get("approved_at"),
                        now,
                    ),
                )

            connection.execute(
                "DELETE FROM workflow_dependencies WHERE project_id=?", (project_id,)
            )
            for item in rows:
                for dependency_id in item.get("depends_on", []):
                    connection.execute(
                        """
                        INSERT INTO workflow_dependencies(
                            project_id, item_id, dependency_item_id
                        ) VALUES (?, ?, ?)
                        """,
                        (project_id, item["id"], dependency_id),
                    )
            self._event(
                connection,
                project_id,
                None,
                "workplan_registered",
                {"plan_sha256": plan_sha256, "work_item_count": len(rows)},
                timestamp=now,
            )
            connection.commit()

    def record_event(
        self,
        project_id: str,
        event_type: str,
        payload: dict,
        *,
        item_id: str | None = None,
    ) -> None:
        """Append a workflow event with a dedicated connection (SSE visible)."""
        with self._connect() as connection:
            self._event(connection, project_id, item_id, event_type, payload)
            connection.commit()

    def has_project(self, project_id: str) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM workflow_items WHERE project_id=? LIMIT 1",
                (project_id,),
            ).fetchone()
            return row is not None

    def get_item(self, project_id: str, item_id: str) -> dict:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM workflow_items WHERE project_id=? AND item_id=?
                """,
                (project_id, item_id),
            ).fetchone()
            if row is None:
                raise ContractError(f"execution item does not exist: {item_id}")
            return dict(row)

    def list_items(self, project_id: str) -> list[dict]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM workflow_items
                WHERE project_id=? ORDER BY position, item_id
                """,
                (project_id,),
            ).fetchall()
            output = []
            for row in rows:
                value = dict(row)
                blockers = connection.execute(
                    """
                    SELECT d.dependency_item_id
                    FROM workflow_dependencies d
                    JOIN workflow_items upstream
                      ON upstream.project_id=d.project_id
                     AND upstream.item_id=d.dependency_item_id
                    WHERE d.project_id=? AND d.item_id=?
                      AND upstream.state!='approved'
                    ORDER BY d.dependency_item_id
                    """,
                    (project_id, row["item_id"]),
                ).fetchall()
                value["blocked_by"] = [item["dependency_item_id"] for item in blockers]
                value["ready"] = (
                    value["state"] in {"pending", "retry_wait"}
                    and not value["blocked_by"]
                    and (
                        value["next_attempt_at"] is None
                        or value["next_attempt_at"] <= _iso()
                    )
                )
                output.append(value)
            return output

    def mark_waiting_for_approval(
        self,
        project_id: str,
        item_id: str,
        output_sha256: str,
    ) -> None:
        now = _iso()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT state, approval_required FROM workflow_items
                WHERE project_id=? AND item_id=?
                """,
                (project_id, item_id),
            ).fetchone()
            if row is None:
                connection.rollback()
                raise ContractError(f"execution item does not exist: {item_id}")
            if row["state"] == "running":
                connection.rollback()
                raise ContractError(
                    f"work item {item_id} is owned by a running Worker"
                )
            target = "waiting_for_approval" if row["approval_required"] else "approved"
            connection.execute(
                """
                UPDATE workflow_items
                SET state=?, output_sha256=?, finished_at=?, approved_at=?, last_error=NULL,
                    row_version=row_version+1, updated_at=?
                WHERE project_id=? AND item_id=?
                """,
                (
                    target,
                    output_sha256,
                    now,
                    now if target == "approved" else None,
                    now,
                    project_id,
                    item_id,
                ),
            )
            self._event(
                connection,
                project_id,
                item_id,
                "item_completed",
                {
                    "output_sha256": output_sha256,
                    "source": "manual_submit",
                    "next_state": target,
                },
                timestamp=now,
            )
            connection.commit()

    def record_review(
        self,
        project_id: str,
        item_id: str,
        decision: str,
        artifact_sha256: str,
        reviewer: str,
    ) -> None:
        if decision not in {"approved", "rejected"}:
            raise ContractError(f"invalid review decision: {decision}")
        target = "approved" if decision == "approved" else "rejected"
        now = _iso()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT state, output_sha256 FROM workflow_items
                WHERE project_id=? AND item_id=?
                """,
                (project_id, item_id),
            ).fetchone()
            if row is None:
                connection.rollback()
                raise ContractError(f"execution item does not exist: {item_id}")
            if row["state"] == "running":
                connection.rollback()
                raise ContractError(f"cannot review running work item {item_id}")
            if row["output_sha256"] and row["output_sha256"] != artifact_sha256:
                connection.rollback()
                raise ContractError(
                    f"reviewed artifact hash does not match execution state for {item_id}"
                )
            connection.execute(
                """
                UPDATE workflow_items
                SET state=?, output_sha256=?, approved_at=?,
                    row_version=row_version+1, updated_at=?
                WHERE project_id=? AND item_id=?
                """,
                (
                    target,
                    artifact_sha256,
                    now if target == "approved" else None,
                    now,
                    project_id,
                    item_id,
                ),
            )
            self._event(
                connection,
                project_id,
                item_id,
                "item_reviewed",
                {
                    "decision": decision,
                    "artifact_sha256": artifact_sha256,
                    "reviewer": reviewer,
                },
                timestamp=now,
            )
            connection.commit()

    def claim_ready(
        self,
        project_id: str,
        worker_id: str,
        *,
        limit: int,
        lease_seconds: int,
        eligible_item_ids: set[str],
        idempotency_keys: dict[str, str],
        input_fingerprints: dict[str, str] | None = None,
        max_attempts: int = 3,
    ) -> list[WorkClaim]:
        if limit < 1 or not eligible_item_ids:
            return []
        now_value = _utc_now()
        now = _iso(now_value)
        expires = _iso(now_value + timedelta(seconds=max(5, lease_seconds)))
        fingerprints = input_fingerprints or idempotency_keys
        claims: list[WorkClaim] = []
        placeholders = ",".join("?" for _ in eligible_item_ids)

        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._recover_expired(connection, now)
            rows = connection.execute(
                f"""
                SELECT w.*
                FROM workflow_items w
                WHERE w.project_id=?
                  AND w.state IN ('pending', 'retry_wait')
                  AND (w.next_attempt_at IS NULL OR w.next_attempt_at<=?)
                  AND w.item_id IN ({placeholders})
                  AND w.attempt < ?
                  AND NOT EXISTS (
                      SELECT 1
                      FROM workflow_dependencies d
                      JOIN workflow_items upstream
                        ON upstream.project_id=d.project_id
                       AND upstream.item_id=d.dependency_item_id
                      WHERE d.project_id=w.project_id
                        AND d.item_id=w.item_id
                        AND upstream.state!='approved'
                  )
                ORDER BY w.position, w.item_id
                LIMIT ?
                """,
                [
                    project_id,
                    now,
                    *sorted(eligible_item_ids),
                    max(1, max_attempts),
                    limit,
                ],
            ).fetchall()
            for row in rows:
                item_id = row["item_id"]
                lease_token = uuid.uuid4().hex
                attempt = int(row["attempt"]) + 1
                idempotency_key = idempotency_keys[item_id]
                fingerprint = fingerprints[item_id]
                cursor = connection.execute(
                    """
                    UPDATE workflow_items
                    SET state='running', attempt=?, max_attempts=?,
                        row_version=row_version+1, worker_id=?, lease_token=?,
                        lease_expires_at=?, heartbeat_at=?, idempotency_key=?,
                        input_fingerprint=?, last_error=NULL, next_attempt_at=NULL,
                        started_at=?, finished_at=NULL, updated_at=?
                    WHERE project_id=? AND item_id=?
                      AND state IN ('pending', 'retry_wait')
                    """,
                    (
                        attempt,
                        max(1, max_attempts),
                        worker_id,
                        lease_token,
                        expires,
                        now,
                        idempotency_key,
                        fingerprint,
                        now,
                        now,
                        project_id,
                        item_id,
                    ),
                )
                if cursor.rowcount != 1:
                    continue
                updated = connection.execute(
                    """
                    SELECT row_version FROM workflow_items
                    WHERE project_id=? AND item_id=?
                    """,
                    (project_id, item_id),
                ).fetchone()
                connection.execute(
                    """
                    INSERT INTO workflow_attempts(
                        attempt_id, project_id, item_id, attempt, worker_id,
                        lease_token, idempotency_key, state, started_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 'running', ?)
                    """,
                    (
                        uuid.uuid4().hex,
                        project_id,
                        item_id,
                        attempt,
                        worker_id,
                        lease_token,
                        idempotency_key,
                        now,
                    ),
                )
                claim = WorkClaim(
                    project_id=project_id,
                    item_id=item_id,
                    agent_id=row["agent_id"],
                    output_artifact=row["output_artifact"],
                    attempt=attempt,
                    row_version=int(updated["row_version"]),
                    worker_id=worker_id,
                    lease_token=lease_token,
                    lease_expires_at=expires,
                    idempotency_key=idempotency_key,
                    input_fingerprint=fingerprint,
                )
                claims.append(claim)
                self._event(
                    connection,
                    project_id,
                    item_id,
                    "item_claimed",
                    claim.to_dict(),
                    timestamp=now,
                )
            connection.commit()
        return claims

    @staticmethod
    def _recover_expired(connection: sqlite3.Connection, now: str) -> None:
        rows = connection.execute(
            """
            SELECT project_id, item_id, attempt, max_attempts, lease_token
            FROM workflow_items
            WHERE state='running' AND lease_expires_at<?
            """,
            (now,),
        ).fetchall()
        for row in rows:
            target = "retry_wait" if row["attempt"] < row["max_attempts"] else "failed"
            connection.execute(
                """
                UPDATE workflow_items
                SET state=?, worker_id=NULL, lease_token=NULL,
                    lease_expires_at=NULL, heartbeat_at=NULL,
                    last_error='worker lease expired', next_attempt_at=?,
                    row_version=row_version+1, updated_at=?
                WHERE project_id=? AND item_id=? AND state='running'
                """,
                (
                    target,
                    now if target == "retry_wait" else None,
                    now,
                    row["project_id"],
                    row["item_id"],
                ),
            )
            connection.execute(
                """
                UPDATE workflow_attempts
                SET state='lease_expired', finished_at=?, error='worker lease expired'
                WHERE lease_token=? AND state='running'
                """,
                (now, row["lease_token"]),
            )
            ExecutionStore._event(
                connection,
                row["project_id"],
                row["item_id"],
                "item_lease_expired",
                {"next_state": target, "attempt": row["attempt"]},
                timestamp=now,
            )

    def recover_expired(self) -> None:
        now = _iso()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._recover_expired(connection, now)
            connection.commit()

    def heartbeat(self, claim: WorkClaim, *, lease_seconds: int) -> bool:
        now_value = _utc_now()
        now = _iso(now_value)
        expires = _iso(now_value + timedelta(seconds=max(5, lease_seconds)))
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE workflow_items
                SET heartbeat_at=?, lease_expires_at=?, updated_at=?
                WHERE project_id=? AND item_id=? AND state='running'
                  AND lease_token=?
                """,
                (
                    now,
                    expires,
                    now,
                    claim.project_id,
                    claim.item_id,
                    claim.lease_token,
                ),
            )
            return cursor.rowcount == 1

    def assert_active_claim(self, claim: WorkClaim) -> None:
        now = _iso()
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT state, lease_token, lease_expires_at
                FROM workflow_items WHERE project_id=? AND item_id=?
                """,
                (claim.project_id, claim.item_id),
            ).fetchone()
            if (
                row is None
                or row["state"] != "running"
                or row["lease_token"] != claim.lease_token
                or row["lease_expires_at"] is None
                or row["lease_expires_at"] <= now
            ):
                raise ContractError(
                    f"stale or expired execution claim for {claim.item_id}"
                )

    def complete_claim(
        self,
        claim: WorkClaim,
        output_sha256: str,
        *,
        publish: Callable[[], None] | None = None,
    ) -> dict:
        now = _iso()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            active = connection.execute(
                """
                SELECT state, lease_token, lease_expires_at
                FROM workflow_items
                WHERE project_id=? AND item_id=?
                """,
                (claim.project_id, claim.item_id),
            ).fetchone()
            if (
                active is None
                or active["state"] != "running"
                or active["lease_token"] != claim.lease_token
                or active["lease_expires_at"] is None
                or active["lease_expires_at"] <= now
            ):
                connection.rollback()
                raise ContractError(
                    f"cannot commit stale execution claim for {claim.item_id}"
                )
            # BEGIN IMMEDIATE prevents another process from recovering or
            # reclaiming this item while the visible artifact is published.
            if publish is not None:
                publish()
            cursor = connection.execute(
                """
                UPDATE workflow_items
                SET state=CASE WHEN approval_required=1
                               THEN 'waiting_for_approval' ELSE 'approved' END,
                    output_sha256=?, finished_at=?,
                    approved_at=CASE WHEN approval_required=0 THEN ? ELSE NULL END,
                    worker_id=NULL, lease_token=NULL,
                    lease_expires_at=NULL, heartbeat_at=NULL,
                    row_version=row_version+1, updated_at=?
                WHERE project_id=? AND item_id=? AND state='running'
                  AND lease_token=?
                """,
                (
                    output_sha256,
                    now,
                    now,
                    now,
                    claim.project_id,
                    claim.item_id,
                    claim.lease_token,
                ),
            )
            if cursor.rowcount != 1:
                connection.rollback()
                raise ContractError(
                    f"cannot commit stale execution claim for {claim.item_id}"
                )
            completed_row = connection.execute(
                """
                SELECT state FROM workflow_items
                WHERE project_id=? AND item_id=?
                """,
                (claim.project_id, claim.item_id),
            ).fetchone()
            connection.execute(
                """
                UPDATE workflow_attempts
                SET state='completed', finished_at=?, output_sha256=?
                WHERE lease_token=? AND state='running'
                """,
                (now, output_sha256, claim.lease_token),
            )
            self._event(
                connection,
                claim.project_id,
                claim.item_id,
                "item_completed",
                {
                    "attempt": claim.attempt,
                    "output_sha256": output_sha256,
                    "next_state": completed_row["state"],
                },
                timestamp=now,
            )
            connection.commit()
            return self.get_item(claim.project_id, claim.item_id)

    def fail_claim(
        self,
        claim: WorkClaim,
        error: str,
        *,
        retryable: bool = True,
        retry_delay_seconds: int = 5,
    ) -> dict:
        now_value = _utc_now()
        now = _iso(now_value)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT attempt, max_attempts, state, lease_token
                FROM workflow_items WHERE project_id=? AND item_id=?
                """,
                (claim.project_id, claim.item_id),
            ).fetchone()
            if (
                row is None
                or row["state"] != "running"
                or row["lease_token"] != claim.lease_token
            ):
                connection.rollback()
                return {
                    "state": "stale_claim",
                    "item_id": claim.item_id,
                    "error": error,
                }
            should_retry = retryable and row["attempt"] < row["max_attempts"]
            target = "retry_wait" if should_retry else "failed"
            next_attempt = (
                _iso(now_value + timedelta(seconds=max(0, retry_delay_seconds)))
                if should_retry
                else None
            )
            connection.execute(
                """
                UPDATE workflow_items
                SET state=?, worker_id=NULL, lease_token=NULL,
                    lease_expires_at=NULL, heartbeat_at=NULL,
                    last_error=?, next_attempt_at=?, finished_at=?,
                    row_version=row_version+1, updated_at=?
                WHERE project_id=? AND item_id=? AND lease_token=?
                """,
                (
                    target,
                    error[:4000],
                    next_attempt,
                    now,
                    now,
                    claim.project_id,
                    claim.item_id,
                    claim.lease_token,
                ),
            )
            connection.execute(
                """
                UPDATE workflow_attempts
                SET state=?, finished_at=?, error=?
                WHERE lease_token=? AND state='running'
                """,
                (target, now, error[:4000], claim.lease_token),
            )
            self._event(
                connection,
                claim.project_id,
                claim.item_id,
                "item_failed",
                {
                    "attempt": claim.attempt,
                    "next_state": target,
                    "retry_at": next_attempt,
                    "error": error[:1000],
                },
                timestamp=now,
            )
            connection.commit()
            return self.get_item(claim.project_id, claim.item_id)

    def purge_project(self, project_id: str) -> int:
        """Delete every persisted row of one project (hard-delete companion)."""
        tables = (
            "workflow_events",
            "workflow_attempts",
            "workflow_dependencies",
            "workflow_items",
            "conversation_messages",
            "working_memory",
            "long_term_memory",
        )
        removed = 0
        with self._connect() as connection:
            for table in tables:
                cursor = connection.execute(
                    f"DELETE FROM {table} WHERE project_id=?", (project_id,)
                )
                removed += cursor.rowcount if cursor.rowcount > 0 else 0
        return removed

    def events_since(
        self, project_id: str, after_sequence: int = 0, *, limit: int = 500
    ) -> list[dict]:
        """Return workflow events with a sequence strictly after the cursor."""
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT sequence, project_id, item_id, event_type, timestamp,
                       payload_json
                FROM workflow_events
                WHERE project_id=? AND sequence>?
                ORDER BY sequence ASC LIMIT ?
                """,
                (project_id, max(0, after_sequence), max(1, limit)),
            ).fetchall()
            return [
                {
                    "sequence": row["sequence"],
                    "project_id": row["project_id"],
                    "item_id": row["item_id"],
                    "type": row["event_type"],
                    "timestamp": row["timestamp"],
                    "payload": json.loads(row["payload_json"]),
                }
                for row in rows
            ]

    def events(self, project_id: str, *, limit: int = 1000) -> list[dict]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT sequence, project_id, item_id, event_type, timestamp,
                       payload_json
                FROM workflow_events WHERE project_id=?
                ORDER BY sequence DESC LIMIT ?
                """,
                (project_id, max(1, limit)),
            ).fetchall()
            return [
                {
                    "sequence": row["sequence"],
                    "project_id": row["project_id"],
                    "item_id": row["item_id"],
                    "type": row["event_type"],
                    "timestamp": row["timestamp"],
                    "payload": json.loads(row["payload_json"]),
                }
                for row in reversed(rows)
            ]
