from __future__ import annotations

from models import DeployEvent, ServiceState, WorldState
from scenarios.base import Scenario, clamp, get_service


class WebhookRetryStormScenario(Scenario):
    scenario_id = "s05_webhook_retry_storm"
    name = "Webhook Retry Storm"
    summary = (
        "notification-dispatcher looks underprovisioned, but a duplicate-send flag is creating "
        "a retry storm against webhook-relay and backing up order events."
    )

    def build_world(self, episode_id: str) -> WorldState:
        world = WorldState(
            episode_id=episode_id,
            tick=0,
            services=[
                ServiceState(
                    name="notification-dispatcher",
                    status="degraded",
                    version="2026.04.4",
                    latency_p95_ms=840,
                    latency_p99_ms=2400,
                    error_rate=0.13,
                    saturation=0.76,
                    replicas=4,
                    rate_limit_rps=900,
                    dependencies=["webhook-relay", "orders-events-kafka"],
                    root_cause=True,
                ),
                ServiceState(
                    name="webhook-relay",
                    status="degraded",
                    version="2026.03.8",
                    latency_p95_ms=320,
                    latency_p99_ms=980,
                    error_rate=0.06,
                    saturation=0.88,
                    replicas=3,
                    rate_limit_rps=1100,
                    dependencies=[],
                ),
                ServiceState(
                    name="orders-events-kafka",
                    status="healthy",
                    version="3.7.1",
                    latency_p95_ms=20,
                    latency_p99_ms=55,
                    error_rate=0.01,
                    saturation=0.44,
                    replicas=3,
                    rate_limit_rps=5000,
                    dependencies=[],
                ),
                ServiceState(
                    name="orders-api",
                    status="healthy",
                    version="2026.04.1",
                    latency_p95_ms=130,
                    latency_p99_ms=260,
                    error_rate=0.01,
                    saturation=0.47,
                    replicas=5,
                    rate_limit_rps=1400,
                    dependencies=["orders-events-kafka"],
                ),
            ],
            active_alerts=[],
            deploy_history=[
                DeployEvent(
                    service="notification-dispatcher",
                    version_from="2026.04.2",
                    version_to="2026.04.4+ff_duplicate_webhooks",
                    tick=-3,
                    triggered_by="launchdarkly/prod-events",
                )
            ],
            root_cause_service="notification-dispatcher",
            root_cause_type="duplicate_dispatch",
            declared_root_cause=None,
            incident_resolved=False,
            budget_remaining=15.0,
            scenario_id=self.scenario_id,
            scenario_name=self.name,
            max_budget=self.max_budget,
            scenario_state={"duplicate_flag_enabled": True, "backlog_depth": 2200, "scale_mask_ticks": 0},
        )

        self.append_log(
            world,
            "notification-dispatcher",
            "ERROR",
            "partner webhook returned 429; duplicate_send flag active, scheduling retry for event=ord_1842",
        )
        self.append_log(
            world,
            "webhook-relay",
            "WARN",
            "rate limit exceeded for downstream partner endpoint /v1/hooks/orders",
        )
        self.append_log(world, "orders-events-kafka", "INFO", "consumer lag elevated but still recoverable")
        self.append_log(world, "orders-api", "INFO", "order creation healthy; downstream webhook lag isolated")
        return self.bootstrap_world(world)

    def extra_metrics(self, world: WorldState, service_name: str) -> dict[str, float | bool]:
        if service_name == "notification-dispatcher":
            return {
                "duplicate_send_flag_enabled": world.scenario_state["duplicate_flag_enabled"],
                "queue_backlog": world.scenario_state["backlog_depth"],
            }
        if service_name == "webhook-relay":
            return {"queue_backlog": world.scenario_state["backlog_depth"]}
        return {}

    def apply_tick(self, world: WorldState) -> None:
        dispatcher = get_service(world, "notification-dispatcher")
        relay = get_service(world, "webhook-relay")
        kafka = get_service(world, "orders-events-kafka")
        orders_api = get_service(world, "orders-api")
        mask_ticks = world.scenario_state.get("scale_mask_ticks", 0)

        if world.scenario_state["duplicate_flag_enabled"] and dispatcher.rate_limit_rps > 0:
            world.scenario_state["backlog_depth"] += 260

            if mask_ticks > 0:
                dispatcher.latency_p95_ms = 420
                dispatcher.latency_p99_ms = 1180
                dispatcher.error_rate = 0.07
                dispatcher.saturation = 0.54
                world.scenario_state["scale_mask_ticks"] = mask_ticks - 1
            else:
                dispatcher.latency_p95_ms = clamp(dispatcher.latency_p95_ms + 120, 280, 2200)
                dispatcher.latency_p99_ms = clamp(dispatcher.latency_p99_ms + 240, 900, 4200)
                dispatcher.error_rate = clamp(dispatcher.error_rate + 0.025, 0.0, 1.0)
                dispatcher.saturation = clamp(dispatcher.saturation + 0.04, 0.0, 1.0)

            relay.latency_p95_ms = clamp(relay.latency_p95_ms + 55, 200, 900)
            relay.latency_p99_ms = clamp(relay.latency_p99_ms + 120, 420, 2200)
            relay.error_rate = clamp(relay.error_rate + 0.03, 0.0, 1.0)
            relay.saturation = clamp(relay.saturation + 0.05, 0.0, 1.0)

            kafka.saturation = clamp(kafka.saturation + 0.05, 0.0, 1.0)
            kafka.latency_p99_ms = clamp(kafka.latency_p99_ms + 22, 50, 280)
            orders_api.latency_p99_ms = clamp(orders_api.latency_p99_ms + 40, 220, 700)

            self.append_log(
                world,
                "notification-dispatcher",
                "ERROR",
                "partner webhook returned 429; duplicate_send flag active, scheduling retry",
            )
            self.append_log(world, "webhook-relay", "WARN", "429s rising from downstream partner due to replayed events")
        else:
            world.scenario_state["backlog_depth"] = max(60, world.scenario_state["backlog_depth"] - 950)
            dispatcher.latency_p95_ms = clamp(dispatcher.latency_p95_ms - 360, 70, 420)
            dispatcher.latency_p99_ms = clamp(dispatcher.latency_p99_ms - 1500, 140, 820)
            dispatcher.error_rate = clamp(dispatcher.error_rate - 0.11, 0.0, 1.0)
            dispatcher.saturation = clamp(dispatcher.saturation - 0.24, 0.0, 0.66)

            relay.latency_p95_ms = clamp(relay.latency_p95_ms - 150, 55, 260)
            relay.latency_p99_ms = clamp(relay.latency_p99_ms - 520, 95, 420)
            relay.error_rate = clamp(relay.error_rate - 0.07, 0.0, 1.0)
            relay.saturation = clamp(relay.saturation - 0.24, 0.0, 0.62)

            kafka.saturation = clamp(kafka.saturation - 0.1, 0.0, 0.5)
            kafka.latency_p99_ms = clamp(kafka.latency_p99_ms - 34, 35, 100)
            orders_api.latency_p99_ms = clamp(orders_api.latency_p99_ms - 45, 150, 300)

            self.append_log(world, "notification-dispatcher", "INFO", "outgoing webhook traffic paused; retry backlog draining")
            self.append_log(world, "webhook-relay", "INFO", "partner 429 rate returning to baseline")

        self.finalize_tick(world)

    def on_remediation(self, world: WorldState, action, notes: list[str]) -> None:
        if action.action_type == "scale_service" and action.service == "notification-dispatcher":
            world.scenario_state["scale_mask_ticks"] = 2
            notes.append("extra dispatcher replicas hide queue latency briefly while duplicate sends continue")
        if action.action_type == "set_rate_limit" and action.service == "notification-dispatcher" and action.rps == 0:
            world.scenario_state["duplicate_flag_enabled"] = False
            notes.append("pausing dispatcher traffic contains the duplicate-send retry storm")

    def remediates_root_cause(self, world: WorldState, action) -> bool:
        return (
            action.action_type == "set_rate_limit"
            and action.service == "notification-dispatcher"
            and action.rps == 0
        )
