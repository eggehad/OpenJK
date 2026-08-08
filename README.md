# OpenJK v0.4.7

**OpenJK is an open-source desktop monitor, visualizer, and configuration workstation for JK Smart BMS hardware over Bluetooth Low Energy (BLE).**

The central idea is simple: a battery is easier to understand when you can watch it behave, not just read numbers from it.

> **OpenJK lets you see the battery breathing.**

Cell voltages that look like a column of nearly identical numbers become a moving physical picture. You can watch cells separate under load, converge during charging, identify persistent leaders and laggards, see balancing take effect, and replay a charging or balancing session later. Configuration tools, verified writes, and backup/restore are built around that visualization rather than being the whole purpose of the program.

---

## Why OpenJK exists

JK BMS hardware exposes a great deal of useful information, but much of the normal user experience is centered on a phone application. PC software exists for some JK product families, but desktop support is fragmented by model and platform, and conventional BMS interfaces tend to emphasize lists of instantaneous values.

OpenJK was built to solve a different problem: **make the internal behavior of a working battery visible from a general-purpose computer, while also providing a careful engineering interface for configuration and troubleshooting.**

That makes it useful for:

- commissioning a new battery pack;
- watching cell behavior during charge, discharge, and balancing;
- finding cells or groups that consistently lead, lag, or drift;
- comparing behavior between multiple BMS-equipped batteries;
- observing whether balancing is actually correcting a pattern;
- recording and replaying a battery session instead of trying to remember what flashed by on screen;
- reading the complete decoded BMS configuration;
- changing settings with fresh readback verification instead of assuming a write succeeded;
- saving a complete configuration before experimenting;
- restoring a known configuration later.

OpenJK is intended as an **engineering instrument**, not merely another settings screen.

---

## The visualization: see the battery breathing

The **Cells • Live** view is the heart of OpenJK.

For the current 16-channel / 16S2P installation, each JK channel is drawn as a physical pair of cells:

```text
1|2  3|4  5|6 ... 15|16    17|18  19|20 ... 31|32
```

The midpoint gap represents the physical division between the two halves of the battery while preserving the electrical sequence from left to right.

Each block displays live voltage and deviation information. Highest and lowest channels are visually identified, and the lower portion of the display adds two complementary ways to perceive cell behavior:

- **Signed deviation bars** show instantaneous deviation from pack average. Positive deviation rises above zero; negative deviation falls below zero.
- **Color-memory strip** deliberately changes more slowly, using roughly a 25-second exponential response. This suppresses one-frame noise and makes persistent behavior much easier to see.

### Visualization modes

The color-memory strip can display:

- **D — Deviation:** cell voltage relative to the pack average;
- **V — Absolute voltage:** absolute cell voltage;
- **R — Wire resistance:** reported cell-wire resistance;
- **H — 60-second drift:** change in each cell's deviation over roughly one minute.

The modes can be selected from the GUI or directly with the **D, V, R, and H** keys when an editable field does not have keyboard focus.

The **Band (mV)** control sets the full-scale deviation range used by the signed bars and the fixed range for deviation/trend coloring.

### Why this matters

A static value can tell you that a cell is 3 mV high. A moving visualization can tell you whether that cell:

- is always high;
- only rises under heavy charge;
- collapses faster under load;
- converges once balancing begins;
- oscillates with measurement noise;
- is part of a repeatable pack-wide pattern.

That difference is the reason OpenJK exists.

---

## History capture and time-lapse playback

With **Capture history** enabled, live samples are appended to:

```text
history/<BMS-serial>_YYYYMMDD.jsonl
```

Each sample contains the timestamp, pack voltage/current, SOC, cell average and delta, balancing current, temperatures, cell voltages, and wire resistances.

The history format is line-oriented JSON. If a session is interrupted, earlier records remain intact.

Use **Load history…** to open a captured session. The timeline can then be scrubbed manually or played as a time-lapse. The cell visualization becomes a movie of the pack charging, discharging, balancing, drifting, or recovering. **Live** returns to current telemetry.

---

## Dashboard and live telemetry

The **Overview** page groups live information into four areas instead of presenting one long list:

- **Pack:** voltage, current, and power;
- **Capacity:** SOC, remaining/nominal capacity, SOH, and cycle count;
- **Cells:** average voltage, delta, highest/lowest cell, and balancing current;
- **Temperatures & state:** MOS and probe temperatures, charge/discharge MOS state, and error bitmask.

Live cell telemetry continues independently in the Cells view.

---

## Reading settings and identity

OpenJK decodes the JK settings frame into named parameters and displays the complete decoded configuration in the **Settings** tab.

