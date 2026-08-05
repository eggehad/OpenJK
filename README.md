# OpenJK v0.3.0

OpenJK is an open-source protocol engine and engineering toolkit for JK Smart BMS systems over Bluetooth LE.

## First guarded write

v0.3 introduces one deliberately narrow write path:

**JK02_32S / PB / V19 current calibration, holding register `0x67`.**

The entered value is the independently measured current actually flowing through the selected BMS. It is not a gain coefficient or an offset. OpenJK encodes the value in milliamperes as a four-byte little-endian register write.

Before transmission, OpenJK:

1. Requires a live connection and a settings frame.
2. Shows the exact selected BMS identity.
3. Shows the JK current and proposed reference current.
4. Shows the exact register, raw value, and 20-byte BLE frame.
5. Requires explicit confirmation.
6. Saves an automatic JSON backup in `backups/`.
7. Logs the transmitted frame and all replies.
8. Waits for stale telemetry to clear and requests fresh data.

All protection, MOSFET, balancing, temperature, capacity, and voltage settings remain read-only in this release.

## Install

```powershell
python -m pip install -r requirements.txt
```

## Run

```powershell
python openjk.py
```

## Current-calibration procedure

1. Connect to the intended BMS.
2. Verify its advertised name (`-00` master or `-01` slave).
3. Wait for live telemetry.
4. Measure the current through that individual BMS using the Hantek/reference meter.
5. Open **Current calibration**.
6. Enter the independent measurement in amperes.
7. Review the confirmation carefully.
8. Write.
9. Wait for fresh telemetry and compare again.

Because your BMS current telemetry updates in roughly 0.1 A steps and the system current changes rapidly, calibration is best performed under substantial, reasonably stable charge or discharge current.

## Protocol basis

The BLE holding-register frame and the JK02_32S current-calibration register map are based on the community-maintained `syssi/esphome-jk-bms` protocol implementation:

- Register: `0x67`
- Length: `4`
- Scale: `1000` raw counts per ampere
- Frame: `AA 55 90 EB`, register, length, little-endian value, zero padding, additive checksum

OpenJK's Python transport, transaction safeguards, backups, logging, and user interface are implemented independently for this project.

## Important

This is the first write-capable test release. Use it only while watching the selected BMS and an independent reference instrument. Preserve the raw log if the BMS rejects the command or behaves unexpectedly.
