"""Offline stand-in for an SDS1104X-E, speaking the same SCPI as the real scope.

Used by the test suite, and intended as a "demo mode" for the controller/GUI
when the bench scope isn't reachable. It reproduces the quirks the driver has
to handle (see GEMINI.md), so tests exercise the real parsing paths:

- The first reply after connect carries the "Welcome to the SCPI instrument"
  banner.
- Optionally, replies are prefixed with the ">>" prompt and NUL bytes.
- Query replies echo the command header, e.g. ``C1:VDIV 5.00E-01V``.
- ``C<n>:WF? DAT2`` returns an IEEE 488.2 definite-length block of signed
  int8 codes, terminated by ``\\n\\n``.
- Offset can be quantized to mimic the scope's step resolution.

Every command received is appended to ``commands`` so tests can assert on the
exact wire traffic (e.g. that each write is followed by ``*OPC?``).

Unknown commands raise ``ValueError``. The real scope silently ignores them,
but failing loudly catches typos in the driver.
"""

from __future__ import annotations

import math
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Self

IDN_REPLY = "Siglent Technologies,SDS1104X-E,SDSMMSIM000000,8.3.6.1.37R8"
BANNER = "Welcome to the SCPI instrument 'Siglent SDS1104X-E'"
TRIGGERED = "Trig'd"
WAITING = "Ready"

_TDIV_UNITS = {"NS": 1e-9, "US": 1e-6, "MS": 1e-3, "S": 1.0}
_CHANNEL_CMD = re.compile(r"^C([1-4]):(\w+)(\?)?(?:\s+(.*))?$")


@dataclass
class ChannelState:
    enabled: bool = True
    vdiv: float = 1.0
    offset: float = 0.0
    coupling: str = "D1M"
    trigger_level: float = 0.0
    codes: list[int] = field(default_factory=list)


def default_codes(
    points: int = 1000, amplitude: int = 50, cycles: int = 5
) -> list[int]:
    """A sine wave in raw int8 codes, so a fresh simulator returns a plausible trace."""
    return [
        round(amplitude * math.sin(2 * math.pi * cycles * i / points))
        for i in range(points)
    ]


