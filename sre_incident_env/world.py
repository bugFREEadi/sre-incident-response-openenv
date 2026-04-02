from __future__ import annotations

from copy import deepcopy
from uuid import uuid4

from .actions import ActionExecutor
from .models import Action, Observation, StepResult, WorldState
from .observation import ObservationBuilder
from .reward import RewardEngine
from .scenarios import DEFAULT_SCENARIOS
from .scenarios.base import Scenario
from .verifier import VerifierEngine


class IncidentWorld:
    def __init__(self, scenarios: dict[str, Scenario] | None = None) -> None:
        self.scenarios = scenarios or DEFAULT_SCENARIOS
        self.observation_builder = ObservationBuilder()
        self.action_executor = ActionExecutor()
        self.reward_engine = RewardEngine()
        self.verifier_engine = VerifierEngine()
        self.world: WorldState | None = None
        self.scenario: Scenario | None = None

    def reset(self, scenario_id: str | None = None, budget: float | None = None) -> Observation:
        if scenario_id is None:
            scenario_id = "s01_restart_cascade"
        if scenario_id not in self.scenarios:
            raise ValueError(f"Unknown scenario_id: {scenario_id}")
        self.scenario = self.scenarios[scenario_id]
        episode_id = f"{scenario_id}-{uuid4().hex[:8]}"
        self.world = self.scenario.build_world(episode_id=episode_id, budget=budget)
        return self.observe()

    def observe(self) -> Observation:
        world = self._require_world()
        scenario = self._require_scenario()
        return self.observation_builder.build(
            world,
            score_so_far=self.reward_engine.live_score(world, scenario),
        )

    def step(self, action_payload: dict) -> StepResult:
        world = self._require_world()
        scenario = self._require_scenario()
        action = Action.from_dict(action_payload)
        previous_world = deepcopy(world)
        action_result = self.action_executor.execute(world, scenario, action)

        verifier = None
        done = False
        if action.action_type == "finish_incident":
            verifier = self.verifier_engine.verify(world, scenario)
            self.reward_engine.apply_final_breakdown(world, scenario, verifier)
            done = True
        else:
            self._advance_tick(world, scenario)
            if world.budget_remaining <= 0:
                world.finished = False
                verifier = self.verifier_engine.verify(world, scenario)
                self.reward_engine.apply_final_breakdown(world, scenario, verifier)
                verifier.score_breakdown["decision_score"] = min(
                    0.3, float(verifier.score_breakdown["decision_score"])
                )
                verifier.score_breakdown["final_score"] = round(
                    (
                        float(verifier.score_breakdown["recovery_score"])
                        + float(verifier.score_breakdown["decision_score"])
                    )
                    * 0.5,
                    4,
                )
                done = True

        observation = self.observe()
        if verifier is not None:
            observation.score_so_far = verifier.score_breakdown
        reward = self.reward_engine.step_reward(previous_world, world, scenario, action, verifier)
        return StepResult(
            observation=observation,
            reward=reward,
            done=done,
            action_result=action_result,
            verifier=verifier,
        )

    def available_scenarios(self) -> list[dict[str, str]]:
        return [
            {
                "scenario_id": scenario.scenario_id,
                "title": scenario.title,
                "description": scenario.description,
            }
            for scenario in self.scenarios.values()
        ]

    def _advance_tick(self, world: WorldState, scenario: Scenario) -> None:
        next_tick = world.tick + 1
        scenario.advance(world, next_tick)
        scenario.refresh_alerts(world)
        scenario.record_metrics(world)

    def _require_world(self) -> WorldState:
        if self.world is None:
            raise RuntimeError("No active episode. Call reset() first.")
        return self.world

    def _require_scenario(self) -> Scenario:
        if self.scenario is None:
            raise RuntimeError("No active scenario. Call reset() first.")
        return self.scenario
