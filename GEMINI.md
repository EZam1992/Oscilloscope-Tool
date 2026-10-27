# Oscilloscope-Tool — project context

Python tool for controlling a **Siglent SDS1104X-E** oscilloscope over LAN with raw SCPI. Planned end state: a desktop GUI with live waveform view, channel/trigger/timebase controls, a test-sequence runner, and capture export. Owner is an avionics/embedded V&V engineer; treat this as test-equipment automation, not a toy.

## Hardware and network

- Scope: Siglent SDS1104X-E, firmware 8.3.6.1.37R8, 4 channels.
- Link: raw TCP socket on **port 5024** (Siglent's telnet-style SCPI port). Not VISA, not port 5025.
- IP: **static, 192.168.1.110**. DHCP is disabled on the scope. The last-known IP is also cached at `~/.osci_tool/scope_host.txt`, outside the repo on purpose because it belongs to the bench, not the code.
- Quick check from a shell: `printf '*IDN?\n' | nc -w 2 192.168.1.110 5024`

## Environment

- Python `>=3.11` per `pyproject.toml`; the local `.venv` runs **Python 3.14**.
- Package is installed editable in `.venv` (`osci-tool 0.1.0`). Run things with `.venv/bin/python`.
- **No runtime dependencies yet**; transport is stdlib `socket`.
- `ruff` and `pytest` are **not installed** in `.venv` yet. Install with `.venv/bin/python -m pip install ruff pytest`.
- Owner's conventions: type hints on every signature, `ruff format` + `ruff check`, `pathlib` over `os.path`, `logging` over `print`, tests in `tests/test_<module>.py`.

## Layout (src-layout)

```
src/osci_tool/
  transport/scpi_lan.py          ScpiLanTransport: connect/close/write/query/query_binary_block
  driver/siglent_sds1000xe.py    SiglentSDS1000XE: semantic scope API on top of the transport
  discovery.py                   resolve_scope_host(): cached IP first, else /24 sweep on :5024
  sequencer/steps.py             Step protocol, StepResult, three step types
  sequencer/sequencer.py         TestSequencer.run(scope) -> list[StepResult]
  logger/data_logger.py          save_waveform(points, channel) -> CSV in repo-root data/
  controller/                    empty (not started)
  gui/                           empty (not started)
tests/                           only __init__.py, no tests yet
data/                            gitignored capture output
```

## Architecture rules (from README; keep them)

1. **Transport and driver are separate layers.** The driver speaks scope semantics (`set_trigger_level(1, 2.5)`); the transport only moves strings and bytes. USB/VISA would be a new transport, with no driver changes.
2. **Network I/O never runs on the GUI thread.** Live polling belongs on a worker thread owned by the App Controller.
3. **Sequencer doesn't own persistence.** Steps produce `StepResult`s; the logger decides where they go.

## Status

Done and verified live against the scope:
- Transport, including IEEE 488.2 definite-length binary blocks (`#<n><len><payload>`).
- Driver: `identify`, channel on/off, vertical scale/offset/coupling, trigger level/mode/source (edge only), timebase, sample rate, raw and scaled waveform pull.
- Discovery with IP cache and subnet-sweep fallback.
- Sequencer steps: `ChannelConfigRoundTrip`, `WaveformAmplitudeInRange`, `TriggerFiresWithinTimeout`.
- Data logger: CSV per capture.

Not started:
- App Controller.
- GUI (planned: PyQt/PySide + pyqtgraph).
- HDF5 export and run history.
- Automated tests.

## SDS1104X-E protocol gotchas (already handled; don't "fix" them away)

- **The first reply includes a banner.** `Welcome to the SCPI instrument 'Siglent SDS1104X-E'` comes before the first response, and replies can begin with a `>>` prompt and NUL bytes. `SiglentSDS1000XE._strip_framing()` removes all three.
- **Every `_write()` is followed by `*OPC?`.** Without it, a write followed immediately by a query raced and returned stale values.
- **Replies echo the command header,** e.g. `C1:VDIV 5.00E-01V`. Parsers take the last space-separated token and strip unit suffixes (`V`, `S`, `Sa/s`).
- **Waveform scaling for `C<n>:WF? DAT2`:** each byte is signed int8, `volts = code * (vdiv / 25) - offset`. Time starts at `-(tdiv * 14 / 2)` because the screen is 14 horizontal divisions, and advances by `1 / sample_rate`.
- **The scope quantizes scale and offset.** Readback may not equal the request exactly, so round-trip checks use a tolerance (default 0.01).
- **`set_trigger_source()` requires the channel to be enabled first;** it raises otherwise. The command form is `TRSE EDGE,SR,C<n>,HT,OFF`.
- **Valid enums are frozensets in the driver:** coupling `A1M A50 D1M D50 GND`; trigger mode `AUTO NORM SINGLE STOP`; timebase strings `1NS` … `100S`.
- **`SAST?` returns `Trig'd`** once a SINGLE trigger has fired.

## Known rough edges

- `data_logger._DATA_DIR` is `parents[3]` from the module file. That only works for the editable install and points to the wrong place if the package is installed normally.
- `get_waveform()` sends four extra queries (VDIV, OFST, TDIV, SARA) per capture. That's fine for single shots but will matter for live polling.
- No retry or reconnect logic in the transport yet; a dropped socket raises `ConnectionError`.
