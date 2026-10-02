from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Protocol

from osci_tool.driver.siglent_sds1000xe import SiglentSDS1000XE

_TRIGGERED_STATUS = "Trig'd"


@dataclass(frozen=True)
class StepResult:
    name: str
    passed: bool
    detail: str


class Step(Protocol):
    name: str

    def run(self, scope: SiglentSDS1000XE) -> StepResult: ...


@dataclass
class ChannelConfigRoundTrip:
    """Set vertical scale/offset/coupling on a channel, read back, compare.

    The scope quantizes/clamps scale and offset to its own step resolution
    (confirmed during driver development), so the readback won't always
    equal the requested value exactly -- `tolerance` absorbs that.
    """

    channel: int
    vertical_scale: float
    offset: float
    coupling: str
    tolerance: float = 0.01
    name: str = "channel_config_round_trip"

    def run(self, scope: SiglentSDS1000XE) -> StepResult:
        scope.set_vertical_scale(self.channel, self.vertical_scale)
        scope.set_offset(self.channel, self.offset)
        scope.set_coupling(self.channel, self.coupling)

        actual_scale = scope.get_vertical_scale(self.channel)
        actual_offset = scope.get_offset(self.channel)
        actual_coupling = scope.get_coupling(self.channel)

        passed = (
            math.isclose(actual_scale, self.vertical_scale, abs_tol=self.tolerance)
            and math.isclose(actual_offset, self.offset, abs_tol=self.tolerance)
            and actual_coupling == self.coupling
        )
        detail = (
            f"scale={actual_scale} (requested {self.vertical_scale}), "
            f"offset={actual_offset} (requested {self.offset}), "
            f"coupling={actual_coupling} (requested {self.coupling})"
        )
        return StepResult(self.name, passed, detail)


@dataclass
class WaveformAmplitudeInRange:
    """Capture a waveform and check its min/max voltage falls within range."""

    channel: int
    min_volts: float
    max_volts: float
    name: str = "waveform_amplitude_in_range"

    def run(self, scope: SiglentSDS1000XE) -> StepResult:
        points = scope.get_waveform(self.channel)
        voltages = [voltage for _, voltage in points]
        v_min, v_max = min(voltages), max(voltages)

        passed = v_min >= self.min_volts and v_max <= self.max_volts
        detail = (
            f"measured [{v_min:.4f}, {v_max:.4f}]V, "
            f"expected within [{self.min_volts}, {self.max_volts}]V"
        )
        return StepResult(self.name, passed, detail)


@dataclass
class TriggerFiresWithinTimeout:
    """Arm a single trigger and poll SAST? until it fires or times out."""

    timeout_s: float
    poll_interval_s: float = 0.1
    name: str = "trigger_fires_within_timeout"

    def run(self, scope: SiglentSDS1000XE) -> StepResult:
        scope.set_trigger_mode("SINGLE")
        deadline = time.monotonic() + self.timeout_s

        status = scope.get_trigger_status()
        while time.monotonic() < deadline:
            if status == _TRIGGERED_STATUS:
                return StepResult(self.name, True, f"status={status!r}")
            time.sleep(self.poll_interval_s)
            status = scope.get_trigger_status()

        passed = status == _TRIGGERED_STATUS
        detail = f"status={status!r} after {self.timeout_s}s timeout"
        return StepResult(self.name, passed, detail)
