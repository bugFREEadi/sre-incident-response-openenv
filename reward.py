from __future__ import annotations

from models import WorldState


class RewardEngine:
    def score(self, world: WorldState, cap_decision_score: float | None = None) -> dict[str, float]:
        recovery = self.recovery_score(world)
        decision = self.decision_score(world)
        if cap_decision_score is not None:
            decision = min(decision, cap_decision_score)
        final = 0.5 * recovery + 0.5 * decision
        
        # Validator requires ALL score fields strictly in (0, 1) — never exactly 0.0 or 1.0
        # This applies not just to final_score but also the breakdown metrics
        final = max(0.01, min(final, 0.95))
        recovery = max(0.01, min(recovery, 0.95))
        decision = max(0.01, min(decision, 0.95))
        
        return {
            "recovery_score": round(recovery, 4),
            "decision_score": round(decision, 4),
            "final_score": round(final, 4),
        }

    def recovery_score(self, world: WorldState) -> float:
        slo_restored = 1.0 if self._slos_restored(world) else 0.0
        time_factor = max(0.0, 1 - (world.tick / max(world.max_budget, 1.0)))
        residual = 1 - self._residual_degradation(world)
        score = (0.5 * slo_restored) + (0.25 * time_factor) + (0.25 * residual)
        return max(0.0, min(1.0, score))

    def decision_score(self, world: WorldState) -> float:
        root_cause_correct = 1.0 if world.declared_root_cause == world.root_cause_service else 0.0
        reason_code_correct = 1.0 if world.declared_reason_code == world.root_cause_type else 0.0
        unnecessary_remediations = sum(
            1 for record in world.action_history if record.action.is_remediation and record.unnecessary
        )
        blast_radius_violations = sum(record.blast_radius_violations for record in world.action_history)
        repeated_same_action = self._repeated_same_actions(world)
        staged_fix = 1.0 if self._staged_fix_used(world) else 0.0
        investigate_before_act = 1.0 if self._investigated_before_act(world) else 0.0
        late_steps = self._late_step_count(world)

        score = (
            (0.30 * root_cause_correct)
            + (0.20 * reason_code_correct)
            - (0.10 * unnecessary_remediations)
            - (0.15 * blast_radius_violations)
            - (0.08 * repeated_same_action)
            + (0.15 * staged_fix)
            + (0.10 * investigate_before_act)
            - (0.02 * late_steps)
        )
        return max(0.0, min(1.0, score))

    def _slos_restored(self, world: WorldState) -> bool:
        return all(
            service.error_rate < 0.05 and service.latency_p99_ms < 1000 and service.saturation < 0.85
            for service in world.services
        )

    def _residual_degradation(self, world: WorldState) -> float:
        severities = {"healthy": 0.0, "degraded": 0.5, "down": 1.0}
        if not world.services:
            return 0.0
        return sum(severities[service.status] for service in world.services) / len(world.services)

    def _repeated_same_actions(self, world: WorldState) -> int:
        repeated = 0
        previous_key: tuple[str, str | None] | None = None
        seen_counts: dict[tuple[str, str | None], int] = {}
        for record in world.action_history:
            key = (record.action.action_type, record.action.service)
            if record.action.is_remediation and key == previous_key:
                repeated += 1
            if record.action.is_remediation:
                seen_counts[key] = seen_counts.get(key, 0) + 1
                if seen_counts[key] > 2:
                    repeated += 1
            previous_key = key
        return repeated

    def _staged_fix_used(self, world: WorldState) -> bool:
        scenario = world.scenario_state.get("scenario")
        if scenario is not None:
            return scenario.staged_fix_used(world)
        remediation_actions = [record for record in world.action_history if record.action.is_remediation]
        if not remediation_actions:
            return False
        first_remediation = remediation_actions[0].action
        if first_remediation.action_type == "rollback_service" and first_remediation.service == world.root_cause_service:
            return True
        if first_remediation.action_type == "set_rate_limit" and first_remediation.service == world.root_cause_service:
            return True
        return False

    def _investigated_before_act(self, world: WorldState) -> bool:
        inspections = 0
        for record in world.action_history:
            if record.action.is_inspection:
                inspections += 1
                continue
            if record.action.is_remediation:
                return inspections >= 2
        return False

    def _late_step_count(self, world: WorldState) -> int:
        midpoint = world.max_budget / 2
        spent = 0.0
        late = 0
        for record in world.action_history:
            spent += record.budget_cost
            if spent > midpoint:
                late += 1
        return late
