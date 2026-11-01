"""App Controller implementation.

Coordinates between UI and Scope driver:
- Runs live waveform and status polling on a background worker thread.
- Guarantees network I/O never blocks the GUI thread.
- Dispatches test sequences and emits results.
- Supports both hardware LAN transport and SimulatedTransport.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from osci_tool.discovery import resolve_scope_host
from osci_tool.driver.siglent_sds1000xe import SiglentSDS1000XE
from osci_tool.sequencer.sequencer import TestSequencer
from osci_tool.sequencer.steps import Step, StepResult
from osci_tool.transport.base import Transport
from osci_tool.transport.scpi_lan import ScpiLanTransport
from osci_tool.transport.simulated import SimulatedTransport

logger = logging.getLogger(__name__)


class ConnectionState(Enum):
    DISCONNECTED = "DISCONNECTED"
    CONNECTING = "CONNECTING"
    CONNECTED = "CONNECTED"
    ERROR = "ERROR"


@dataclass
class ChannelSettings:
    enabled: bool = False
    vdiv: float = 1.0
    offset: float = 0.0
    coupling: str = "D1M"


@dataclass
class ScopeSnapshot:
    """Thread-safe snapshot of scope state for the UI."""

    connection_state: ConnectionState = ConnectionState.DISCONNECTED
    scope_id: str = ""
    channels: dict[int, ChannelSettings] = field(default_factory=dict)
    timebase_div: float = 1e-3
    sample_rate: float = 1e9
    trigger_mode: str = "AUTO"
    trigger_source: int = 1
    trigger_level: float = 0.0
    trigger_status: str = ""
    waveforms: dict[int, list[tuple[float, float]]] = field(default_factory=dict)


class AppController:
    def __init__(
        self,
        transport: Transport | None = None,
        poll_interval_s: float = 0.1,
    ) -> None:
        self._transport = transport
        self._driver: SiglentSDS1000XE | None = None
        self._poll_interval_s = poll_interval_s

        self._lock = threading.RLock()
        self._worker_thread: threading.Thread | None = None
        self._stop_event = threading.Event()

        self._active_channels: set[int] = {1}
        self._state = ScopeSnapshot()

        # Callbacks for UI updates (invoked thread-safely or via signals)
        self.on_state_changed: Callable[[ScopeSnapshot], None] | None = None
        self.on_waveform_updated: (
            Callable[[int, list[tuple[float, float]]], None] | None
        ) = None
        self.on_sequence_completed: Callable[[list[StepResult]], None] | None = None
        self.on_error: Callable[[str], None] | None = None

    @property
    def is_connected(self) -> bool:
        with self._lock:
            return self._state.connection_state == ConnectionState.CONNECTED

    @property
    def snapshot(self) -> ScopeSnapshot:
        with self._lock:
            return self._state

    def connect(self, host: str | None = None, use_simulated: bool = False) -> None:
        """Connect to the scope in the background or simulated mode."""
        with self._lock:
            if self._state.connection_state in (
                ConnectionState.CONNECTED,
                ConnectionState.CONNECTING,
            ):
                return
            self._state.connection_state = ConnectionState.CONNECTING
            self._notify_state()

        def _do_connect() -> None:
            try:
                if use_simulated:
                    transport: Transport = SimulatedTransport()
                elif self._transport is not None:
                    transport = self._transport
                else:
                    target_host = host if host else resolve_scope_host()
                    transport = ScpiLanTransport(host=target_host)

                driver = SiglentSDS1000XE(transport)
                driver.connect()
                scope_id = driver.identify()

                with self._lock:
                    self._transport = transport
                    self._driver = driver
                    self._state.connection_state = ConnectionState.CONNECTED
                    self._state.scope_id = scope_id
                    # Read initial setup
                    self._refresh_scope_state_locked()
                    self._notify_state()

                # Start polling loop
                self._start_worker()

            except Exception as e:
                logger.exception("Connection failed")
                with self._lock:
                    self._state.connection_state = ConnectionState.ERROR
                    self._notify_state()
                if self.on_error:
                    self.on_error(str(e))

        threading.Thread(target=_do_connect, daemon=True).start()

    def disconnect(self) -> None:
        self._stop_worker()
        with self._lock:
            if self._driver is not None:
                try:
                    self._driver.close()
                except Exception:
                    logger.exception("Error closing driver")
                self._driver = None
            self._state.connection_state = ConnectionState.DISCONNECTED
            self._notify_state()

    def set_active_channels(self, channels: Sequence[int]) -> None:
        with self._lock:
            self._active_channels = set(channels)

    def set_channel_enabled(self, channel: int, enabled: bool) -> None:
        def _task(drv: SiglentSDS1000XE) -> None:
            drv.set_channel(channel, enabled)
            with self._lock:
                if channel not in self._state.channels:
                    self._state.channels[channel] = ChannelSettings()
                self._state.channels[channel].enabled = enabled
                self._notify_state()

        self._execute_async(_task)

    def set_vertical_scale(self, channel: int, volts_per_div: float) -> None:
        def _task(drv: SiglentSDS1000XE) -> None:
            drv.set_vertical_scale(channel, volts_per_div)
            val = drv.get_vertical_scale(channel)
            with self._lock:
                if channel not in self._state.channels:
                    self._state.channels[channel] = ChannelSettings()
                self._state.channels[channel].vdiv = val
                self._notify_state()

        self._execute_async(_task)

    def set_offset(self, channel: int, offset_volts: float) -> None:
        def _task(drv: SiglentSDS1000XE) -> None:
            drv.set_offset(channel, offset_volts)
            val = drv.get_offset(channel)
            with self._lock:
                if channel not in self._state.channels:
                    self._state.channels[channel] = ChannelSettings()
                self._state.channels[channel].offset = val
                self._notify_state()

        self._execute_async(_task)

    def set_coupling(self, channel: int, coupling: str) -> None:
        def _task(drv: SiglentSDS1000XE) -> None:
            drv.set_coupling(channel, coupling)
            val = drv.get_coupling(channel)
            with self._lock:
                if channel not in self._state.channels:
                    self._state.channels[channel] = ChannelSettings()
                self._state.channels[channel].coupling = val
                self._notify_state()

        self._execute_async(_task)

    def set_timebase(self, timebase_div_str: str) -> None:
        def _task(drv: SiglentSDS1000XE) -> None:
            drv.set_horizontal_scale(timebase_div_str)
            val = drv.get_horizontal_scale()
            with self._lock:
                self._state.timebase_div = val
                self._notify_state()

        self._execute_async(_task)

    def set_trigger(
        self,
        source: int | None = None,
        level_volts: float | None = None,
        mode: str | None = None,
    ) -> None:
        def _task(drv: SiglentSDS1000XE) -> None:
            if source is not None:
                drv.set_trigger_source(source)
            if level_volts is not None:
                trig_src = source if source is not None else drv.get_trigger_source()
                drv.set_trigger_level(trig_src, level_volts)
            if mode is not None:
                drv.set_trigger_mode(mode)

            with self._lock:
                if source is not None:
                    self._state.trigger_source = source
                if level_volts is not None:
                    self._state.trigger_level = level_volts
                if mode is not None:
                    self._state.trigger_mode = mode
                self._notify_state()

        self._execute_async(_task)

    def run_sequence(self, steps: list[Step]) -> None:
        """Run a test sequence asynchronously on the worker thread."""

        def _task(drv: SiglentSDS1000XE) -> None:
            sequencer = TestSequencer(steps)
            results = sequencer.run(drv)
            if self.on_sequence_completed:
                self.on_sequence_completed(results)

        self._execute_async(_task)

    # -- Internal worker & polling ------------------------------------------

    def _start_worker(self) -> None:
        self._stop_event.clear()
        self._worker_thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._worker_thread.start()

    def _stop_worker(self) -> None:
        self._stop_event.set()
        if self._worker_thread and self._worker_thread.is_alive():
            self._worker_thread.join(timeout=2.0)
        self._worker_thread = None

    def _poll_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                with self._lock:
                    drv = self._driver
                    if (
                        drv is None
                        or self._state.connection_state != ConnectionState.CONNECTED
                    ):
                        break

                    channels_to_poll = list(self._active_channels)
                    # Pull trigger status
                    try:
                        self._state.trigger_status = drv.get_trigger_status()
                    except (OSError, RuntimeError, ValueError) as e:
                        logger.debug("Failed polling trigger status: %s", e)

                # Poll waveforms
                for ch in channels_to_poll:
                    if self._stop_event.is_set():
                        return
                    with self._lock:
                        if self._driver is None:
                            return
                        try:
                            wf = self._driver.get_waveform(ch)
                            self._state.waveforms[ch] = wf
                        except (OSError, RuntimeError, ValueError) as e:
                            logger.debug("Waveform pull error on C%d: %s", ch, e)
                            continue

                    if self.on_waveform_updated:
                        self.on_waveform_updated(ch, wf)

            except (OSError, RuntimeError, ConnectionError) as e:
                logger.warning("Error in poll loop: %s", e)
                with self._lock:
                    self._state.connection_state = ConnectionState.ERROR
                    self._notify_state()
                if self.on_error:
                    self.on_error(str(e))
                break

            time.sleep(self._poll_interval_s)

    def _refresh_scope_state_locked(self) -> None:
        """Populate initial scope settings while holding self._lock."""
        if not self._driver:
            return
        drv = self._driver
        try:
            self._state.timebase_div = drv.get_horizontal_scale()
            self._state.sample_rate = drv.get_sample_rate()
            self._state.trigger_mode = drv.get_trigger_mode()
            self._state.trigger_source = drv.get_trigger_source()
            self._state.trigger_level = drv.get_trigger_level(
                self._state.trigger_source
            )
            self._state.trigger_status = drv.get_trigger_status()

            for ch in (1, 2, 3, 4):
                is_on = drv.is_channel_enabled(ch)
                vdiv = drv.get_vertical_scale(ch)
                offset = drv.get_offset(ch)
                cpl = drv.get_coupling(ch)
                self._state.channels[ch] = ChannelSettings(
                    enabled=is_on,
                    vdiv=vdiv,
                    offset=offset,
                    coupling=cpl,
                )
        except Exception:
            logger.exception("Failed refreshing scope state")

    def _execute_async(self, fn: Callable[[SiglentSDS1000XE], Any]) -> None:
        def _runner() -> None:
            with self._lock:
                drv = self._driver
                if (
                    drv is None
                    or self._state.connection_state != ConnectionState.CONNECTED
                ):
                    return
                try:
                    fn(drv)
                except Exception as e:
                    logger.exception("Error executing scope command")
                    if self.on_error:
                        self.on_error(str(e))

        threading.Thread(target=_runner, daemon=True).start()

    def _notify_state(self) -> None:
        if self.on_state_changed:
            self.on_state_changed(self._state)
