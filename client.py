from __future__ import annotations

from world import IncidentWorld


class IncidentEnvClient:
    def __init__(self, engine: IncidentWorld | None = None) -> None:
        self.engine = engine or IncidentWorld()

    def reset(self, scenario_id: str | None = None) -> dict:
        return self.engine.reset(scenario_id=scenario_id)

    def step(self, episode_id: str, action: dict) -> dict:
        return self.engine.step(episode_id=episode_id, action_payload=action)

    def state(self, episode_id: str) -> dict:
        return self.engine.state(episode_id=episode_id)
