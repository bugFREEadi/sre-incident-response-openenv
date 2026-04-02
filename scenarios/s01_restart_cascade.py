from __future__ import annotations

from models import DeployEvent, ServiceState, WorldState
from scenarios.base import Scenario, clamp, get_service


class RestartCascadeScenario(Scenario):
    scenario_id = "s01_restart_cascade"
    name = "Restart Makes It Worse"
    summary = (
        "payments-api looks sick, but an invoice-consumer connection leak is exhausting "
        "orders-postgres and naive API restarts worsen recovery."
    )

    def build_world(self, episode_id: str) -> WorldState:
        world = WorldState(
            episode_id=episode_id,
            tick=0,
            services=[
                ServiceState(
                    name="payments-api",
                    status="degraded",
                    version="2026.04.1",
                    latency_p95_ms=1600,
                    latency_p99_ms=4200,
                    error_rate=0.18,
                    saturation=0.78,
                    replicas=6,
                    rate_limit_rps=1400,
                    dependencies=["invoice-consumer", "orders-postgres"],
                ),
                ServiceState(
                    name="invoice-consumer",
                    status="healthy",
                    version="2026.04.1",
                    latency_p95_ms=120,
                    latency_p99_ms=320,
                    error_rate=0.03,
                    saturation=0.56,
                    replicas=5,
                    rate_limit_rps=2000,
                    dependencies=["orders-postgres"],
                    root_cause=True,
                ),
                ServiceState(
                    name="orders-postgres",
                    status="degraded",
                    version="postgres-15.6",
                    latency_p95_ms=90,
                    latency_p99_ms=180,
                    error_rate=0.02,
                    saturation=0.97,
                    replicas=1,
                    rate_limit_rps=2500,
                    dependencies=[],
                ),
                ServiceState(
                    name="redis-session",
                    status="healthy",
                    version="7.2.4",
                    latency_p95_ms=11,
                    latency_p99_ms=34,
                    error_rate=0.0,
                    saturation=0.28,
                    replicas=2,
                    rate_limit_rps=3000,
                    dependencies=[],
                ),
            ],
            active_alerts=[],
            deploy_history=[
                DeployEvent(
                    service="invoice-consumer",
                    version_from="2026.03.7",
                    version_to="2026.04.1",
                    tick=-3,
                    triggered_by="argocd/prod-us-east-1",
                )
            ],
            root_cause_service="invoice-consumer",
            root_cause_type="connection_leak",
            declared_root_cause=None,
            incident_resolved=False,
            budget_remaining=15.0,
            scenario_id=self.scenario_id,
            scenario_name=self.name,
            max_budget=self.max_budget,
            scenario_state={
                "connection_count": 470,
                "api_restart_relief_ticks": 0,
                "payments_api_restart_count": 0,
            },
        )

        self.append_log(
            world,
            "payments-api",
            "ERROR",
            'POST /v1/payments/authorize upstream timeout after 4.0s while waiting on ledger write path',
        )
        self.append_log(
            world,
            "invoice-consumer",
            "INFO",
            "rollout completed for deployment/invoice-consumer image=registry.prod/invoice-consumer:2026.04.1",
        )
        self.append_log(
            world,
            "orders-postgres",
            "ERROR",
            "FATAL: remaining connection slots are reserved for non-replication superuser connections (application_name=invoice-consumer pod=invoice-consumer-7f8c6d49df-h2k9q)",
        )
        self.append_log(world, "redis-session", "INFO", "cache hit ratio steady at 97.4% in prod-us-east-1")
        return self.bootstrap_world(world)

    def extra_metrics(self, world: WorldState, service_name: str) -> dict[str, float]:
        if service_name == "invoice-consumer":
            return {"db_connection_count": world.scenario_state["connection_count"]}
        if service_name == "orders-postgres":
            return {"active_connections": world.scenario_state["connection_count"]}
        return {}

    def apply_tick(self, world: WorldState) -> None:
        api = get_service(world, "payments-api")
        worker = get_service(world, "invoice-consumer")
        postgres = get_service(world, "orders-postgres")

        root_cause_active = worker.version == "2026.04.1"
        relief_ticks = world.scenario_state.get("api_restart_relief_ticks", 0)

        if root_cause_active:
            world.scenario_state["connection_count"] += 28
            postgres.saturation = clamp(postgres.saturation + 0.035, 0.0, 1.0)
            postgres.error_rate = clamp(postgres.error_rate + 0.008, 0.0, 1.0)
            postgres.latency_p95_ms = clamp(postgres.latency_p95_ms + 12, 40, 400)
            postgres.latency_p99_ms = clamp(postgres.latency_p99_ms + 25, 80, 700)

            worker.error_rate = 0.03
            worker.latency_p95_ms = 125
            worker.latency_p99_ms = 330
            worker.saturation = clamp(worker.saturation + 0.01, 0.0, 0.78)

            if relief_ticks > 0:
                api.latency_p95_ms = 480
                api.latency_p99_ms = 950
                api.error_rate = 0.045
                api.saturation = 0.58
                world.scenario_state["api_restart_relief_ticks"] = relief_ticks - 1
            else:
                api.latency_p95_ms = clamp(api.latency_p95_ms + 180, 250, 2500)
                api.latency_p99_ms = clamp(api.latency_p99_ms + 300, 900, 5000)
                api.error_rate = clamp(api.error_rate + 0.025, 0.0, 1.0)
                api.saturation = clamp(api.saturation + 0.035, 0.0, 1.0)

            self.append_log(
                world,
                "orders-postgres",
                "ERROR",
                "FATAL: remaining connection slots are reserved for non-replication superuser connections (application_name=invoice-consumer pod=invoice-consumer-7f8c6d49df-h2k9q)",
            )
            self.append_log(
                world,
                "invoice-consumer",
                "WARN",
                "db checkout latency rising to 1.8s while consumer ack rate still looks nominal",
            )
            self.append_log(
                world,
                "payments-api",
                "ERROR",
                "dependency timeout on orders-postgres write path from authorize_payment",
            )
        else:
            world.scenario_state["connection_count"] = max(110, world.scenario_state["connection_count"] - 140)
            postgres.saturation = clamp(postgres.saturation - 0.22, 0.0, 1.0)
            postgres.error_rate = clamp(postgres.error_rate - 0.035, 0.0, 1.0)
            postgres.latency_p95_ms = clamp(postgres.latency_p95_ms - 32, 35, 200)
            postgres.latency_p99_ms = clamp(postgres.latency_p99_ms - 70, 70, 250)

            worker.error_rate = clamp(worker.error_rate - 0.01, 0.0, 0.03)
            worker.latency_p95_ms = 95
            worker.latency_p99_ms = 220
            worker.saturation = clamp(worker.saturation - 0.05, 0.0, 0.6)

            api.latency_p95_ms = clamp(api.latency_p95_ms - 950, 120, 900)
            api.latency_p99_ms = clamp(api.latency_p99_ms - 3600, 250, 950)
            api.error_rate = clamp(api.error_rate - 0.24, 0.0, 1.0)
            api.saturation = clamp(api.saturation - 0.22, 0.0, 0.7)

            self.append_log(world, "invoice-consumer", "INFO", "active DB sessions falling after rollout rollback")
            self.append_log(world, "orders-postgres", "INFO", "pool pressure easing and client wait events clearing")
            self.append_log(world, "payments-api", "INFO", "authorize_payment latency normalizing after dependency recovery")

        self.finalize_tick(world)

    def on_remediation(self, world: WorldState, action, notes: list[str]) -> None:
        if action.action_type == "restart_service" and action.service == "payments-api":
            world.scenario_state["payments_api_restart_count"] += 1
            world.scenario_state["connection_count"] += 22
            notes.append("payments-api restart clears the ingress backlog briefly but reconnect surge worsens DB recovery")
        if action.action_type == "rollback_service" and action.service == "invoice-consumer":
            notes.append("invoice-consumer rollback removes the leaking DB session behavior")

    def remediates_root_cause(self, world: WorldState, action) -> bool:
        return (
            action.action_type == "rollback_service"
            and action.service == "invoice-consumer"
            and action.target_version == "2026.03.7"
        )
