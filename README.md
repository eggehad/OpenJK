# OpenJK v0.3.2

OpenJK is an open-source protocol engine and engineering toolkit for JK Smart BMS systems over Bluetooth LE.

## v0.3.2: rejected-write handling

JK BMS firmware sometimes ignores a valid BLE write. OpenJK now treats a
readback mismatch as **NOT ACCEPTED**, not as a mysterious generic failure.

For every write it now:

1. Sends the value.
2. Requests fresh settings.
3. Verifies the readback.
4. If unchanged, reports the rejected attempt clearly.
5. Waits 1.5 seconds and retries automatically.
6. Stops after three attempts.
7. Reports either `PASS after N attempts` or `FAILED after 3 attempts`.
8. Logs `NOT_ACCEPTED`, `RETRY_SCHEDULED`, and the final result.

This matches the observed behavior in which the same valid value may fail once
and stick on a later transmission.

## Milestone: first verified writes

This release adds a deliberately small whitelist of reversible voltage settings for JK02_32S / PB / V19 hardware:

| Setting | Register | Scale |
|---|---:|---:|
| Start balance voltage | `0x22` | 1000 counts/V |
| SOC 100% voltage | `0x07` | 1000 counts/V |
| Cell request charge voltage | `0x09` | 1000 counts/V |
| Cell request float voltage | `0x0A` | 1000 counts/V |

All writes use a four-byte little-endian value in the standard 20-byte JK BLE holding-register frame.

## Safety transaction

OpenJK performs the exact reversible experiment requested:

1. Connect to the intended BMS.
2. Read all settings.
3. Choose one whitelisted voltage.
4. Save a complete JSON backup.
5. Show the exact old value, new value, register, raw value, and BLE frame.
6. Require explicit confirmation.
7. Send the write using BLE write-without-response.
8. Request two fresh settings frames.
9. Verify the value by readback.
10. Offer **Restore original value**.
11. Verify the restoration by readback.

Every transaction is appended to:

```text
transactions/openjk_write_transactions.log
```

Pre-write backups are saved under:

```text
backups/
```

## Recommended first test

Use **Start balance voltage** and make a tiny reversible change:

```text
3.400 V → 3.410 V
```

After OpenJK reports `PASS`, click **Restore original value** and confirm a second `PASS`.

## Install

No new dependencies were added. If v0.2.1 already runs:

```powershell
python openjk.py
```

For a fresh installation:

```powershell
python -m pip install -r requirements.txt
python openjk.py
```

## Important limitations

- This release targets the JK02_32S protocol used by your PB/V19 BMS.
- It does not expose protection thresholds, current limits, MOS controls, cell count, communication protocols, or calibration writes.
- JK uses BLE write-without-response. OpenJK therefore treats fresh settings readback, not the host write call, as proof of success.
- If verification fails, OpenJK stops and does not attempt an automatic rollback.
