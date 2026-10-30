from __future__ import annotations

from collections.abc import Iterator

import pytest

from osci_tool.driver.siglent_sds1000xe import SiglentSDS1000XE
from osci_tool.transport.simulated import SimulatedTransport


@pytest.fixture
def sim() -> SimulatedTransport:
    return SimulatedTransport()


@pytest.fixture
def scope(sim: SimulatedTransport) -> Iterator[SiglentSDS1000XE]:
    with SiglentSDS1000XE(sim) as driver:
        yield driver
