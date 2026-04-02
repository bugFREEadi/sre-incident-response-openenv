from __future__ import annotations

import abc
import hashlib
import random
from dataclasses import replace
from typing import Any

from ..models import Alert, DeployEvent, LogLine, ServiceState, WorldState


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


class Scenario(abc.ABC):
    scenario_id: str
    title: str
    description: str
    default_budget: float = 15.0
    staged_fix_action_types: tuple[str, ...] = ("rollback_service", "set_rate_limit")

    @abc.abstractmethod
    def build_world(self, episode_id: str, budget: float | None = None) -> WorldState:
        raise NotImplementedError

    @abc.abstractmethod
    def advance(self, world: WorldState, next_tick: int) -> None:
        raise NotImplementedError

    @abc.abstractmethod
    def slos_restored(self, world: WorldState) -> bool:
        raise NotImplementedError

    def inspect_logs(self, world: WorldState, service: str, tail_n: int) -> dict[str, Any]:
        logs = world.logs.get(service, [])
        return {
            "service": service,
            "kind": "logs",
            "lines": logs[-tail_n:],
        }

    def inspect_metrics(
        self, world: WorldState, service: str, window_ticks: int
    ) -> dict[str, Any]:
        metrics = world.metrics_history.get(service, [])
        return {
            "service": service,
            "kind": "metrics",
            "window": metrics[-window_ticks:],
        }

    def inspect_dependencies(self, world: WorldState, service: str) -> dict[str, Any]:
        target = self.get_service(world, service)
        return {
            "service": service,
            "kind": "dependencies",
            "dependencies": {
                dependency: self.get_service(world, dependency).status
                for dependency in target.dependencies
            },
        }

    def refresh_alerts(self, world: WorldState) -> None:
        previous_ages = {
            (alert.service, alert.message): alert.age_ticks for alert in world.active_alerts
        }
        alerts: list[Alert] = []
        for service in world.services:
            candidates: list[tuple[str, str | None, str]] = []
            if service.status == "down":
                candidates.append(("critical", "availability", "Service unavailable"))
            elif service.error_rate >= 0.12:
                candidates.append(("critical", "errors", "Error rate above 12%"))
            elif service.latency_p99_ms >= 3000:
                candidates.append(("critical", "latency", "p99 latency above 3000ms"))
            elif service.status == "degraded":
                candidates.append(("warning", None, "Service degraded"))
            elif service.saturation >= 0.90:
                candidates.append(("warning", "saturation", "Resource saturation above 90%"))

            for severity, slo_violated, message in candidates:
                age = previous_ages.get((service.name, message), 0) + 1
                alerts.append(
                    Alert(
                        id=f"{service.name}:{severity}:{message}",
                        severity=severity,  # type: ignore[arg-type]
                        service=service.name,
                        message=message,
                        slo_violated=slo_violated,
                        age_ticks=age,
                    )
                )
        world.active_alerts = alerts

    def record_metrics(self, world: WorldState) -> None:
        for service in world.services:
            history = world.metrics_history.setdefault(service.name, [])
            snapshot = {
                "tick": float(world.tick),
                "latency_p95_ms": service.latency_p95_ms,
                "latency_p99_ms": service.latency_p99_ms,
                "error_rate": service.error_rate,
                "saturation": service.saturation,
                "replicas": float(service.replicas),
                "rate_limit_rps": float(service.rate_limit_rps),
            }
            snapshot.update(self.extra_metrics(world, service.name))
            history.append(snapshot)

    def extra_metrics(self, world: WorldState, service_name: str) -> dict[str, float]:
        return {}

    def apply_pending_effects(self, world: WorldState, next_tick: int) -> None:
        remaining: list[Any] = []
        restart_relief = world.scenario_state.setdefault("restart_relief", {})
        restarting_until = world.scenario_state.setdefault("restarting_until", {})
        disruptions = world.scenario_state.setdefault("operator_disruptions", {})

        for effect in world.pending_effects:
            if effect.trigger_tick > next_tick:
                remaining.append(effect)
                continue
            if effect.effect_type == "restart_complete":
                restarting_until.pop(effect.service, None)
                relief_ticks = int(effect.payload.get("relief_ticks", 1))
                if relief_ticks > 0:
                    restart_relief[effect.service] = relief_ticks
            elif effect.effect_type == "clear_disruption":
                disruptions.pop(effect.service, None)
        world.pending_effects = remaining

    def decay_transients(self, world: WorldState) -> None:
        restart_relief = world.scenario_state.setdefault("restart_relief", {})
        disruptions = world.scenario_state.setdefault("operator_disruptions", {})

        for service, remaining in list(restart_relief.items()):
            if remaining <= 1:
                restart_relief.pop(service, None)
            else:
                restart_relief[service] = remaining - 1

        for service, remaining in list(disruptions.items()):
            if remaining <= 1:
                disruptions.pop(service, None)
            else:
                disruptions[service] = remaining - 1

    def stable_noise(self, *parts: Any, span: float = 1.0) -> float:
        raw = "::".join(str(part) for part in parts)
        seed = int(hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16], 16)
        return random.Random(seed).uniform(-span, span)

    def get_service(self, world: WorldState, service_name: str) -> ServiceState:
        for service in world.services:
            if service.name == service_name:
                return service
        raise KeyError(f"Unknown service: {service_name}")

    def set_service(self, world: WorldState, service_name: str, **updates: Any) -> None:
        service = self.get_service(world, service_name)
        for key, value in updates.items():
            setattr(service, key, value)

    def append_log(self, world: WorldState, service: str, level: str, message: str) -> None:
        world.logs.setdefault(service, []).append(
            LogLine(service=service, level=level, message=message, tick=world.tick)
        )

    def append_deploy(
        self,
        world: WorldState,
        service: str,
        version_from: str,
        version_to: str,
        triggered_by: str,
    ) -> None:
        world.deploy_history.append(
            DeployEvent(
                service=service,
                version_from=version_from,
                version_to=version_to,
                tick=world.tick,
                triggered_by=triggered_by,
            )
        )

    def restarting(self, world: WorldState, service_name: str, next_tick: int) -> bool:
        restarting_until = world.scenario_state.setdefault("restarting_until", {})
        return next_tick <= int(restarting_until.get(service_name, -1))

    def disruption_level(self, world: WorldState, service_name: str) -> float:
        disruptions = world.scenario_state.setdefault("operator_disruptions", {})
        return float(disruptions.get(service_name, 0))

    def initial_world(
        self,
        *,
        episode_id: str,
        services: list[ServiceState],
        deploy_history: list[DeployEvent],
        root_cause_service: str,
        root_cause_type: str,
        budget: float | None,
    ) -> WorldState:
        max_budget = budget if budget is not None else self.default_budget
        world = WorldState(
            episode_id=episode_id,
            scenario_id=self.scenario_id,
            scenario_title=self.title,
            tick=0,
            services=[replace(service) for service in services],
            active_alerts=[],
            deploy_history=list(deploy_history),
            root_cause_service=root_cause_service,
            root_cause_type=root_cause_type,
            declared_root_cause=None,
            declared_reason_code=None,
            incident_resolved=False,
            budget_remaining=max_budget,
            max_budget=max_budget,
        )
        world.scenario_state["initial_healthy_services"] = [
            service.name for service in world.services if service.status == "healthy"
        ]
        world.scenario_state["restart_relief"] = {}
        world.scenario_state["restarting_until"] = {}
        world.scenario_state["operator_disruptions"] = {}
        return world
