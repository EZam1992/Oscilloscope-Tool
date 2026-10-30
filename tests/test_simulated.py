"""The simulator is test infrastructure; these checks keep it honest."""

from __future__ import annotations

import pytest

from osci_tool.transport.simulated import BANNER, SimulatedTransport


def test_banner_on_first_reply_only(sim: SimulatedTransport) -> None:
    sim.connect()
    assert sim.query("*OPC?").startswith(BANNER)
    assert sim.query("*OPC?") == "1"


def test_banner_returns_after_reconnect(sim: SimulatedTransport) -> None:
    sim.connect()
    sim.query("*OPC?")
    sim.close()
    sim.connect()
    assert sim.query("*OPC?").startswith(BANNER)


def test_requires_connection(sim: SimulatedTransport) -> None:
    with pytest.raises(RuntimeError):
        sim.query("*IDN?")


@pytest.mark.parametrize("command", ["C1:BOGUS 1", "FOO?", "C5:VDIV?"])
def test_unknown_commands_fail_loudly(sim: SimulatedTransport, command: str) -> None:
    sim.connect()
    with pytest.raises(ValueError):
        if command.endswith("?"):
            sim.query(command)
        else:
            sim.write(command)


def test_binary_block_framing(sim: SimulatedTransport) -> None:
    sim.connect()
    sim.set_waveform_codes(1, [0, -1, 127])
    assert sim.query_binary_block("C1:WF? DAT2") == b"C1:WF DAT2,#13\x00\xff\x7f\n\n"


def test_rejects_out_of_range_codes(sim: SimulatedTransport) -> None:
    with pytest.raises(ValueError):
        sim.set_waveform_codes(1, [128])
