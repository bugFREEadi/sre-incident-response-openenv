from __future__ import annotations

from ..models import DeployEvent, ServiceState, WorldState
from .base import Scenario, clamp


class CorruptScaleUpScenario(Scenario):
    scenario_id = "s02_corrupt_scaleup"
    title = "Scale-Up Hides Corruption"
    description = (
        "checkout-service latency comes from retry storms after a broken pricing flag."
    )

    def build_world(self, episode_id: str, budget: float | None = None) -> WorldState:
        services = [
            ServiceState(
                name="checkout-service",
                status="degraded",
                version="v5.7.9",
                latency_p95_ms=1100,
                latency_p99_ms=3100,
                error_rate=0.09,
                saturation=0.76,
                replicas=2,
                rate_limit_rps=700,
                dependencies=["orders-db", "pricing-engine"],
                root_cause=True,
            ),
            ServiceState(
                name="orders-db",
                status="healthy",
                version="v14.4",
                latency_p95_ms=35,
                latency_p99_ms=70,
                error_rate=0.01,
                saturation=0.58,
                replicas=1,
                rate_limit_rps=3000,
                dependencies=[],
            ),
            ServiceState(
                name="pricing-engine",
                status="healthy",
                version="v3.2.0",
                latency_p95_ms=80,
                latency_p99_ms=150,
                error_rate=0.01,
                saturation=0.41,
                replicas=2,
                rate_limit_rps=1800,
                dependencies=[],
            ),
            ServiceState(
                name="cart-api",
                status="healthy",
                version="v2.9.4",
                latency_p95_ms=95,
                latency_p99_ms=180,
                error_rate=0.01,
                saturation=0.46,
                replicas=3,
                rate_limit_rps=1400,
                dependencies=["checkout-service"],
            ),
        ]
        deploy_history = [
            DeployEvent(
                service="checkout-service",
                version_from="v5.7.9",
                version_to="v5.7.9",
                tick=-4,
                triggered_by="feature-flag: ff-new-pricing enabled",
            )
        ]
        world = self.initial_world(
            episode_id=episode_id,
            services=services,
            deploy_history=deploy_history,
            root_cause_service="checkout-service",
            root_cause_type="feature_flag_corruption",
            budget=budget,
        )
        world.scenario_state.update(
            {
                "flag_enabled": True,
                "corruption_pressure": 0.58,
                "orders_write_pressure": 0.22,
            }
        )
        self.append_log(
            world,
            "checkout-service",
            "ERROR",
            "CartTotal mismatch: expected 1099 got 899, retrying",
        )
        self.record_metrics(world)
        self.refresh_alerts(world)
        return world

    def extra_metrics(self, world: WorldState, service_name: str) -> dict[str, float]:
        if service_name != "checkout-service":
            return {}
        pressure = float(world.scenario_state.get("corruption_pressure", 0.0))
        return {"retry_queue_depth": 8 + pressure * 35}

    def advance(self, world: WorldState, next_tick: int) -> None:
        self.apply_pending_effects(world, next_tick)
        checkout = self.get_service(world, "checkout-service")
        replicas = max(1, checkout.replicas)
        traffic_live = checkout.rate_limit_rps > 0
        flag_enabled = bool(world.scenario_state.get("flag_enabled", True))
        corruption_pressure = float(world.scenario_state.get("corruption_pressure", 0.58))
        orders_write_pressure = float(world.scenario_state.get("orders_write_pressure", 0.22))

        if flag_enabled and traffic_live:
            corruption_pressure = clamp(corruption_pressure + 0.10, 0.0, 1.25)
            orders_write_pressure = clamp(orders_write_pressure + 0.08, 0.0, 1.0)
        else:
            corruption_pressure = clamp(corruption_pressure - 0.22, 0.0, 1.25)
            orders_write_pressure = clamp(orders_write_pressure - 0.18, 0.0, 1.0)

        disruption = self.disruption_level(world, "checkout-service")
        latency_p99 = 620 + (corruption_pressure * 3100) / (replicas ** 0.5) + disruption * 240
        latency_p95 = 260 + (corruption_pressure * 1100) / (replicas ** 0.5) + disruption * 110
        error_rate = clamp(0.02 + corruption_pressure * 0.16, 0.0, 0.45)
        if not traffic_live:
            latency_p99 = 220
            latency_p95 = 95
            error_rate = 0.0
        status = "healthy"
        if latency_p99 >= 1400 or error_rate >= 0.05:
            status = "degraded"
        self.set_service(
            world,
            "checkout-service",
            status=status,
            latency_p95_ms=latency_p95,
            latency_p99_ms=latency_p99,
            error_rate=error_rate,
            saturation=clamp(0.42 + corruption_pressure * 0.28 / replicas, 0.0, 1.0),
        )

        orders_status = "healthy"
        orders_error = clamp(max(0.0, orders_write_pressure - 0.55) * 0.20, 0.0, 0.35)
        if orders_write_pressure >= 0.65 or orders_error >= 0.04:
            orders_status = "degraded"
        self.set_service(
            world,
            "orders-db",
            status=orders_status,
            latency_p95_ms=30 + orders_write_pressure * 25,
            latency_p99_ms=60 + orders_write_pressure * 40,
            error_rate=orders_error,
            saturation=clamp(0.36 + orders_write_pressure * 0.52, 0.0, 1.0),
        )

        self.set_service(
            world,
            "pricing-engine",
            status="healthy",
            latency_p95_ms=80,
            latency_p99_ms=150,
            error_rate=0.01,
            saturation=0.41,
        )
        self.set_service(
            world,
            "cart-api",
            status="healthy" if status == "healthy" else "degraded",
            latency_p95_ms=100 if status == "healthy" else 180,
            latency_p99_ms=180 if status == "healthy" else 420,
            error_rate=0.01 if status == "healthy" else 0.04,
            saturation=0.46,
        )

        world.scenario_state["corruption_pressure"] = corruption_pressure
        world.scenario_state["orders_write_pressure"] = orders_write_pressure
        world.tick = next_tick
        if flag_enabled and traffic_live:
            self.append_log(
                world,
                "checkout-service",
                "ERROR",
                "CartTotal mismatch: expected 1099 got 899, retrying",
            )
        if orders_status != "healthy":
            self.append_log(
                world,
                "orders-db",
                "WARN",
                "write amplification from checkout retries is increasing lock wait time",
            )
        self.decay_transients(world)

    def slos_restored(self, world: WorldState) -> bool:
        checkout = self.get_service(world, "checkout-service")
        orders = self.get_service(world, "orders-db")
        return (
            checkout.latency_p99_ms <= 500
            and checkout.error_rate <= 0.01
            and orders.status == "healthy"
            and orders.error_rate <= 0.02
        )