The **Identity** tab displays information reported by the connected BMS, including identifiers and firmware/device information when the BMS supplies them.

The **Raw frames** tab remains available for protocol work and troubleshooting.

---

## Safe writes

Configuration changes are intentionally conservative.

OpenJK does not consider a transmitted write successful merely because the BLE operation completed. For a supported writable setting it:

1. identifies the selected BMS and current value;
2. validates and encodes the requested value;
3. saves a complete JSON backup before the write;
4. sends the JK write frame;
5. requests fresh settings frames;
6. compares the value actually read back with the requested value;
7. reports **PASS** only when the fresh readback agrees;
8. retries a rejected/unaccepted write up to the configured retry limit;
9. preserves an append-only transaction log of the operation.

The **New value** field belongs to the user while it is being edited. Background settings refreshes update the current value without overwriting a value being typed.

After a successful individual write, OpenJK can also restore the original value and verify that restoration the same way.

> Changing protection thresholds, current limits, cell-count-dependent values, or other critical BMS parameters can disable protection or damage equipment. OpenJK's verification reduces uncertainty; it does not make an unsafe value safe.

---

## Backup and restore

### Save backup

**Save backup…** writes an OpenJK JSON document containing:

- backup format/version;
- timestamp;
- selected BMS identity/address information;
- decoded settings;
- a live status snapshot.

Backups are human-readable JSON and can also be inspected with normal text/data tools.

### Restore backup

v0.4.6 added **Restore backup…**.

Restore is deliberately transactional and conservative:

1. OpenJK loads an existing OpenJK JSON backup.
2. It compares the backup with the currently connected BMS.
3. Only supported writable settings that differ are queued for restoration.
4. A serial-number mismatch is presented as a warning rather than silently ignored.
5. Before changing anything, OpenJK saves the *current* BMS configuration to a new `*_pre_restore_*.json` safety backup.
6. Settings are restored **one at a time**.
7. Every setting must pass fresh readback verification before the next write is attempted.
8. If any value cannot be verified, the restore stops immediately and no later restore writes are sent.

Read-only telemetry and unknown fields in a backup are not blindly written to the BMS.

This makes the backup feature useful not only as an archive but as a practical recovery and commissioning tool.

---

## Supported and tested environment

### Operating systems

OpenJK is written in Python using **Tkinter** for the GUI and **Bleak** for Bluetooth LE. Those libraries support the major desktop operating systems, so OpenJK is designed to run on:

| Platform | Status | Notes |
| --- | --- | --- |
| Windows 10/11 | **Field tested** | Primary development/testing environment to date. |
| Linux | **Designed to work; field validation in progress** | Requires working Bluetooth/BlueZ access and Tkinter. Fedora is a natural target. |
| macOS | **Expected compatible, not yet field tested** | Bleak and Tkinter are cross-platform, but OpenJK has not yet been validated against a real JK BMS on macOS. |

The table deliberately distinguishes **tested** from **expected to work**. Hardware-management software should not claim validation it has not received.

### JK BMS hardware

OpenJK currently targets the JK BLE protocol implemented by the hardware used during development. The present visualization is specifically tuned to a **16-channel battery represented as 32 physical cells in 16 parallel pairs (16S2P)**.

Other JK models using the same BLE protocol may communicate successfully, but they should be considered **unverified until tested**. Do not assume every JK product family, firmware revision, cell count, or inverter-BMS variant is interchangeable.

If you test OpenJK successfully on another JK model or platform, that result is useful project information and should be documented.

---

## Requirements

- Python 3.10 or newer recommended;
- Bluetooth Low Energy hardware supported by the host operating system;
- Python Tkinter support;
- `bleak>=1.0,<2` (installed from `requirements.txt`).

No cloud account is required by OpenJK itself. Communication with the BMS is direct over BLE.

---

## Installation

### Windows

From a terminal in the OpenJK directory:

```powershell
python -m pip install -r requirements.txt
python openjk.py
```

`run_openjk.bat` is also included for convenience once Python and the required packages are installed.

### Fedora / Linux

Install Python, Tkinter, and Bluetooth support using the packages appropriate for your distribution. On Fedora, the Python GUI package is normally provided separately from the base Python installation.

Then, from the OpenJK directory:

```bash
python3 -m pip install -r requirements.txt
python3 openjk.py
```

Your desktop session/user must have permission to use the system Bluetooth stack. If scanning fails, verify Bluetooth operation at the operating-system level before changing OpenJK.

### macOS

Install a current Python distribution with Tk support, then:

```bash
python3 -m pip install -r requirements.txt
python3 openjk.py
```

