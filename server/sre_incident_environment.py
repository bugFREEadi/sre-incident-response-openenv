from __future__ import annotations

from uuid import uuid4

from openenv.core.env_server.interfaces import Environment
from openenv.core.env_server.types import EnvironmentMetadata

from sre_incident_env.models import (
    SREIncidentAction,
    SREIncidentObservation,
    SREIncidentState,
)
from world import IncidentWorld


class SREIncidentEnvironment(
    Environment[SREIncidentAction, SREIncidentObservation, SREIncidentState]
):
    """Stateful OpenEnv wrapper around the incident-response simulator."""

    SUPPORTS_CONCURRENT_SESSIONS = True

    def __init__(self) -> None:
        super().__init__()
        self._engine = IncidentWorld()
        self._episode_id: str | None = None
        self._state = SREIncidentState(episode_id=str(uuid4()), step_count=0)

    def reset(
        self,
        seed: int | None = None,
        episode_id: str | None = None,
        scenario_id: str | None = None,
        **kwargs,
    ) -> SREIncidentObservation:
        del seed, episode_id, kwargs
        result = self._engine.reset(scenario_id=scenario_id)
        self._episode_id = result["episode_id"]
        self._state = SREIncidentState(
            episode_id=self._episode_id,
            step_count=0,
            scenario_id=result["scenario_id"],
            scenario_name=result["scenario_name"],
            budget_remaining=result["observation"]["budget_remaining"],
            terminated=False,
        )
        return self._build_observation(
            result["observation"],
            reward=0.0,
            done=False,
        )

    def step(
        self,
        action: SREIncidentAction,
        timeout_s: float | None = None,
        **kwargs,
    ) -> SREIncidentObservation:
        del timeout_s, kwargs
        if self._episode_id is None:
            raise RuntimeError("Call reset() before step().")

        payload = action.model_dump(exclude_none=True, exclude={"metadata"})
        result = self._engine.step(self._episode_id, payload)
        current_world = self._engine._get_world(self._episode_id)
        reward_breakdown = result.get("reward", {})
        reward_value = float(reward_breakdown.get("final_score", 0.0))
        self._state.step_count += 1
        self._state.budget_remaining = result["observation"]["budget_remaining"]
        self._state.terminated = result["done"]
        self._state.declared_root_cause = current_world.declared_root_cause
        self._state.declared_reason_code = current_world.declared_reason_code
        return self._build_observation(
            result["observation"],
            reward=reward_value,
            done=result["done"],
            metadata={
                "action_result": result.get("action_result", {}),
                "reward_breakdown": reward_breakdown,
                "verifier_result": result.get("verifier_result"),
                "error": result.get("error"),
            },
        )

    @property
    def state(self) -> SREIncidentState:
        if self._episode_id is None:
            return self._state

        snapshot = self._engine.state(self._episode_id)
        self._state.episode_id = self._episode_id
        self._state.scenario_id = snapshot["scenario_id"]
        self._state.scenario_name = snapshot["scenario_name"]
        self._state.budget_remaining = snapshot["observation"]["budget_remaining"]
        self._state.terminated = snapshot["done"]
        current_world = self._engine._get_world(self._episode_id)
        self._state.declared_root_cause = current_world.declared_root_cause
        self._state.declared_reason_code = current_world.declared_reason_code
        return self._state

    def get_metadata(self) -> EnvironmentMetadata:
        return EnvironmentMetadata(
            name="sre_incident_response",
            description=(
                "Causal incident-response benchmark where locally obvious remediation "
                "can worsen the global system state."
            ),
            version="0.1.0",
            documentation_url="https://github.com/meta-pytorch/OpenEnv",
        )

    def _build_observation(
        self,
        observation_payload: dict,
        reward: float | None,
        done: bool,
        metadata: dict | None = None,
    ) -> SREIncidentObservation:
        return SREIncidentObservation.model_validate(
            {
                **observation_payload,
                "scenario_id": self._state.scenario_id or observation_payload.get("scenario_id") or "",
                "scenario_name": self._state.scenario_name or observation_payload.get("scenario_name") or "",
                "reward": reward,
                "done": done,
                "metadata": metadata or {},
            }
        )
