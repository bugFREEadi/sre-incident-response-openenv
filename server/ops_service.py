from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from fastapi import HTTPException, status

from server.ops_adapters import (
    ArgoCDDeployHistoryAdapter,
    HttpTopologyAdapter,
    LokiLogsAdapter,
    NoOpRemediationAdapter,
    PrometheusMetricsAdapter,
    StaticTopologyAdapter,
    WebhookRemediationAdapter,
)
from server.ops_config import OpsControlPlaneConfig, load_ops_config
from server.ops_models import (
    ActorIdentity,
    AdvisoryPreviewRequest,
    AdvisoryPreviewResponse,
    ApprovalCreateRequest,
    BackupBundle,
    ApprovalDecisionRequest,
    ApprovalRecord,
    AuditEvent,
    ControlPlaneStatus,
    DrillPlan,
    DrillRunRequest,
    DrillRunResult,
    DrillScenarioResult,
    ExecutionRecord,
    ExecutionRequest,
    ExecutionResponse,
    GuardrailDecision,
    ModeChangeRequest,
    PolicyRule,
    ReadOnlyTelemetryResponse,
    RemediationActionRequest,
    utcnow_iso,
)
from server.ops_store import OpsStore
from sre_incident_env.world import IncidentWorld


MUTATING_ACTIONS = {
    "restart_service",
    "rollback_service",
    "scale_service",
    "set_rate_limit",
}


