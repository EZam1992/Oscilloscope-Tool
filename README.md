# Oscilloscope-Tool

GUI tool for controlling a Siglent SDS1104X-E oscilloscope over LAN (SCPI).

## Architecture

```mermaid
graph TD
    GUI["GUI Layer (PyQt/PySide + pyqtgraph)<br/>- Live waveform view<br/>- Channel / trigger / timebase control panel<br/>- Test sequence runner panel<br/>- Capture / export controls"]
    APP["App Controller<br/>- Owns scope connection state<br/>- Runs live polling on a worker thread (not main)<br/>- Dispatches test sequences"]
    DRV["Scope Driver (SDS1104X-E)<br/>- Semantic API: set_channel, get_waveform, set_trigger, set_timebase"]
    SEQ["Test Sequencer<br/>- Step definitions<br/>- Pass/fail criteria<br/>- Logs results"]
    TRN["SCPI/LAN Transport<br/>- Raw socket :5024<br/>- cmd/query, retry<br/>- Error checking"]
    LOG["Data Logger<br/>- CSV/HDF5 export<br/>- Run history"]

    GUI --> APP
    APP --> DRV
    APP --> SEQ
    DRV --> TRN
    SEQ --> LOG

    classDef gui fill:#a5d8ff,stroke:#1e1e1e,color:#1e1e1e
    classDef ctrl fill:#ffec99,stroke:#1e1e1e,color:#1e1e1e
    classDef core fill:#b2f2bb,stroke:#1e1e1e,color:#1e1e1e
    classDef io fill:#ffc9c9,stroke:#1e1e1e,color:#1e1e1e
    classDef data fill:#d0bfff,stroke:#1e1e1e,color:#1e1e1e

    class GUI gui
    class APP ctrl
    class DRV core
    class SEQ core
    class TRN io
    class LOG data
```

### Key design calls

1. **Transport is a separate layer from the Scope Driver.** The driver speaks scope semantics (`set_trigger_level(1, 2.5)`); the transport only sends strings / reads responses. This is the layer you swap if USB/VISA gets added later.
2. **Live polling runs on a worker thread.** GUI thread only renders. Network I/O on the main event loop stalls the UI — same problem as a blocking call in an ISR.
3. **Test Sequencer and Data Logger are separate.** Sequencing (what to do) doesn't own persistence (where results go). Sequencer calls the logger, doesn't implement it.

## Status

- [x] Repo scaffolded (`src/` layout, six modules matching the architecture above)
- [x] SCPI/LAN Transport — implemented, verified live against the scope (`*IDN?` round trip working)
- [x] Scope Driver — `identify()`, channel enable/scale/offset/coupling, trigger (level/mode/edge source), timebase, waveform pull, all verified live
- [ ] Test Sequencer
- [x] Data Logger — `save_waveform()` writes captures to gitignored `data/` (CSV/HDF5 export and run history still open)
- [ ] App Controller
- [ ] GUI Layer
