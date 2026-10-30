"""SiglentSDS1000XE driver tests against the offline simulator.

Each test class maps to a protocol gotcha in GEMINI.md, so a regression points
straight at which scope quirk broke.
"""

from __future__ import annotations

import math
from collections.abc import Callable

import pytest

from osci_tool.driver.siglent_sds1000xe import SiglentSDS1000XE
from osci_tool.transport.simulated import IDN_REPLY, SimulatedTransport

Action = Callable[[SiglentSDS1000XE], object]


class TestFraming:
    """Banner, '>>' prompt, and NUL bytes must never leak into parsed values."""

    @pytest.mark.parametrize(
        "raw",
        [
            "Welcome to the SCPI instrument 'Siglent SDS1104X-E'\nC1:TRA ON",
            ">>C1:TRA ON",
            ">>\x00\x00C1:TRA ON",
            "Welcome to the SCPI instrument 'Siglent SDS1104X-E'\n>>\x00C1:TRA ON\n",
            "  C1:TRA ON  \r\n",
        ],
    )
    def test_strip_framing(self, raw: str) -> None:
        assert SiglentSDS1000XE._strip_framing(raw) == "C1:TRA ON"

    def test_strip_framing_empty(self) -> None:
        assert SiglentSDS1000XE._strip_framing("\n\n") == ""

    def test_banner_alone_is_not_mistaken_for_a_reply(self) -> None:
        # If the banner arrives as its own line, it must not be returned as data.
        banner = "Welcome to the SCPI instrument 'Siglent SDS1104X-E'\n"
        assert SiglentSDS1000XE._strip_framing(banner) == ""

    def test_first_reply_with_banner_parses(self, scope: SiglentSDS1000XE) -> None:
        assert scope.identify() == IDN_REPLY

    def test_banner_only_on_first_reply(self, scope: SiglentSDS1000XE) -> None:
        assert scope.identify() == IDN_REPLY
        assert scope.identify() == IDN_REPLY

    def test_prompt_and_nul_prefixed_replies_parse(self) -> None:
        with SiglentSDS1000XE(SimulatedTransport(prompt=True)) as scope:
            assert scope.identify() == IDN_REPLY
            assert scope.get_vertical_scale(1) == 1.0
            assert scope.is_channel_enabled(1) is True


class TestOpcAfterWrite:
    """Every write must be followed by *OPC? to avoid stale readback."""

    @pytest.mark.parametrize(
        ("action", "command"),
        [
            (lambda s: s.set_channel(2, False), "C2:TRA OFF"),
            (lambda s: s.set_vertical_scale(1, 0.5), "C1:VDIV 0.5"),
            (lambda s: s.set_offset(3, -0.2), "C3:OFST -0.2"),
            (lambda s: s.set_coupling(4, "A1M"), "C4:CPL A1M"),
            (lambda s: s.set_trigger_level(1, 2.5), "C1:TRLV 2.5"),
            (lambda s: s.set_trigger_mode("NORM"), "TRMD NORM"),
            (lambda s: s.set_horizontal_scale("5US"), "TDIV 5US"),
        ],
    )
    def test_write_followed_by_opc(
        self,
        scope: SiglentSDS1000XE,
        sim: SimulatedTransport,
        action: Action,
        command: str,
    ) -> None:
        action(scope)
        assert sim.commands[-2:] == [command, "*OPC?"]


class TestHeaderEchoParsing:
    """Replies echo the header; parsers take the last token and strip units."""

    def test_vertical_scale_round_trip(self, scope: SiglentSDS1000XE) -> None:
        scope.set_vertical_scale(1, 0.5)
        assert scope.get_vertical_scale(1) == 0.5

    def test_negative_offset_round_trip(self, scope: SiglentSDS1000XE) -> None:
        scope.set_offset(2, -1.25)
        assert scope.get_offset(2) == -1.25

    def test_coupling_round_trip(self, scope: SiglentSDS1000XE) -> None:
        scope.set_coupling(3, "A50")
        assert scope.get_coupling(3) == "A50"

    def test_channel_enable_round_trip(self, scope: SiglentSDS1000XE) -> None:
        scope.set_channel(4, False)
        assert scope.is_channel_enabled(4) is False
        scope.set_channel(4, True)
        assert scope.is_channel_enabled(4) is True

    def test_trigger_level_round_trip(self, scope: SiglentSDS1000XE) -> None:
        scope.set_trigger_level(1, 1.5)
        assert scope.get_trigger_level(1) == 1.5

    def test_trigger_mode_round_trip(self, scope: SiglentSDS1000XE) -> None:
        scope.set_trigger_mode("STOP")
        assert scope.get_trigger_mode() == "STOP"

    @pytest.mark.parametrize(
        ("setting", "seconds"),
        [("1NS", 1e-9), ("500US", 500e-6), ("20MS", 20e-3), ("100S", 100.0)],
    )
    def test_horizontal_scale_strips_s_suffix(
        self, scope: SiglentSDS1000XE, setting: str, seconds: float
    ) -> None:
        scope.set_horizontal_scale(setting)
        assert math.isclose(scope.get_horizontal_scale(), seconds)

    def test_sample_rate_strips_sa_per_s(
        self, scope: SiglentSDS1000XE, sim: SimulatedTransport
    ) -> None:
        sim.sample_rate = 5e8
        assert scope.get_sample_rate() == 5e8


