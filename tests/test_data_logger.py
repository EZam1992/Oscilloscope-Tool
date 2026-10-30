from __future__ import annotations

import csv
from pathlib import Path

import pytest

from osci_tool.logger import data_logger


@pytest.fixture
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    target = tmp_path / "data"
    monkeypatch.setattr(data_logger, "_DATA_DIR", target)
    return target


def test_writes_csv_with_header_and_points(data_dir: Path) -> None:
    points = [(-7e-3, 0.0), (-6.999e-3, 0.02), (-6.998e-3, -0.5)]

    path = data_logger.save_waveform(points, channel=2)

    assert path.parent == data_dir
    assert path.name.startswith("waveform_C2_") and path.suffix == ".csv"
    with path.open(newline="") as f:
        rows = list(csv.reader(f))
    assert rows[0] == ["time_s", "voltage_v"]
    assert [(float(t), float(v)) for t, v in rows[1:]] == points


def test_creates_missing_data_dir(data_dir: Path) -> None:
    assert not data_dir.exists()
    data_logger.save_waveform([(0.0, 0.0)], channel=1)
    assert data_dir.is_dir()


def test_empty_capture_writes_header_only(data_dir: Path) -> None:
    path = data_logger.save_waveform([], channel=1)
    assert path.read_text().splitlines() == ["time_s,voltage_v"]
