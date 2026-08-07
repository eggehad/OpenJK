# OpenJK v0.4.1

OpenJK is an open-source engineering workstation for JK Smart BMS systems over Bluetooth LE.

## v0.4.1 cell-view refinement

The 16 JK BMS cell channels are displayed as 16 parallel-pair blocks representing
32 physical cells: `1|2, 3|4, ... 31|32`.  All blocks remain in one horizontal
row, with a small mechanical midpoint gap between physical cells 16 and 17.

The cell bodies are deliberately neutral.  A segmented color strip directly below
them carries the quantitative color information and uses an approximately 25-second
exponential fade so normal sub-millivolt sample jitter does not flash the display.
The numerical voltage and deviation remain live.

Cell-view keyboard modes:

- **D** — deviation from pack average
- **V** — absolute voltage
- **R** — wire resistance
- **H** — change in cell deviation over roughly 60 seconds

Keyboard shortcuts are ignored while focus is in an editable control.


## Milestone: the battery becomes visible

v0.4.0 adds a live physical heat map and a time-lapse recorder.

### Physical pack view

A 16-channel JK BMS in this installation represents 16 parallel cell groups, or
32 physical cells.  OpenJK renders each BMS channel as one split compound block:

```text
1|2  3|4  5|6  ...  15|16     17|18  19|20  ...  31|32
```

The small center gap marks the physical 16/17 battery-half boundary while keeping
the electrical/color progression visually continuous.  Each block shows one shared
BMS voltage and deviation because the two physical cells in the block are parallel.
The highest BMS group has a red outline and the lowest has a blue outline.

Hovering a block shows its BMS group, physical-cell pair, voltage, deviation, and
wire resistance.

### Color modes

- **Deviation**: colors cells by millivolts above or below the pack average
- **Absolute voltage**: auto-ranges over the current pack voltage span
- **Wire resistance**: auto-ranges over the reported wire-resistance values

The Deviation mode includes an adjustable band width in millivolts.

### Continuous history capture

With **Capture history** enabled, every live frame is appended to:

```text
history/<BMS-serial>_YYYYMMDD.jsonl
```

Each sample contains:

- timestamp
- pack voltage and current
- SOC
- average and delta
- balancing current
- three temperatures
- every cell voltage and wire resistance

The file is line-oriented JSON, so an interrupted session does not destroy the
earlier samples.

### Time-lapse playback

Use **Load history…** to open a captured file.

Then:

- scrub the timeline manually
- press **Play** to animate the entire charging or balancing session
- press **Live** to return to current telemetry

The heat map itself becomes the movie. Cell patterns can be watched forming,
moving, balancing, or persisting over time.

## Existing v0.3.3 capabilities retained

- BLE discovery and dynamic characteristic resolution
- live pack and cell telemetry
- complete settings decoding
- 47 mapped writable settings and controls
- automatic backups
- guarded writes
- fresh readback verification
- rejection reporting and retry
- restoration of the original value
- append-only transaction logging

## Install

No new Python dependencies were added.

```powershell
python -m pip install -r requirements.txt
python openjk.py
```

Existing v0.3.3 installations only need the new files copied over the working
repository.
