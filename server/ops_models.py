from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field

ExecutionMode = Literal["advisory_only", "approval_required", "enabled"]
ApprovalStatus = Literal["pending", "approved", "rejected", "executed", "expired"]
TelemetryKind = Literal["logs", "metrics", "deploy_history", "topology"]
RemediationStatus = Literal["accepted", "running", "succeeded", "failed", "unknown"]
MutatingActionType = Literal[
    "restart_service",
    "rollback_service",
    "scale_service",
    "set_rate_limit",
]


class ActorIdentity(BaseModel):
    actor_id: str
    roles: list[str] = Field(default_factory=list)
    token_name: str | None = None
    allowed_tenants: list[str] = Field(default_factory=lambda: ["default"])
    tenant_id: str = "default"


class LogRecord(BaseModel):
    timestamp: str | None = None
    service: str
    level: str
    message: str
    labels: dict[str, str] = Field(default_factory=dict)
    source: str | None = None


class MetricPoint(BaseModel):
    timestamp: str
    value: float


class MetricSeries(BaseModel):
    name: str
    service: str
    points: list[MetricPoint] = Field(default_factory=list)
    labels: dict[str, str] = Field(default_factory=dict)


class DeployRecord(BaseModel):
    service: str
    version_from: str | None = None
    version_to: str | None = None
    timestamp: str | None = None
    triggered_by: str | None = None
    source: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class TopologyRecord(BaseModel):
    service: str
    dependencies: list[str] = Field(default_factory=list)
    dependents: list[str] = Field(default_factory=list)
    owner: str | None = None
    tier: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ReadOnlyTelemetryResponse(BaseModel):
    service: str
    backend: str
    kind: TelemetryKind
    fetched_at: str
    data: list[dict[str, Any]] | dict[str, Any]
    metadata: dict[str, Any] = Field(default_factory=dict)


class RemediationActionRequest(BaseModel):
    action_type: MutatingActionType
    service: str
    target_version: str | None = None
    replicas: int | None = Field(default=None, ge=1, le=200)
    rps: int | None = Field(default=None, ge=0, le=100000)

    def normalized_payload(self) -> dict[str, Any]:
        return self.model_dump(exclude_none=True)


class AdvisoryPreviewRequest(BaseModel):
    incident_id: str
    action: RemediationActionRequest
    justification: str = ""
    evidence: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    tenant_id: str | None = None


class GuardrailDecision(BaseModel):
    allowed: bool
    execution_mode: ExecutionMode
    requires_approval: bool
    reasons: list[str] = Field(default_factory=list)
    normalized_action: dict[str, Any]
    allowed_services: list[str] = Field(default_factory=list)
    allowed_actions: list[str] = Field(default_factory=list)


class AdvisoryPreviewResponse(BaseModel):
    incident_id: str
    tenant_id: str
    action: RemediationActionRequest
    advisory_only: bool
    guardrail: GuardrailDecision
    next_step: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class ApprovalCreateRequest(BaseModel):
    incident_id: str
    action: RemediationActionRequest
    justification: str
    evidence: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    expires_in_minutes: int = Field(default=60, ge=5, le=1440)
    tenant_id: str | None = None


class ApprovalDecisionRequest(BaseModel):
    note: str = ""


class ApprovalRecord(BaseModel):
    approval_id: str
    tenant_id: str
    incident_id: str
    action: RemediationActionRequest
    requested_by: str
    status: ApprovalStatus
    justification: str
    evidence: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: str
    expires_at: str
    approved_by: str | None = None
    approved_at: str | None = None
    note: str | None = None


class ExecutionRequest(BaseModel):
    incident_id: str
    action: RemediationActionRequest
    approval_id: str
    dry_run: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)
    tenant_id: str | None = None


class ExecutionResponse(BaseModel):
    executed: bool
    execution_id: str
    approval_id: str
    tenant_id: str
    execution_mode: ExecutionMode
    guardrail: GuardrailDecision
    backend: str
    result: dict[str, Any] = Field(default_factory=dict)


class AuditEvent(BaseModel):
    event_id: str
    timestamp: str
    tenant_id: str
    actor_id: str
    event_type: str
    target: str
    decision: str
    payload: dict[str, Any] = Field(default_factory=dict)


class DrillPlan(BaseModel):
    scenario_id: str
    actions: list[dict[str, Any]] = Field(default_factory=list)


class DrillRunRequest(BaseModel):
    strategy: Literal["safe_fallback", "naive_restart"] = "safe_fallback"
    scenarios: list[str] = Field(default_factory=list)
    plans: list[DrillPlan] = Field(default_factory=list)
    minimum_average_score: float = Field(default=0.75, ge=0.0, le=1.0)
    minimum_scenario_score: float = Field(default=0.65, ge=0.0, le=1.0)


class DrillScenarioResult(BaseModel):
    scenario_id: str
    score: float
    passed: bool
    root_cause_correct: bool
    decision_score: float
    recovery_score: float


class DrillRunResult(BaseModel):
    drill_id: str
    tenant_id: str
    requested_by: str
    started_at: str
    completed_at: str
    strategy: str
    average_score: float
    passed: bool
    scenarios: list[DrillScenarioResult] = Field(default_factory=list)
    thresholds: dict[str, float] = Field(default_factory=dict)


class ModeChangeRequest(BaseModel):
    execution_mode: ExecutionMode


class ControlPlaneStatus(BaseModel):
    execution_mode: ExecutionMode
    tenant_id: str
    auth_enabled: bool
    allowed_services: list[str] = Field(default_factory=list)
    allowed_mutating_actions: list[str] = Field(default_factory=list)
    approval_required_for_mutations: bool = True
    drill_gate_enabled: bool = True
    last_passing_drill_at: str | None = None
    configured_backends: dict[str, bool] = Field(default_factory=dict)
    persistence_backend: str = "sqlite"


class PolicyRule(BaseModel):
    rule_id: str
    description: str = ""
    action_types: list[str] = Field(default_factory=list)
    services: list[str] = Field(default_factory=list)
    tenants: list[str] = Field(default_factory=list)
    roles: list[str] = Field(default_factory=list)
    deny: bool = False
    require_approval: bool | None = None
    max_replicas: int | None = None
    max_rps: int | None = None
    active_from_hour_utc: int | None = Field(default=None, ge=0, le=23)
    active_to_hour_utc: int | None = Field(default=None, ge=0, le=23)


class ExecutionRecord(BaseModel):
    execution_id: str
    tenant_id: str
    approval_id: str
    incident_id: str
    action: RemediationActionRequest
    requested_by: str
    backend: str
    status: RemediationStatus
    operation_id: str | None = None
    dry_run: bool = False
    created_at: str
    updated_at: str
    details: dict[str, Any] = Field(default_factory=dict)


class BackupBundle(BaseModel):
    exported_at: str
    tenant_id: str
    execution_mode: str
    approvals: list[ApprovalRecord] = Field(default_factory=list)
    audit_events: list[AuditEvent] = Field(default_factory=list)
    drills: list[DrillRunResult] = Field(default_factory=list)
    executions: list[ExecutionRecord] = Field(default_factory=list)


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
