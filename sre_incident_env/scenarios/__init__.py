from .s01_restart_cascade import RestartCascadeScenario
from .s02_corrupt_scaleup import CorruptScaleUpScenario
from .s03_wrong_rollback import WrongRollbackScenario

DEFAULT_SCENARIOS = {
    scenario.scenario_id: scenario
    for scenario in (
        RestartCascadeScenario(),
        CorruptScaleUpScenario(),
        WrongRollbackScenario(),
    )
}

__all__ = [
    "DEFAULT_SCENARIOS",
    "RestartCascadeScenario",
    "CorruptScaleUpScenario",
    "WrongRollbackScenario",
]
