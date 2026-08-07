# OpenJK v0.3.3

OpenJK is an open-source protocol engine and engineering toolkit for JK Smart BMS systems over Bluetooth LE.

## Milestone: wholesale writes

v0.3.3 removes the four-setting whitelist. Every **decoded and mapped** JK02_32S / PB / V19 setting now uses the same transaction engine:

- complete JSON backup before the first write
- explicit BMS identity, old value, new value, register and raw frame
- range validation
- critical-setting warning where appropriate
- transmit
- fresh settings readback
- verification
- clear BMS rejection reporting
- up to three attempts
- one-click restoration of the original value
- append-only transaction log

No parameter-specific write functions were added. The engine remains generic; capability is defined by one metadata table.

## Writable categories

- Cell voltage protection and recovery
- SOC reference voltages
- Request charge and float voltages
- Balancing thresholds and current
- Charge and discharge current limits
- Protection and recovery delays
- Temperature protection and recovery thresholds
- Cell count and nominal capacity
- Short-circuit timing
- Precharge and sleep timing
- Charge, discharge and balancer MOS controls
- Heating, display, smart-sleep, PCL and logging controls

The application intentionally does not claim to know every undocumented cross-parameter constraint enforced by JK firmware. A value may pass OpenJK's documented range check and still be rejected by the BMS.

## Boolean entries

For switch settings, enter any of:

```text
0 / Off / False / No
1 / On  / True  / Yes
```

## Install

No new dependencies were added. If v0.3.2 already runs:

```powershell
python openjk.py
```

For a fresh installation:

```powershell
python -m pip install -r requirements.txt
python openjk.py
```

## Files produced

```text
backups/
transactions/openjk_write_transactions.log
openjk_raw_*.log
```

## Protocol basis

The JK02_32S register addresses, lengths, scales and published ranges are based on the community-maintained `syssi/esphome-jk-bms` implementation. OpenJK's Python BLE transport, settings decoder, transaction handling, backup format, verification, restoration and GUI are implemented for this project.