class OpsControlPlaneService:
    def __init__(
        self,
        config: OpsControlPlaneConfig | None = None,
        store: OpsStore | None = None,
        logs_adapter: Any | None = None,
        metrics_adapter: Any | None = None,
        deploy_adapter: Any | None = None,
        topology_adapter: Any | None = None,
        remediation_adapter: Any | None = None,
    ) -> None:
        self.config = config or load_ops_config()
        self.store = store or OpsStore(
            self.config.database_path,
            self.config.audit_jsonl_path,
            self.config.database_url,
        )
        self.logs_adapter = logs_adapter or self._build_logs_adapter()
        self.metrics_adapter = metrics_adapter or self._build_metrics_adapter()
        self.deploy_adapter = deploy_adapter or self._build_deploy_adapter()
        self.topology_adapter = topology_adapter or self._build_topology_adapter()
        self.remediation_adapter = remediation_adapter or self._build_remediation_adapter()

        stored_mode = self.store.get_setting("execution_mode", tenant_id="default")
        if stored_mode:
            self.config.execution_mode = stored_mode
        else:
            self.store.set_setting("execution_mode", self.config.execution_mode, tenant_id="default")

    def _build_logs_adapter(self) -> Any | None:
        if self.config.loki_base_url:
            return LokiLogsAdapter(
                base_url=self.config.loki_base_url,
                query_template=self.config.loki_query_template,
                bearer_token=self.config.loki_bearer_token,
            )
        return None

    def _build_metrics_adapter(self) -> Any | None:
        if self.config.prometheus_base_url:
            return PrometheusMetricsAdapter(
                base_url=self.config.prometheus_base_url,
                query_template=self.config.prometheus_query_template,
                bearer_token=self.config.prometheus_bearer_token,
            )
        return None

    def _build_deploy_adapter(self) -> Any | None:
        if self.config.argocd_base_url:
            return ArgoCDDeployHistoryAdapter(
                base_url=self.config.argocd_base_url,
                bearer_token=self.config.argocd_bearer_token,
            )
        return None

    def _build_topology_adapter(self) -> Any | None:
        if self.config.topology_file:
            return StaticTopologyAdapter(self.config.topology_file)
        if self.config.topology_url:
            return HttpTopologyAdapter(
                self.config.topology_url,
                bearer_token=self.config.topology_bearer_token,
            )
        return None

    def _build_remediation_adapter(self) -> Any:
        if self.config.remediation_webhook_url:
            return WebhookRemediationAdapter(
                self.config.remediation_webhook_url,
                bearer_token=self.config.remediation_bearer_token,
                status_url_template=self.config.remediation_status_url_template,
                verify_attempts=self.config.remediation_verify_attempts,
                verify_delay_seconds=self.config.remediation_verify_delay_seconds,
            )
        return NoOpRemediationAdapter()

    def status(self, actor: ActorIdentity) -> ControlPlaneStatus:
        latest_passing = self.store.latest_drill(actor.tenant_id, only_passing=True)
        return ControlPlaneStatus(
            execution_mode=self.config.execution_mode,
            tenant_id=actor.tenant_id,
            auth_enabled=self.config.require_auth and not self.config.disable_auth_for_local_dev,
            allowed_services=sorted(self.config.allowed_services),
            allowed_mutating_actions=sorted(self.config.allowed_mutating_actions),
            approval_required_for_mutations=self.config.approval_required_for_mutations,
            drill_gate_enabled=self.config.drill_gate_enabled,
            last_passing_drill_at=None if latest_passing is None else latest_passing.completed_at,
            configured_backends={
                "logs": self.logs_adapter is not None,
                "metrics": self.metrics_adapter is not None,
                "deploy_history": self.deploy_adapter is not None,
                "topology": self.topology_adapter is not None,
                "remediation": not isinstance(self.remediation_adapter, NoOpRemediationAdapter),
            },
            persistence_backend=self.store.backend,
        )

    async def fetch_logs(
        self,
        actor: ActorIdentity,
        service: str,
        tail_n: int,
    ) -> ReadOnlyTelemetryResponse:
        self._record_audit(actor, "telemetry.logs", service, "read", {"tail_n": tail_n})
        if self.logs_adapter is None:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Logs adapter not configured")
        records = await self.logs_adapter.fetch_logs(service, tail_n)
        return ReadOnlyTelemetryResponse(
            service=service,
            backend=self.logs_adapter.backend_name,
            kind="logs",
            fetched_at=utcnow_iso(),
            data=[record.model_dump() for record in records],
            metadata={"tail_n": tail_n, "tenant_id": actor.tenant_id},
        )

    async def fetch_metrics(
        self,
        actor: ActorIdentity,
        service: str,
        lookback_minutes: int,
    ) -> ReadOnlyTelemetryResponse:
        self._record_audit(
            actor,
            "telemetry.metrics",
            service,
            "read",
            {"lookback_minutes": lookback_minutes},
        )
        if self.metrics_adapter is None:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Metrics adapter not configured")
        series = await self.metrics_adapter.fetch_metrics(service, lookback_minutes)
        return ReadOnlyTelemetryResponse(
            service=service,
            backend=self.metrics_adapter.backend_name,
            kind="metrics",
            fetched_at=utcnow_iso(),
            data=[entry.model_dump() for entry in series],
            metadata={"lookback_minutes": lookback_minutes, "tenant_id": actor.tenant_id},
        )

    async def fetch_deploy_history(
        self,
        actor: ActorIdentity,
        service: str,
        limit: int,
    ) -> ReadOnlyTelemetryResponse:
        self._record_audit(actor, "telemetry.deploy_history", service, "read", {"limit": limit})
        if self.deploy_adapter is None:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Deploy adapter not configured")
        records = await self.deploy_adapter.fetch_deploy_history(service, limit)
        return ReadOnlyTelemetryResponse(
            service=service,
            backend=self.deploy_adapter.backend_name,
            kind="deploy_history",
            fetched_at=utcnow_iso(),
            data=[record.model_dump() for record in records],
            metadata={"limit": limit, "tenant_id": actor.tenant_id},
        )

    async def fetch_topology(
        self,
        actor: ActorIdentity,
        service: str | None,
    ) -> ReadOnlyTelemetryResponse:
        self._record_audit(actor, "telemetry.topology", service or "*", "read", {})
        if self.topology_adapter is None:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Topology adapter not configured")
        records = await self.topology_adapter.fetch_topology(service)
        return ReadOnlyTelemetryResponse(
            service=service or "*",
            backend=self.topology_adapter.backend_name,
            kind="topology",
            fetched_at=utcnow_iso(),
            data=[record.model_dump() for record in records],
        )

    def preview_action(
        self,
        actor: ActorIdentity,
        request: AdvisoryPreviewRequest,
    ) -> AdvisoryPreviewResponse:
        decision = self._evaluate_guardrails(actor, request.action)
        self._record_audit(
            actor,
            "advisory.preview",
            request.action.service,
            "allow" if decision.allowed else "deny",
            {
                "tenant_id": actor.tenant_id,
                "incident_id": request.incident_id,
                "action": request.action.model_dump(),
                "reasons": decision.reasons,
            },
        )
        next_step = "request_approval" if decision.allowed else "revise_action"
        if self.config.execution_mode == "advisory_only":
            next_step = "request_approval" if decision.allowed else "revise_action"
        return AdvisoryPreviewResponse(
            incident_id=request.incident_id,
            tenant_id=actor.tenant_id,
            action=request.action,
            advisory_only=self.config.execution_mode == "advisory_only",
            guardrail=decision,
            next_step=next_step,
            metadata={
                "evidence_count": len(request.evidence),
                "justification_present": bool(request.justification),
                "tenant_id": actor.tenant_id,
            },
        )

    def create_approval(
        self,
        actor: ActorIdentity,
        request: ApprovalCreateRequest,
    ) -> ApprovalRecord:
        decision = self._evaluate_guardrails(actor, request.action)
        if not decision.allowed:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"Action denied by guardrails: {'; '.join(decision.reasons)}",
            )
        approval = ApprovalRecord(
            approval_id=f"apr_{uuid4().hex[:12]}",
            tenant_id=actor.tenant_id,
            incident_id=request.incident_id,
            action=request.action,
            requested_by=actor.actor_id,
            status="pending",
            justification=request.justification,
            evidence=request.evidence,
            metadata=request.metadata,
            created_at=utcnow_iso(),
            expires_at=_future_iso(minutes=request.expires_in_minutes),
        )
        self.store.create_approval(approval)
        self._record_audit(
            actor,
            "approval.requested",
            request.action.service,
            "pending",
            approval.model_dump(),
        )
        return approval

    def get_approval(self, actor: ActorIdentity, approval_id: str) -> ApprovalRecord:
        approval = self.store.get_approval(approval_id, actor.tenant_id)
        if approval is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Approval not found")
        self._record_audit(actor, "approval.read", approval.action.service, approval.status, {"approval_id": approval_id})
        return approval

    def approve(
        self,
        actor: ActorIdentity,
        approval_id: str,
        request: ApprovalDecisionRequest,
    ) -> ApprovalRecord:
        approval = self.get_approval(actor, approval_id)
        if approval.status != "pending":
            raise HTTPException(status.HTTP_409_CONFLICT, f"Approval is already {approval.status}")
        approval.status = "approved"
        approval.approved_by = actor.actor_id
        approval.approved_at = utcnow_iso()
        approval.note = request.note
        self.store.update_approval(approval)
        self._record_audit(actor, "approval.approved", approval.action.service, "approved", {"approval_id": approval_id})
        return approval

    def reject(
        self,
        actor: ActorIdentity,
        approval_id: str,
        request: ApprovalDecisionRequest,
    ) -> ApprovalRecord:
        approval = self.get_approval(actor, approval_id)
        if approval.status != "pending":
            raise HTTPException(status.HTTP_409_CONFLICT, f"Approval is already {approval.status}")
        approval.status = "rejected"
        approval.approved_by = actor.actor_id
        approval.approved_at = utcnow_iso()
        approval.note = request.note
        self.store.update_approval(approval)
        self._record_audit(actor, "approval.rejected", approval.action.service, "rejected", {"approval_id": approval_id})
        return approval

    async def execute_action(
        self,
        actor: ActorIdentity,
        request: ExecutionRequest,
    ) -> ExecutionResponse:
        decision = self._evaluate_guardrails(actor, request.action)
        if not decision.allowed:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"Action denied by guardrails: {'; '.join(decision.reasons)}",
            )
        if self.config.execution_mode == "advisory_only":
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "Execution mode is advisory_only; real remediation is disabled",
            )
        approval = self.store.get_approval(request.approval_id, actor.tenant_id)
        if approval is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Approval not found")
        if approval.status != "approved":
            raise HTTPException(status.HTTP_409_CONFLICT, f"Approval is {approval.status}, not approved")
        if approval.action != request.action:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Approval action does not match execution request")
        if _is_expired(approval.expires_at):
            approval.status = "expired"
            self.store.update_approval(approval)
            raise HTTPException(status.HTTP_409_CONFLICT, "Approval expired before execution")
        payload = {
            "incident_id": request.incident_id,
            "action": request.action.model_dump(),
            "approval_id": request.approval_id,
            "actor_id": actor.actor_id,
            "metadata": request.metadata,
        }
        result = await self.remediation_adapter.execute(payload, dry_run=request.dry_run)
        execution_status = str(result.get("status", "accepted"))
        execution = ExecutionRecord(
            execution_id=f"exe_{uuid4().hex[:12]}",
            tenant_id=actor.tenant_id,
            approval_id=request.approval_id,
            incident_id=request.incident_id,
            action=request.action,
            requested_by=actor.actor_id,
            backend=self.remediation_adapter.backend_name,
            status=execution_status if execution_status in {"accepted", "running", "succeeded", "failed", "unknown"} else "unknown",
            operation_id=result.get("operation_id"),
            dry_run=request.dry_run,
            created_at=utcnow_iso(),
            updated_at=utcnow_iso(),
            details=result,
        )
        self.store.save_execution(execution)
        approval.status = "executed"
        self.store.update_approval(approval)
        self._record_audit(
            actor,
            "action.executed",
            request.action.service,
            "executed",
            {
                "approval_id": request.approval_id,
                "dry_run": request.dry_run,
                "backend": self.remediation_adapter.backend_name,
                "result": result,
            },
        )
        return ExecutionResponse(
            executed=True,
            execution_id=execution.execution_id,
            approval_id=request.approval_id,
            tenant_id=actor.tenant_id,
            execution_mode=self.config.execution_mode,
            guardrail=decision,
            backend=self.remediation_adapter.backend_name,
            result=result,
        )

    def list_audit(self, actor: ActorIdentity, limit: int = 100) -> list[AuditEvent]:
        self._record_audit(actor, "audit.list", "audit", "read", {"limit": limit})
        return self.store.list_audit(actor.tenant_id, limit)

    def export_backup(self, actor: ActorIdentity) -> BackupBundle:
        self._record_audit(actor, "backup.export", "control-plane", "read", {})
        return self.store.export_bundle(actor.tenant_id, self.config.execution_mode)

    def get_execution(self, actor: ActorIdentity, execution_id: str) -> ExecutionRecord:
        execution = self.store.get_execution(execution_id, actor.tenant_id)
        if execution is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Execution not found")
        self._record_audit(actor, "execution.read", execution.action.service, execution.status, {"execution_id": execution_id})
        return execution

    def run_drills(self, actor: ActorIdentity, request: DrillRunRequest) -> DrillRunResult:
        scenarios = request.scenarios or sorted(IncidentWorld().scenarios.keys())
        plans_by_scenario = {plan.scenario_id: plan for plan in request.plans}
        world = IncidentWorld()
        started_at = utcnow_iso()
        scenario_results: list[DrillScenarioResult] = []

        for scenario_id in scenarios:
            step_actions = plans_by_scenario.get(scenario_id)
            if step_actions is None:
                actions = _default_drill_actions(scenario_id, request.strategy)
            else:
                actions = step_actions.actions

            reset_payload = world.reset(scenario_id=scenario_id)
            episode_id = reset_payload["episode_id"]
            last_reward = reset_payload["observation"].get("score_so_far", {})
            verifier_result = None

            for action_payload in actions:
                step_payload = world.step(episode_id, action_payload)
                last_reward = step_payload.get("reward", last_reward)
                verifier_result = step_payload.get("verifier_result")
                if step_payload.get("done"):
                    break

            final_score = float(last_reward.get("final_score", 0.0))
            recovery_score = float(last_reward.get("recovery_score", 0.0))
            decision_score = float(last_reward.get("decision_score", 0.0))
            root_cause_correct = bool((verifier_result or {}).get("root_cause_correct", False))
            scenario_results.append(
                DrillScenarioResult(
                    scenario_id=scenario_id,
                    score=final_score,
                    passed=final_score >= request.minimum_scenario_score,
                    root_cause_correct=root_cause_correct,
                    decision_score=decision_score,
                    recovery_score=recovery_score,
                )
            )

        average_score = sum(item.score for item in scenario_results) / max(len(scenario_results), 1)
        passed = average_score >= request.minimum_average_score and all(item.passed for item in scenario_results)
        result = DrillRunResult(
            drill_id=f"drill_{uuid4().hex[:12]}",
            tenant_id=actor.tenant_id,
            requested_by=actor.actor_id,
            started_at=started_at,
            completed_at=utcnow_iso(),
            strategy=request.strategy,
            average_score=round(average_score, 4),
            passed=passed,
            scenarios=scenario_results,
            thresholds={
                "minimum_average_score": request.minimum_average_score,
                "minimum_scenario_score": request.minimum_scenario_score,
            },
        )
        self.store.save_drill(result)
        self._record_audit(
            actor,
            "drill.run",
            "control-plane",
            "passed" if passed else "failed",
            result.model_dump(),
        )
        return result

    def latest_drill(self, actor: ActorIdentity) -> DrillRunResult | None:
        self._record_audit(actor, "drill.latest", "control-plane", "read", {})
        return self.store.latest_drill(actor.tenant_id)

    def set_execution_mode(
        self,
        actor: ActorIdentity,
        request: ModeChangeRequest,
    ) -> ControlPlaneStatus:
        if request.execution_mode != "advisory_only" and self.config.drill_gate_enabled:
            latest = self.store.latest_drill(actor.tenant_id, only_passing=True)
            if latest is None:
                raise HTTPException(
                    status.HTTP_409_CONFLICT,
                    "A passing internal drill run is required before enabling automation",
                )
            if _hours_since(latest.completed_at) > self.config.drill_validity_hours:
                raise HTTPException(
                    status.HTTP_409_CONFLICT,
                    "The latest passing drill is too old to enable automation",
                )

        self.config.execution_mode = request.execution_mode
        self.store.set_setting("execution_mode", request.execution_mode, actor.tenant_id)
        self._record_audit(
            actor,
            "mode.changed",
            "control-plane",
            request.execution_mode,
            {"execution_mode": request.execution_mode},
        )
        return self.status(actor)

    def _evaluate_guardrails(self, actor: ActorIdentity, action: RemediationActionRequest) -> GuardrailDecision:
        reasons: list[str] = []
        requires_approval = self.config.approval_required_for_mutations
        max_scale_replicas = self.config.max_scale_replicas
        max_rate_limit_rps = self.config.max_rate_limit_rps
        if self.config.allowed_mutating_actions and action.action_type not in self.config.allowed_mutating_actions:
            reasons.append(f"{action.action_type} is not in the mutating action allowlist")
        if self.config.allowed_services and action.service not in self.config.allowed_services:
            reasons.append(f"{action.service} is not in the service allowlist")
        if action.action_type == "rollback_service" and not action.target_version:
            reasons.append("rollback_service requires target_version")
        if action.action_type == "scale_service":
            if action.replicas is None:
                reasons.append("scale_service requires replicas")
            elif action.replicas > max_scale_replicas:
                reasons.append(
                    f"scale_service exceeds max replicas guardrail ({max_scale_replicas})"
                )
        if action.action_type == "set_rate_limit":
            if action.rps is None:
                reasons.append("set_rate_limit requires rps")
            elif action.rps > max_rate_limit_rps:
                reasons.append(
                    f"set_rate_limit exceeds max rps guardrail ({max_rate_limit_rps})"
                )

        for rule in self.config.policy_rules:
            if not _policy_matches(rule, actor, action):
                continue
            if rule.deny:
                reasons.append(f"policy {rule.rule_id} denied this action")
            if rule.require_approval is not None:
                requires_approval = rule.require_approval
            if rule.max_replicas is not None:
                max_scale_replicas = min(max_scale_replicas, rule.max_replicas)
                if action.action_type == "scale_service" and action.replicas and action.replicas > max_scale_replicas:
                    reasons.append(f"policy {rule.rule_id} lowered max replicas to {max_scale_replicas}")
            if rule.max_rps is not None:
                max_rate_limit_rps = min(max_rate_limit_rps, rule.max_rps)
                if action.action_type == "set_rate_limit" and action.rps and action.rps > max_rate_limit_rps:
                    reasons.append(f"policy {rule.rule_id} lowered max rps to {max_rate_limit_rps}")

        allowed = not reasons
        return GuardrailDecision(
            allowed=allowed,
            execution_mode=self.config.execution_mode,
            requires_approval=requires_approval,
            reasons=reasons,
            normalized_action=action.normalized_payload(),
            allowed_services=sorted(self.config.allowed_services),
            allowed_actions=sorted(self.config.allowed_mutating_actions),
        )

    def _record_audit(
        self,
        actor: ActorIdentity,
        event_type: str,
        target: str,
        decision: str,
        payload: dict[str, Any],
    ) -> None:
        event = AuditEvent(
            event_id=f"evt_{uuid4().hex[:12]}",
            timestamp=utcnow_iso(),
            tenant_id=actor.tenant_id,
            actor_id=actor.actor_id,
            event_type=event_type,
            target=target,
            decision=decision,
            payload=payload,
        )
        self.store.record_audit(event)


