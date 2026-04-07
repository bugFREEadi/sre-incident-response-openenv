from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from models import (
    Action,
    Alert,
    LogLine,
    ServiceState,
    ServiceStatus,
    WorldState,
)


def get_service(world: WorldState, service_name: str) -> ServiceState:
    for service in world.services:
        if service.name == service_name:
            return service
    raise KeyError(f"Unknown service: {service_name}")


import random

def set_status_from_metrics(service: ServiceState) -> None:
    # Add small jitter to simulate non-deterministic monitoring observations.
    # This prevents agents from over-fitting to exact numbers.
    jitter = random.uniform(-0.02, 0.02)
    p99_jitter = random.uniform(-10, 10)
    
    eff_error = service.error_rate + jitter
    eff_p99 = service.latency_p99_ms + p99_jitter
    eff_sat = service.saturation + jitter

    if eff_error >= 0.25 or eff_p99 >= 3500 or eff_sat >= 0.99:
        service.status = "down"
    elif eff_error >= 0.05 or eff_p99 >= 1000 or eff_sat >= 0.85:
        service.status = "degraded"
    else:
        service.status = "healthy"


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


class Scenario(ABC):
    scenario_id: str
    name: str
    summary: str
    max_budget: float = 15.0

    @abstractmethod
    def build_world(self, episode_id: str) -> WorldState:
        raise NotImplementedError

    @abstractmethod
    def apply_tick(self, world: WorldState) -> None:
        raise NotImplementedError

    def inspect_logs(self, world: WorldState, service: str, tail_n: int) -> dict[str, Any]:
        lines = world.service_logs.get(service, [])[-tail_n:]
        return {
            "service": service,
            "tail_n": tail_n,
            "logs": [line.__dict__ for line in lines],
        }

    def inspect_metrics(self, world: WorldState, service: str, window_ticks: int) -> dict[str, Any]:
        history = world.metric_history.get(service, [])[-window_ticks:]
        return {
            "service": service,
            "window_ticks": window_ticks,
            "metrics": history,
        }

    def inspect_dependencies(self, world: WorldState, service: str) -> dict[str, Any]:
        target = get_service(world, service)
        return {
            "service": service,
            "dependencies": [
                {
                    "name": dep,
                    "status": get_service(world, dep).status,
                    "version": get_service(world, dep).version,
                }
                for dep in target.dependencies
            ],
        }

    def on_remediation(self, world: WorldState, action: Action, notes: list[str]) -> None:
        return None

    def is_root_cause_resolved(self, world: WorldState) -> bool:
        return world.root_cause_service == world.declared_root_cause and world.incident_resolved

    def remediates_root_cause(self, world: WorldState, action: Action) -> bool:
        return False

    def staged_fix_used(self, world: WorldState) -> bool:
        remediation_actions = [record for record in world.action_history if record.action.is_remediation]
        if not remediation_actions:
            return False
        first_remediation = remediation_actions[0]
        return self.remediates_root_cause(world, first_remediation.action)

    def bootstrap_world(self, world: WorldState) -> WorldState:
        self.record_metrics(world)
        return world

    def record_metrics(self, world: WorldState) -> None:
        for service in world.services:
            history = world.metric_history.setdefault(service.name, [])
            snapshot = {
                "tick": world.tick,
                "latency_p95_ms": round(service.latency_p95_ms, 2),
                "latency_p99_ms": round(service.latency_p99_ms, 2),
                "error_rate": round(service.error_rate, 4),
                "saturation": round(service.saturation, 4),
                "replicas": service.replicas,
                "rate_limit_rps": service.rate_limit_rps,
            }
            snapshot.update(self.extra_metrics(world, service.name))
            history.append(snapshot)

    def extra_metrics(self, world: WorldState, service_name: str) -> dict[str, Any]:
        return {}

    def append_log(self, world: WorldState, service: str, level: str, message: str) -> None:
        log_line = LogLine(service=service, level=level, message=message, tick=world.tick)
        world.service_logs.setdefault(service, []).append(log_line)

    def refresh_alerts(self, world: WorldState) -> None:
        existing: dict[tuple[str, str], Alert] = {
            (alert.service, alert.slo_violated or alert.message): alert for alert in world.active_alerts
        }
        alerts: list[Alert] = []
        for service in world.services:
            generated = self.alerts_for_service(service)
            for alert in generated:
                key = (alert.service, alert.slo_violated or alert.message)
                previous = existing.get(key)
                alert.age_ticks = previous.age_ticks + 1 if previous else 0
                alerts.append(alert)
        world.active_alerts = alerts

    def alerts_for_service(self, service: ServiceState) -> list[Alert]:
        alerts: list[Alert] = []
        if service.latency_p99_ms >= 1000:
            alerts.append(
                Alert(
                    id=f"{service.name}-latency",
                    severity="critical" if service.latency_p99_ms >= 2500 else "warning",
                    service=service.name,
                    message=f"{service.name} p99 latency elevated",
                    slo_violated="latency_p99",
                    age_ticks=0,
                )
            )
        if service.error_rate >= 0.05:
            alerts.append(
                Alert(
                    id=f"{service.name}-errors",
                    severity="critical" if service.error_rate >= 0.15 else "warning",
                    service=service.name,
                    message=f"{service.name} error budget burn detected",
                    slo_violated="error_rate",
                    age_ticks=0,
                )
            )
        if service.saturation >= 0.85:
            alerts.append(
                Alert(
                    id=f"{service.name}-saturation",
                    severity="critical" if service.saturation >= 0.95 else "warning",
                    service=service.name,
                    message=f"{service.name} saturation is elevated",
                    slo_violated="saturation",
                    age_ticks=0,
                )
            )
        return alerts

    def finalize_tick(self, world: WorldState) -> None:
        for service in world.services:
            set_status_from_metrics(service)
        self.record_metrics(world)
        self.refresh_alerts(world)
        world.incident_resolved = self._all_slos_restored(world)

    def _all_slos_restored(self, world: WorldState) -> bool:
        return all(
            service.error_rate < 0.05 and service.latency_p99_ms < 1000 and service.saturation < 0.85
            for service in world.services
        )
