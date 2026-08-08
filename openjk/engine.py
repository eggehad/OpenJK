
from __future__ import annotations

import asyncio
import datetime as dt
import json
import queue
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from bleak import BleakClient, BleakScanner
from bleak.backends.device import BLEDevice

from .protocol import (
    CHAR_UUID,
    GET_DEVICE_INFO,
    GET_SETTINGS,
    FrameAssembler,
    decode_frame,
    SAFE_WRITABLE_PARAMETERS,
)


BLE_CONNECT_TIMEOUT_SECONDS = 20.0
BLE_WRITE_TIMEOUT_SECONDS = 5.0
BLE_NOTIFY_TIMEOUT_SECONDS = 5.0
BLE_DISCONNECT_TIMEOUT_SECONDS = 5.0
BLE_CANCEL_TIMEOUT_SECONDS = 2.0


class OperationInterrupted(Exception):
    """Internal control-flow exception for a user-requested disconnect."""


@dataclass
class DeviceRow:
    device: BLEDevice
    name: str
    address: str
    rssi: Optional[int]


@dataclass
class BMSState:
    device_info: dict[str, Any] = field(default_factory=dict)
    settings: dict[str, Any] = field(default_factory=dict)
    live: dict[str, Any] = field(default_factory=dict)
    selected_device_name: str = ""
    selected_device_address: str = ""
    last_update: str = ""

    def backup_document(self) -> dict[str, Any]:
        return {
            "format": "openjk-backup",
            "format_version": 1,
            "created_at": dt.datetime.now(dt.timezone.utc).astimezone().isoformat(),
            "device": {
                "advertised_name": self.selected_device_name,
                "address": self.selected_device_address,
                **self.device_info,
            },
            "settings": self.settings,
            "status_snapshot": self.live,
        }

    def save_backup(self, path: Path) -> None:
        path.write_text(
            json.dumps(self.backup_document(), indent=2, sort_keys=True),
            encoding="utf-8",
        )


