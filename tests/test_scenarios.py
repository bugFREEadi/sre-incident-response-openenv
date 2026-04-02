from __future__ import annotations

import unittest

from client import IncidentEnvClient


class ScenarioScoringTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = IncidentEnvClient()

    def test_restart_cascade_separates_naive_and_correct_policies(self) -> None:
        naive_reset = self.client.reset("s01_restart_cascade")
        naive_episode = naive_reset["episode_id"]
        for _ in range(3):
            self.client.step(
                naive_episode,
                {"action_type": "restart_service", "service": "payments-api"},
            )
        self.client.step(
            naive_episode,
            {
                "action_type": "declare_root_cause",
                "service": "payments-api",
                "reason_code": "overload",
            },
        )
        naive_result = self.client.step(naive_episode, {"action_type": "finish_incident"})
        naive_final = naive_result["verifier_result"]["score_breakdown"]["final_score"]

        correct_reset = self.client.reset("s01_restart_cascade")
        correct_episode = correct_reset["episode_id"]
        for action in [
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
        ]:
            correct_result = self.client.step(correct_episode, action)
        correct_final = correct_result["verifier_result"]["score_breakdown"]["final_score"]

        self.assertLess(naive_final, 0.35)
        self.assertGreater(correct_final, 0.80)

    def test_all_scenarios_are_registered(self) -> None:
        reset_ids = []
        for scenario_id in [
            "s01_restart_cascade",
            "s02_corrupt_scaleup",
            "s03_wrong_rollback",
            "s04_cache_stampede",
            "s05_webhook_retry_storm",
        ]:
            result = self.client.reset(scenario_id)
            reset_ids.append(result["scenario_id"])
        self.assertEqual(
            reset_ids,
            [
                "s01_restart_cascade",
                "s02_corrupt_scaleup",
                "s03_wrong_rollback",
                "s04_cache_stampede",
                "s05_webhook_retry_storm",
            ],
        )

    def test_finish_requires_root_cause_declaration(self) -> None:
        reset = self.client.reset("s02_corrupt_scaleup")
        result = self.client.step(reset["episode_id"], {"action_type": "finish_incident"})
        self.assertFalse(result["done"])
        self.assertIn("declare_root_cause", result["error"])

    def test_unknown_service_returns_error(self) -> None:
        reset = self.client.reset("s03_wrong_rollback")
        result = self.client.step(
            reset["episode_id"],
            {"action_type": "inspect_logs", "service": "not-a-real-service"},
        )
        self.assertFalse(result["done"])
        self.assertEqual(result["error"], "Unknown service: not-a-real-service")

    def test_budget_exhaustion_caps_decision_score(self) -> None:
        reset = self.client.reset("s01_restart_cascade")
        episode_id = reset["episode_id"]
        result = None
        for _ in range(30):
            result = self.client.step(
                episode_id,
                {"action_type": "inspect_logs", "service": "payments-api", "tail_n": 1},
            )
            if result["done"]:
                break
        self.assertIsNotNone(result)
        self.assertTrue(result["done"])
        self.assertLessEqual(result["verifier_result"]["score_breakdown"]["decision_score"], 0.3)

    def test_all_scenarios_have_correct_root_cause_paths(self) -> None:
        plans = {
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

        for scenario_id, actions in plans.items():
            reset = self.client.reset(scenario_id)
            episode_id = reset["episode_id"]
            result = None
            for action in actions:
                result = self.client.step(episode_id, action)
            self.assertIsNotNone(result)
            self.assertTrue(result["done"], scenario_id)
            self.assertTrue(result["verifier_result"]["root_cause_correct"], scenario_id)
            self.assertGreaterEqual(result["verifier_result"]["score_breakdown"]["final_score"], 0.70, scenario_id)


if __name__ == "__main__":
    unittest.main()
