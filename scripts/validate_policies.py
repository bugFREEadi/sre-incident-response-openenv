from __future__ import annotations

import sys
from pathlib import Path
from pprint import pprint

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from client import IncidentEnvClient


def run_policy(client: IncidentEnvClient, scenario_id: str, actions: list[dict]) -> dict:
    reset = client.reset(scenario_id)
    episode_id = reset["episode_id"]
    last_result = None
    for action in actions:
        last_result = client.step(episode_id, action)
    return last_result or {}


def main() -> None:
    client = IncidentEnvClient()
    naive = run_policy(
        client,
        "s01_restart_cascade",
        [
            {"action_type": "restart_service", "service": "payments-api"},
            {"action_type": "restart_service", "service": "payments-api"},
            {"action_type": "restart_service", "service": "payments-api"},
            {
                "action_type": "declare_root_cause",
                "service": "payments-api",
                "reason_code": "overload",
            },
            {"action_type": "finish_incident"},
        ],
    )
    correct = run_policy(
        client,
        "s01_restart_cascade",
        [
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
    )

    print("Naive policy score:")
    pprint(naive["verifier_result"]["score_breakdown"])
    print("\nCorrect policy score:")
    pprint(correct["verifier_result"]["score_breakdown"])


if __name__ == "__main__":
    main()
