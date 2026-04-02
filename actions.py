from __future__ import annotations

from models import Action, ActionRecord, DeployEvent, PendingEffect, WorldState
from scenarios.base import get_service


class ActionExecutor:
    def __init__(self, scenarios: dict):
        self.scenarios = scenarios

    def execute(self, world: WorldState, action: Action) -> tuple[dict, ActionRecord | None, str | None]:
        scenario = self.scenarios[world.scenario_id]
        notes: list[str] = []

        if action.action_type != "finish_incident" and action.cost > world.budget_remaining:
            return {}, None, "Budget exhausted before action could be applied"

        if action.action_type in {
            "inspect_logs",
            "inspect_metrics",
            "inspect_dependencies",
            "restart_service",
            "rollback_service",
            "scale_service",
            "set_rate_limit",
            "declare_root_cause",
        } and action.service and action.service not in {service.name for service in world.services}:
            return {}, None, f"Unknown service: {action.service}"

        before_statuses = {service.name: service.status for service in world.services}
        record = ActionRecord(
            tick=world.tick,
            action=action,
            budget_cost=action.cost,
            before_statuses=before_statuses,
        )
        result: dict = {}

        if action.action_type == "inspect_logs":
            result = scenario.inspect_logs(world, action.service or "", action.tail_n)
        elif action.action_type == "inspect_metrics":
            result = scenario.inspect_metrics(world, action.service or "", action.window_ticks)
        elif action.action_type == "inspect_dependencies":
            result = scenario.inspect_dependencies(world, action.service or "")
        elif action.action_type == "restart_service":
            service = get_service(world, action.service or "")
            record.unnecessary = service.status == "healthy" and not scenario.remediates_root_cause(world, action)
            service.status = "down"
            service.latency_p95_ms = max(service.latency_p95_ms, 300)
            service.latency_p99_ms = max(service.latency_p99_ms, 700)
            service.error_rate = max(service.error_rate, 0.22)
            world.pending_effects.append(
                PendingEffect(
                    effect_type="restart_complete",
                    service=service.name,
                    apply_at=world.tick + 2,
                )
            )
            notes.append(f"{service.name} restart queued with 2-tick recovery delay")
        elif action.action_type == "rollback_service":
            if not action.target_version:
                return {}, None, "rollback_service requires target_version"
            service = get_service(world, action.service or "")
            record.unnecessary = service.status == "healthy" and not scenario.remediates_root_cause(world, action)
            old_version = service.version
            service.version = action.target_version
            world.deploy_history.append(
                DeployEvent(
                    service=service.name,
                    version_from=old_version,
                    version_to=action.target_version,
                    tick=world.tick,
                    triggered_by="agent",
                )
            )
            notes.append(f"{service.name} rolled back from {old_version} to {action.target_version}")
        elif action.action_type == "scale_service":
            if action.replicas is None or action.replicas < 1:
                return {}, None, "scale_service requires replicas >= 1"
            service = get_service(world, action.service or "")
            record.unnecessary = service.status == "healthy" and not scenario.remediates_root_cause(world, action)
            service.replicas = action.replicas
            notes.append(f"{service.name} scaled to {action.replicas} replicas")
        elif action.action_type == "set_rate_limit":
            if action.rps is None or action.rps < 0:
                return {}, None, "set_rate_limit requires rps >= 0"
            service = get_service(world, action.service or "")
            record.unnecessary = service.status == "healthy" and not scenario.remediates_root_cause(world, action)
            service.rate_limit_rps = action.rps
            notes.append(f"{service.name} rate limit set to {action.rps} rps")
        elif action.action_type == "declare_root_cause":
            if not action.reason_code:
                return {}, None, "declare_root_cause requires reason_code"
            world.declared_root_cause = action.service
            world.declared_reason_code = action.reason_code
            notes.append(f"declared root cause as {action.service}:{action.reason_code}")
        elif action.action_type == "finish_incident":
            return {"message": "Incident finished"}, record, None
        else:
            return {}, None, f"Unsupported action: {action.action_type}"

        world.budget_remaining -= action.cost
        scenario.on_remediation(world, action, notes)
        record.notes = notes
        result.setdefault("notes", notes)
        return result, record, None
