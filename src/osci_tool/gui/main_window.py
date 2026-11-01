"""Oscilloscope Desktop GUI using PySide6 and pyqtgraph.

Features:
- Live waveform plot for Channels 1 to 4 with color coding.
- Connection bar (LAN IP resolution / Simulated Demo toggle).
- Channel Controls (enable, V/div, offset, coupling).
- Timebase Controls (scale, sample rate indicator).
- Trigger Controls (source, mode, level).
- Sequence Runner Panel (execute automated tests and view pass/fail results).
- Capture / Export toolbar (CSV and HDF5).
"""

from __future__ import annotations

import sys
from typing import Any

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from osci_tool.controller.app_controller import (
    AppController,
    ConnectionState,
    ScopeSnapshot,
)
from osci_tool.logger import data_logger
from osci_tool.sequencer.steps import (
    ChannelConfigRoundTrip,
    Step,
    StepResult,
    TriggerFiresWithinTimeout,
    WaveformAmplitudeInRange,
)

CHANNEL_COLORS = {
    1: "#f1c40f",  # Yellow
    2: "#e74c3c",  # Red / Pink
    3: "#3498db",  # Blue
    4: "#2ecc71",  # Green
}

# fmt: off
TIMEBASE_CHOICES = [
    "1NS", "2NS", "5NS", "10NS", "20NS", "50NS", "100NS", "200NS", "500NS",
    "1US", "2US", "5US", "10US", "20US", "50US", "100US", "200US", "500US",
    "1MS", "2MS", "5MS", "10MS", "20MS", "50MS", "100MS", "200MS", "500MS",
    "1S", "2S", "5S", "10S", "20S", "50S", "100S",
]
# fmt: on


class ControllerBridge(QObject):
    """Bridge background AppController callbacks onto Qt's main event loop signals."""

    state_changed = Signal(object)
    waveform_updated = Signal(int, object)
    sequence_completed = Signal(object)
    error_occurred = Signal(str)

    def __init__(self, controller: AppController) -> None:
        super().__init__()
        self.controller = controller
        self.controller.on_state_changed = lambda s: self.state_changed.emit(s)
        self.controller.on_waveform_updated = lambda ch, pts: (
            self.waveform_updated.emit(ch, pts)
        )
        self.controller.on_sequence_completed = lambda r: self.sequence_completed.emit(
            r
        )
        self.controller.on_error = lambda err: self.error_occurred.emit(err)


