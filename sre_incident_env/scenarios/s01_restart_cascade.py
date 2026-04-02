from __future__ import annotations

from ..models import DeployEvent, ServiceState, WorldState
from .base import Scenario, clamp


class RestartCascadeScenario(Scenario):
    scenario_id = "s01_restart_cascade"
    title = "Restart Makes It Worse"
    description = (
        "api-gateway looks sick, but worker-pool v2.3.1 is leaking postgres connections."
    )

    def build_world(self, episode_id: str, budget: float | None = None) -> WorldState:
        services = [
            ServiceState(
                name="api-gateway",
                status="degraded",
                version="v4.8.2",
                latency_p95_ms=1400,
                latency_p99_ms=4200,
                error_rate=0.18,
                saturation=0.72,
                replicas=3,
                rate_limit_rps=900,
                dependencies=["worker-pool", "postgres-primary"],
            ),
            ServiceState(
                name="worker-pool",
                status="healthy",
                version="v2.3.1",
                latency_p95_ms=180,
                latency_p99_ms=320,
                error_rate=0.03,
                saturation=0.61,
                replicas=6,
                rate_limit_rps=1200,
                dependencies=["postgres-primary"],
                root_cause=True,
            ),
            ServiceState(
                name="postgres-primary",
                status="degraded",
                version="v13.11",
                latency_p95_ms=45,
                latency_p99_ms=90,
                error_rate=0.02,
                saturation=0.97,
                replicas=1,
                rate_limit_rps=5000,
                dependencies=[],
            ),
            ServiceState(
                name="redis-cache",
                status="healthy",
                version="v7.2.0",
                latency_p95_ms=12,
                latency_p99_ms=20,
                error_rate=0.0,
                saturation=0.30,
                replicas=1,
                rate_limit_rps=4000,
                dependencies=[],
            ),
        ]
        deploy_history = [
            DeployEvent(
                service="worker-pool",
                version_from="v2.3.0",
                version_to="v2.3.1",
                tick=-3,
                triggered_by="ci-deploy",
            )
        ]
        world = self.initial_world(
            episode_id=episode_id,
            services=services,
            deploy_history=deploy_history,
            root_cause_service="worker-pool",
            root_cause_type="connection_leak",
            budget=budget,
        )
        world.scenario_state.update(
            {
                "pool_pressure": 0.97,
                "gateway_backlog": 0.86,
            }
        )
        self.append_log(
            world,
            "postgres-primary",
            "ERROR",
            "FATAL: remaining connection slots reserved for non-replication superuser connections (client=worker-pool/10.0.4.12)",
        )
        self.append_log(
            world,
            "api-gateway",
            "WARN",
            "upstream timeout waiting on postgres-primary connection checkout",
        )
        self.append_log(
            world,
            "worker-pool",
            "INFO",
            "deploy completed for worker-pool v2.3.1",
        )
        self.record_metrics(world)
        self.refresh_alerts(world)
        return world

    def extra_metrics(self, world: WorldState, service_name: str) -> dict[str, float]:
        if service_name != "worker-pool":
            return {}
        pressure = float(world.scenario_state.get("pool_pressure", 0.0))
        return {"db_connections_estimate": 65 + pressure * 80}

    def advance(self, world: WorldState, next_tick: int) -> None:
        self.apply_pending_effects(world, next_tick)
        leak_active = self.get_service(world, "worker-pool").version == "v2.3.1"
        pool_pressure = float(world.scenario_state.get("pool_pressure", 0.95))
        gateway_backlog = float(world.scenario_state.get("gateway_backlog", 0.8))

        if leak_active:
            pool_pressure = clamp(pool_pressure + 0.07, 0.35, 1.25)
        else:
            pool_pressure = clamp(pool_pressure - 0.58, 0.18, 1.25)

        postgres = self.get_service(world, "postgres-primary")
        postgres_saturation = clamp(0.28 + pool_pressure * 0.72, 0.0, 1.0)
        postgres_error = clamp(max(0.0, pool_pressure - 0.86) * 0.22, 0.0, 0.35)
        postgres_status = "healthy"
        if postgres_saturation >= 0.82 or postgres_error >= 0.03:
            postgres_status = "degraded"
        if postgres_saturation >= 0.99:
            postgres_status = "down"
        self.set_service(
            world,
            "postgres-primary",
            status=postgres_status,
            latency_p95_ms=55 + postgres_saturation * 45,
            latency_p99_ms=110 + postgres_saturation * 80,
            error_rate=postgres_error,
            saturation=postgres_saturation,
        )

        worker = self.get_service(world, "worker-pool")
        worker_disruption = self.disruption_level(world, "worker-pool")
        worker_error = 0.03 if leak_active else 0.01
        worker_error = clamp(worker_error + worker_disruption * 0.05, 0.0, 0.25)
        worker_status = "healthy" if worker_error < 0.08 else "degraded"
        self.set_service(
            world,
            "worker-pool",
            status=worker_status,
            latency_p95_ms=170 + pool_pressure * 25,
            latency_p99_ms=300 + pool_pressure * 45,
            error_rate=worker_error,
            saturation=clamp(0.58 + pool_pressure * 0.12, 0.0, 1.0),
        )

        if self.restarting(world, "api-gateway", next_tick):
            self.set_service(
                world,
                "api-gateway",
                status="down",
                latency_p95_ms=2500,
                latency_p99_ms=5200,
                error_rate=1.0,
                saturation=0.15,
            )
        else:
            if not leak_active:
                gateway_backlog = clamp(gateway_backlog - 0.95, 0.0, 1.2)
            elif postgres_saturation >= 0.88:
                gateway_backlog = clamp(gateway_backlog + 0.16, 0.05, 1.2)
            else:
                gateway_backlog = clamp(gateway_backlog - 0.28, 0.0, 1.2)
            if world.scenario_state["restart_relief"].get("api-gateway"):
                gateway_backlog = clamp(gateway_backlog - 0.28, 0.0, 1.2)

            gateway_disruption = self.disruption_level(world, "api-gateway")
            recovery_credit = 700 if not leak_active else 0
            latency_p99 = max(420, 620 + gateway_backlog * 3400 + gateway_disruption * 450 - recovery_credit)
            latency_p95 = max(160, 240 + gateway_backlog * 1100 + gateway_disruption * 180 - (180 if not leak_active else 0))
            error_rate = clamp(
                0.015 + max(0.0, gateway_backlog - 0.22) * 0.26 - (0.06 if not leak_active else 0.0),
                0.0,
                0.35,
            )
            status = "healthy"
            if latency_p99 >= 1400 or error_rate >= 0.05:
                status = "degraded"
            if error_rate >= 0.28:
                status = "down"
            self.set_service(
                world,
                "api-gateway",
                status=status,
                latency_p95_ms=latency_p95,
                latency_p99_ms=latency_p99,
                error_rate=error_rate,
                saturation=clamp(0.48 + gateway_backlog * 0.28, 0.0, 1.0),
            )

        self.set_service(
            world,
            "redis-cache",
            status="healthy",
            latency_p95_ms=12,
            latency_p99_ms=20,
            error_rate=0.0,
            saturation=0.30,
        )

        world.scenario_state["pool_pressure"] = pool_pressure
        world.scenario_state["gateway_backlog"] = gateway_backlog
        world.tick = next_tick

        if leak_active:
            self.append_log(
                world,
                "postgres-primary",
                "ERROR",
                "FATAL: remaining connection slots reserved for non-replication superuser connections (client=worker-pool/10.0.4.12)",
            )
        if self.get_service(world, "api-gateway").status != "healthy":
            self.append_log(
                world,
                "api-gateway",
                "WARN",
                "request queue backpressure from postgres-primary connection starvation",
            )
        if not leak_active:
            self.append_log(
                world,
                "worker-pool",
                "INFO",
                "worker-pool rollback complete; postgres checkout latency returning to baseline",
            )
        self.decay_transients(world)

    def slos_restored(self, world: WorldState) -> bool:
        api = self.get_service(world, "api-gateway")
        postgres = self.get_service(world, "postgres-primary")
        return (
            api.status == "healthy"
            and api.error_rate <= 0.03
            and api.latency_p99_ms <= 1200
            and postgres.status == "healthy"
            and postgres.saturation <= 0.78
        )