class SimulatedTransport:
    def __init__(
        self,
        *,
        banner: bool = True,
        prompt: bool = False,
        offset_resolution: float = 0.0,
        trigger_after_polls: int | None = 0,
    ) -> None:
        """
        Args:
            banner: Prepend the welcome banner to the first reply after connect.
            prompt: Prefix every reply with ">>" and NUL bytes.
            offset_resolution: If > 0, stored offsets are rounded to this step.
            trigger_after_polls: In SINGLE mode, number of ``SAST?`` polls that
                report ``Ready`` before reporting ``Trig'd``. ``None`` means the
                trigger never fires.
        """
        self._banner = banner
        self._prompt = prompt
        self._offset_resolution = offset_resolution
        self.trigger_after_polls = trigger_after_polls

        self.connected = False
        self.commands: list[str] = []
        self.channels = {n: ChannelState(codes=default_codes()) for n in (1, 2, 3, 4)}
        self.tdiv = 1e-3
        self.sample_rate = 1e9
        self.trigger_mode = "AUTO"
        self.trigger_source = 1
        self._banner_pending = False
        self._sast_polls = 0

    # -- Transport protocol -------------------------------------------------

    def connect(self) -> None:
        self.connected = True
        self._banner_pending = self._banner

    def close(self) -> None:
        self.connected = False

    def write(self, command: str) -> None:
        self._require_connected()
        self.commands.append(command)
        self._handle_write(command)

    def query(self, command: str) -> str:
        self._require_connected()
        self.commands.append(command)
        return self._frame(self._handle_query(command))

    def query_binary_block(self, command: str) -> bytes:
        self._require_connected()
        self.commands.append(command)
        match = re.fullmatch(r"C([1-4]):WF\? DAT2", command)
        if match is None:
            raise ValueError(f"simulator: unsupported binary query {command!r}")
        channel = int(match.group(1))
        payload = bytes(code & 0xFF for code in self.channels[channel].codes)
        length = str(len(payload))
        header = f"C{channel}:WF DAT2,#{len(length)}{length}".encode("ascii")
        return header + payload + b"\n\n"

    # -- Test helpers -------------------------------------------------------

    def set_waveform_codes(self, channel: int, codes: Sequence[int]) -> None:
        if any(not -128 <= c <= 127 for c in codes):
            raise ValueError("codes must be signed int8 (-128..127)")
        self.channels[channel].codes = list(codes)

    # -- Command handling ---------------------------------------------------

    def _require_connected(self) -> None:
        if not self.connected:
            raise RuntimeError("Not connected")

    def _frame(self, reply: str) -> str:
        if self._prompt:
            reply = ">>\x00" + reply
        if self._banner_pending:
            self._banner_pending = False
            reply = f"{BANNER}\n{reply}"
        return reply

    def _handle_write(self, command: str) -> None:
        match = _CHANNEL_CMD.match(command)
        if match and not match.group(3):
            channel, header, value = int(match.group(1)), match.group(2), match.group(4)
            state = self.channels[channel]
            if header == "TRA":
                state.enabled = value == "ON"
            elif header == "VDIV":
                state.vdiv = float(value)
            elif header == "OFST":
                state.offset = self._quantize_offset(float(value))
            elif header == "CPL":
                state.coupling = value
            elif header == "TRLV":
                state.trigger_level = float(value)
            else:
                raise ValueError(f"simulator: unknown write {command!r}")
            return

        header, _, value = command.partition(" ")
        if header == "TDIV":
            self.tdiv = _parse_tdiv(value)
        elif header == "TRMD":
            self.trigger_mode = value
            self._sast_polls = 0
        elif header == "TRSE":
            parts = value.split(",")
            self.trigger_source = int(parts[parts.index("SR") + 1].lstrip("C"))
        else:
            raise ValueError(f"simulator: unknown write {command!r}")

    def _handle_query(self, command: str) -> str:
        if command == "*IDN?":
            return IDN_REPLY
        if command == "*OPC?":
            return "1"

        match = _CHANNEL_CMD.match(command)
        if match and match.group(3):
            channel, header = int(match.group(1)), match.group(2)
            state = self.channels[channel]
            prefix = f"C{channel}:{header}"
            if header == "TRA":
                return f"{prefix} {'ON' if state.enabled else 'OFF'}"
            if header == "VDIV":
                return f"{prefix} {state.vdiv:.2E}V"
            if header == "OFST":
                return f"{prefix} {state.offset:.2E}V"
            if header == "CPL":
                return f"{prefix} {state.coupling}"
            if header == "TRLV":
                return f"{prefix} {state.trigger_level:.2E}V"

        if command == "TDIV?":
            return f"TDIV {self.tdiv:.2E}S"
        if command == "SARA?":
            return f"SARA {self.sample_rate:.2E}Sa/s"
        if command == "TRMD?":
            return f"TRMD {self.trigger_mode}"
        if command == "TRSE?":
            return f"TRSE EDGE,SR,C{self.trigger_source},HT,OFF"
        if command == "SAST?":
            return f"SAST {self._trigger_status()}"
        raise ValueError(f"simulator: unknown query {command!r}")

    def _trigger_status(self) -> str:
        if self.trigger_mode != "SINGLE":
            return "Auto" if self.trigger_mode == "AUTO" else "Stop"
        if self.trigger_after_polls is None:
            return WAITING
        if self._sast_polls >= self.trigger_after_polls:
            return TRIGGERED
        self._sast_polls += 1
        return WAITING

    def _quantize_offset(self, value: float) -> float:
        if self._offset_resolution <= 0:
            return value
        return round(value / self._offset_resolution) * self._offset_resolution

    def __enter__(self) -> Self:
        self.connect()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


def _parse_tdiv(value: str) -> float:
    for suffix in ("NS", "US", "MS", "S"):
        if value.endswith(suffix):
            return float(value.removesuffix(suffix)) * _TDIV_UNITS[suffix]
    raise ValueError(f"simulator: bad TDIV value {value!r}")