class TestWaveformScaling:
    """volts = int8_code * (vdiv / 25) - offset; t0 = -(tdiv * 14 / 2)."""

    CODES = (0, 25, -25, 127, -128, 1, -1)

    def test_raw_payload_extracted_from_block(
        self, scope: SiglentSDS1000XE, sim: SimulatedTransport
    ) -> None:
        sim.set_waveform_codes(1, self.CODES)
        assert scope.get_waveform_raw(1) == bytes(c & 0xFF for c in self.CODES)

    def test_voltage_scaling_uses_signed_codes(
        self, scope: SiglentSDS1000XE, sim: SimulatedTransport
    ) -> None:
        sim.set_waveform_codes(2, self.CODES)
        scope.set_vertical_scale(2, 0.5)
        scope.set_offset(2, 0.1)

        volts = [v for _, v in scope.get_waveform(2)]

        expected = [code * (0.5 / 25) - 0.1 for code in self.CODES]
        assert volts == pytest.approx(expected)
        assert volts[1] == pytest.approx(0.4)  # +25 codes = +1 division
        assert volts[4] < volts[0]  # 0x80 is -128, not +128

    def test_time_axis(self, scope: SiglentSDS1000XE, sim: SimulatedTransport) -> None:
        sim.set_waveform_codes(1, self.CODES)
        scope.set_horizontal_scale("1MS")
        sim.sample_rate = 1e6

        times = [t for t, _ in scope.get_waveform(1)]

        assert times[0] == pytest.approx(-7e-3)  # 14 divisions / 2
        assert times[1] - times[0] == pytest.approx(1e-6)
        assert len(times) == len(self.CODES)

    def test_waveform_query_uses_dat2(
        self, scope: SiglentSDS1000XE, sim: SimulatedTransport
    ) -> None:
        scope.get_waveform_raw(3)
        assert sim.commands[-1] == "C3:WF? DAT2"


class TestTriggerSource:
    def test_requires_enabled_channel(
        self, scope: SiglentSDS1000XE, sim: SimulatedTransport
    ) -> None:
        scope.set_channel(2, False)
        with pytest.raises(RuntimeError, match="enabled"):
            scope.set_trigger_source(2)
        assert not any(cmd.startswith("TRSE ") for cmd in sim.commands)

    def test_command_form_and_round_trip(
        self, scope: SiglentSDS1000XE, sim: SimulatedTransport
    ) -> None:
        scope.set_trigger_source(3)
        assert "TRSE EDGE,SR,C3,HT,OFF" in sim.commands
        assert scope.get_trigger_source() == 3

    def test_trigger_status_reports_trigd(self, scope: SiglentSDS1000XE) -> None:
        scope.set_trigger_mode("SINGLE")
        assert scope.get_trigger_status() == "Trig'd"


class TestValidation:
    """Bad arguments are rejected before anything goes on the wire."""

    @pytest.mark.parametrize("channel", [0, 5, -1])
    def test_bad_channel(
        self, scope: SiglentSDS1000XE, sim: SimulatedTransport, channel: int
    ) -> None:
        sent_before = len(sim.commands)
        with pytest.raises(ValueError, match="channel"):
            scope.set_vertical_scale(channel, 1.0)
        assert len(sim.commands) == sent_before

    @pytest.mark.parametrize(
        "action",
        [
            lambda s: s.set_coupling(1, "AC"),
            lambda s: s.set_trigger_mode("auto"),
            lambda s: s.set_horizontal_scale("3MS"),
        ],
    )
    def test_bad_enum(
        self, scope: SiglentSDS1000XE, sim: SimulatedTransport, action: Action
    ) -> None:
        sent_before = len(sim.commands)
        with pytest.raises(ValueError):
            action(scope)
        assert len(sim.commands) == sent_before


def test_context_manager_connects_and_closes(sim: SimulatedTransport) -> None:
    with SiglentSDS1000XE(sim):
        assert sim.connected
    assert not sim.connected
