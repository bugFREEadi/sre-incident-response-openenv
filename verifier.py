from __future__ import annotations

from models import VerifierResult, WorldState
from reward import RewardEngine


class VerifierEngine:
    def __init__(self, reward_engine: RewardEngine):
        self.reward_engine = reward_engine

    def verify(self, world: WorldState, cap_decision_score: float | None = None) -> VerifierResult:
        score_breakdown = self.reward_engine.score(world, cap_decision_score=cap_decision_score)
        spent_budget = world.max_budget - world.budget_remaining
        blast_radius_violations = sum(record.blast_radius_violations for record in world.action_history)
        return VerifierResult(
            root_cause_correct=world.declared_root_cause == world.root_cause_service,
            reason_code_correct=world.declared_reason_code == world.root_cause_type,
            slos_restored=self.reward_engine._slos_restored(world),
            blast_radius_clean=blast_radius_violations == 0,
            efficiency_ok=spent_budget <= (world.max_budget * 0.6),
            staged_fix_used=self.reward_engine._staged_fix_used(world),
            investigate_before_act=self.reward_engine._investigated_before_act(world),
            final_world_state={
                "tick": world.tick,
                "budget_remaining": round(world.budget_remaining, 2),
                "services": {
                    service.name: {
                        "status": service.status,
                        "version": service.version,
                        "latency_p99_ms": round(service.latency_p99_ms, 2),
                        "error_rate": round(service.error_rate, 4),
                        "saturation": round(service.saturation, 4),
                    }
                    for service in world.services
                },
            },
            score_breakdown=score_breakdown,
        )
