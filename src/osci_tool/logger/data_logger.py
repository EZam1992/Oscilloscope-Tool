import csv
from datetime import datetime
from pathlib import Path

_DATA_DIR = Path(__file__).resolve().parents[3] / "data"


def save_waveform(points: list[tuple[float, float]], channel: int) -> Path:
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = _DATA_DIR / f"waveform_C{channel}_{timestamp}.csv"
    with path.open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["time_s", "voltage_v"])
        writer.writerows(points)
    return path
