from __future__ import annotations

from models import DeployEvent, ServiceState, WorldState
from scenarios.base import Scenario, clamp, get_service


class CorruptScaleUpScenario(Scenario):
    scenario_id = "s02_corrupt_scaleup"
    name = "Scale-Up Hides Corruption"
    summary = (
        "checkout-api latency looks like a capacity problem, but a feature-flagged "
        "pricing path is corrupting order totals and retries poison downstream writes."
    )

    def build_world(self, episode_id: str) -> WorldState:
        world = WorldState(
            episode_id=episode_id,
            tick=0,
            services=[
                ServiceState(
                    name="checkout-api",
                    status="degraded",
                    version="2026.04.2",
                    latency_p95_ms=1300,
                    latency_p99_ms=3200,
                    error_rate=0.12,
                    saturation=0.74,
                    replicas=5,
                    rate_limit_rps=650,
                    dependencies=["orders-postgres", "pricing-engine", "payments-gateway"],
                    root_cause=True,
                ),
                ServiceState(
                    name="orders-postgres",
                    status="healthy",
                    version="postgres-15.6",
                    latency_p95_ms=75,
                    latency_p99_ms=140,
                    error_rate=0.01,
                    saturation=0.54,
                    replicas=1,
                    rate_limit_rps=1600,
                    dependencies=[],
                ),
                ServiceState(
                    name="pricing-engine",
                    status="healthy",
                    version="2026.03.9",
                    latency_p95_ms=45,
                    latency_p99_ms=85,
                    error_rate=0.01,
                    saturation=0.32,
                    replicas=2,
                    rate_limit_rps=1800,
                    dependencies=[],
                ),
                ServiceState(
                    name="payments-gateway",
                    status="healthy",
                    version="2026.04.0",
                    latency_p95_ms=62,
                    latency_p99_ms=130,
                    error_rate=0.01,
                    saturation=0.37,
                    replicas=2,
                    rate_limit_rps=1200,
                    dependencies=[],
                ),
            ],
            active_alerts=[],
            deploy_history=[
                DeployEvent(
                    service="checkout-api",
                    version_from="2026.04.2",
                    version_to="2026.04.2+ff-dynamic-pricing",
                    tick=-4,
                    triggered_by="launchdarkly/prod-web",
                )
            ],
            root_cause_service="checkout-api",
            root_cause_type="feature_flag_corruption",
            declared_root_cause=None,
            incident_resolved=False,
            budget_remaining=15.0,
            scenario_id=self.scenario_id,
            scenario_name=self.name,
            max_budget=self.max_budget,
            scenario_state={
                "flag_enabled": True,
                "corruption_counter": 4,
                "scale_mask_ticks": 0,
            },
        )

        self.append_log(
            world,
            "checkout-api",
            "ERROR",
            "OrderTotal mismatch: quoted_total=84.50 computed_total=79.50 request_id=chk_1842 retrying",
        )
        self.append_log(
            world,
            "checkout-api",
            "WARN",
            "feature_flag ff_dynamic_pricing enabled for 100% of prod-web traffic",
        )
        self.append_log(world, "orders-postgres", "INFO", "write latency stable but retry volume trending up")
        self.append_log(world, "payments-gateway", "INFO", "authorization latency steady at p95=62ms")
        return self.bootstrap_world(world)

    def extra_metrics(self, world: WorldState, service_name: str) -> dict[str, float | bool]:
        if service_name == "checkout-api":
            return {
                "retry_pressure": world.scenario_state["corruption_counter"],
                "ff_dynamic_pricing_enabled": world.scenario_state["flag_enabled"],
            }
        return {}

    def apply_tick(self, world: WorldState) -> None:
        checkout = get_service(world, "checkout-api")
        orders_db = get_service(world, "orders-postgres")
        flag_enabled = world.scenario_state["flag_enabled"]
        scale_mask_ticks = world.scenario_state.get("scale_mask_ticks", 0)

        if flag_enabled and checkout.rate_limit_rps > 0:
            world.scenario_state["corruption_counter"] += 1

            if scale_mask_ticks > 0:
                checkout.latency_p95_ms = 650
                checkout.latency_p99_ms = 1400
                checkout.error_rate = 0.07
                checkout.saturation = 0.52
                world.scenario_state["scale_mask_ticks"] = scale_mask_ticks - 1
            else:
                checkout.latency_p95_ms = clamp(checkout.latency_p95_ms + 140, 400, 2200)
                checkout.latency_p99_ms = clamp(checkout.latency_p99_ms + 260, 900, 4200)
                checkout.error_rate = clamp(checkout.error_rate + 0.02, 0.0, 1.0)
                checkout.saturation = clamp(checkout.saturation + 0.03, 0.0, 1.0)

            if world.scenario_state["corruption_counter"] >= 5:
                orders_db.latency_p95_ms = clamp(orders_db.latency_p95_ms + 20, 60, 260)
                orders_db.latency_p99_ms = clamp(orders_db.latency_p99_ms + 55, 100, 780)
                orders_db.error_rate = clamp(orders_db.error_rate + 0.03, 0.0, 1.0)
                orders_db.saturation = clamp(orders_db.saturation + 0.08, 0.0, 1.0)

            self.append_log(
                world,
                "checkout-api",
                "ERROR",
                "OrderTotal mismatch: pricing quote diverged from persisted total, request requeued for retry",
            )
            self.append_log(
                world,
                "orders-postgres",
                "WARN",
                "idempotency conflicts rising due to repeated checkout-api write attempts",
            )
        else:
            checkout.latency_p95_ms = 70
            checkout.latency_p99_ms = 150
            checkout.error_rate = 0.0
            checkout.saturation = 0.08

            orders_db.latency_p95_ms = clamp(orders_db.latency_p95_ms - 30, 55, 180)
            orders_db.latency_p99_ms = clamp(orders_db.latency_p99_ms - 65, 90, 300)
            orders_db.error_rate = clamp(orders_db.error_rate - 0.04, 0.0, 1.0)
            orders_db.saturation = clamp(orders_db.saturation - 0.1, 0.0, 0.75)
            self.append_log(world, "checkout-api", "INFO", "new checkout traffic paused by temporary rate limit")
            self.append_log(world, "orders-postgres", "INFO", "retry pressure clearing after traffic pause")

        self.finalize_tick(world)

    def on_remediation(self, world: WorldState, action, notes: list[str]) -> None:
        if action.action_type == "scale_service" and action.service == "checkout-api":
            world.scenario_state["scale_mask_ticks"] = 2
            notes.append("extra checkout-api replicas hide latency briefly while corrupted order writes continue")
        if action.action_type == "set_rate_limit" and action.service == "checkout-api" and action.rps == 0:
            world.scenario_state["flag_enabled"] = False
            notes.append("rate limit to zero contains the bad ff_dynamic_pricing rollout")

    def remediates_root_cause(self, world: WorldState, action) -> bool:
        return (
            action.action_type == "set_rate_limit"
            and action.service == "checkout-api"
            and action.rps == 0
        )
