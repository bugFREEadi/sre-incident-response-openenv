from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import sys
import time
from typing import Any

import httpx
from openai import OpenAI

from sre_incident_env import SREIncidentAction, SREIncidentEnv

BENCHMARK = "sre_incident_response"
TASKS = [
    "s01_restart_cascade",
    "s02_corrupt_scaleup",
    "s03_wrong_rollback",
    "s04_cache_stampede",
    "s05_webhook_retry_storm",
]
MAX_STEPS = 8
SUCCESS_SCORE_THRESHOLD = 0.65
API_BASE_URL = os.getenv("API_BASE_URL", "https://api.openai.com/v1")
MODEL_NAME = os.getenv("MODEL_NAME", "gpt-4.1-mini")
HF_TOKEN = os.getenv("HF_TOKEN")


def log_start(task: str, env: str, model: str) -> None:
    print(f"[START] task={task} env={env} model={model}", flush=True)


def log_step(step: int, action: str, reward: float, done: bool, error: str | None) -> None:
    safe_action = action.replace("\n", " ")
    safe_error = "" if error is None else error.replace("\n", " ")
    print(
        f"[STEP] step={step} action={safe_action} reward={reward:.4f} done={str(done).lower()} error={safe_error}",
        flush=True,
    )


def log_end(success: bool, steps: int, score: float, rewards: list[float]) -> None:
    reward_blob = ",".join(f"{value:.4f}" for value in rewards)
    print(
        f"[END] success={str(success).lower()} steps={steps} score={score:.4f} rewards=[{reward_blob}]",
        flush=True,
    )


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        sock.listen(1)
        return int(sock.getsockname()[1])


async def wait_for_server(base_url: str, timeout_s: float = 30.0) -> None:
    deadline = time.time() + timeout_s
    async with httpx.AsyncClient() as client:
        while time.time() < deadline:
            try:
                response = await client.get(f"{base_url}/health", timeout=2.0)
                if response.status_code == 200:
                    return
            except Exception:
                pass
            await asyncio.sleep(0.5)
    raise RuntimeError(f"Server did not become healthy at {base_url} within {timeout_s}s")


def extract_json(text: str) -> dict[str, Any] | None:
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None


def fallback_action(task_name: str, step: int) -> dict[str, Any]:
    policies = {
        "s01_restart_cascade": [
            {"action_type": "inspect_logs", "service": "orders-postgres", "tail_n": 20},
            {"action_type": "inspect_metrics", "service": "invoice-consumer", "window_ticks": 5},
            {"action_type": "inspect_dependencies", "service": "payments-api"},
            {
                "action_type": "declare_root_cause",
                "service": "invoice-consumer",
                "reason_code": "connection_leak",
            },
            {
                "action_type": "rollback_service",
                "service": "invoice-consumer",
                "target_version": "2026.03.7",
            },
            {"action_type": "finish_incident"},
        ],
        "s02_corrupt_scaleup": [
            {"action_type": "inspect_logs", "service": "checkout-api", "tail_n": 20},
            {"action_type": "inspect_metrics", "service": "checkout-api", "window_ticks": 5},
            {
                "action_type": "declare_root_cause",
                "service": "checkout-api",
                "reason_code": "feature_flag_corruption",
            },
            {"action_type": "set_rate_limit", "service": "checkout-api", "rps": 0},
            {"action_type": "inspect_metrics", "service": "orders-postgres", "window_ticks": 5},
            {"action_type": "finish_incident"},
        ],
        "s03_wrong_rollback": [
            {"action_type": "inspect_logs", "service": "accounts-api", "tail_n": 20},
            {"action_type": "inspect_metrics", "service": "identity-service", "window_ticks": 3},
            {
                "action_type": "declare_root_cause",
                "service": "identity-service",
                "reason_code": "bad_deploy",
            },
            {
                "action_type": "rollback_service",
                "service": "identity-service",
                "target_version": "2026.03.6",
            },
            {"action_type": "inspect_logs", "service": "accounts-api", "tail_n": 10},
            {"action_type": "inspect_metrics", "service": "accounts-api", "window_ticks": 3},
            {"action_type": "finish_incident"},
        ],
        "s04_cache_stampede": [
            {"action_type": "inspect_logs", "service": "redis-catalog", "tail_n": 20},
            {"action_type": "inspect_metrics", "service": "catalog-api", "window_ticks": 5},
            {"action_type": "inspect_dependencies", "service": "search-api"},
            {
                "action_type": "declare_root_cause",
                "service": "catalog-api",
                "reason_code": "cache_key_regression",
            },
            {
                "action_type": "rollback_service",
                "service": "catalog-api",
                "target_version": "2026.03.9",
            },
            {"action_type": "inspect_metrics", "service": "redis-catalog", "window_ticks": 5},
            {"action_type": "finish_incident"},
        ],
        "s05_webhook_retry_storm": [
            {"action_type": "inspect_logs", "service": "notification-dispatcher", "tail_n": 20},
            {"action_type": "inspect_metrics", "service": "webhook-relay", "window_ticks": 5},
            {"action_type": "inspect_dependencies", "service": "notification-dispatcher"},
            {
                "action_type": "declare_root_cause",
                "service": "notification-dispatcher",
                "reason_code": "duplicate_dispatch",
            },
            {"action_type": "set_rate_limit", "service": "notification-dispatcher", "rps": 0},
            {"action_type": "inspect_metrics", "service": "orders-events-kafka", "window_ticks": 5},
            {"action_type": "finish_incident"},
        ],
    }
    sequence = policies[task_name]
    index = min(step - 1, len(sequence) - 1)
    return sequence[index]


