from __future__ import annotations

import json
import os
import sqlite3
import threading
from typing import Any

from server.ops_models import ApprovalRecord, AuditEvent, BackupBundle, DrillRunResult, ExecutionRecord

try:
    import psycopg
    from psycopg.rows import dict_row
except Exception:  # pragma: no cover - optional dependency in local dev
    psycopg = None
    dict_row = None


class OpsStore:
    def __init__(
        self,
        database_path: str | None = None,
        audit_jsonl_path: str | None = None,
        database_url: str | None = None,
    ) -> None:
        self.database_path = database_path or "data/ops_control_plane.sqlite3"
        self.database_url = database_url
        self.audit_jsonl_path = audit_jsonl_path
        self.backend = "postgres" if database_url else "sqlite"
        self._lock = threading.Lock()
        self._ensure_parent_dirs()
        self._init_db()

    def _ensure_parent_dirs(self) -> None:
        for path in (self.database_path, self.audit_jsonl_path):
            if not path or self.backend == "postgres":
                continue
            parent = os.path.dirname(path)
            if parent:
                os.makedirs(parent, exist_ok=True)

    def _connect(self):
        if self.backend == "postgres":
            if psycopg is None:
                raise RuntimeError("psycopg is required when OPS_DATABASE_URL is configured")
            return psycopg.connect(self.database_url, row_factory=dict_row)
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _execute(self, connection, query: str, params: tuple[Any, ...] = ()) -> Any:
        if self.backend == "postgres":
            return connection.execute(query.replace("?", "%s"), params)
        return connection.execute(query, params)

    def _query_all(self, query: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = self._execute(connection, query, params).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def _query_one(self, query: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = self._execute(connection, query, params).fetchone()
        return None if row is None else self._row_to_dict(row)

    def _row_to_dict(self, row: Any) -> dict[str, Any]:
        if isinstance(row, sqlite3.Row):
            return dict(row)
        return dict(row)

    def _init_db(self) -> None:
        statements = [
            """
            CREATE TABLE IF NOT EXISTS approvals (
                approval_id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                incident_id TEXT NOT NULL,
                action_json TEXT NOT NULL,
                requested_by TEXT NOT NULL,
                status TEXT NOT NULL,
                justification TEXT NOT NULL,
                evidence_json TEXT NOT NULL,
                metadata_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                approved_by TEXT,
                approved_at TEXT,
                note TEXT
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS audit_events (
                event_id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                actor_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                target TEXT NOT NULL,
                decision TEXT NOT NULL,
                payload_json TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS drills (
                drill_id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                requested_by TEXT NOT NULL,
                started_at TEXT NOT NULL,
                completed_at TEXT NOT NULL,
                strategy TEXT NOT NULL,
                average_score REAL NOT NULL,
                passed INTEGER NOT NULL,
                scenarios_json TEXT NOT NULL,
                thresholds_json TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS executions (
                execution_id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                approval_id TEXT NOT NULL,
                incident_id TEXT NOT NULL,
                action_json TEXT NOT NULL,
                requested_by TEXT NOT NULL,
                backend TEXT NOT NULL,
                status TEXT NOT NULL,
                operation_id TEXT,
                dry_run INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                details_json TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS settings (
                tenant_id TEXT NOT NULL,
                key TEXT NOT NULL,
                value_json TEXT NOT NULL,
                PRIMARY KEY (tenant_id, key)
            )
            """,
        ]
        with self._connect() as connection:
            for statement in statements:
                self._execute(connection, statement)
            if self.backend == "sqlite":
                self._migrate_sqlite_schema(connection)
            connection.commit()

    def _migrate_sqlite_schema(self, connection) -> None:
        migrations = {
            "approvals": [
                ("tenant_id", "TEXT NOT NULL DEFAULT 'default'"),
            ],
            "audit_events": [
                ("tenant_id", "TEXT NOT NULL DEFAULT 'default'"),
            ],
            "drills": [
                ("tenant_id", "TEXT NOT NULL DEFAULT 'default'"),
            ],
            "settings": [
                ("tenant_id", "TEXT NOT NULL DEFAULT 'default'"),
            ],
            "executions": [],
        }
        for table_name, columns in migrations.items():
            existing = {
                row["name"]
                for row in connection.execute(f"PRAGMA table_info({table_name})").fetchall()
            }
            for column_name, ddl in columns:
                if column_name not in existing:
                    try:
                        connection.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {ddl}")
                    except sqlite3.OperationalError as exc:
                        if "duplicate column name" not in str(exc):
                            raise

    def create_approval(self, approval: ApprovalRecord) -> None:
        with self._lock, self._connect() as connection:
            self._execute(
                connection,
                """
                INSERT INTO approvals (
                    approval_id, tenant_id, incident_id, action_json, requested_by, status,
                    justification, evidence_json, metadata_json, created_at, expires_at,
                    approved_by, approved_at, note
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    approval.approval_id,
                    approval.tenant_id,
                    approval.incident_id,
                    approval.action.model_dump_json(),
                    approval.requested_by,
                    approval.status,
                    approval.justification,
                    json.dumps(approval.evidence),
                    json.dumps(approval.metadata),
                    approval.created_at,
                    approval.expires_at,
                    approval.approved_by,
                    approval.approved_at,
                    approval.note,
                ),
            )
            connection.commit()

    def get_approval(self, approval_id: str, tenant_id: str) -> ApprovalRecord | None:
        row = self._query_one(
            "SELECT * FROM approvals WHERE approval_id = ? AND tenant_id = ?",
            (approval_id, tenant_id),
        )
        if row is None:
            return None
        return ApprovalRecord.model_validate(
            {
                "approval_id": row["approval_id"],
                "tenant_id": row["tenant_id"],
                "incident_id": row["incident_id"],
                "action": json.loads(row["action_json"]),
                "requested_by": row["requested_by"],
                "status": row["status"],
                "justification": row["justification"],
                "evidence": json.loads(row["evidence_json"]),
                "metadata": json.loads(row["metadata_json"]),
                "created_at": row["created_at"],
                "expires_at": row["expires_at"],
                "approved_by": row["approved_by"],
                "approved_at": row["approved_at"],
                "note": row["note"],
            }
        )

    def update_approval(self, approval: ApprovalRecord) -> None:
        with self._lock, self._connect() as connection:
            self._execute(
                connection,
                """
                UPDATE approvals
                SET status = ?, approved_by = ?, approved_at = ?, note = ?, metadata_json = ?
                WHERE approval_id = ? AND tenant_id = ?
                """,
                (
                    approval.status,
                    approval.approved_by,
                    approval.approved_at,
                    approval.note,
                    json.dumps(approval.metadata),
                    approval.approval_id,
                    approval.tenant_id,
                ),
            )
            connection.commit()

    def record_audit(self, event: AuditEvent) -> None:
        with self._lock, self._connect() as connection:
            self._execute(
                connection,
                """
                INSERT INTO audit_events (
                    event_id, tenant_id, timestamp, actor_id, event_type, target, decision, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.event_id,
                    event.tenant_id,
                    event.timestamp,
                    event.actor_id,
                    event.event_type,
                    event.target,
                    event.decision,
                    json.dumps(event.payload),
                ),
            )
            connection.commit()
        if self.audit_jsonl_path:
            with open(self.audit_jsonl_path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(event.model_dump()) + "\n")

    def list_audit(self, tenant_id: str, limit: int = 100) -> list[AuditEvent]:
        rows = self._query_all(
            """
            SELECT * FROM audit_events
            WHERE tenant_id = ?
            ORDER BY timestamp DESC
            LIMIT ?
            """,
            (tenant_id, limit),
        )
        return [
            AuditEvent.model_validate(
                {
                    "event_id": row["event_id"],
                    "tenant_id": row["tenant_id"],
                    "timestamp": row["timestamp"],
                    "actor_id": row["actor_id"],
                    "event_type": row["event_type"],
                    "target": row["target"],
                    "decision": row["decision"],
                    "payload": json.loads(row["payload_json"]),
                }
            )
            for row in rows
        ]

    def save_drill(self, result: DrillRunResult) -> None:
        with self._lock, self._connect() as connection:
            self._execute(
                connection,
                """
                INSERT INTO drills (
                    drill_id, tenant_id, requested_by, started_at, completed_at, strategy,
                    average_score, passed, scenarios_json, thresholds_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    result.drill_id,
                    result.tenant_id,
                    result.requested_by,
                    result.started_at,
                    result.completed_at,
                    result.strategy,
                    result.average_score,
                    1 if result.passed else 0,
                    json.dumps([scenario.model_dump() for scenario in result.scenarios]),
                    json.dumps(result.thresholds),
                ),
            )
            connection.commit()

    def latest_drill(self, tenant_id: str, only_passing: bool = False) -> DrillRunResult | None:
        query = "SELECT * FROM drills WHERE tenant_id = ?"
        params: list[Any] = [tenant_id]
        if only_passing:
            query += " AND passed = 1"
        query += " ORDER BY completed_at DESC LIMIT 1"
        row = self._query_one(query, tuple(params))
        if row is None:
            return None
        return DrillRunResult.model_validate(
            {
                "drill_id": row["drill_id"],
                "tenant_id": row["tenant_id"],
                "requested_by": row["requested_by"],
                "started_at": row["started_at"],
                "completed_at": row["completed_at"],
                "strategy": row["strategy"],
                "average_score": row["average_score"],
                "passed": bool(row["passed"]),
                "scenarios": json.loads(row["scenarios_json"]),
                "thresholds": json.loads(row["thresholds_json"]),
            }
        )

    def save_execution(self, execution: ExecutionRecord) -> None:
        with self._lock, self._connect() as connection:
            self._execute(
                connection,
                """
                INSERT INTO executions (
                    execution_id, tenant_id, approval_id, incident_id, action_json, requested_by,
                    backend, status, operation_id, dry_run, created_at, updated_at, details_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    execution.execution_id,
                    execution.tenant_id,
                    execution.approval_id,
                    execution.incident_id,
                    execution.action.model_dump_json(),
                    execution.requested_by,
                    execution.backend,
                    execution.status,
                    execution.operation_id,
                    1 if execution.dry_run else 0,
                    execution.created_at,
                    execution.updated_at,
                    json.dumps(execution.details),
                ),
            )
            connection.commit()

    def update_execution(self, execution: ExecutionRecord) -> None:
        with self._lock, self._connect() as connection:
            self._execute(
                connection,
                """
                UPDATE executions
                SET status = ?, operation_id = ?, updated_at = ?, details_json = ?
                WHERE execution_id = ? AND tenant_id = ?
                """,
                (
                    execution.status,
                    execution.operation_id,
                    execution.updated_at,
                    json.dumps(execution.details),
                    execution.execution_id,
                    execution.tenant_id,
                ),
            )
            connection.commit()

    def get_execution(self, execution_id: str, tenant_id: str) -> ExecutionRecord | None:
        row = self._query_one(
            "SELECT * FROM executions WHERE execution_id = ? AND tenant_id = ?",
            (execution_id, tenant_id),
        )
        if row is None:
            return None
        return ExecutionRecord.model_validate(
            {
                "execution_id": row["execution_id"],
                "tenant_id": row["tenant_id"],
                "approval_id": row["approval_id"],
                "incident_id": row["incident_id"],
                "action": json.loads(row["action_json"]),
                "requested_by": row["requested_by"],
                "backend": row["backend"],
                "status": row["status"],
                "operation_id": row["operation_id"],
                "dry_run": bool(row["dry_run"]),
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
                "details": json.loads(row["details_json"]),
            }
        )

    def get_setting(self, key: str, tenant_id: str) -> Any | None:
        row = self._query_one(
            "SELECT value_json FROM settings WHERE key = ? AND tenant_id = ?",
            (key, tenant_id),
        )
        if row is None:
            return None
        return json.loads(row["value_json"])

    def set_setting(self, key: str, value: object, tenant_id: str) -> None:
        encoded = json.dumps(value)
        with self._lock, self._connect() as connection:
            if self.backend == "postgres":
                query = """
                    INSERT INTO settings (key, tenant_id, value_json)
                    VALUES (?, ?, ?)
                    ON CONFLICT (tenant_id, key) DO UPDATE SET value_json = EXCLUDED.value_json
                """
            else:
                query = """
                    INSERT INTO settings (key, tenant_id, value_json)
                    VALUES (?, ?, ?)
                    ON CONFLICT(tenant_id, key) DO UPDATE SET value_json = excluded.value_json
                """
            self._execute(connection, query, (key, tenant_id, encoded))
            connection.commit()

    def export_bundle(self, tenant_id: str, execution_mode: str) -> BackupBundle:
        approvals = self._query_all("SELECT * FROM approvals WHERE tenant_id = ? ORDER BY created_at DESC", (tenant_id,))
        audits = self._query_all("SELECT * FROM audit_events WHERE tenant_id = ? ORDER BY timestamp DESC", (tenant_id,))
        drills = self._query_all("SELECT * FROM drills WHERE tenant_id = ? ORDER BY completed_at DESC", (tenant_id,))
        executions = self._query_all("SELECT * FROM executions WHERE tenant_id = ? ORDER BY created_at DESC", (tenant_id,))
        return BackupBundle(
            exported_at=_utc_now(),
            tenant_id=tenant_id,
            execution_mode=execution_mode,
            approvals=[
                ApprovalRecord.model_validate(
                    {
                        "approval_id": row["approval_id"],
                        "tenant_id": row["tenant_id"],
                        "incident_id": row["incident_id"],
                        "action": json.loads(row["action_json"]),
                        "requested_by": row["requested_by"],
                        "status": row["status"],
                        "justification": row["justification"],
                        "evidence": json.loads(row["evidence_json"]),
                        "metadata": json.loads(row["metadata_json"]),
                        "created_at": row["created_at"],
                        "expires_at": row["expires_at"],
                        "approved_by": row["approved_by"],
                        "approved_at": row["approved_at"],
                        "note": row["note"],
                    }
                )
                for row in approvals
            ],
            audit_events=[
                AuditEvent.model_validate(
                    {
                        "event_id": row["event_id"],
                        "tenant_id": row["tenant_id"],
                        "timestamp": row["timestamp"],
                        "actor_id": row["actor_id"],
                        "event_type": row["event_type"],
                        "target": row["target"],
                        "decision": row["decision"],
                        "payload": json.loads(row["payload_json"]),
                    }
                )
                for row in audits
            ],
            drills=[
                DrillRunResult.model_validate(
                    {
                        "drill_id": row["drill_id"],
                        "tenant_id": row["tenant_id"],
                        "requested_by": row["requested_by"],
                        "started_at": row["started_at"],
                        "completed_at": row["completed_at"],
                        "strategy": row["strategy"],
                        "average_score": row["average_score"],
                        "passed": bool(row["passed"]),
                        "scenarios": json.loads(row["scenarios_json"]),
                        "thresholds": json.loads(row["thresholds_json"]),
                    }
                )
                for row in drills
            ],
            executions=[
                ExecutionRecord.model_validate(
                    {
                        "execution_id": row["execution_id"],
                        "tenant_id": row["tenant_id"],
                        "approval_id": row["approval_id"],
                        "incident_id": row["incident_id"],
                        "action": json.loads(row["action_json"]),
                        "requested_by": row["requested_by"],
                        "backend": row["backend"],
                        "status": row["status"],
                        "operation_id": row["operation_id"],
                        "dry_run": bool(row["dry_run"]),
                        "created_at": row["created_at"],
                        "updated_at": row["updated_at"],
                        "details": json.loads(row["details_json"]),
                    }
                )
                for row in executions
            ],
        )


def _utc_now() -> str:
    from server.ops_models import utcnow_iso

    return utcnow_iso()
