"""HTTP client for the SRE Incident Response OpenEnv environment.

Pure httpx implementation — no openenv_core dependency — so that inference.py
can be executed in any environment that has httpx installed.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import httpx

from .models import SREIncidentAction, SREIncidentObservation, SREIncidentState


@dataclass
class StepResult:
    observation: SREIncidentObservation
    reward: float | None
    done: bool
    info: dict[str, Any] = field(default_factory=dict)


class SREIncidentEnv:
    """Async HTTP client for the SRE Incident Response environment server."""

    def __init__(self, base_url: str, timeout: float = 30.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "SREIncidentEnv":
        self._client = httpx.AsyncClient(timeout=self._timeout)
        return self

    async def __aexit__(self, *exc: Any) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        assert self._client is not None, "Use as async context manager"
        response = await self._client.post(
            f"{self._base_url}{path}",
            content=json.dumps(payload),
            headers={"Content-Type": "application/json"},
        )
        response.raise_for_status()
        return response.json()

    async def reset(self, scenario_id: str | None = None) -> StepResult:
        payload: dict[str, Any] = {}
        if scenario_id:
            payload["scenario_id"] = scenario_id
        data = await self._post("/reset", payload)
        return StepResult(
            observation=SREIncidentObservation.model_validate(data.get("observation", {})),
            reward=data.get("reward"),
            done=data.get("done", False),
            info=data.get("info", {}),
        )

    async def step(self, action: SREIncidentAction) -> StepResult:
        payload = action.model_dump(exclude_none=True, exclude={"metadata"})
        data = await self._post("/step", payload)
        return StepResult(
            observation=SREIncidentObservation.model_validate(data.get("observation", {})),
            reward=data.get("reward"),
            done=data.get("done", False),
            info=data.get("info", {}),
        )

    async def state(self) -> SREIncidentState:
        assert self._client is not None, "Use as async context manager"
        response = await self._client.get(f"{self._base_url}/state")
        response.raise_for_status()
        return SREIncidentState.model_validate(response.json())
