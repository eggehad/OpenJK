
from __future__ import annotations

import asyncio
import datetime as dt
import json
import queue
import threading
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
)


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
    def __init__(self, events: "queue.Queue[tuple[str, Any]]") -> None:
        self.events = events
        self.commands: "queue.Queue[tuple[str, Any]]" = queue.Queue()
        self.thread = threading.Thread(target=self._thread_main, daemon=True)
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self.client: Optional[BleakClient] = None
        self.assembler = FrameAssembler()
        self.poll_task: Optional[asyncio.Task] = None
        self.running = True
        self.counter = 0
        self.thread.start()

    def send(self, command: str, payload: Any = None) -> None:
        self.commands.put((command, payload))
        if self.loop:
            self.loop.call_soon_threadsafe(lambda: None)

    def _emit(self, kind: str, payload: Any = None) -> None:
        self.events.put((kind, payload))

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

            try:
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
                elif command == "stop":
                    self.running = False
                    await self._disconnect()
            except Exception as exc:
                self._emit("error", f"{type(exc).__name__}: {exc}")

    async def _scan(self) -> None:
        self._emit("status", "Scanning for BLE devices...")
        discovered = await BleakScanner.discover(timeout=8.0, return_adv=True)
        rows: list[DeviceRow] = []
        for _, (device, adv) in discovered.items():
            name = device.name or adv.local_name or "(unnamed)"
            rssi = getattr(adv, "rssi", None)
            rows.append(DeviceRow(device, name, device.address, rssi))
        rows.sort(key=lambda row: (not self._looks_like_jk(row.name), row.name.lower()))
        self._emit("devices", rows)
        self._emit("status", f"Scan complete: {len(rows)} device(s) found")

    @staticmethod
    def _looks_like_jk(name: str) -> bool:
        upper = name.upper()
        return (
            "JK" in upper
            or "BMS" in upper
            or upper.startswith("60")
        )

    async def _connect(self, device: BLEDevice) -> None:
        await self._disconnect()
        self.assembler = FrameAssembler()
        self._emit("status", f"Connecting to {device.name or device.address}...")
        self.client = BleakClient(device, disconnected_callback=self._on_disconnect)
        await self.client.connect(timeout=20.0)
        await self.client.start_notify(CHAR_UUID, self._notification)
        self._emit("connected", {
            "connected": True,
            "name": device.name or "",
            "address": device.address,
        })
        self._emit("status", f"Connected to {device.name or device.address}")

        # The JK BLE flow requires both requests. 0x96 returns settings and
        # starts the periodic 0x02 live-data stream after 0x97 is also sent.
        await self._write(GET_DEVICE_INFO)
        await asyncio.sleep(0.35)
        await self._write(GET_SETTINGS)
        self.poll_task = asyncio.create_task(self._poll_settings())

    async def _poll_settings(self) -> None:
        while self.client and self.client.is_connected:
            await asyncio.sleep(10.0)
            await self._write(GET_SETTINGS)

    async def _write(self, payload: bytes) -> None:
        if not self.client or not self.client.is_connected:
            raise RuntimeError("Not connected")
        await self.client.write_gatt_char(CHAR_UUID, payload, response=False)
        self._emit("tx", payload)

    async def _disconnect(self) -> None:
        if self.poll_task:
            self.poll_task.cancel()
            self.poll_task = None
        if self.client:
            try:
                if self.client.is_connected:
                    try:
                        await self.client.stop_notify(CHAR_UUID)
                    except Exception:
                        pass
                    await self.client.disconnect()
            finally:
                self.client = None
        self._emit("connected", {"connected": False})

    def _on_disconnect(self, _client: BleakClient) -> None:
        self._emit("connected", {"connected": False})
        self._emit("status", "BMS disconnected")

    def _notification(self, _sender: Any, data: bytearray) -> None:
        chunk = bytes(data)
        self._emit("rx_chunk", chunk)
        for frame in self.assembler.feed(chunk):
            self._emit("rx_frame", frame)
            try:
                kind, decoded = decode_frame(frame)
            except Exception as exc:
                self._emit("error", f"Decode failed: {exc}")
                continue
            self._emit(kind, decoded)
