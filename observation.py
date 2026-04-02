from __future__ import annotations

import random

from models import Observation, ServiceObservation, WorldState
from scenarios.base import get_service


class ObservationBuilder:
    def build(self, world: WorldState, score_so_far: dict) -> Observation:
        services = [
            ServiceObservation(
                name=service.name,
                status=service.status,
                version=service.version,
                latency_p95_ms=round(service.latency_p95_ms, 2),
                latency_p99_ms=round(service.latency_p99_ms, 2),
                error_rate=round(service.error_rate, 4),
                saturation=round(service.saturation, 4),
                replicas=service.replicas,
                rate_limit_rps=service.rate_limit_rps,
                dependency_statuses={
                    dependency: get_service(world, dependency).status
                    for dependency in service.dependencies
                },
            )
            for service in world.services
        ]

        rng = random.Random(f"{world.episode_id}:{world.tick}")
        combined_logs = [log for logs in world.service_logs.values() for log in logs]
        combined_logs.sort(key=lambda line: (line.tick, line.service))
        tail = combined_logs[-16:]
        if len(tail) > 8:
            tail = rng.sample(tail, 8)
            tail.sort(key=lambda line: (line.tick, line.service))

        return Observation(
            episode_id=world.episode_id,
            tick=world.tick,
            budget_remaining=round(world.budget_remaining, 2),
            services=services,
            alerts=list(world.active_alerts),
            recent_logs=tail,
            deploy_history=world.deploy_history[-10:],
            score_so_far=score_so_far,
        )
