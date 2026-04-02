from __future__ import annotations

from ..models import DeployEvent, ServiceState, WorldState
from .base import Scenario, clamp


class WrongRollbackScenario(Scenario):
    scenario_id = "s03_wrong_rollback"
    title = "Wrong-Service Rollback Worsens Auth"
    description = "user-service errors are downstream of an auth-service token expiry bug."

    def build_world(self, episode_id: str, budget: float | None = None) -> WorldState:
        services = [
            ServiceState(
                name="user-service",
                status="degraded",
                version="v2.2.1",
                latency_p95_ms=620,
                latency_p99_ms=1800,
                error_rate=0.22,
                saturation=0.68,
                replicas=4,
                rate_limit_rps=1100,
                dependencies=["auth-service", "profile-db"],
            ),
            ServiceState(
                name="auth-service",
                status="healthy",
                version="v1.4.0",
                latency_p95_ms=120,
                latency_p99_ms=260,
                error_rate=0.04,
                saturation=0.57,
                replicas=3,
                rate_limit_rps=1800,
                dependencies=[],
                root_cause=True,
            ),
            ServiceState(
                name="profile-db",
                status="healthy",
                version="v12.9",
                latency_p95_ms=28,
                latency_p99_ms=48,
                error_rate=0.0,
                saturation=0.33,
                replicas=1,
                rate_limit_rps=2800,
                dependencies=[],
            ),
            ServiceState(
                name="session-cache",
                status="healthy",
                version="v6.5.1",
                latency_p95_ms=8,
                latency_p99_ms=18,
                error_rate=0.0,
                saturation=0.25,
                replicas=1,
                rate_limit_rps=2500,
                dependencies=[],
            ),
        ]
        deploy_history = [
            DeployEvent(
                service="auth-service",
                version_from="v1.3.9",
                version_to="v1.4.0",
                tick=-2,
                triggered_by="ci-deploy",
            )
        ]
        world = self.initial_world(
            episode_id=episode_id,
            services=services,
            deploy_history=deploy_history,
            root_cause_service="auth-service",
            root_cause_type="bad_deploy",
            budget=budget,
        )
        world.scenario_state["token_rejection_rate"] = 0.38
        self.append_log(
            world,
            "user-service",
            "ERROR",
            "401 Unauthorized from auth-service while fetching user profile",
        )
        self.record_metrics(world)
        self.refresh_alerts(world)
        return world

    def extra_metrics(self, world: WorldState, service_name: str) -> dict[str, float]:
        if service_name != "auth-service":
            return {}
        return {"token_rejection_rate": float(world.scenario_state.get("token_rejection_rate", 0.0))}

    def advance(self, world: WorldState, next_tick: int) -> None:
        self.apply_pending_effects(world, next_tick)
        bad_deploy_live = self.get_service(world, "auth-service").version == "v1.4.0"
        rejection_rate = float(world.scenario_state.get("token_rejection_rate", 0.38))

        if bad_deploy_live:
            rejection_rate = clamp(rejection_rate + 0.05, 0.12, 0.70)
        else:
            rejection_rate = clamp(rejection_rate - 0.52, 0.0, 0.70)

        auth_status = "healthy" if rejection_rate < 0.12 else "degraded"
        self.set_service(
            world,
            "auth-service",
            status=auth_status,
            latency_p95_ms=105 + rejection_rate * 60,
            latency_p99_ms=210 + rejection_rate * 90,
            error_rate=clamp(
                0.01 + rejection_rate * 0.12 - (0.02 if not bad_deploy_live else 0.0),
                0.0,
                0.30,
            ),
            saturation=clamp(0.42 + rejection_rate * 0.22, 0.0, 1.0),
        )

        user_disruption = self.disruption_level(world, "user-service")
        recovery_credit = 280 if not bad_deploy_live else 0
        user_error = clamp(
            0.02 + rejection_rate * 0.48 + user_disruption * 0.06 - (0.08 if not bad_deploy_live else 0.0),
            0.0,
            0.65,
        )
        user_latency = max(320, 280 + rejection_rate * 3200 + user_disruption * 260 - recovery_credit - (40 if not bad_deploy_live else 0))
        user_status = "healthy"
        if user_latency >= 1200 or user_error >= 0.05:
            user_status = "degraded"
        if user_error >= 0.45:
            user_status = "down"
        self.set_service(
            world,
            "user-service",
            status=user_status,
            latency_p95_ms=120 + rejection_rate * 950 + user_disruption * 120,
            latency_p99_ms=user_latency,
            error_rate=user_error,
            saturation=clamp(0.44 + rejection_rate * 0.25, 0.0, 1.0),
        )

        self.set_service(
            world,
            "profile-db",
            status="healthy",
            latency_p95_ms=28,
            latency_p99_ms=48,
            error_rate=0.0,
            saturation=0.33,
        )
        self.set_service(
            world,
            "session-cache",
            status="healthy",
            latency_p95_ms=8,
            latency_p99_ms=18,
            error_rate=0.0,
            saturation=0.25,
        )

        world.scenario_state["token_rejection_rate"] = rejection_rate
        world.tick = next_tick
        if user_status != "healthy":
            self.append_log(
                world,
                "user-service",
                "ERROR",
                "401 Unauthorized from auth-service while fetching user profile",
            )
        if auth_status != "healthy":
            self.append_log(
                world,
                "auth-service",
                "WARN",
                "token validation failures spiked after deployment to v1.4.0",
            )
        self.decay_transients(world)

    def slos_restored(self, world: WorldState) -> bool:
        user = self.get_service(world, "user-service")
        auth = self.get_service(world, "auth-service")
        return (
            user.status == "healthy"
            and user.error_rate <= 0.03
            and user.latency_p99_ms <= 600
            and auth.status == "healthy"
            and auth.error_rate <= 0.03
        )
