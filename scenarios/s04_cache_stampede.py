from __future__ import annotations

from models import DeployEvent, ServiceState, WorldState
from scenarios.base import Scenario, clamp, get_service


class CacheStampedeScenario(Scenario):
    scenario_id = "s04_cache_stampede"
    name = "Cache Stampede From Key Regression"
    summary = (
        "catalog-api looks like it needs more cache capacity, but a bad deploy changed cache-key "
        "generation and created a read storm against redis-catalog."
    )

    def build_world(self, episode_id: str) -> WorldState:
        world = WorldState(
            episode_id=episode_id,
            tick=0,
            services=[
                ServiceState(
                    name="catalog-api",
                    status="degraded",
                    version="2026.04.5",
                    latency_p95_ms=980,
                    latency_p99_ms=2800,
                    error_rate=0.11,
                    saturation=0.72,
                    replicas=6,
                    rate_limit_rps=1800,
                    dependencies=["redis-catalog", "products-postgres"],
                    root_cause=True,
                ),
                ServiceState(
                    name="redis-catalog",
                    status="degraded",
                    version="7.2.4",
                    latency_p95_ms=26,
                    latency_p99_ms=92,
                    error_rate=0.01,
                    saturation=0.94,
                    replicas=2,
                    rate_limit_rps=4000,
                    dependencies=[],
                ),
                ServiceState(
                    name="products-postgres",
                    status="healthy",
                    version="postgres-15.6",
                    latency_p95_ms=70,
                    latency_p99_ms=140,
                    error_rate=0.01,
                    saturation=0.48,
                    replicas=1,
                    rate_limit_rps=2200,
                    dependencies=[],
                ),
                ServiceState(
                    name="search-api",
                    status="healthy",
                    version="2026.04.0",
                    latency_p95_ms=85,
                    latency_p99_ms=160,
                    error_rate=0.01,
                    saturation=0.42,
                    replicas=4,
                    rate_limit_rps=1500,
                    dependencies=["catalog-api"],
                ),
            ],
            active_alerts=[],
            deploy_history=[
                DeployEvent(
                    service="catalog-api",
                    version_from="2026.03.9",
                    version_to="2026.04.5",
                    tick=-2,
                    triggered_by="argocd/prod-us-central-1",
                )
            ],
            root_cause_service="catalog-api",
            root_cause_type="cache_key_regression",
            declared_root_cause=None,
            incident_resolved=False,
            budget_remaining=15.0,
            scenario_id=self.scenario_id,
            scenario_name=self.name,
            max_budget=self.max_budget,
            scenario_state={"cache_miss_ratio": 0.83, "replica_mask_ticks": 0},
        )

        self.append_log(
            world,
            "catalog-api",
            "ERROR",
            "cache lookup miss for key=product:v2:region:us:sku_1842 after deploy 2026.04.5; fetching from postgres",
        )
        self.append_log(
            world,
            "redis-catalog",
            "WARN",
            "keyspace_misses spiking with new prefix product:v2:* from catalog-api",
        )
        self.append_log(world, "products-postgres", "INFO", "read throughput elevated but within capacity")
        self.append_log(world, "search-api", "INFO", "dependency latency rising on catalog-api")
        return self.bootstrap_world(world)

    def extra_metrics(self, world: WorldState, service_name: str) -> dict[str, float]:
        if service_name == "catalog-api":
            return {"cache_miss_ratio": round(world.scenario_state["cache_miss_ratio"], 4)}
        if service_name == "redis-catalog":
            return {"keyspace_miss_ratio": round(world.scenario_state["cache_miss_ratio"], 4)}
        return {}

    def apply_tick(self, world: WorldState) -> None:
        catalog = get_service(world, "catalog-api")
        redis = get_service(world, "redis-catalog")
        postgres = get_service(world, "products-postgres")
        search = get_service(world, "search-api")
        mask_ticks = world.scenario_state.get("replica_mask_ticks", 0)

        if catalog.version == "2026.04.5":
            world.scenario_state["cache_miss_ratio"] = clamp(
                world.scenario_state["cache_miss_ratio"] + 0.03,
                0.0,
                0.98,
            )
            if mask_ticks > 0:
                catalog.latency_p95_ms = 520
                catalog.latency_p99_ms = 1350
                catalog.error_rate = 0.06
                catalog.saturation = 0.49
                world.scenario_state["replica_mask_ticks"] = mask_ticks - 1
            else:
                catalog.latency_p95_ms = clamp(catalog.latency_p95_ms + 110, 300, 2100)
                catalog.latency_p99_ms = clamp(catalog.latency_p99_ms + 280, 900, 4200)
                catalog.error_rate = clamp(catalog.error_rate + 0.025, 0.0, 1.0)
                catalog.saturation = clamp(catalog.saturation + 0.03, 0.0, 1.0)

            redis.saturation = clamp(redis.saturation + 0.05, 0.0, 1.0)
            redis.latency_p95_ms = clamp(redis.latency_p95_ms + 10, 15, 180)
            redis.latency_p99_ms = clamp(redis.latency_p99_ms + 28, 40, 420)
            redis.error_rate = clamp(redis.error_rate + 0.008, 0.0, 1.0)

            postgres.saturation = clamp(postgres.saturation + 0.04, 0.0, 1.0)
            postgres.latency_p99_ms = clamp(postgres.latency_p99_ms + 25, 120, 420)

            search.latency_p99_ms = clamp(search.latency_p99_ms + 90, 140, 1200)
            search.error_rate = clamp(search.error_rate + 0.015, 0.0, 1.0)

            self.append_log(
                world,
                "catalog-api",
                "ERROR",
                "cache lookup miss for key=product:v2:* caused fallback read to postgres",
            )
            self.append_log(
                world,
                "redis-catalog",
                "WARN",
                "cpu high and misses rising; top key prefix product:v2:* from catalog-api",
            )
        else:
            world.scenario_state["cache_miss_ratio"] = clamp(
                world.scenario_state["cache_miss_ratio"] - 0.22,
                0.02,
                0.98,
            )
            catalog.latency_p95_ms = clamp(catalog.latency_p95_ms - 450, 85, 700)
            catalog.latency_p99_ms = clamp(catalog.latency_p99_ms - 1500, 180, 900)
            catalog.error_rate = clamp(catalog.error_rate - 0.09, 0.0, 1.0)
            catalog.saturation = clamp(catalog.saturation - 0.18, 0.0, 0.72)

            redis.saturation = clamp(redis.saturation - 0.22, 0.0, 0.7)
            redis.latency_p95_ms = clamp(redis.latency_p95_ms - 15, 10, 80)
            redis.latency_p99_ms = clamp(redis.latency_p99_ms - 45, 22, 180)
            redis.error_rate = clamp(redis.error_rate - 0.02, 0.0, 1.0)

            postgres.saturation = clamp(postgres.saturation - 0.08, 0.0, 0.6)
            postgres.latency_p99_ms = clamp(postgres.latency_p99_ms - 35, 90, 240)
            search.latency_p99_ms = clamp(search.latency_p99_ms - 110, 120, 300)
            search.error_rate = clamp(search.error_rate - 0.02, 0.0, 1.0)

            self.append_log(world, "catalog-api", "INFO", "cache key prefix reverted to product:v1:* after rollback")
            self.append_log(world, "redis-catalog", "INFO", "miss ratio returning to baseline")

        self.finalize_tick(world)

    def on_remediation(self, world: WorldState, action, notes: list[str]) -> None:
        if action.action_type == "scale_service" and action.service == "catalog-api":
            world.scenario_state["replica_mask_ticks"] = 2
            notes.append("extra catalog-api replicas briefly hide the cache-key bug while cache misses continue")
        if action.action_type == "rollback_service" and action.service == "catalog-api":
            notes.append("catalog-api rollback restores the old cache key prefix")

    def remediates_root_cause(self, world: WorldState, action) -> bool:
        return (
            action.action_type == "rollback_service"
            and action.service == "catalog-api"
            and action.target_version == "2026.03.9"
        )
