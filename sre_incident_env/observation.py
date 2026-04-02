from __future__ import annotations

from .models import Observation, ServiceObservation, WorldState
from .scenarios.base import clamp


class ObservationBuilder:
    def build(self, world: WorldState, score_so_far: dict[str, float]) -> Observation:
        services: list[ServiceObservation] = []
        for service in world.services:
            services.append(
                ServiceObservation(
                    name=service.name,
                    status=service.status,
                    version=service.version,
                    latency_p95_ms=round(
                        clamp(
                            service.latency_p95_ms
                            * (1 + self._noise(world, service.name, "p95", 0.02)),
                            0.0,
                            100000.0,
                        ),
                        2,
                    ),
                    latency_p99_ms=round(
                        clamp(
                            service.latency_p99_ms
                            * (1 + self._noise(world, service.name, "p99", 0.03)),
                            0.0,
                            100000.0,
                        ),
                        2,
                    ),
                    error_rate=round(
                        clamp(
                            service.error_rate + self._noise(world, service.name, "error", 0.004),
                            0.0,
                            1.0,
                        ),
                        4,
                    ),
                    saturation=round(
                        clamp(
                            service.saturation + self._noise(world, service.name, "sat", 0.015),
                            0.0,
                            1.0,
                        ),
                        4,
                    ),
                    replicas=service.replicas,
                    rate_limit_rps=service.rate_limit_rps,
                    dependency_statuses={
                        dependency: self._service_status(world, dependency)
                        for dependency in service.dependencies
                    },
                )
            )

        recent_logs = sorted(
            [line for entries in world.logs.values() for line in entries],
            key=lambda line: (line.tick, line.service),
        )[-12:]
        return Observation(
            episode_id=world.episode_id,
            tick=world.tick,
            budget_remaining=round(world.budget_remaining, 2),
            services=services,
            alerts=world.active_alerts,
            recent_logs=recent_logs,
            deploy_history=world.deploy_history[-10:],
            score_so_far=score_so_far,
        )

    def _service_status(self, world: WorldState, service_name: str) -> str:
        for service in world.services:
            if service.name == service_name:
                return service.status
        return "unknown"

    def _noise(
        self, world: WorldState, service_name: str, channel: str, amplitude: float
    ) -> float:
        import hashlib
        import random

        raw = f"{world.episode_id}:{world.tick}:{service_name}:{channel}"
        seed = int(hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16], 16)
        return random.Random(seed).uniform(-amplitude, amplitude)
