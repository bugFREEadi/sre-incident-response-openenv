from __future__ import annotations

from dataclasses import asdict
from uuid import uuid4

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from actions import ActionExecutor
from models import Action, StepResult, WorldState
from observation import ObservationBuilder
from reward import RewardEngine
from scenarios import build_scenarios
from scenarios.base import get_service
from verifier import VerifierEngine


class IncidentWorld:
    def __init__(self) -> None:
        self.scenarios = build_scenarios()
        self.observation_builder = ObservationBuilder()
        self.reward_engine = RewardEngine()
        self.verifier = VerifierEngine(self.reward_engine)
        self.action_executor = ActionExecutor(self.scenarios)
        self.episodes: dict[str, WorldState] = {}

    def reset(self, scenario_id: str | None = None) -> dict:
        selected_id = scenario_id or next(iter(self.scenarios.keys()))
        scenario = self.scenarios[selected_id]
        episode_id = f"{selected_id}-{uuid4().hex[:8]}"
        world = scenario.build_world(episode_id)
        world.scenario_state["scenario"] = scenario
        scenario.refresh_alerts(world)
        self.episodes[episode_id] = world
        reward = self.reward_engine.score(world)
        observation = self.observation_builder.build(world, reward)
        return {
            "episode_id": episode_id,
            "scenario_id": selected_id,
            "scenario_name": scenario.name,
            "observation": asdict(observation),
        }

    def state(self, episode_id: str) -> dict:
        world = self._get_world(episode_id)
        reward = self.reward_engine.score(world)
        return {
            "episode_id": episode_id,
            "scenario_id": world.scenario_id,
            "scenario_name": world.scenario_name,
            "observation": asdict(self.observation_builder.build(world, reward)),
            "done": world.terminated,
        }

    def step(self, episode_id: str, action_payload: dict) -> dict:
        world = self._get_world(episode_id)
        if world.terminated:
            reward = self.reward_engine.score(world)
            observation = self.observation_builder.build(world, reward)
            return StepResult(
                observation=observation,
                action_result={},
                reward=reward,
                done=True,
                error=f"Episode already terminated: {world.termination_reason or 'completed'}",
            ).to_dict()

        action = Action.from_payload(action_payload)
        if action.action_type == "finish_incident":
            return self._finish(world).to_dict()

        action_result, record, error = self.action_executor.execute(world, action)
        if error:
            reward = self.reward_engine.score(world)
            observation = self.observation_builder.build(world, reward)
            return StepResult(
                observation=observation,
                action_result={},
                reward=reward,
                done=False,
                error=error,
            ).to_dict()

        self._advance_tick(world)
        if record is not None:
            record.after_statuses = {service.name: service.status for service in world.services}
            record.blast_radius_violations = self._compute_blast_radius(record)
            world.action_history.append(record)

        if world.budget_remaining <= 0:
            world.terminated = True
            world.termination_reason = "budget_exhausted"
            verifier_result = self.verifier.verify(world, cap_decision_score=0.3)
            reward = verifier_result.score_breakdown
            observation = self.observation_builder.build(world, reward)
            return StepResult(
                observation=observation,
                action_result=action_result,
                reward=reward,
                done=True,
                verifier_result=verifier_result,
                error="Budget exhausted before finish_incident() was called",
            ).to_dict()

        reward = self.reward_engine.score(world)
        observation = self.observation_builder.build(world, reward)
        return StepResult(
            observation=observation,
            action_result=action_result,
            reward=reward,
            done=False,
        ).to_dict()

    def _finish(self, world: WorldState) -> StepResult:
        if not world.declared_root_cause or not world.declared_reason_code:
            reward = self.reward_engine.score(world)
            observation = self.observation_builder.build(world, reward)
            return StepResult(
                observation=observation,
                action_result={},
                reward=reward,
                done=False,
                error="declare_root_cause(service, reason_code) must be called before finish_incident()",
            )

        verifier_result = self.verifier.verify(world)
        world.terminated = True
        world.termination_reason = "finished"
        observation = self.observation_builder.build(world, verifier_result.score_breakdown)
        return StepResult(
            observation=observation,
            action_result={"message": "Incident finished"},
            reward=verifier_result.score_breakdown,
            done=True,
            verifier_result=verifier_result,
        )

    def _advance_tick(self, world: WorldState) -> None:
        scenario = self.scenarios[world.scenario_id]
        self._apply_pending_effects(world)
        scenario.apply_tick(world)
        world.tick += 1

    def _apply_pending_effects(self, world: WorldState) -> None:
        remaining = []
        for effect in world.pending_effects:
            if effect.apply_at <= world.tick:
                service = get_service(world, effect.service)
                if effect.effect_type == "restart_complete":
                    service.error_rate = min(service.error_rate, 0.05)
                    service.latency_p95_ms = min(service.latency_p95_ms, 250)
                    service.latency_p99_ms = min(service.latency_p99_ms, 800)
                    service.saturation = min(service.saturation, 0.65)
                    world.scenario_state["api_restart_relief_ticks"] = 1
            else:
                remaining.append(effect)
        world.pending_effects = remaining

    def _compute_blast_radius(self, record) -> int:
        target = record.action.service
        violations = 0
        for service_name, previous_status in record.before_statuses.items():
            if service_name == target:
                continue
            new_status = record.after_statuses.get(service_name, previous_status)
            if previous_status == "healthy" and new_status in {"degraded", "down"}:
                violations += 1
        return violations

    def _get_world(self, episode_id: str) -> WorldState:
        try:
            return self.episodes[episode_id]
        except KeyError as exc:
            raise KeyError(f"Unknown episode_id: {episode_id}") from exc
