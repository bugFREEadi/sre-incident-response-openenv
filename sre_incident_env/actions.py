from __future__ import annotations

from typing import Any

from .models import Action, ActionRecord, PendingEffect, WorldState
from .scenarios.base import Scenario


class ActionExecutor:
    INSPECTION_ACTIONS = {"inspect_logs", "inspect_metrics", "inspect_dependencies"}
    REMEDIATION_ACTIONS = {
        "restart_service",
        "rollback_service",
        "scale_service",
        "set_rate_limit",
    }
    COMPLETION_ACTIONS = {"declare_root_cause", "finish_incident"}
    ACTION_COSTS = {
        "inspect_logs": 0.5,
        "inspect_metrics": 0.5,
        "inspect_dependencies": 0.5,
        "restart_service": 1.0,
        "rollback_service": 1.0,
        "scale_service": 1.0,
        "set_rate_limit": 1.0,
        "declare_root_cause": 0.0,
        "finish_incident": 0.0,
    }

    def execute(
        self, world: WorldState, scenario: Scenario, action: Action
    ) -> dict[str, Any]:
        self._validate(world, scenario, action)
        cost = self.ACTION_COSTS[action.action_type]
        if cost > 0 and world.budget_remaining < cost:
            raise ValueError("Budget exhausted for requested action.")

        if action.action_type in self.INSPECTION_ACTIONS:
            world.budget_remaining -= cost
            world.action_history.append(
                ActionRecord(
                    action_type=action.action_type,
                    tick=world.tick,
                    budget_cost=cost,
                    params=self._params(action),
                )
            )
            if world.first_remediation_tick is None:
                world.inspect_actions_before_first_remediation += 1
            if action.action_type == "inspect_logs":
                return scenario.inspect_logs(world, action.service or "", action.tail_n or 20)
            if action.action_type == "inspect_metrics":
                return scenario.inspect_metrics(world, action.service or "", action.window_ticks or 5)
            return scenario.inspect_dependencies(world, action.service or "")

        if action.action_type in self.REMEDIATION_ACTIONS:
            if world.first_remediation_tick is None:
                world.first_remediation_tick = world.tick
            world.budget_remaining -= cost
            service = scenario.get_service(world, action.service or "")
            if service.status == "healthy" and service.name != world.root_cause_service:
                world.unnecessary_remediations += 1
                if service.name in world.scenario_state.get("initial_healthy_services", []):
                    world.blast_radius_violations += 1
            if any(
                record.action_type in self.REMEDIATION_ACTIONS
                and tuple(record.params.get(key) for key in ("service", "target_version", "replicas", "rps"))
                == tuple(
                    self._params(action).get(key)
                    for key in ("service", "target_version", "replicas", "rps")
                )
                and record.action_type == action.action_type
                for record in world.action_history
            ):
                world.repeated_same_action += 1

            if not world.staged_fix_used and service.name == world.root_cause_service:
                if action.action_type in scenario.staged_fix_action_types:
                    prior_remediations = [
                        record
                        for record in world.action_history
                        if record.action_type in self.REMEDIATION_ACTIONS
                    ]
                    if not prior_remediations:
                        world.staged_fix_used = True

            result = self._apply_remediation(world, scenario, action)
            world.action_history.append(
                ActionRecord(
                    action_type=action.action_type,
                    tick=world.tick,
                    budget_cost=cost,
                    params=self._params(action),
                )
            )
            return result

        if action.action_type == "declare_root_cause":
            world.declared_root_cause = action.service
            world.declared_reason_code = action.reason_code
            world.action_history.append(
                ActionRecord(
                    action_type=action.action_type,
                    tick=world.tick,
                    budget_cost=0.0,
                    params=self._params(action),
                )
            )
            return {
                "kind": "declaration",
                "declared_root_cause": world.declared_root_cause,
                "declared_reason_code": world.declared_reason_code,
            }

        world.finished = True
        world.action_history.append(
            ActionRecord(
                action_type=action.action_type,
                tick=world.tick,
                budget_cost=0.0,
                params=self._params(action),
            )
        )
        return {"kind": "finish", "message": "Incident marked for verification."}

    def _apply_remediation(
        self, world: WorldState, scenario: Scenario, action: Action
    ) -> dict[str, Any]:
        service = scenario.get_service(world, action.service or "")
        disruptions = world.scenario_state.setdefault("operator_disruptions", {})
        if action.action_type == "restart_service":
            world.scenario_state.setdefault("restarting_until", {})[service.name] = world.tick + 2
            world.pending_effects.append(
                PendingEffect(
                    effect_type="restart_complete",
                    service=service.name,
                    trigger_tick=world.tick + 3,
                    payload={"relief_ticks": 1},
                )
            )
            disruptions[service.name] = max(int(disruptions.get(service.name, 0)), 2)
            service.status = "down"
            service.error_rate = 1.0
            service.latency_p95_ms = max(service.latency_p95_ms, 2000.0)
            service.latency_p99_ms = max(service.latency_p99_ms, 4500.0)
            service.saturation = 0.15
            return {
                "kind": "remediation",
                "action": "restart_service",
                "service": service.name,
                "message": "Restart initiated; service will recover in roughly 2 ticks.",
            }

        if action.action_type == "rollback_service":
            if not action.target_version:
                raise ValueError("rollback_service requires target_version.")
            previous_version = service.version
            service.version = action.target_version
            disruptions[service.name] = max(int(disruptions.get(service.name, 0)), 1)
            scenario.append_deploy(
                world,
                service=service.name,
                version_from=previous_version,
                version_to=action.target_version,
                triggered_by="operator-rollback",
            )
            return {
                "kind": "remediation",
                "action": "rollback_service",
                "service": service.name,
                "from_version": previous_version,
                "to_version": action.target_version,
            }

        if action.action_type == "scale_service":
            if action.replicas is None or action.replicas < 1:
                raise ValueError("scale_service requires replicas >= 1.")
            previous_replicas = service.replicas
            service.replicas = action.replicas
            return {
                "kind": "remediation",
                "action": "scale_service",
                "service": service.name,
                "from_replicas": previous_replicas,
                "to_replicas": action.replicas,
            }

        if action.rps is None or action.rps < 0:
            raise ValueError("set_rate_limit requires a non-negative rps.")
        previous_rps = service.rate_limit_rps
        service.rate_limit_rps = action.rps
        return {
            "kind": "remediation",
            "action": "set_rate_limit",
            "service": service.name,
            "from_rps": previous_rps,
            "to_rps": action.rps,
        }

    def _validate(self, world: WorldState, scenario: Scenario, action: Action) -> None:
        known_actions = (
            self.INSPECTION_ACTIONS | self.REMEDIATION_ACTIONS | self.COMPLETION_ACTIONS
        )
        if action.action_type not in known_actions:
            raise ValueError(f"Unsupported action: {action.action_type}")
        if action.action_type != "finish_incident" and not action.service and action.action_type != "finish_incident":
            raise ValueError(f"{action.action_type} requires a service.")
        if action.service:
            scenario.get_service(world, action.service)
        if action.action_type == "finish_incident" and not world.declared_root_cause:
            raise ValueError("declare_root_cause must be called before finish_incident.")

    def _params(self, action: Action) -> dict[str, Any]:
        params: dict[str, Any] = {}
        for key in ("service", "tail_n", "window_ticks", "target_version", "replicas", "rps", "reason_code"):
            value = getattr(action, key)
            if value is not None:
                params[key] = value
        return params
