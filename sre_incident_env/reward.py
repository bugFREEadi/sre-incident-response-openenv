from __future__ import annotations

from .models import Action, VerifierResult, WorldState
from .scenarios.base import Scenario, clamp


class RewardEngine:
    def live_score(self, world: WorldState, scenario: Scenario) -> dict[str, float]:
        recovery = self._recovery_score(world, scenario)
        decision = self._decision_score(world, scenario, completed=False)
        return {
            "recovery_score": round(recovery, 4),
            "decision_score": round(decision, 4),
            "final_score": round((recovery + decision) * 0.5, 4),
        }

    def step_reward(
        self,
        previous_world: WorldState | None,
        world: WorldState,
        scenario: Scenario,
        action: Action,
        verifier: VerifierResult | None = None,
    ) -> float:
        if verifier is not None:
            breakdown = verifier.score_breakdown
            return float(breakdown.get("final_score", 0.0))
        if previous_world is None:
            return 0.0
        before = self._recovery_score(previous_world, scenario)
        after = self._recovery_score(world, scenario)
        penalty = 0.0
        if action.action_type == "restart_service":
            penalty = 0.03
        elif action.action_type in {"rollback_service", "scale_service", "set_rate_limit"}:
            penalty = 0.01
        return round((after - before) - penalty, 4)

    def apply_final_breakdown(
        self, world: WorldState, scenario: Scenario, verifier: VerifierResult
    ) -> None:
        recovery = self._recovery_score(world, scenario)
        decision = self._decision_score(world, scenario, completed=world.finished)
        final_score = (recovery + decision) * 0.5
        verifier.score_breakdown = {
            "recovery_score": round(recovery, 4),
            "decision_score": round(decision, 4),
            "final_score": round(final_score, 4),
            "unnecessary_remediations": world.unnecessary_remediations,
            "blast_radius_violations": world.blast_radius_violations,
            "repeated_same_action": world.repeated_same_action,
            "ticks_taken": world.tick,
            "budget_remaining": round(world.budget_remaining, 2),
        }

    def _recovery_score(self, world: WorldState, scenario: Scenario) -> float:
        slo_restored = 1.0 if scenario.slos_restored(world) else 0.0
        time_factor = clamp(1.0 - (world.tick / max(world.max_budget, 1.0)), 0.0, 1.0)
        residual = 1.0 - self._residual_degradation(world)
        return clamp(0.5 * slo_restored + 0.25 * time_factor + 0.25 * residual, 0.0, 1.0)

    def _decision_score(self, world: WorldState, scenario: Scenario, completed: bool) -> float:
        root_cause_correct = world.declared_root_cause == world.root_cause_service
        reason_code_correct = world.declared_reason_code == world.root_cause_type
        investigate_before_act = world.inspect_actions_before_first_remediation >= 2
        midpoint_penalty = 0.02 * max(0, world.tick - int(world.max_budget / 2))
        decision = (
            (0.30 if root_cause_correct else 0.0)
            + (0.20 if reason_code_correct else 0.0)
            - 0.10 * world.unnecessary_remediations
            - 0.15 * world.blast_radius_violations
            - 0.08 * world.repeated_same_action
            + (0.15 if world.staged_fix_used else 0.0)
            + (0.10 if investigate_before_act else 0.0)
            - midpoint_penalty
        )
        decision = clamp(decision, 0.0, 1.0)
        if not completed:
            return min(decision, 0.3)
        return decision

    def _residual_degradation(self, world: WorldState) -> float:
        penalties: list[float] = []
        for service in world.services:
            status_penalty = {"healthy": 0.0, "degraded": 0.55, "down": 1.0}[service.status]
            latency_penalty = clamp((service.latency_p99_ms - 800.0) / 4000.0, 0.0, 1.0)
            error_penalty = clamp(service.error_rate / 0.25, 0.0, 1.0)
            penalties.append(clamp(0.45 * status_penalty + 0.35 * latency_penalty + 0.20 * error_penalty, 0.0, 1.0))
        return sum(penalties) / max(len(penalties), 1)