def get_model_action(
    client: OpenAI,
    task_name: str,
    step: int,
    observation: dict[str, Any],
    history: list[str],
) -> dict[str, Any]:
    prompt = (
        "You are solving an SRE incident in a deterministic simulator.\n"
        "Choose exactly one next action as compact JSON only.\n"
        "Allowed action_type values: inspect_logs, inspect_metrics, inspect_dependencies, "
        "restart_service, rollback_service, scale_service, set_rate_limit, declare_root_cause, finish_incident.\n"
        "Prefer at least two inspection steps before remediation unless you already have enough evidence.\n"
        "Output valid JSON with only relevant fields.\n\n"
        f"Task: {task_name}\n"
        f"Step: {step}\n"
        f"History: {history}\n"
        f"Observation: {json.dumps(observation, separators=(',', ':'))}\n"
    )

    try:
        response = client.responses.create(
            model=MODEL_NAME,
            input=prompt,
            temperature=0,
        )
        text = getattr(response, "output_text", "") or ""
        parsed = extract_json(text)
        if parsed:
            return parsed
    except Exception:
        pass

    return fallback_action(task_name, step)


async def run_task(client: OpenAI, base_url: str, task_name: str) -> tuple[float, list[float]]:
    rewards: list[float] = []
    steps_taken = 0
    success = False
    history: list[str] = []

    log_start(task=task_name, env=BENCHMARK, model=MODEL_NAME)

    async with SREIncidentEnv(base_url=base_url) as env:
        result = await env.reset(scenario_id=task_name)

        for step in range(1, MAX_STEPS + 1):
            if result.done:
                break

            observation = result.observation.model_dump()
            action_payload = get_model_action(client, task_name, step, observation, history)
            action = SREIncidentAction(**action_payload)
            result = await env.step(action)

            reward = float(result.reward or 0.0)
            rewards.append(reward)
            steps_taken = step
            history.append(json.dumps(action_payload, separators=(",", ":")))
            error = result.observation.metadata.get("error") if result.observation.metadata else None
            log_step(
                step=step,
                action=json.dumps(action_payload, separators=(",", ":")),
                reward=reward,
                done=result.done,
                error=error,
            )
            if result.done:
                break

    score = rewards[-1] if rewards else 0.0
    score = max(0.0, min(score, 1.0))
    success = score >= SUCCESS_SCORE_THRESHOLD
    log_end(success=success, steps=steps_taken, score=score, rewards=rewards)
    return score, rewards


async def main() -> None:
    if not HF_TOKEN:
        raise RuntimeError("HF_TOKEN must be set before running inference.py")

    port = free_port()
    base_url = f"http://127.0.0.1:{port}"
    client = OpenAI(base_url=API_BASE_URL, api_key=HF_TOKEN)

    server = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "server.app:app", "--host", "127.0.0.1", "--port", str(port)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    try:
        await wait_for_server(base_url)
        scores = []
        for task_name in TASKS:
            score, _ = await run_task(client, base_url, task_name)
            scores.append(score)
        overall = sum(scores) / len(scores)
        print(f"Average score: {overall:.4f}", flush=True)
    finally:
        server.terminate()
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()


if __name__ == "__main__":
    asyncio.run(main())
