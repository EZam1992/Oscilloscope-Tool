from __future__ import annotations

import time

from osci_tool.controller.app_controller import AppController, ConnectionState
from osci_tool.sequencer.steps import ChannelConfigRoundTrip
from osci_tool.transport.simulated import SimulatedTransport


def test_controller_connect_and_disconnect() -> None:
    sim = SimulatedTransport()
    controller = AppController(transport=sim, poll_interval_s=0.01)

    controller.connect(use_simulated=False)

    # Wait for connection
    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline and not controller.is_connected:
        time.sleep(0.01)

    assert controller.is_connected
    assert controller.snapshot.connection_state == ConnectionState.CONNECTED
    assert "SDS1104X-E" in controller.snapshot.scope_id

    controller.disconnect()
    assert not controller.is_connected
    assert controller.snapshot.connection_state == ConnectionState.DISCONNECTED


def test_controller_state_callbacks_and_settings() -> None:
    sim = SimulatedTransport()
    controller = AppController(transport=sim, poll_interval_s=0.01)
    controller.connect(use_simulated=False)

    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline and not controller.is_connected:
        time.sleep(0.01)

    # Verify channel settings change
    controller.set_vertical_scale(1, 0.5)
    controller.set_coupling(1, "A1M")

    time.sleep(0.05)
    snap = controller.snapshot
    assert snap.channels[1].vdiv == 0.5
    assert snap.channels[1].coupling == "A1M"

    controller.disconnect()


def test_controller_waveform_polling() -> None:
    sim = SimulatedTransport()
    sim.set_waveform_codes(1, [10, 20, 30])
    controller = AppController(transport=sim, poll_interval_s=0.01)

    received_waveforms: list[tuple[int, list[tuple[float, float]]]] = []
    controller.on_waveform_updated = lambda ch, wf: received_waveforms.append((ch, wf))

    controller.connect(use_simulated=False)

    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline and not received_waveforms:
        time.sleep(0.02)

    assert len(received_waveforms) > 0
    ch, points = received_waveforms[0]
    assert ch == 1
    assert len(points) == 3

    controller.disconnect()


def test_controller_run_sequence() -> None:
    sim = SimulatedTransport()
    controller = AppController(transport=sim, poll_interval_s=0.01)

    step = ChannelConfigRoundTrip(
        channel=1, vertical_scale=0.5, offset=0.0, coupling="D1M"
    )
    results_received = []
    controller.on_sequence_completed = lambda res: results_received.extend(res)

    controller.connect(use_simulated=False)

    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline and not controller.is_connected:
        time.sleep(0.01)

    controller.run_sequence([step])

    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline and not results_received:
        time.sleep(0.02)

    assert len(results_received) == 1
    assert results_received[0].passed

    controller.disconnect()
