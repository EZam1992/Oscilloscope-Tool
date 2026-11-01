"""Data logger for CSV and HDF5 waveform export and run history tracking."""

from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path
from typing import Any

import h5py
import numpy as np

# Default to repo root data/ if editable install, otherwise ~/.osci_tool/data
_REPO_ROOT = Path(__file__).resolve().parents[3]
_DATA_DIR = (
    _REPO_ROOT / "data"
    if (_REPO_ROOT / "pyproject.toml").exists()
    else Path.home() / ".osci_tool" / "data"
)


def save_waveform(
    points: list[tuple[float, float]],
    channel: int,
    output_dir: Path | None = None,
) -> Path:
    """Save waveform data to CSV."""
    target_dir = output_dir or _DATA_DIR
    target_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = target_dir / f"waveform_C{channel}_{timestamp}.csv"
    with path.open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["time_s", "voltage_v"])
        writer.writerows(points)
    return path


def save_waveform_hdf5(
    waveforms: dict[int, list[tuple[float, float]]],
    metadata: dict[str, Any] | None = None,
    output_dir: Path | None = None,
) -> Path:
    """Save multi-channel waveform capture and run metadata into an HDF5 container."""
    target_dir = output_dir or _DATA_DIR
    target_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = target_dir / f"capture_{timestamp}.h5"

    with h5py.File(path, "w") as f:
        # Save metadata attributes
        if metadata:
            meta_grp = f.create_group("metadata")
            for k, v in metadata.items():
                meta_grp.attrs[k] = str(v)

        # Save channel datasets
        ch_grp = f.create_group("channels")
        for ch, pts in waveforms.items():
            if not pts:
                continue
            arr = np.array(pts, dtype=np.float64)
            dset = ch_grp.create_dataset(f"C{ch}", data=arr, compression="gzip")
            dset.attrs["columns"] = ["time_s", "voltage_v"]

    return path
