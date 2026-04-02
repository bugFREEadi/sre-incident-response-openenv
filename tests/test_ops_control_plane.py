from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from server.app import build_app
from server.ops_config import OpsControlPlaneConfig
from server.ops_models import (
    ActorIdentity,
    DeployRecord,
    LogRecord,
    MetricPoint,
    MetricSeries,
    PolicyRule,
    TopologyRecord,
)
from server.ops_service import OpsControlPlaneService
from server.ops_store import OpsStore


class FakeLogsAdapter:
    backend_name = "fake-logs"

    async def fetch_logs(self, service: str, tail_n: int = 20):
        return [
            LogRecord(
                timestamp="2026-04-02T10:00:00Z",
                service=service,
                level="ERROR",
                message="sample log",
                labels={"env": "test"},
                source=self.backend_name,
            )
        ]


class FakeMetricsAdapter:
    backend_name = "fake-metrics"

    async def fetch_metrics(self, service: str, lookback_minutes: int = 15):
        return [
            MetricSeries(
                name="http_requests_total",
                service=service,
                points=[MetricPoint(timestamp="1", value=42.0)],
                labels={"service": service},
            )
        ]


class FakeDeployAdapter:
    backend_name = "fake-deploys"

    async def fetch_deploy_history(self, service: str, limit: int = 20):
        return [
            DeployRecord(
                service=service,
                version_from="2026.03.7",
                version_to="2026.04.1",
                timestamp="2026-04-02T09:00:00Z",
                triggered_by="argocd/test",
                source=self.backend_name,
                metadata={},
            )
        ]


class FakeTopologyAdapter:
    backend_name = "fake-topology"

    async def fetch_topology(self, service: str | None = None):
        records = [
            TopologyRecord(
                service="payments-api",
                dependencies=["orders-postgres"],
                dependents=[],
                owner="payments-team",
                tier="tier1",
                metadata={},
            )
        ]
        if service:
            return [record for record in records if record.service == service]
        return records


class FakeRemediationAdapter:
    backend_name = "fake-remediation"

    async def execute(self, payload: dict, dry_run: bool = False) -> dict:
        return {
            "accepted": True,
            "dry_run": dry_run,
            "payload": payload,
            "operation_id": payload["approval_id"],
            "status": "succeeded",
        }


class OpsControlPlaneTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        base = Path(self.temp_dir.name)
        config = OpsControlPlaneConfig(
            execution_mode="advisory_only",
            require_auth=True,
            approval_required_for_mutations=True,
            drill_gate_enabled=True,
            database_path=str(base / "ops.sqlite3"),
            audit_jsonl_path=str(base / "ops_audit.jsonl"),
            allowed_services={"payments-api", "invoice-consumer", "checkout-api"},
            allowed_mutating_actions={
                "restart_service",
                "rollback_service",
                "scale_service",
                "set_rate_limit",
            },
            api_tokens={
                "viewer-token": ActorIdentity(actor_id="viewer", roles=["viewer"], allowed_tenants=["default"]),
                "operator-token": ActorIdentity(actor_id="operator", roles=["operator"], allowed_tenants=["default"]),
                "approver-token": ActorIdentity(actor_id="approver", roles=["approver"], allowed_tenants=["default"]),
                "admin-token": ActorIdentity(actor_id="admin", roles=["admin"], allowed_tenants=["default"]),
            },
            max_scale_replicas=6,
            policy_rules=[
                PolicyRule(
                    rule_id="protect-checkout-scale",
                    services=["checkout-api"],
                    action_types=["scale_service"],
                    max_replicas=4,
                )
            ],
        )
        store = OpsStore(config.database_path, config.audit_jsonl_path)
        self.control_plane = OpsControlPlaneService(
            config=config,
            store=store,
            logs_adapter=FakeLogsAdapter(),
            metrics_adapter=FakeMetricsAdapter(),
            deploy_adapter=FakeDeployAdapter(),
            topology_adapter=FakeTopologyAdapter(),
            remediation_adapter=FakeRemediationAdapter(),
        )
        self.client = TestClient(build_app(control_plane=self.control_plane))

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _headers(self, token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}", "X-Tenant-Id": "default"}

    def test_read_only_routes_require_auth_and_return_adapter_payloads(self) -> None:
        unauthorized = self.client.get("/ops/v1/logs", params={"service": "payments-api"})
        self.assertEqual(unauthorized.status_code, 401)

        authorized = self.client.get(
            "/ops/v1/logs",
            params={"service": "payments-api", "tail_n": 5},
            headers=self._headers("viewer-token"),
        )
        self.assertEqual(authorized.status_code, 200)
        payload = authorized.json()
        self.assertEqual(payload["backend"], "fake-logs")
        self.assertEqual(payload["kind"], "logs")
        self.assertEqual(payload["data"][0]["service"], "payments-api")

        metrics = self.client.get(
            "/ops/v1/metrics",
            params={"service": "payments-api"},
            headers=self._headers("viewer-token"),
        )
        self.assertEqual(metrics.status_code, 200)
        self.assertEqual(metrics.json()["backend"], "fake-metrics")

    def test_advisory_mode_blocks_execution_until_drill_and_mode_change(self) -> None:
        preview = self.client.post(
            "/ops/v1/advisories/preview",
            json={
                "incident_id": "inc-123",
                "action": {"action_type": "restart_service", "service": "payments-api"},
                "justification": "temporary mitigation while investigating",
                "evidence": ["payments-api is timing out"],
            },
            headers=self._headers("operator-token"),
        )
        self.assertEqual(preview.status_code, 200)
        self.assertTrue(preview.json()["advisory_only"])
        self.assertTrue(preview.json()["guardrail"]["allowed"])

        approval = self.client.post(
            "/ops/v1/approvals",
            json={
                "incident_id": "inc-123",
                "action": {"action_type": "restart_service", "service": "payments-api"},
                "justification": "pager noise is high and operator wants approval trail",
                "evidence": ["ticket-42"],
            },
            headers=self._headers("operator-token"),
        )
        self.assertEqual(approval.status_code, 200)
        approval_id = approval.json()["approval_id"]

        approved = self.client.post(
            f"/ops/v1/approvals/{approval_id}/approve",
            json={"note": "approved by incident commander"},
            headers=self._headers("approver-token"),
        )
        self.assertEqual(approved.status_code, 200)
        self.assertEqual(approved.json()["status"], "approved")

        blocked = self.client.post(
            "/ops/v1/actions/execute",
            json={
                "incident_id": "inc-123",
                "approval_id": approval_id,
                "action": {"action_type": "restart_service", "service": "payments-api"},
                "dry_run": False,
            },
            headers=self._headers("operator-token"),
        )
        self.assertEqual(blocked.status_code, 409)
        self.assertIn("advisory_only", blocked.json()["detail"])

        drill = self.client.post(
            "/ops/v1/drills/run",
            json={
                "strategy": "safe_fallback",
                "minimum_average_score": 0.70,
                "minimum_scenario_score": 0.60,
            },
            headers=self._headers("admin-token"),
        )
        self.assertEqual(drill.status_code, 200)
        self.assertTrue(drill.json()["passed"])

        mode = self.client.post(
            "/ops/v1/mode",
            json={"execution_mode": "approval_required"},
            headers=self._headers("admin-token"),
        )
        self.assertEqual(mode.status_code, 200)
        self.assertEqual(mode.json()["execution_mode"], "approval_required")

        executed = self.client.post(
            "/ops/v1/actions/execute",
            json={
                "incident_id": "inc-123",
                "approval_id": approval_id,
                "action": {"action_type": "restart_service", "service": "payments-api"},
                "dry_run": False,
            },
            headers=self._headers("operator-token"),
        )
        self.assertEqual(executed.status_code, 200)
        self.assertTrue(executed.json()["executed"])
        self.assertEqual(executed.json()["backend"], "fake-remediation")
        execution_id = executed.json()["execution_id"]

        execution = self.client.get(
            f"/ops/v1/executions/{execution_id}",
            headers=self._headers("operator-token"),
        )
        self.assertEqual(execution.status_code, 200)
        self.assertEqual(execution.json()["status"], "succeeded")

    def test_guardrails_deny_disallowed_or_oversized_actions(self) -> None:
        disallowed_service = self.client.post(
            "/ops/v1/advisories/preview",
            json={
                "incident_id": "inc-999",
                "action": {"action_type": "restart_service", "service": "auth-service"},
            },
            headers=self._headers("operator-token"),
        )
        self.assertEqual(disallowed_service.status_code, 200)
        self.assertFalse(disallowed_service.json()["guardrail"]["allowed"])

        oversized_scale = self.client.post(
            "/ops/v1/advisories/preview",
            json={
                "incident_id": "inc-999",
                "action": {"action_type": "scale_service", "service": "payments-api", "replicas": 20},
            },
            headers=self._headers("operator-token"),
        )
        self.assertEqual(oversized_scale.status_code, 200)
        self.assertFalse(oversized_scale.json()["guardrail"]["allowed"])
        reasons = oversized_scale.json()["guardrail"]["reasons"]
        self.assertTrue(any("max replicas" in reason for reason in reasons))

        policy_limited = self.client.post(
            "/ops/v1/advisories/preview",
            json={
                "incident_id": "inc-999",
                "action": {"action_type": "scale_service", "service": "checkout-api", "replicas": 5},
            },
            headers=self._headers("operator-token"),
        )
        self.assertEqual(policy_limited.status_code, 200)
        self.assertFalse(policy_limited.json()["guardrail"]["allowed"])
        self.assertTrue(
            any("protect-checkout-scale" in reason for reason in policy_limited.json()["guardrail"]["reasons"])
        )

    def test_audit_endpoint_returns_control_plane_events(self) -> None:
        self.client.get(
            "/ops/v1/topology",
            params={"service": "payments-api"},
            headers=self._headers("viewer-token"),
        )
        audit = self.client.get(
            "/ops/v1/audit",
            headers=self._headers("approver-token"),
        )
        self.assertEqual(audit.status_code, 200)
        events = audit.json()
        self.assertGreaterEqual(len(events), 1)
        self.assertIn("event_type", events[0])

    def test_backup_export_and_tenant_isolation(self) -> None:
        denied = self.client.get(
            "/ops/v1/status",
            headers={"Authorization": "Bearer viewer-token", "X-Tenant-Id": "other-tenant"},
        )
        self.assertEqual(denied.status_code, 403)

        backup = self.client.get(
            "/ops/v1/admin/backup",
            headers=self._headers("admin-token"),
        )
        self.assertEqual(backup.status_code, 200)
        self.assertEqual(backup.json()["tenant_id"], "default")
        self.assertIn("approvals", backup.json())


if __name__ == "__main__":
    unittest.main()
