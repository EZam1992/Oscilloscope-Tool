from __future__ import annotations

import logging

from osci_tool.driver.siglent_sds1000xe import SiglentSDS1000XE
from osci_tool.sequencer.steps import Step, StepResult

logger = logging.getLogger(__name__)


class TestSequencer:
    def __init__(self, steps: list[Step]) -> None:
        self._steps = steps

    def run(self, scope: SiglentSDS1000XE) -> list[StepResult]:
        results = []
        for step in self._steps:
            result = step.run(scope)
            logger.info(
                "%s: %s (%s)",
                result.name,
                "PASS" if result.passed else "FAIL",
                result.detail,
            )
            results.append(result)
        return results
