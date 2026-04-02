from scenarios.s01_restart_cascade import RestartCascadeScenario
from scenarios.s02_corrupt_scaleup import CorruptScaleUpScenario
from scenarios.s03_wrong_rollback import WrongRollbackScenario
from scenarios.s04_cache_stampede import CacheStampedeScenario
from scenarios.s05_webhook_retry_storm import WebhookRetryStormScenario


def build_scenarios():
    scenario_list = [
        RestartCascadeScenario(),
        CorruptScaleUpScenario(),
        WrongRollbackScenario(),
        CacheStampedeScenario(),
        WebhookRetryStormScenario(),
    ]
    return {scenario.scenario_id: scenario for scenario in scenario_list}