class MainWindow(QMainWindow):
    def __init__(self, controller: AppController | None = None) -> None:
        super().__init__()
        self.setWindowTitle("Siglent SDS1104X-E Scope Controller")
        self.resize(1280, 800)

        self.controller = controller or AppController()
        self.bridge = ControllerBridge(self.controller)

        self._plot_curves: dict[int, pg.PlotDataItem] = {}
        self._current_waveforms: dict[int, list[tuple[float, float]]] = {}
        self._updating_ui = False

        self._init_ui()
        self._connect_signals()

    def _init_ui(self) -> None:
        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        root_layout = QVBoxLayout(main_widget)

        # 1. Connection Header
        conn_box = QGroupBox("Scope Connection")
        conn_layout = QHBoxLayout(conn_box)

        self.ip_input = QLineEdit("192.168.1.110")
        self.ip_input.setPlaceholderText("Scope IP or blank for auto-discovery")
        self.sim_checkbox = QCheckBox("Simulated Demo Scope")
        self.connect_btn = QPushButton("Connect")
        self.status_label = QLabel("Status: Disconnected")
        self.status_label.setStyleSheet("font-weight: bold; color: gray;")

        conn_layout.addWidget(QLabel("Target IP:"))
        conn_layout.addWidget(self.ip_input)
        conn_layout.addWidget(self.sim_checkbox)
        conn_layout.addWidget(self.connect_btn)
        conn_layout.addWidget(self.status_label)
        conn_layout.addStretch()

        root_layout.addWidget(conn_box)

        # 2. Splitter: Left (Controls + Sequencer) & Right (Plot)
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # Left Panel
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)

        # Channel Controls Box
        ch_box = QGroupBox("Channel Controls")
        ch_layout = QVBoxLayout(ch_box)
        self.channel_controls: dict[int, dict[str, Any]] = {}

        for ch in (1, 2, 3, 4):
            c_row = QHBoxLayout()
            cb = QCheckBox(f"CH {ch}")
            cb.setStyleSheet(f"color: {CHANNEL_COLORS[ch]}; font-weight: bold;")
            if ch == 1:
                cb.setChecked(True)

            vdiv_spin = QDoubleSpinBox()
            vdiv_spin.setRange(0.001, 100.0)
            vdiv_spin.setValue(1.0)
            vdiv_spin.setSuffix(" V/div")

            ofst_spin = QDoubleSpinBox()
            ofst_spin.setRange(-50.0, 50.0)
            ofst_spin.setValue(0.0)
            ofst_spin.setSuffix(" V")

            cpl_combo = QComboBox()
            cpl_combo.addItems(["D1M", "A1M", "GND"])

            c_row.addWidget(cb)
            c_row.addWidget(vdiv_spin)
            c_row.addWidget(ofst_spin)
            c_row.addWidget(cpl_combo)
            ch_layout.addLayout(c_row)

            self.channel_controls[ch] = {
                "checkbox": cb,
                "vdiv": vdiv_spin,
                "offset": ofst_spin,
                "coupling": cpl_combo,
            }

        left_layout.addWidget(ch_box)

        # Timebase & Trigger Box
        tb_box = QGroupBox("Timebase & Trigger")
        tb_layout = QVBoxLayout(tb_box)

        tb_row = QHBoxLayout()
        self.timebase_combo = QComboBox()
        self.timebase_combo.addItems(TIMEBASE_CHOICES)
        self.timebase_combo.setCurrentText("1MS")
        self.sample_rate_label = QLabel("SARA: --")

        tb_row.addWidget(QLabel("Time/div:"))
        tb_row.addWidget(self.timebase_combo)
        tb_row.addWidget(self.sample_rate_label)
        tb_layout.addLayout(tb_row)

        trig_row = QHBoxLayout()
        self.trig_src_combo = QComboBox()
        self.trig_src_combo.addItems(["C1", "C2", "C3", "C4"])
        self.trig_mode_combo = QComboBox()
        self.trig_mode_combo.addItems(["AUTO", "NORM", "SINGLE", "STOP"])
        self.trig_lvl_spin = QDoubleSpinBox()
        self.trig_lvl_spin.setRange(-20.0, 20.0)
        self.trig_lvl_spin.setValue(0.0)
        self.trig_lvl_spin.setSuffix(" V")

        trig_row.addWidget(QLabel("Trigger:"))
        trig_row.addWidget(self.trig_src_combo)
        trig_row.addWidget(self.trig_mode_combo)
        trig_row.addWidget(self.trig_lvl_spin)
        tb_layout.addLayout(trig_row)

        left_layout.addWidget(tb_box)

        # Export Controls Box
        exp_box = QGroupBox("Capture & Export")
        exp_layout = QHBoxLayout(exp_box)
        self.export_csv_btn = QPushButton("Export CSV (Active)")
        self.export_h5_btn = QPushButton("Export HDF5 (All)")
        exp_layout.addWidget(self.export_csv_btn)
        exp_layout.addWidget(self.export_h5_btn)
        left_layout.addWidget(exp_box)

        # Sequencer Panel Box
        seq_box = QGroupBox("Automated Test Sequencer")
        seq_layout = QVBoxLayout(seq_box)
        self.run_seq_btn = QPushButton("Run Verification Sequence")
        self.seq_table = QTableWidget(0, 3)
        self.seq_table.setHorizontalHeaderLabels(["Step Name", "Status", "Detail"])
        self.seq_table.horizontalHeader().setStretchLastSection(True)

        seq_layout.addWidget(self.run_seq_btn)
        seq_layout.addWidget(self.seq_table)
        left_layout.addWidget(seq_box)

        splitter.addWidget(left_widget)

        # Right Panel (Waveform Graph)
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)

        self.plot_widget = pg.PlotWidget()
        self.plot_widget.setBackground("#111111")
        self.plot_widget.showGrid(x=True, y=True, alpha=0.3)
        self.plot_widget.setLabel("left", "Voltage", units="V")
        self.plot_widget.setLabel("bottom", "Time", units="s")

        for ch in (1, 2, 3, 4):
            pen = pg.mkPen(color=CHANNEL_COLORS[ch], width=1.5)
            self._plot_curves[ch] = self.plot_widget.plot(name=f"CH{ch}", pen=pen)

        right_layout.addWidget(self.plot_widget)
        splitter.addWidget(right_widget)

        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 3)
        root_layout.addWidget(splitter)

    def _connect_signals(self) -> None:
        self.connect_btn.clicked.connect(self._toggle_connection)
        self.run_seq_btn.clicked.connect(self._run_test_sequence)
        self.export_csv_btn.clicked.connect(self._export_csv)
        self.export_h5_btn.clicked.connect(self._export_hdf5)

        self.bridge.state_changed.connect(self._on_state_changed)
        self.bridge.waveform_updated.connect(self._on_waveform_updated)
        self.bridge.sequence_completed.connect(self._on_sequence_completed)
        self.bridge.error_occurred.connect(self._on_error)

        for ch, controls in self.channel_controls.items():
            controls["checkbox"].toggled.connect(
                lambda val, c=ch: self._on_ch_enable_changed(c, val)
            )
            controls["vdiv"].editingFinished.connect(
                lambda c=ch: self._on_vdiv_changed(c)
            )
            controls["offset"].editingFinished.connect(
                lambda c=ch: self._on_offset_changed(c)
            )
            controls["coupling"].currentTextChanged.connect(
                lambda val, c=ch: self._on_cpl_changed(c, val)
            )

        self.timebase_combo.currentTextChanged.connect(self._on_timebase_changed)
        self.trig_src_combo.currentTextChanged.connect(self._on_trigger_changed)
        self.trig_mode_combo.currentTextChanged.connect(self._on_trigger_changed)
        self.trig_lvl_spin.editingFinished.connect(self._on_trigger_changed)

    def _toggle_connection(self) -> None:
        if self.controller.is_connected:
            self.controller.disconnect()
        else:
            host = self.ip_input.text().strip() or None
            use_sim = self.sim_checkbox.isChecked()
            self.controller.connect(host=host, use_simulated=use_sim)

    def _on_ch_enable_changed(self, channel: int, enabled: bool) -> None:
        if self._updating_ui:
            return
        self.controller.set_channel_enabled(channel, enabled)
        active = [
            c
            for c, ctrl in self.channel_controls.items()
            if ctrl["checkbox"].isChecked()
        ]
        self.controller.set_active_channels(active)
        if not enabled and channel in self._plot_curves:
            self._plot_curves[channel].clear()

    def _on_vdiv_changed(self, channel: int) -> None:
        if self._updating_ui:
            return
        vdiv = self.channel_controls[channel]["vdiv"].value()
        self.controller.set_vertical_scale(channel, vdiv)

    def _on_offset_changed(self, channel: int) -> None:
        if self._updating_ui:
            return
        ofst = self.channel_controls[channel]["offset"].value()
        self.controller.set_offset(channel, ofst)

    def _on_cpl_changed(self, channel: int, coupling: str) -> None:
        if self._updating_ui:
            return
        self.controller.set_coupling(channel, coupling)

    def _on_timebase_changed(self, timebase_str: str) -> None:
        if self._updating_ui:
            return
        self.controller.set_timebase(timebase_str)

    def _on_trigger_changed(self) -> None:
        if self._updating_ui:
            return
        src_str = self.trig_src_combo.currentText()
        src = int(src_str.lstrip("C"))
        mode = self.trig_mode_combo.currentText()
        lvl = self.trig_lvl_spin.value()
        self.controller.set_trigger(source=src, level_volts=lvl, mode=mode)

    def _on_state_changed(self, state: ScopeSnapshot) -> None:
        self._updating_ui = True
        try:
            if state.connection_state == ConnectionState.CONNECTED:
                self.status_label.setText(f"Connected: {state.scope_id}")
                self.status_label.setStyleSheet("color: green; font-weight: bold;")
                self.connect_btn.setText("Disconnect")
            elif state.connection_state == ConnectionState.CONNECTING:
                self.status_label.setText("Connecting...")
                self.status_label.setStyleSheet("color: orange; font-weight: bold;")
            elif state.connection_state == ConnectionState.ERROR:
                self.status_label.setText("Connection Error")
                self.status_label.setStyleSheet("color: red; font-weight: bold;")
                self.connect_btn.setText("Connect")
            else:
                self.status_label.setText("Status: Disconnected")
                self.status_label.setStyleSheet("color: gray; font-weight: bold;")
                self.connect_btn.setText("Connect")

            if state.sample_rate:
                self.sample_rate_label.setText(
                    f"SARA: {state.sample_rate / 1e6:.1f} MSa/s"
                )

            # Update channel values
            for ch, settings in state.channels.items():
                if ch in self.channel_controls:
                    ctrl = self.channel_controls[ch]
                    ctrl["checkbox"].setChecked(settings.enabled)
                    ctrl["vdiv"].setValue(settings.vdiv)
                    ctrl["offset"].setValue(settings.offset)
                    ctrl["coupling"].setCurrentText(settings.coupling)

        finally:
            self._updating_ui = False

    def _on_waveform_updated(
        self, channel: int, points: list[tuple[float, float]]
    ) -> None:
        self._current_waveforms[channel] = points
        if channel in self._plot_curves and points:
            arr = np.array(points)
            times = arr[:, 0]
            volts = arr[:, 1]
            self._plot_curves[channel].setData(times, volts)

    def _export_csv(self) -> None:
        active_ch = [
            c
            for c, ctrl in self.channel_controls.items()
            if ctrl["checkbox"].isChecked()
        ]
        if not active_ch:
            QMessageBox.warning(self, "Export CSV", "No channel currently active.")
            return

        ch = active_ch[0]
        points = self._current_waveforms.get(ch, [])
        if not points:
            QMessageBox.warning(
                self, "Export CSV", f"No waveform data available for CH{ch}."
            )
            return

        out_path = data_logger.save_waveform(points, ch)
        QMessageBox.information(
            self, "Export Complete", f"Waveform saved to:\n{out_path}"
        )

    def _export_hdf5(self) -> None:
        if not self._current_waveforms:
            QMessageBox.warning(self, "Export HDF5", "No waveform data available.")
            return

        meta = {
            "scope_id": self.controller.snapshot.scope_id,
            "sample_rate": self.controller.snapshot.sample_rate,
            "timebase": self.controller.snapshot.timebase_div,
        }
        out_path = data_logger.save_waveform_hdf5(
            self._current_waveforms, metadata=meta
        )
        QMessageBox.information(
            self, "Export Complete", f"HDF5 dataset saved to:\n{out_path}"
        )

    def _run_test_sequence(self) -> None:
        if not self.controller.is_connected:
            QMessageBox.warning(
                self, "Sequencer", "Connect to scope before running tests."
            )
            return

        steps: list[Step] = [
            ChannelConfigRoundTrip(
                channel=1, vertical_scale=1.0, offset=0.0, coupling="D1M"
            ),
            WaveformAmplitudeInRange(channel=1, min_volts=-10.0, max_volts=10.0),
            TriggerFiresWithinTimeout(timeout_s=0.5),
        ]
        self.seq_table.setRowCount(0)
        self.controller.run_sequence(steps)

    def _on_sequence_completed(self, results: list[StepResult]) -> None:
        self.seq_table.setRowCount(len(results))
        for idx, res in enumerate(results):
            self.seq_table.setItem(idx, 0, QTableWidgetItem(res.name))
            status_item = QTableWidgetItem("PASS" if res.passed else "FAIL")
            status_item.setForeground(
                Qt.GlobalColor.green if res.passed else Qt.GlobalColor.red
            )
            self.seq_table.setItem(idx, 1, status_item)
            self.seq_table.setItem(idx, 2, QTableWidgetItem(res.detail))

    def _on_error(self, err: str) -> None:
        self.status_label.setText(f"Error: {err}")
        self.status_label.setStyleSheet("color: red; font-weight: bold;")


def main() -> None:
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