def _future_iso(minutes: int) -> str:
    future = datetime.now(timezone.utc) + timedelta(minutes=minutes)
    return future.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _is_expired(expires_at: str) -> bool:
    return _parse_timestamp(expires_at) < datetime.now(timezone.utc)


def _hours_since(timestamp: str) -> float:
    return (datetime.now(timezone.utc) - _parse_timestamp(timestamp)).total_seconds() / 3600.0


def _parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _policy_matches(rule: PolicyRule, actor: ActorIdentity, action: RemediationActionRequest) -> bool:
    now_hour = datetime.now(timezone.utc).hour
    if rule.action_types and action.action_type not in rule.action_types:
        return False
    if rule.services and action.service not in rule.services:
        return False
    if rule.tenants and actor.tenant_id not in rule.tenants:
        return False
    if rule.roles and not set(rule.roles).intersection(actor.roles):
        return False
    if rule.active_from_hour_utc is not None and now_hour < rule.active_from_hour_utc:
        return False
    if rule.active_to_hour_utc is not None and now_hour > rule.active_to_hour_utc:
        return False
    return True


def _default_drill_actions(scenario_id: str, strategy: str) -> list[dict[str, Any]]:
    safe_plans = {
        "s01_restart_cascade": [
            {"action_type": "inspect_logs", "service": "orders-postgres", "tail_n": 20},
            {"action_type": "inspect_metrics", "service": "invoice-consumer", "window_ticks": 5},
            {"action_type": "inspect_dependencies", "service": "payments-api"},
            {"action_type": "declare_root_cause", "service": "invoice-consumer", "reason_code": "connection_leak"},
            {"action_type": "rollback_service", "service": "invoice-consumer", "target_version": "2026.03.7"},
            {"action_type": "finish_incident"},
        ],
        "s02_corrupt_scaleup": [
            {"action_type": "inspect_logs", "service": "checkout-api", "tail_n": 20},
            {"action_type": "inspect_metrics", "service": "checkout-api", "window_ticks": 5},
            {
                "action_type": "declare_root_cause",
                "service": "checkout-api",
                "reason_code": "feature_flag_corruption",
            },
            {"action_type": "set_rate_limit", "service": "checkout-api", "rps": 0},
            {"action_type": "inspect_metrics", "service": "orders-postgres", "window_ticks": 5},
            {"action_type": "finish_incident"},
        ],
        "s03_wrong_rollback": [
            {"action_type": "inspect_logs", "service": "accounts-api", "tail_n": 20},
            {"action_type": "inspect_metrics", "service": "identity-service", "window_ticks": 3},
            {"action_type": "declare_root_cause", "service": "identity-service", "reason_code": "bad_deploy"},
            {"action_type": "rollback_service", "service": "identity-service", "target_version": "2026.03.6"},
            {"action_type": "inspect_logs", "service": "accounts-api", "tail_n": 10},
            {"action_type": "inspect_metrics", "service": "accounts-api", "window_ticks": 3},
            {"action_type": "finish_incident"},
        ],
        "s04_cache_stampede": [
            {"action_type": "inspect_logs", "service": "redis-catalog", "tail_n": 20},
            {"action_type": "inspect_metrics", "service": "catalog-api", "window_ticks": 5},
            {"action_type": "inspect_dependencies", "service": "search-api"},
            {"action_type": "declare_root_cause", "service": "catalog-api", "reason_code": "cache_key_regression"},
            {"action_type": "rollback_service", "service": "catalog-api", "target_version": "2026.03.9"},
            {"action_type": "inspect_metrics", "service": "redis-catalog", "window_ticks": 5},
            {"action_type": "finish_incident"},
        ],
        "s05_webhook_retry_storm": [
            {"action_type": "inspect_logs", "service": "notification-dispatcher", "tail_n": 20},
            {"action_type": "inspect_metrics", "service": "webhook-relay", "window_ticks": 5},
            {"action_type": "inspect_dependencies", "service": "notification-dispatcher"},
            {
                "action_type": "declare_root_cause",
                "service": "notification-dispatcher",
                "reason_code": "duplicate_dispatch",
            },
            {"action_type": "set_rate_limit", "service": "notification-dispatcher", "rps": 0},
            {"action_type": "inspect_metrics", "service": "orders-events-kafka", "window_ticks": 5},
            {"action_type": "finish_incident"},
        ],
    }
    naive_plans = {
        "s01_restart_cascade": [
            {"action_type": "restart_service", "service": "payments-api"},
            {"action_type": "restart_service", "service": "payments-api"},
            {"action_type": "finish_incident"},
        ],
        "s02_corrupt_scaleup": [
            {"action_type": "scale_service", "service": "checkout-api", "replicas": 8},
            {"action_type": "finish_incident"},
        ],
        "s03_wrong_rollback": [
            {"action_type": "rollback_service", "service": "accounts-api", "target_version": "2026.03.1"},
            {"action_type": "finish_incident"},
        ],
        "s04_cache_stampede": [
            {"action_type": "restart_service", "service": "redis-catalog"},
            {"action_type": "finish_incident"},
        ],
        "s05_webhook_retry_storm": [
            {"action_type": "scale_service", "service": "notification-dispatcher", "replicas": 8},
            {"action_type": "finish_incident"},
        ],
    }
    selected = safe_plans if strategy == "safe_fallback" else naive_plans
    if scenario_id not in selected:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Unknown scenario for drill: {scenario_id}")
    return selected[scenario_id]
