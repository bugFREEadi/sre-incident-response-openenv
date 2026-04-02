from __future__ import annotations

from .models import VerifierResult, WorldState
from .scenarios.base import Scenario


class VerifierEngine:
    def verify(self, world: WorldState, scenario: Scenario) -> VerifierResult:
        return VerifierResult(
            root_cause_correct=world.declared_root_cause == world.root_cause_service,
            reason_code_correct=world.declared_reason_code == world.root_cause_type,
            slos_restored=scenario.slos_restored(world),
            blast_radius_clean=world.blast_radius_violations == 0,
            efficiency_ok=world.tick <= world.max_budget * 0.60,
            staged_fix_used=world.staged_fix_used,
            investigate_before_act=world.inspect_actions_before_first_remediation >= 2,
            final_world_state={
                service.name: {
                    "status": service.status,
                    "version": service.version,
                    "latency_p99_ms": round(service.latency_p99_ms, 2),
                    "error_rate": round(service.error_rate, 4),
                    "saturation": round(service.saturation, 4),
                }
                for service in world.services
            },
            score_breakdown={},
        )
