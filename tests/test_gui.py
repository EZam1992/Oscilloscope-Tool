from __future__ import annotations

import os

import pytest
from PySide6.QtWidgets import QApplication

from osci_tool.controller.app_controller import AppController
from osci_tool.gui.main_window import MainWindow
from osci_tool.transport.simulated import SimulatedTransport

# Run Qt in offscreen platform mode for headless automated test environments
os.environ["QT_QPA_PLATFORM"] = "offscreen"


@pytest.fixture(scope="session")
def qapp() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_main_window_init(qapp: QApplication) -> None:
    sim = SimulatedTransport()
    controller = AppController(transport=sim)
    window = MainWindow(controller=controller)

    assert window.windowTitle() == "Siglent SDS1104X-E Scope Controller"
    assert 1 in window.channel_controls
    assert 4 in window.channel_controls
    assert window.plot_widget is not None

    window.close()