class BleWorker:
    def __init__(self, events: "queue.Queue[tuple[str, Any]]", trace_path: Optional[Path] = None) -> None:
        self.events = events
        self.trace_path = trace_path
        self.commands: "queue.Queue[tuple[str, Any]]" = queue.Queue()
        self.thread = threading.Thread(target=self._thread_main, daemon=True)
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self.client: Optional[BleakClient] = None
        self.assembler = FrameAssembler()
        self.poll_task: Optional[asyncio.Task] = None
        self.running = True
        self.counter = 0
        self.characteristic_uuid: Optional[str] = None
        self.disconnect_requested = False
        self.disconnect_task: Optional[asyncio.Task] = None
        self.active_operation_task: Optional[asyncio.Task] = None
        self.connect_trace_started: Optional[float] = None
        self.connect_trace_seen: set[str] = set()
        self.last_target_address: Optional[str] = None
        self.client_generation = 0
        self.thread.start()

    def send(self, command: str, payload: Any = None) -> None:
        # Disconnect/stop are control-plane commands.  They must never sit behind
        # a stalled BLE read/write in the ordinary FIFO command queue.
        if command in {"disconnect", "stop"} and self.loop is not None:
            if command == "stop":
                self.running = False
            self.disconnect_requested = True
            self.loop.call_soon_threadsafe(self._schedule_priority_disconnect)
            return

        self.commands.put((command, payload))
        if self.loop:
            self.loop.call_soon_threadsafe(lambda: None)

    def _schedule_priority_disconnect(self) -> None:
        if self.disconnect_task and not self.disconnect_task.done():
            return
        self.disconnect_task = asyncio.create_task(self._priority_disconnect())

    async def _priority_disconnect(self) -> None:
        started = time.perf_counter()
        self._emit("timing", "DISCONNECT interrupt requested")
        self._stage("Disconnect requested; cancelling active BLE work")
        try:
            # Cancel the command that currently owns the BLE stack before
            # attempting teardown.  A disconnect queued alongside a wedged
            # WinRT/Bleak operation is not an interrupt; cancellation makes it one.
            active = self.active_operation_task
            current = asyncio.current_task()
            if active and active is not current and not active.done():
                active.cancel()
                try:
                    await asyncio.wait_for(active, timeout=BLE_CANCEL_TIMEOUT_SECONDS)
                except asyncio.CancelledError:
                    self._emit("timing", "Active BLE command cancelled")
                except asyncio.TimeoutError:
                    self._emit(
                        "timing",
                        f"Active BLE command did not cancel within {BLE_CANCEL_TIMEOUT_SECONDS:.1f}s",
                    )
                except Exception as exc:
                    self._emit(
                        "timing",
                        f"Active BLE command ended during cancellation: {type(exc).__name__}: {exc}",
                    )

            await self._disconnect()
        except Exception as exc:
            self._emit("error", f"Disconnect error: {type(exc).__name__}: {exc}")
        finally:
            elapsed = time.perf_counter() - started
            self._emit("timing", f"DISCONNECT complete in {elapsed:.3f}s")
            self._stage("Disconnected")
            self.disconnect_requested = False

    def _emit(self, kind: str, payload: Any = None) -> None:
        # Timing records are written directly from the BLE worker thread before
        # they are queued for the GUI.  This makes connection diagnostics survive
        # a sluggish UI, a blocked Tk event loop, or even a forced process kill.
        if kind == "timing" and self.trace_path is not None:
            timestamp = dt.datetime.now().astimezone().isoformat(timespec="milliseconds")
            try:
                with self.trace_path.open("a", encoding="utf-8") as handle:
                    handle.write(f"{timestamp} BLE-TIMING {payload}\n")
                    handle.flush()
            except OSError:
                pass
        self.events.put((kind, payload))

    def _stage(self, message: str) -> None:
        self._emit("connection_stage", message)
        self._emit("timing", f"STAGE {message}")

    def _thread_main(self) -> None:
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.loop.run_until_complete(self._run())

    async def _run(self) -> None:
        while self.running:
            try:
                command, payload = self.commands.get_nowait()
            except queue.Empty:
                await asyncio.sleep(0.05)
                continue

            started = time.perf_counter()
            self._emit("timing", f"COMMAND {command} begin")
            task = asyncio.create_task(self._execute_command(command, payload))
            self.active_operation_task = task
            try:
                await task
            except asyncio.CancelledError:
                if not self.disconnect_requested:
                    self._emit("error", f"Command {command} was unexpectedly cancelled")
            except OperationInterrupted:
                # A priority disconnect intentionally interrupted the active BLE
                # transaction.  That is successful cancellation, not an error.
                pass
            except Exception as exc:
                if not self.disconnect_requested:
                    self._emit("error", f"{type(exc).__name__}: {exc}")
            finally:
                if self.active_operation_task is task:
                    self.active_operation_task = None
                self._emit(
                    "timing",
                    f"COMMAND {command} end after {time.perf_counter() - started:.3f}s",
                )

    async def _execute_command(self, command: str, payload: Any) -> None:
        if command == "scan":
            await self._scan()
        elif command == "connect":
            await self._connect(payload)
        elif command == "disconnect":
            await self._disconnect()
        elif command == "read_settings":
            await self._write(GET_SETTINGS)
        elif command == "read_device":
            await self._write(GET_DEVICE_INFO)
        elif command == "write_parameter":
            await self._write_parameter(payload)
        elif command == "stop":
            self.running = False
            await self._disconnect()

    async def _scan(self) -> None:
        self._stage("Scanning for nearby Bluetooth devices")
        self._emit("status", "Scanning for BLE devices...")
        scan_started = time.perf_counter()
        discovered = await BleakScanner.discover(timeout=8.0, return_adv=True)
        self._emit("timing", f"BleakScanner.discover completed in {time.perf_counter() - scan_started:.3f}s")
        rows: list[DeviceRow] = []
        for _, (device, adv) in discovered.items():
            name = device.name or adv.local_name or "(unnamed)"
            rssi = getattr(adv, "rssi", None)
            rows.append(DeviceRow(device, name, device.address, rssi))
        rows.sort(key=lambda row: (not self._looks_like_jk(row.name), row.name.lower()))
        self._emit("devices", rows)
        self._emit("status", f"Scan complete: {len(rows)} device(s) found")
        self._stage(f"Scan complete: {len(rows)} device(s) found")

    @staticmethod
    def _looks_like_jk(name: str) -> bool:
        upper = name.upper()
        return (
            "JK" in upper
            or "BMS" in upper
            or upper.startswith("60")
        )

    @staticmethod
    def _is_transient_windows_ble_error(exc: Exception) -> bool:
        text = f"{type(exc).__name__}: {exc}".lower()
        winerror = getattr(exc, "winerror", None)
        return (
            winerror == -2147023673
            or "-2147023673" in text
            or "operation was canceled" in text
            or "operation was cancelled" in text
            or "no jk notify/write characteristic found" in text
        )

    async def _reacquire_device(self, device: BLEDevice) -> BLEDevice:
        """Resolve a fresh Windows BLEDevice instead of reusing scan backend state."""
        target = device.address
        self._stage(f"Refreshing Windows BLE handle for {device.name or target}")
        started = time.perf_counter()

        fresh = await BleakScanner.find_device_by_address(target, timeout=6.0)
        if fresh is None:
            # Some Windows adapters are more reliable when discovery is allowed to
            # match by both address and advertised name.  This is intentionally a
            # short fallback, not a second full 8-second UI scan.
            name = device.name
            if name:
                fresh = await BleakScanner.find_device_by_filter(
                    lambda d, adv: d.address == target or (adv.local_name or d.name) == name,
                    timeout=4.0,
                )

        if fresh is None:
            raise RuntimeError(
                f"Could not reacquire {device.name or target} from Windows Bluetooth. "
                "Scan again and retry."
            )

        self._emit(
            "timing",
            f"Fresh BLEDevice reacquired in {time.perf_counter() - started:.3f}s "
            f"address={fresh.address}",
        )
        return fresh

    async def _connect(self, device: BLEDevice) -> None:
        target_switched = (
            self.last_target_address is not None
            and device.address != self.last_target_address
        )

        # A Windows BLEDevice returned by BleakScanner is a backend object, not a
        # timeless device identifier.  Reusing it after talking to a different
        # peripheral can carry stale WinRT/GATT state.  On a BMS switch, rebuild
        # the session from a freshly discovered BLEDevice before constructing the
        # new BleakClient.
        current_device = device
        if target_switched:
            self._emit(
                "timing",
                f"BMS target switch {self.last_target_address} -> {device.address}; "
                "forcing clean Windows BLE reacquisition",
            )
            await self._disconnect()
            await asyncio.sleep(0.35)
            current_device = await self._reacquire_device(device)

        attempts = 2
        for attempt in range(1, attempts + 1):
            try:
                await self._connect_once(current_device)
                self.last_target_address = current_device.address
                return
            except OperationInterrupted:
                raise
            except Exception as exc:
                if self.disconnect_requested or not self._is_transient_windows_ble_error(exc):
                    raise
                if attempt >= attempts:
                    raise

                self._emit(
                    "timing",
                    f"Transient Windows BLE handoff failure on attempt {attempt}: "
                    f"{type(exc).__name__}: {exc}",
                )
                self._stage("Windows BLE handoff hiccup; rebuilding Bluetooth session")
                await self._disconnect()
                await asyncio.sleep(0.75)
                current_device = await self._reacquire_device(device)

    async def _connect_once(self, device: BLEDevice) -> None:
        self.connect_trace_started = time.perf_counter()
        self.connect_trace_seen = set()
        self._emit("timing", f"CONNECT target={device.name or device.address}")
        self._stage("Preparing Bluetooth connection")
        await self._disconnect()
        self.assembler = FrameAssembler()
        self.characteristic_uuid = None
        self._emit("status", f"Connecting to {device.name or device.address}...")
        self._stage(f"Windows/Bleak connecting to {device.name or device.address}")
        self.client_generation += 1
        generation = self.client_generation
        self.client = BleakClient(
            device,
            disconnected_callback=lambda client, gen=generation: self._on_disconnect(client, gen),
        )
        connect_started = time.perf_counter()
        await asyncio.wait_for(
            self.client.connect(timeout=BLE_CONNECT_TIMEOUT_SECONDS),
            timeout=BLE_CONNECT_TIMEOUT_SECONDS + 2.0,
        )
        self._emit("timing", f"BleakClient.connect completed in {time.perf_counter() - connect_started:.3f}s")
        self._stage("Bluetooth link established; resolving GATT services")
        if self.disconnect_requested:
            raise OperationInterrupted()

        # Do not assume every JK firmware exposes the data characteristic in
        # exactly the same cached Windows service layout. Resolve it from the
        # services actually reported by this BMS.
        services_started = time.perf_counter()
        services = self.client.services
        characteristic = services.get_characteristic(CHAR_UUID)
        self._emit("timing", f"Service/characteristic lookup completed in {time.perf_counter() - services_started:.3f}s")
        self._stage("JK data characteristic found; enabling notifications")

        if characteristic is None:
            candidates = []
            for service in services:
                for item in service.characteristics:
                    short_uuid = item.uuid.lower().replace("-", "")
                    properties = {prop.lower() for prop in item.properties}
                    if short_uuid.startswith("0000ffe1"):
                        candidates.insert(0, item)
                    elif (
                        ("notify" in properties or "indicate" in properties)
                        and ("write" in properties or "write-without-response" in properties)
                    ):
                        candidates.append(item)

            if candidates:
                characteristic = candidates[0]

        if characteristic is None:
            discovered = []
            for service in services:
                for item in service.characteristics:
                    discovered.append(
                        f"{item.uuid} [{', '.join(item.properties)}]"
                    )
            raise RuntimeError(
                "No JK notify/write characteristic found. "
                "Discovered characteristics: " + "; ".join(discovered)
            )

        self.characteristic_uuid = characteristic.uuid
        self._emit(
            "status",
            f"Using BLE characteristic {self.characteristic_uuid}",
        )
        notify_started = time.perf_counter()
        await asyncio.wait_for(
            self.client.start_notify(self.characteristic_uuid, self._notification),
            timeout=BLE_NOTIFY_TIMEOUT_SECONDS,
        )
        self._emit("timing", f"start_notify completed in {time.perf_counter() - notify_started:.3f}s")
        self._stage("Notifications enabled; requesting BMS identity and settings")
        if self.disconnect_requested:
            raise OperationInterrupted()

        self._emit("connected", {
            "connected": True,
            "name": device.name or "",
            "address": device.address,
            "characteristic": self.characteristic_uuid,
        })
        self._emit("status", f"Connected to {device.name or device.address}")

        # The JK BLE flow requires both requests. 0x96 returns settings and
        # starts the periodic 0x02 live-data stream after 0x97 is also sent.
        initial_started = time.perf_counter()
        await self._write(GET_DEVICE_INFO)
        self._emit("timing", f"Initial device-info request sent at +{time.perf_counter() - self.connect_trace_started:.3f}s")
        await asyncio.sleep(0.35)
        await self._write(GET_SETTINGS)
        self._emit("timing", f"Initial settings request sent at +{time.perf_counter() - self.connect_trace_started:.3f}s")
        self._emit("timing", f"CONNECT setup completed in {time.perf_counter() - initial_started:.3f}s after notifications enabled")
        self._stage("Connected; waiting for live BMS data")
        self.poll_task = asyncio.create_task(self._poll_settings())

    async def _poll_settings(self) -> None:
        while self.client and self.client.is_connected:
            await asyncio.sleep(10.0)
            await self._write(GET_SETTINGS)

    async def _write_parameter(self, payload: dict[str, Any]) -> None:
        key = str(payload["key"])
        value = float(payload["value"])
        if key not in SAFE_WRITABLE_PARAMETERS:
            raise ValueError(f"Parameter is not write-enabled: {key}")

        definition = SAFE_WRITABLE_PARAMETERS[key]
        raw_value, frame = definition.encode(value)
        self._emit(
            "write_started",
            {
                "key": key,
                "label": definition.label,
                "value": value,
                "raw_value": raw_value,
                "register": definition.register,
                "frame": frame,
            },
        )

        # ESPHome's proven implementation uses a BLE write-without-response.
        # Verification therefore comes from a fresh 0x01 settings frame.
        await self._write(frame)
        await asyncio.sleep(1.0)
        if self.disconnect_requested:
            raise OperationInterrupted()
        await self._write(GET_SETTINGS)
        await asyncio.sleep(1.5)
        if self.disconnect_requested:
            raise OperationInterrupted()
        await self._write(GET_SETTINGS)

    async def _write(self, payload: bytes) -> None:
        if not self.client or not self.client.is_connected:
            raise RuntimeError("Not connected")
        if not self.characteristic_uuid:
            raise RuntimeError("JK BLE characteristic has not been resolved")
        if self.disconnect_requested:
            raise OperationInterrupted()
        await asyncio.wait_for(
            self.client.write_gatt_char(
                self.characteristic_uuid,
                payload,
                response=False,
            ),
            timeout=BLE_WRITE_TIMEOUT_SECONDS,
        )
        if self.disconnect_requested:
            raise OperationInterrupted()
        self._emit("tx", payload)

    async def _disconnect(self) -> None:
        poll_task = self.poll_task
        self.poll_task = None
        if poll_task:
            poll_task.cancel()
            try:
                await poll_task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass

        client = self.client
        characteristic_uuid = self.characteristic_uuid
        # Clear references first so no other task can begin another write while
        # teardown is underway.
        self.client = None
        self.characteristic_uuid = None

        if client:
            try:
                if client.is_connected and characteristic_uuid:
                    try:
                        await asyncio.wait_for(
                            client.stop_notify(characteristic_uuid),
                            timeout=BLE_NOTIFY_TIMEOUT_SECONDS,
                        )
                    except Exception:
                        pass
                if client.is_connected:
                    try:
                        await asyncio.wait_for(
                            client.disconnect(),
                            timeout=BLE_DISCONNECT_TIMEOUT_SECONDS,
                        )
                    except Exception:
                        pass
            finally:
                self.client = None
                self.characteristic_uuid = None

        self._emit("connected", {"connected": False})

    def _on_disconnect(self, _client: BleakClient, generation: int) -> None:
        # Windows may deliver the old client's disconnected callback after a new
        # BMS session has already started.  Ignore callbacks from obsolete client
        # generations so they cannot tear down the GUI state of the new target.
        if generation != self.client_generation:
            self._emit(
                "timing",
                f"Ignoring stale disconnect callback generation={generation}; "
                f"current={self.client_generation}",
            )
            return
        self._emit("connected", {"connected": False})
        self._emit("status", "BMS disconnected")

    def _notification(self, _sender: Any, data: bytearray) -> None:
        chunk = bytes(data)
        # v0.4.4: do not send every BLE fragment through the GUI/log queue.
        # Assembled RX frames retain the complete protocol bytes and are sufficient
        # for normal diagnostics while avoiding duplicate high-volume logging.
        for frame in self.assembler.feed(chunk):
            self._emit("rx_frame", frame)
            try:
                kind, decoded = decode_frame(frame)
            except Exception as exc:
                self._emit("error", f"Decode failed: {exc}")
                continue
            if (
                self.connect_trace_started is not None
                and kind in {"device_info", "settings", "live"}
                and kind not in self.connect_trace_seen
            ):
                self.connect_trace_seen.add(kind)
                self._emit(
                    "timing",
                    f"FIRST {kind} frame at +{time.perf_counter() - self.connect_trace_started:.3f}s",
                )
            self._emit(kind, decoded)
