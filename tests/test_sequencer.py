from __future__ import annotations

import logging

import pytest

from osci_tool.driver.siglent_sds1000xe import SiglentSDS1000XE
from osci_tool.sequencer import sequencer
from osci_tool.sequencer.steps import (
    ChannelConfigRoundTrip,
    StepResult,
    TriggerFiresWithinTimeout,
    WaveformAmplitudeInRange,
)
from osci_tool.transport.simulated import SimulatedTransport

# Always referenced as `sequencer.TestSequencer`: binding the bare name in a test
# module would make pytest try to collect it as a test class.


class TestChannelConfigRoundTrip:
    def test_passes_on_exact_readback(self, scope: SiglentSDS1000XE) -> None:
        step = ChannelConfigRoundTrip(
            channel=1, vertical_scale=0.5, offset=0.2, coupling="A1M"
        )
        result = step.run(scope)
        assert result.passed, result.detail

    def test_tolerance_absorbs_quantization(self) -> None:
        with SiglentSDS1000XE(SimulatedTransport(offset_resolution=0.008)) as scope:
            step = ChannelConfigRoundTrip(
                channel=2, vertical_scale=1.0, offset=0.123, coupling="D1M"
            )
            result = step.run(scope)
            readback = scope.get_offset(2)
        assert readback != 0.123  # the scope really did move the value
        assert result.passed, result.detail

    def test_fails_outside_tolerance(self) -> None:
        with SiglentSDS1000XE(SimulatedTransport(offset_resolution=0.5)) as scope:
            step = ChannelConfigRoundTrip(
                channel=1, vertical_scale=1.0, offset=0.2, coupling="D1M"
            )
            result = step.run(scope)
        assert not result.passed

    def test_fails_on_coupling_mismatch(
        self, scope: SiglentSDS1000XE, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(scope, "get_coupling", lambda channel: "GND")
        step = ChannelConfigRoundTrip(
            channel=1, vertical_scale=1.0, offset=0.0, coupling="D1M"
        )
        assert not step.run(scope).passed


class TestWaveformAmplitudeInRange:
    def test_passes_when_inside(
        self, scope: SiglentSDS1000XE, sim: SimulatedTransport
    ) -> None:
        sim.set_waveform_codes(1, [-25, 0, 25])  # +/-1 V at 1 V/div
        result = WaveformAmplitudeInRange(channel=1, min_volts=-1.1, max_volts=1.1).run(
            scope
        )
        assert result.passed, result.detail

    @pytest.mark.parametrize("codes", [[-50, 0, 25], [-25, 0, 50]])
    def test_fails_when_either_edge_outside(
        self, scope: SiglentSDS1000XE, sim: SimulatedTransport, codes: list[int]
    ) -> None:
        sim.set_waveform_codes(1, codes)
        result = WaveformAmplitudeInRange(channel=1, min_volts=-1.1, max_volts=1.1).run(
            scope
        )
        assert not result.passed


class TestTriggerFiresWithinTimeout:
    def test_passes_after_a_few_polls(self) -> None:
        sim = SimulatedTransport(trigger_after_polls=3)
        with SiglentSDS1000XE(sim) as scope:
            result = TriggerFiresWithinTimeout(
                timeout_s=1.0, poll_interval_s=0.001
            ).run(scope)
        assert result.passed, result.detail
        assert "TRMD SINGLE" in sim.commands
        assert sim.commands.count("SAST?") == 4

    def test_fails_on_timeout(self) -> None:
        with SiglentSDS1000XE(SimulatedTransport(trigger_after_polls=None)) as scope:
            result = TriggerFiresWithinTimeout(
                timeout_s=0.05, poll_interval_s=0.005
            ).run(scope)
        assert not result.passed
        assert "timeout" in result.detail


class TestSequencerRun:
    def test_runs_steps_in_order_and_collects_results(
        self, scope: SiglentSDS1000XE
    ) -> None:
        steps = [
            ChannelConfigRoundTrip(
                channel=1, vertical_scale=0.5, offset=0.0, coupling="D1M"
            ),
            WaveformAmplitudeInRange(channel=1, min_volts=-0.01, max_volts=0.01),
        ]
        results = sequencer.TestSequencer(steps).run(scope)
        assert [r.name for r in results] == [
            "channel_config_round_trip",
            "waveform_amplitude_in_range",
        ]
        assert results[0].passed
        assert not results[1].passed  # default sine is +/-1 V at 0.5 V/div

    def test_logs_pass_fail(
        self, scope: SiglentSDS1000XE, caplog: pytest.LogCaptureFixture
    ) -> None:
        class Fixed:
            name = "fixed"

            def __init__(self, passed: bool) -> None:
                self.passed = passed

            def run(self, scope: SiglentSDS1000XE) -> StepResult:
                return StepResult(self.name, self.passed, "detail")

        with caplog.at_level(logging.INFO, logger=sequencer.__name__):
            sequencer.TestSequencer([Fixed(True), Fixed(False)]).run(scope)
        assert "fixed: PASS (detail)" in caplog.text
        assert "fixed: FAIL (detail)" in caplog.text

    def test_empty_sequence(self, scope: SiglentSDS1000XE) -> None:
        assert sequencer.TestSequencer([]).run(scope) == []
