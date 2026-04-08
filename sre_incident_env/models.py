from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

ServiceStatus = Literal["healthy", "degraded", "down"]
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


# ---------------------------------------------------------------------------
# Local base classes (previously inherited from openenv_core.env_server.types)
# ---------------------------------------------------------------------------

class Action(BaseModel):
    """Base class for all OpenEnv actions."""
    metadata: dict[str, Any] | None = None


class Observation(BaseModel):
    """Base class for all OpenEnv observations."""
    reward: float | None = None
    done: bool = False
    metadata: dict[str, Any] | None = None


class State(BaseModel):
    """Base class for all OpenEnv states."""
    episode_id: str
    step_count: int = 0


# ---------------------------------------------------------------------------
# Domain models
# ---------------------------------------------------------------------------

class AlertModel(BaseModel):
    id: str
    severity: Literal["critical", "warning", "info"]
    service: str
    message: str
    slo_violated: str | None = None
    age_ticks: int


class LogLineModel(BaseModel):
    service: str
    level: str
    message: str
    tick: int


class DeployEventModel(BaseModel):
    service: str
    version_from: str
    version_to: str
    tick: int
    triggered_by: str


class ServiceObservationModel(BaseModel):
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


class SREIncidentAction(Action):
    action_type: ActionType = Field(..., description="Typed action selected by the agent")
    service: str | None = Field(default=None, description="Target service when applicable")
    tail_n: int = Field(default=20, ge=1, le=200)
    window_ticks: int = Field(default=5, ge=1, le=20)
    target_version: str | None = Field(default=None)
    replicas: int | None = Field(default=None, ge=1, le=20)
    rps: int | None = Field(default=None, ge=0, le=10000)
    reason_code: str | None = Field(default=None, description="Declared root-cause type")


class SREIncidentObservation(Observation):
    episode_id: str = Field(..., description="Current episode identifier")
    scenario_id: str = Field(..., description="Scenario/task identifier")
    scenario_name: str = Field(..., description="Human-friendly task name")
    tick: int = Field(..., ge=0)
    budget_remaining: float = Field(..., ge=0)
    services: list[ServiceObservationModel] = Field(default_factory=list)
    alerts: list[AlertModel] = Field(default_factory=list)
    recent_logs: list[LogLineModel] = Field(default_factory=list)
    deploy_history: list[DeployEventModel] = Field(default_factory=list)
    score_so_far: dict[str, Any] = Field(default_factory=dict)
    available_actions: list[str] = Field(
        default_factory=lambda: [
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
    )


class SREIncidentState(State):
    scenario_id: str | None = None
    scenario_name: str | None = None
    budget_remaining: float = 0.0
    terminated: bool = False
    declared_root_cause: str | None = None
    declared_reason_code: str | None = None
