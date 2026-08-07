# OpenJK v0.4.3

OpenJK is an open-source engineering workstation for JK Smart BMS systems over Bluetooth LE.

## Milestone: the battery becomes visible

v0.4.3 keeps the bounded GUI event drain introduced in v0.4.2, removes routine high-volume GUI diagnostics and per-fragment RX logging, and adds a dual deviation display: live signed bars plus a slow color-memory strip.

### Physical pack view

A 16-channel JK BMS is shown as sixteen compound blocks representing the 32 physical cells in parallel pairs:

```text
1|2  3|4  5|6 ... 15|16    17|18  19|20 ... 31|32
```

The small midpoint gap marks the physical 16/17 battery-half boundary while preserving one continuous left-to-right electrical view. Each block shows the physical pair, live group voltage, and live deviation from pack average. Highest and lowest groups retain red/blue outlines.

### Dual deviation display

Under the cells are two complementary views:

- **signed bars**: live deviation from pack average; positive rises above zero and negative falls below zero
- **color-memory strip**: the selected D/V/R/H metric with an approximately 25-second exponential fade

The **Band (mV)** control sets the full-scale ± range of the signed deviation bars and the fixed color range in Deviation/Trend modes. Bars are deliberately neutral with only a small colored tip, so geometry carries the quantitative information and color remains secondary.

Keyboard modes for the slow color-memory strip:

- **D** Deviation
- **V** Absolute voltage
- **R** Wire resistance
- **H** 60-second deviation drift

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


## v0.4.3 responsiveness / logging note

The GUI event drain remains bounded so continuous BLE traffic cannot monopolize Tk/Windows message processing. Complete assembled RX frames are logged, but individual BLE RX fragments are no longer duplicated into the raw log. The GUI diagnostic file is sparse and is created only when OpenJK sees an error, a GUI call slower than 100 ms, or a significant event-queue backlog.


## v0.4.5 Safe Writes fix
The New value field is now user-owned while editing. Background settings refreshes update Current value only and no longer overwrite a typed New value. Selecting a parameter initializes New value once; successful verified writes/resores resynchronize it intentionally.
