from __future__ import annotations

from models import DeployEvent, ServiceState, WorldState
from scenarios.base import Scenario, clamp, get_service


class WrongRollbackScenario(Scenario):
    scenario_id = "s03_wrong_rollback"
    name = "Wrong-Service Rollback Worsens Auth"
    summary = (
        "accounts-api is failing requests, but the real bug lives in identity-service "
        "and rolling back accounts-api only adds stale-version drift."
    )

    def build_world(self, episode_id: str) -> WorldState:
        world = WorldState(
            episode_id=episode_id,
            tick=0,
            services=[
                ServiceState(
                    name="accounts-api",
                    status="degraded",
                    version="2026.04.3",
                    latency_p95_ms=700,
                    latency_p99_ms=1900,
                    error_rate=0.20,
                    saturation=0.63,
                    replicas=3,
                    rate_limit_rps=750,
                    dependencies=["identity-service", "customer-profile-db"],
                ),
                ServiceState(
                    name="identity-service",
                    status="healthy",
                    version="2026.04.0",
                    latency_p95_ms=160,
                    latency_p99_ms=420,
                    error_rate=0.04,
                    saturation=0.45,
                    replicas=2,
                    rate_limit_rps=900,
                    dependencies=["session-redis"],
                    root_cause=True,
                ),
                ServiceState(
                    name="customer-profile-db",
                    status="healthy",
                    version="postgres-15.5",
                    latency_p95_ms=60,
                    latency_p99_ms=110,
                    error_rate=0.01,
                    saturation=0.34,
                    replicas=1,
                    rate_limit_rps=1500,
                    dependencies=[],
                ),
                ServiceState(
                    name="session-redis",
                    status="healthy",
                    version="7.2.3",
                    latency_p95_ms=8,
                    latency_p99_ms=19,
                    error_rate=0.0,
                    saturation=0.22,
                    replicas=2,
                    rate_limit_rps=2400,
                    dependencies=[],
                ),
            ],
            active_alerts=[],
            deploy_history=[
                DeployEvent(
                    service="identity-service",
                    version_from="2026.03.6",
                    version_to="2026.04.0",
                    tick=-2,
                    triggered_by="argocd/prod-eu-west-1",
                )
            ],
            root_cause_service="identity-service",
            root_cause_type="bad_deploy",
            declared_root_cause=None,
            incident_resolved=False,
            budget_remaining=15.0,
            scenario_id=self.scenario_id,
            scenario_name=self.name,
            max_budget=self.max_budget,
            scenario_state={"stale_user_rollback": False, "rejection_rate": 0.16},
        )

        self.append_log(
            world,
            "accounts-api",
            "ERROR",
            "401 Unauthorized from identity-service while validating bearer token for GET /v1/accounts/me",
        )
        self.append_log(
            world,
            "identity-service",
            "WARN",
            "token expiry validation rejected valid sessions after rollout 2026.04.0",
        )
        self.append_log(world, "customer-profile-db", "INFO", "query latency stable")
        self.append_log(world, "session-redis", "INFO", "cache warm and healthy")
        return self.bootstrap_world(world)

    def extra_metrics(self, world: WorldState, service_name: str) -> dict[str, float]:
        if service_name == "identity-service":
            return {"token_rejection_rate": round(world.scenario_state["rejection_rate"], 4)}
        return {}

    def apply_tick(self, world: WorldState) -> None:
        user = get_service(world, "accounts-api")
        auth = get_service(world, "identity-service")
        stale_rollback = world.scenario_state["stale_user_rollback"]

        if auth.version == "2026.04.0":
            world.scenario_state["rejection_rate"] = clamp(
                world.scenario_state["rejection_rate"] + 0.025,
                0.0,
                1.0,
            )
            auth.error_rate = clamp(auth.error_rate + 0.02, 0.0, 1.0)
            auth.latency_p95_ms = clamp(auth.latency_p95_ms + 30, 80, 450)
            auth.latency_p99_ms = clamp(auth.latency_p99_ms + 70, 150, 1200)
            auth.saturation = clamp(auth.saturation + 0.04, 0.0, 1.0)

            user.error_rate = clamp(user.error_rate + 0.025, 0.0, 1.0)
            user.latency_p95_ms = clamp(user.latency_p95_ms + 80, 150, 1600)
            user.latency_p99_ms = clamp(user.latency_p99_ms + 200, 400, 3200)
            user.saturation = clamp(user.saturation + 0.03, 0.0, 1.0)
            if stale_rollback:
                user.error_rate = clamp(user.error_rate + 0.03, 0.0, 1.0)
                user.latency_p99_ms = clamp(user.latency_p99_ms + 180, 400, 3200)

            self.append_log(
                world,
                "accounts-api",
                "ERROR",
                "401 Unauthorized from identity-service during account profile lookup",
            )
            self.append_log(
                world,
                "identity-service",
                "ERROR",
                "token expiry regression rejected a valid session token for tenant=prod-eu",
            )
        else:
            world.scenario_state["rejection_rate"] = clamp(
                world.scenario_state["rejection_rate"] - 0.08,
                0.0,
                1.0,
            )
            auth.error_rate = clamp(auth.error_rate - 0.035, 0.0, 1.0)
            auth.latency_p95_ms = clamp(auth.latency_p95_ms - 60, 90, 250)
            auth.latency_p99_ms = clamp(auth.latency_p99_ms - 160, 180, 500)
            auth.saturation = clamp(auth.saturation - 0.08, 0.0, 0.6)

            user.error_rate = clamp(user.error_rate - 0.09, 0.0, 1.0)
            user.latency_p95_ms = clamp(user.latency_p95_ms - 180, 120, 800)
            user.latency_p99_ms = clamp(user.latency_p99_ms - 500, 220, 1200)
            user.saturation = clamp(user.saturation - 0.09, 0.0, 0.65)
            self.append_log(world, "identity-service", "INFO", "token validation normal after rollback")
            self.append_log(world, "accounts-api", "INFO", "authenticated request success rate recovering")

        self.finalize_tick(world)

    def on_remediation(self, world: WorldState, action, notes: list[str]) -> None:
        if action.action_type == "rollback_service" and action.service == "accounts-api":
            world.scenario_state["stale_user_rollback"] = True
            notes.append("accounts-api rollback adds stale version drift without touching the identity bug")
        if action.action_type == "rollback_service" and action.service == "identity-service":
            notes.append("identity-service rollback removes the token expiry regression")

    def remediates_root_cause(self, world: WorldState, action) -> bool:
        return (
            action.action_type == "rollback_service"
            and action.service == "identity-service"
            and action.target_version == "2026.03.6"
        )