macOS support is presently unverified on physical JK hardware.

---

## Basic use

1. Start OpenJK.
2. Click **Scan for BMS**.
3. Select the intended JK BMS from the Bluetooth device list. Double-clicking also connects.
4. After connection, OpenJK requests identity/settings data and begins receiving live telemetry.
5. Use **Overview** for pack-level information.
6. Use **Cells • Live** to watch cell behavior. Try D/V/R/H visualization modes and adjust the deviation band as needed.
7. Enable **Capture history** when you want a session recorded for later playback.
8. Use **Settings** to inspect the decoded configuration.
9. Use **Safe writes** when a supported setting must be changed and verified.
10. Use **Save backup…** before significant configuration work, and **Restore backup…** when a saved configuration needs to be reapplied.

When working with multiple batteries, always verify the selected BMS identity before writing or restoring settings.

---

## Files OpenJK creates

Depending on the features used, OpenJK creates data alongside the working directory:

```text
backups/          configuration backups and automatic pre-write/pre-restore backups
history/          captured live telemetry in JSONL format
transactions/     append-only write/verification transaction log
openjk_raw_*.log  assembled protocol/raw diagnostic log
auto diagnostics sparse GUI diagnostics when abnormal latency/backlog is detected
```

The exact raw/diagnostic filenames contain a startup timestamp.

---

## Design principles

OpenJK is being developed around a few deliberately conservative principles:

- **Make behavior visible.** Visualization is more valuable than another wall of numbers.
- **Never confuse “command sent” with “setting changed.”** Verify writes from fresh BMS data.
- **Back up before changing things.** Recovery should be designed in, not improvised afterward.
- **Fail closed during restoration.** One failed verification stops the remaining writes.
- **Preserve evidence.** History, raw frames, backups, and transaction logs make troubleshooting reproducible.
- **State what is actually tested.** Expected compatibility is not the same thing as validation.
- **Keep the protocol inspectable.** OpenJK is open source and its behavior can be audited and extended.

---

## Single-instance protection

OpenJK intentionally allows only one running instance per computer. Two copies
can otherwise compete for BLE access to the same BMS and multiply the polling
and visualization workload. If OpenJK is already running, a second launch now
shows a warning and exits cleanly.

## Known limitations

- Current real-hardware validation is limited to the JK BMS/firmware family used during development.
- The physical cell drawing currently assumes the project's 16S2P / 32-physical-cell layout rather than automatically adapting to every possible pack topology.
- macOS has not yet been field tested.
- Linux support should be considered under active field validation until routine operation has been demonstrated on a real installation.
- OpenJK communicates over BLE; it is not currently a general RS485/CAN/USB JK transport layer.
- Backup restore writes only parameters for which OpenJK has an explicit safe writable definition.

---

## Release history

### v0.4.7

- Added single-instance protection. A second launch now warns and exits before starting BLE workers or polling.
- Prevents two OpenJK processes from competing for a BMS and multiplying visualization/polling load.
- Retains the polished GUI and backup/restore work from v0.4.6.

### v0.4.6

- Added **Restore backup…** with automatic pre-restore safety backup.
- Restore writes only differing writable settings, verifies each readback, retries failures, and aborts safely on an unverified write.
- Major README rewrite centered on live battery visualization and the ability to “see the battery breathing.”
- GUI hierarchy and readability improvements, including **Cells • Live** prominence and clearer overview/configuration panels.

### v0.4.5

- Fixed the Safe Writes editor so background settings refreshes no longer overwrite a value while the user is editing it.
- Current value continues to refresh independently.
- Successful verified writes/restores intentionally resynchronize the editable value.

### v0.4.4

- Continued the GUI responsiveness work by keeping the event drain bounded.
- Removed high-volume per-fragment BLE traffic from the GUI/log event path.
- Reduced GUI diagnostics to abnormal conditions such as errors, slow GUI calls, or meaningful queue backlog.

### v0.4.3

- Added/refined the physical cell visualization.
- Added live signed deviation bars and the slower color-memory strip.
- Added D/V/R/H visualization modes.
- Added continuous JSONL history capture and time-lapse playback.

### Earlier 0.4.x work

The earlier 0.4.x series established the live visualization and responsiveness work that the current release builds upon. Historical release notes were incomplete; this README intentionally avoids inventing detailed version-by-version claims that were not preserved in the repository.

---

## Project status

OpenJK is functional software being developed against real battery hardware. It is still evolving, especially around broader JK-model compatibility and UI refinement.

The project's strongest capability is already the one it was built for: **turning live BMS telemetry into something you can see, follow, compare, and understand.**
