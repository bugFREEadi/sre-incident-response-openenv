from __future__ import annotations

from typing import Any

from openenv.core import EnvClient
from openenv.core.client_types import StepResult

from .models import SREIncidentAction, SREIncidentObservation, SREIncidentState


class SREIncidentEnv(
    EnvClient[SREIncidentAction, SREIncidentObservation, SREIncidentState]
):
    """OpenEnv client for the SRE Incident Response environment."""

    def _step_payload(self, action: SREIncidentAction) -> dict[str, Any]:
        return action.model_dump(exclude_none=True, exclude={"metadata"})

    def _parse_result(self, payload: dict[str, Any]) -> StepResult[SREIncidentObservation]:
        observation = SREIncidentObservation.model_validate(payload.get("observation", {}))
        return StepResult(
            observation=observation,
            reward=payload.get("reward"),
            done=payload.get("done", False),
        )

    def _parse_state(self, payload: dict[str, Any]) -> SREIncidentState:
        return SREIncidentState.model_validate(payload)
