from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

ServiceStatus = Literal["healthy", "degraded", "down"]
AlertSeverity = Literal["critical", "warning", "info"]
ActionType = Literal[
    "inspect_logs",
    "inspect_metrics",
    "inspect_dependencies",
    "restart_service",
    "rollback_service",
    "scale_service",
    "set_rate_limit",
    "declare_root_cause",
    "finish_incident",
]


@dataclass
class Alert:
    id: str
    severity: AlertSeverity
    service: str
    message: str
    slo_violated: str | None
    age_ticks: int


@dataclass
class LogLine:
    service: str
    level: str
    message: str
    tick: int


@dataclass
class DeployEvent:
    service: str
    version_from: str
    version_to: str
    tick: int
    triggered_by: str


@dataclass
class ServiceState:
    name: str
    status: ServiceStatus
    version: str
    latency_p95_ms: float
    latency_p99_ms: float
    error_rate: float
    saturation: float
    replicas: int
    rate_limit_rps: int
    dependencies: list[str]
    root_cause: bool = False


@dataclass
class ServiceObservation:
    name: str
    status: ServiceStatus
    version: str
    latency_p95_ms: float
    latency_p99_ms: float
    error_rate: float
    saturation: float
    replicas: int
    rate_limit_rps: int
    dependency_statuses: dict[str, ServiceStatus]


@dataclass
class Observation:
    episode_id: str
    tick: int
    budget_remaining: float
    services: list[ServiceObservation]
    alerts: list[Alert]
    recent_logs: list[LogLine]
    deploy_history: list[DeployEvent]
    score_so_far: dict[str, Any]


@dataclass
class Action:
    action_type: ActionType
    service: str | None = None
    tail_n: int = 20
    window_ticks: int = 5
    target_version: str | None = None
    replicas: int | None = None
    rps: int | None = None
    reason_code: str | None = None

    @property
    def is_inspection(self) -> bool:
        return self.action_type in {
            "inspect_logs",
            "inspect_metrics",
            "inspect_dependencies",
        }

    @property
    def is_remediation(self) -> bool:
        return self.action_type in {
            "restart_service",
            "rollback_service",
            "scale_service",
            "set_rate_limit",
        }

    @property
    def cost(self) -> float:
        if self.is_inspection:
            return 0.5
        if self.is_remediation:
            return 1.0
        return 0.0

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "Action":
        return cls(
            action_type=payload["action_type"],
            service=payload.get("service"),
            tail_n=payload.get("tail_n", 20),
            window_ticks=payload.get("window_ticks", 5),
            target_version=payload.get("target_version"),
            replicas=payload.get("replicas"),
            rps=payload.get("rps"),
            reason_code=payload.get("reason_code"),
        )


@dataclass
class PendingEffect:
    effect_type: str
    service: str
    apply_at: int
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class ActionRecord:
    tick: int
    action: Action
    budget_cost: float
    before_statuses: dict[str, ServiceStatus]
    after_statuses: dict[str, ServiceStatus] = field(default_factory=dict)
    unnecessary: bool = False
    blast_radius_violations: int = 0
    notes: list[str] = field(default_factory=list)


@dataclass
class WorldState:
    episode_id: str
    tick: int
    services: list[ServiceState]
    active_alerts: list[Alert]
    deploy_history: list[DeployEvent]
    root_cause_service: str
    root_cause_type: str
    declared_root_cause: str | None
    incident_resolved: bool
    budget_remaining: float
    declared_reason_code: str | None = None
    scenario_id: str = ""
    scenario_name: str = ""
    max_budget: float = 15.0
    action_history: list[ActionRecord] = field(default_factory=list)
    pending_effects: list[PendingEffect] = field(default_factory=list)
    service_logs: dict[str, list[LogLine]] = field(default_factory=dict)
    metric_history: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    scenario_state: dict[str, Any] = field(default_factory=dict)
    terminated: bool = False
    termination_reason: str | None = None


@dataclass
class VerifierResult:
    root_cause_correct: bool
    reason_code_correct: bool
    slos_restored: bool
    blast_radius_clean: bool
    efficiency_ok: bool
    staged_fix_used: bool
    investigate_before_act: bool
    final_world_state: dict[str, Any]
    score_breakdown: dict[str, Any]


@dataclass
class StepResult:
    observation: Observation
    action_result: dict[str, Any]
    reward: dict[str, Any]
    done: bool
    verifier_result: VerifierResult | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def to_dict(value: Any) -> dict[str, Any]:
    return asdict(value)
