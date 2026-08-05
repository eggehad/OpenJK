# OpenJK v0.2.1

OpenJK is an open-source protocol engine and engineering toolkit for JK Smart BMS systems over Bluetooth LE.

## v0.2.1 compatibility fix

- Resolves the JK GATT characteristic from each BMS's actual discovered services
- Supports firmware variants that do not expose the expected characteristic through the same Windows cache entry
- Reports every discovered characteristic if no suitable notify/write endpoint exists

## Deliverable 1: complete read engine

v0.2 adds the first engine-focused milestone:

- Reads and decodes the complete JK PB/V19 settings frame (`0x01`)
- Reads the complete live-data frame (`0x02`)
- Reads device identity and communications information (`0x03`)
- Displays decoded settings by category
- Captures all 32 configured wire-resistance values
- Decodes the controls bitmask
- Saves a complete JSON backup containing:
  - BMS identity
  - every decoded setting
  - wire resistance array
  - control flags
  - a live status snapshot
- Keeps raw BLE traffic for protocol verification
- Uses separate protocol, engine, and GUI modules

**v0.2 remains read-only. It does not write settings.**

## Install

```powershell
python -m pip install -r requirements.txt
```

## Run

```powershell
python openjk.py
```

Or double-click `run_openjk.bat`.

## Test procedure

1. Close or disconnect the JK Android app so it releases the BMS BLE connection.
2. Click **Scan**.
3. Select one of the JK devices.
4. Click **Connect**.
5. Open the **Settings** tab.
6. Confirm the decoded values against the JK app.
7. Click **Save backup…** and inspect the generated JSON.

Please preserve the generated `openjk_raw_*.log` if any field is wrong. It lets us correct firmware-specific offsets without guessing.

## Protocol acknowledgement

The JK BLE framing and PB-series field map are based on community reverse-engineering, especially the `syssi/esphome-jk-bms` project. OpenJK's Python engine, application structure, backup format, Windows BLE transport, and user interface are implemented for this project.

## Next milestone

- Parameter metadata for writable registers
- Backup diff and restore preview
- Transactional writes with read-back verification
- Current-calibration tool
