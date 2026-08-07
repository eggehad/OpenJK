# OpenJK v0.4.0

OpenJK is an open-source engineering workstation for JK Smart BMS systems over Bluetooth LE.

## Milestone: the battery becomes visible

v0.4.0 adds a live physical heat map and a time-lapse recorder.

### Physical pack view

The cell display follows the supplied serpentine layout:

```text
Top:     17 18 19 ... 31 32
Bottom:  16 15 14 ... 02 01
```

Both terminals and the current path are on the right side. If a connected BMS
reports only 16 channels, OpenJK places them on the lower right-to-left row.

Each tile shows:

- cell number
- cell voltage
- deviation from the pack average

The highest cell has a red outline. The lowest cell has a blue outline.

Hovering a tile shows its voltage, deviation, wire resistance, and physical row.

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
