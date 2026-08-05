
from __future__ import annotations

import datetime as dt
import json
import queue
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any

from .engine import BMSState, BleWorker, DeviceRow
from .protocol import SETTINGS, settings_rows

APP_VERSION = "0.3.1"


class OpenJKApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title(f"OpenJK v{APP_VERSION} — JK BMS BLE Monitor")
        self.root.geometry("1260x820")
        self.root.minsize(980, 650)

        self.events: "queue.Queue[tuple[str, Any]]" = queue.Queue()
        self.worker = BleWorker(self.events)
        self.devices: list[DeviceRow] = []
        self.state = BMSState()
        self.pending_write: dict[str, Any] | None = None
        self.restore_value: dict[str, Any] | None = None

        stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        self.raw_log = Path.cwd() / f"openjk_raw_{stamp}.log"

        self._build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self._close)
        self.root.after(75, self._drain_events)

    def _build_ui(self) -> None:
        outer = ttk.Frame(self.root, padding=10)
        outer.pack(fill="both", expand=True)

        toolbar = ttk.Frame(outer)
        toolbar.pack(fill="x")
        ttk.Button(toolbar, text="Scan", command=lambda: self.worker.send("scan")).pack(side="left")
        ttk.Button(toolbar, text="Connect", command=self._connect_selected).pack(side="left", padx=(8, 0))
        ttk.Button(toolbar, text="Disconnect", command=lambda: self.worker.send("disconnect")).pack(side="left", padx=(8, 0))
        ttk.Separator(toolbar, orient="vertical").pack(side="left", fill="y", padx=10)
        ttk.Button(toolbar, text="Read settings", command=lambda: self.worker.send("read_settings")).pack(side="left")
        ttk.Button(toolbar, text="Read identity", command=lambda: self.worker.send("read_device")).pack(side="left", padx=(8, 0))
        self.backup_button = ttk.Button(toolbar, text="Save backup…", command=self._save_backup, state="disabled")
        self.backup_button.pack(side="left", padx=(8, 0))
        ttk.Label(toolbar, text=f"Raw log: {self.raw_log.name}").pack(side="right")

        self.status_var = tk.StringVar(value="Ready. Click Scan.")
        ttk.Label(outer, textvariable=self.status_var, relief="sunken", anchor="w").pack(fill="x", pady=(8, 8))

        paned = ttk.Panedwindow(outer, orient="horizontal")
        paned.pack(fill="both", expand=True)
        left = ttk.Frame(paned, padding=4)
        right = ttk.Frame(paned, padding=4)
        paned.add(left, weight=1)
        paned.add(right, weight=4)

        ttk.Label(left, text="Nearby BLE devices").pack(anchor="w")
        self.device_tree = ttk.Treeview(left, columns=("name", "address", "rssi"), show="headings", height=20)
        for col, title, width in (
            ("name", "Name", 190),
            ("address", "Address / Windows ID", 250),
            ("rssi", "RSSI", 55),
        ):
            self.device_tree.heading(col, text=title)
            self.device_tree.column(col, width=width, anchor="w")
        self.device_tree.pack(fill="both", expand=True, pady=(4, 0))
        self.device_tree.bind("<Double-1>", lambda _event: self._connect_selected())

        notebook = ttk.Notebook(right)
        notebook.pack(fill="both", expand=True)

        dashboard = ttk.Frame(notebook, padding=10)
        settings_tab = ttk.Frame(notebook, padding=10)
        cells_tab = ttk.Frame(notebook, padding=10)
        raw_tab = ttk.Frame(notebook, padding=10)
        identity_tab = ttk.Frame(notebook, padding=10)
        writes_tab = ttk.Frame(notebook, padding=10)
        notebook.add(dashboard, text="Dashboard")
        notebook.add(settings_tab, text="Settings")
        notebook.add(cells_tab, text="Cells")
        notebook.add(writes_tab, text="Safe writes")
        notebook.add(identity_tab, text="Identity")
        notebook.add(raw_tab, text="Raw frames")

        self.live_values: dict[str, tk.StringVar] = {}
        live_fields = [
            ("pack_voltage", "Pack voltage", "V"),
            ("pack_current", "Pack current", "A"),
            ("pack_power", "Pack power", "W"),
            ("soc", "SOC", "%"),
            ("remaining_capacity", "Remaining capacity", "Ah"),
            ("nominal_capacity", "Nominal capacity", "Ah"),
            ("soh", "SOH", "%"),
            ("cycle_count", "Cycle count", ""),
            ("cell_average", "Cell average", "V"),
            ("cell_delta", "Cell delta", "V"),
            ("highest_cell", "Highest cell", ""),
            ("lowest_cell", "Lowest cell", ""),
            ("mos_temperature", "MOS temperature", "°C"),
            ("temperature_1", "Temperature 1", "°C"),
            ("temperature_2", "Temperature 2", "°C"),
            ("balance_current", "Balance current", "A"),
            ("charge_mos", "Charge MOS", ""),
            ("discharge_mos", "Discharge MOS", ""),
            ("errors_bitmask", "Errors", ""),
        ]
        self.live_units = {key: unit for key, _, unit in live_fields}
        for row, (key, label, _unit) in enumerate(live_fields):
            ttk.Label(dashboard, text=label + ":").grid(row=row, column=0, sticky="e", padx=(0, 12), pady=2)
            var = tk.StringVar(value="—")
            self.live_values[key] = var
            ttk.Label(dashboard, textvariable=var, font=("Consolas", 10)).grid(row=row, column=1, sticky="w", pady=2)
        dashboard.columnconfigure(1, weight=1)

        self.settings_tree = ttk.Treeview(
            settings_tab,
            columns=("category", "setting", "value", "unit", "key"),
            show="headings",
        )
        for col, title, width, anchor in (
            ("category", "Category", 130, "w"),
            ("setting", "Setting", 300, "w"),
            ("value", "Value", 130, "e"),
            ("unit", "Unit", 70, "w"),
            ("key", "OpenJK key", 210, "w"),
        ):
            self.settings_tree.heading(col, text=title)
            self.settings_tree.column(col, width=width, anchor=anchor)
        self.settings_tree.pack(fill="both", expand=True)

        self.cells_tree = ttk.Treeview(
            cells_tab,
            columns=("cell", "voltage", "wire_resistance"),
            show="headings",
        )
        for col, title, width in (
            ("cell", "Cell", 80),
            ("voltage", "Voltage", 140),
            ("wire_resistance", "Measured resistance", 160),
        ):
            self.cells_tree.heading(col, text=title)
            self.cells_tree.column(col, width=width, anchor="e")
        self.cells_tree.pack(fill="both", expand=True)

        from .protocol import SAFE_WRITABLE_PARAMETERS

        ttk.Label(
            writes_tab,
            text=(
                "First write milestone: four reversible JK02_32S voltage settings. "
                "Every write saves a backup and is verified from a fresh settings frame."
            ),
            wraplength=760,
            justify="left",
        ).grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 16))

        ttk.Label(writes_tab, text="Selected BMS:").grid(
            row=1, column=0, sticky="e", padx=(0, 10), pady=4
        )
        self.write_device_var = tk.StringVar(value="Not connected")
        ttk.Label(
            writes_tab, textvariable=self.write_device_var, font=("Consolas", 10)
        ).grid(row=1, column=1, columnspan=2, sticky="w", pady=4)

        ttk.Label(writes_tab, text="Parameter:").grid(
            row=2, column=0, sticky="e", padx=(0, 10), pady=4
        )
        self.write_parameter_var = tk.StringVar()
        self.write_parameter_combo = ttk.Combobox(
            writes_tab,
            textvariable=self.write_parameter_var,
            state="readonly",
            width=38,
        )
        self.write_parameter_labels = {
            definition.label: key
            for key, definition in SAFE_WRITABLE_PARAMETERS.items()
        }
        self.write_parameter_combo["values"] = list(self.write_parameter_labels)
        self.write_parameter_combo.grid(row=2, column=1, sticky="w", pady=4)
        self.write_parameter_combo.bind(
            "<<ComboboxSelected>>", self._write_parameter_selected
        )

        ttk.Label(writes_tab, text="Current value:").grid(
            row=3, column=0, sticky="e", padx=(0, 10), pady=4
        )
        self.write_current_var = tk.StringVar(value="—")
        ttk.Label(
            writes_tab, textvariable=self.write_current_var, font=("Consolas", 11)
        ).grid(row=3, column=1, sticky="w", pady=4)

        ttk.Label(writes_tab, text="New value:").grid(
            row=4, column=0, sticky="e", padx=(0, 10), pady=4
        )
        self.write_new_var = tk.StringVar()
        self.write_new_entry = ttk.Entry(
            writes_tab, textvariable=self.write_new_var, width=18, font=("Consolas", 11)
        )
        self.write_new_entry.grid(row=4, column=1, sticky="w", pady=4)
        self.write_unit_var = tk.StringVar(value="")
        ttk.Label(writes_tab, textvariable=self.write_unit_var).grid(
            row=4, column=2, sticky="w", padx=(6, 0), pady=4
        )

        buttons = ttk.Frame(writes_tab)
        buttons.grid(row=5, column=1, columnspan=2, sticky="w", pady=(14, 8))
        self.write_button = ttk.Button(
            buttons,
            text="Back up, write, and verify",
            command=self._safe_write,
            state="disabled",
        )
        self.write_button.pack(side="left")
        self.restore_button = ttk.Button(
            buttons,
            text="Restore original value",
            command=self._restore_original,
            state="disabled",
        )
        self.restore_button.pack(side="left", padx=(8, 0))

        self.write_status_var = tk.StringVar(
            value="Connect, read settings, and choose a parameter."
        )
        ttk.Label(
            writes_tab,
            textvariable=self.write_status_var,
            wraplength=780,
            justify="left",
        ).grid(row=6, column=0, columnspan=3, sticky="w", pady=(10, 0))

        ttk.Separator(writes_tab, orient="horizontal").grid(
            row=7, column=0, columnspan=3, sticky="ew", pady=18
        )
        ttk.Label(
            writes_tab,
            text=(
                "Recommended first test: change Start balance voltage by 0.010 V, "
                "verify PASS, then click Restore original value and verify PASS again."
            ),
            wraplength=760,
            justify="left",
        ).grid(row=8, column=0, columnspan=3, sticky="w")
        writes_tab.columnconfigure(1, weight=1)

        self.identity_tree = ttk.Treeview(identity_tab, columns=("field", "value"), show="headings")
        self.identity_tree.heading("field", text="Field")
        self.identity_tree.heading("value", text="Value")
        self.identity_tree.column("field", width=250, anchor="w")
        self.identity_tree.column("value", width=420, anchor="w")
        self.identity_tree.pack(fill="both", expand=True)

        self.raw_text = tk.Text(raw_tab, wrap="none", font=("Consolas", 9))
        ybar = ttk.Scrollbar(raw_tab, orient="vertical", command=self.raw_text.yview)
        xbar = ttk.Scrollbar(raw_tab, orient="horizontal", command=self.raw_text.xview)
        self.raw_text.configure(yscrollcommand=ybar.set, xscrollcommand=xbar.set)
        self.raw_text.grid(row=0, column=0, sticky="nsew")
        ybar.grid(row=0, column=1, sticky="ns")
        xbar.grid(row=1, column=0, sticky="ew")
        raw_tab.rowconfigure(0, weight=1)
        raw_tab.columnconfigure(0, weight=1)

        ttk.Label(
            outer,
            text=(
                "v0.3.1 enables four guarded JK02_32S voltage writes with "
                "automatic backup, readback verification, and one-click restore."
            ),
        ).pack(anchor="w", pady=(8, 0))

    def _connect_selected(self) -> None:
        selection = self.device_tree.selection()
        if not selection:
            messagebox.showinfo("OpenJK", "Select a BLE device first.")
            return
        index = int(selection[0])
        self.worker.send("connect", self.devices[index].device)

    def _selected_write_definition(self):
        from .protocol import SAFE_WRITABLE_PARAMETERS
        label = self.write_parameter_var.get()
        key = self.write_parameter_labels.get(label)
        if key is None:
            return None, None
        return key, SAFE_WRITABLE_PARAMETERS[key]

    def _write_parameter_selected(self, _event=None) -> None:
        self._refresh_write_panel()

    def _refresh_write_panel(self) -> None:
        key, definition = self._selected_write_definition()
        if not key or not definition:
            self.write_current_var.set("—")
            self.write_unit_var.set("")
            self.write_button.configure(state="disabled")
            return

        value = self.state.settings.get(key)
        if value is None:
            self.write_current_var.set("Not read")
            self.write_button.configure(state="disabled")
            return

        self.write_current_var.set(f"{float(value):.3f} {definition.unit}")
        self.write_unit_var.set(definition.unit)
        if not self.write_new_var.get():
            self.write_new_var.set(f"{float(value):.3f}")
        enabled = bool(self.state.selected_device_name and self.state.settings)
        self.write_button.configure(state="normal" if enabled else "disabled")

    def _safe_write(self) -> None:
        key, definition = self._selected_write_definition()
        if not key or not definition:
            messagebox.showerror("OpenJK", "Choose a write-enabled parameter.")
            return
        if not self.state.selected_device_name:
            messagebox.showerror("OpenJK", "Connect to the intended BMS first.")
            return
        if key not in self.state.settings:
            messagebox.showerror("OpenJK", "Read settings before writing.")
            return

        try:
            new_value = float(self.write_new_var.get().strip())
            raw_value, frame = definition.encode(new_value)
        except ValueError as exc:
            messagebox.showerror("OpenJK", str(exc))
            return

        old_value = float(self.state.settings[key])
        if abs(new_value - old_value) < (0.5 / definition.factor):
            messagebox.showinfo("OpenJK", "The new value is identical to the current value.")
            return

        confirm = messagebox.askyesno(
            "Confirm guarded JK write",
            (
                f"Selected BMS:\n{self.state.selected_device_name}\n"
                f"{self.state.selected_device_address}\n\n"
                f"Parameter: {definition.label}\n"
                f"Current: {old_value:.3f} {definition.unit}\n"
                f"New: {new_value:.3f} {definition.unit}\n\n"
                f"Register: 0x{definition.register:02X}\n"
                f"Raw value: {raw_value}\n"
                f"Frame:\n{frame.hex(' ').upper()}\n\n"
                "OpenJK will save a complete JSON backup, transmit the write, "
                "request fresh settings twice, and verify the readback.\n\n"
                "Proceed?"
            ),
        )
        if not confirm:
            return

        backups = Path.cwd() / "backups"
        backups.mkdir(exist_ok=True)
        serial = (
            self.state.device_info.get("serial_number")
            or self.state.selected_device_name
            or "jkbms"
        )
        safe_serial = "".join(
            ch if ch.isalnum() or ch in "-_" else "_" for ch in str(serial)
        )
        stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = backups / f"{safe_serial}_pre_write_{stamp}.json"
        self.state.save_backup(backup_path)

        self.pending_write = {
            "key": key,
            "label": definition.label,
            "expected": new_value,
            "old": old_value,
            "unit": definition.unit,
            "factor": definition.factor,
            "register": definition.register,
            "frame": frame,
            "backup_path": str(backup_path),
            "started_at": dt.datetime.now().isoformat(timespec="seconds"),
        }
        self.restore_value = {
            "key": key,
            "label": definition.label,
            "value": old_value,
            "unit": definition.unit,
        }
        self.write_button.configure(state="disabled")
        self.restore_button.configure(state="disabled")
        self.write_status_var.set(f"Backup saved to {backup_path}; transmitting...")
        self.worker.send("write_parameter", {"key": key, "value": new_value})

    def _check_pending_write(self, settings: dict[str, Any]) -> None:
        if not self.pending_write:
            return
        pending = self.pending_write
        actual = settings.get(pending["key"])
        if actual is None:
            return

        tolerance = 0.5 / float(pending["factor"])
        passed = abs(float(actual) - float(pending["expected"])) <= tolerance
        result = "PASS" if passed else "FAIL"
        self._append_transaction_log(
            "VERIFY",
            {
                **pending,
                "actual": float(actual),
            },
            result=result,
        )

        if passed:
            self.write_status_var.set(
                f"PASS: {pending['label']} read back as "
                f"{float(actual):.3f} {pending['unit']}. "
                "The original value is available for restoration."
            )
            self.restore_button.configure(state="normal")
            self.write_current_var.set(f"{float(actual):.3f} {pending['unit']}")
        else:
            self.write_status_var.set(
                f"FAIL: expected {float(pending['expected']):.3f} "
                f"{pending['unit']}, read back {float(actual):.3f} "
                f"{pending['unit']}. No further write was attempted."
            )
        self.pending_write = None
        self.write_button.configure(state="normal")

    def _restore_original(self) -> None:
        if not self.restore_value:
            messagebox.showinfo("OpenJK", "No original value is queued for restoration.")
            return

        from .protocol import SAFE_WRITABLE_PARAMETERS
        restore = self.restore_value
        definition = SAFE_WRITABLE_PARAMETERS[restore["key"]]
        value = float(restore["value"])
        raw_value, frame = definition.encode(value)

        confirm = messagebox.askyesno(
            "Restore original value",
            (
                f"Restore {restore['label']} to "
                f"{value:.3f} {restore['unit']}?\n\n"
                f"Register: 0x{definition.register:02X}\n"
                f"Frame:\n{frame.hex(' ').upper()}"
            ),
        )
        if not confirm:
            return

        current = float(self.state.settings.get(restore["key"], value))
        self.pending_write = {
            "key": restore["key"],
            "label": restore["label"] + " (restore)",
            "expected": value,
            "old": current,
            "unit": restore["unit"],
            "factor": definition.factor,
            "register": definition.register,
            "frame": frame,
            "backup_path": "pre-write backup already saved",
            "started_at": dt.datetime.now().isoformat(timespec="seconds"),
        }
        self.restore_button.configure(state="disabled")
        self.write_button.configure(state="disabled")
        self.write_status_var.set("Restoring original value and waiting for readback...")
        self.worker.send("write_parameter", {"key": restore["key"], "value": value})

    def _append_transaction_log(
        self,
        phase: str,
        payload: dict[str, Any],
        result: str,
    ) -> None:
        transactions = Path.cwd() / "transactions"
        transactions.mkdir(exist_ok=True)
        path = transactions / "openjk_write_transactions.log"
        record = {
            "timestamp": dt.datetime.now().isoformat(timespec="seconds"),
            "phase": phase,
            "result": result,
            "bms_name": self.state.selected_device_name,
            "bms_address": self.state.selected_device_address,
            **{
                key: (
                    value.hex(" ").upper()
                    if isinstance(value, (bytes, bytearray))
                    else value
                )
                for key, value in payload.items()
            },
        }
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, sort_keys=True) + "\n")

    def _save_backup(self) -> None:
        if not self.state.settings:
            messagebox.showinfo("OpenJK", "No settings frame has been received yet.")
            return
        serial = self.state.device_info.get("serial_number") or self.state.selected_device_name or "jkbms"
        safe_serial = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in str(serial))
        stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        default = f"{safe_serial}_openjk_backup_{stamp}.json"
        filename = filedialog.asksaveasfilename(
            title="Save complete JK BMS backup",
            initialfile=default,
            defaultextension=".json",
            filetypes=[("JSON backup", "*.json"), ("All files", "*.*")],
        )
        if not filename:
            return
        self.state.save_backup(Path(filename))
        self.status_var.set(f"Backup saved: {filename}")

    def _drain_events(self) -> None:
        while True:
            try:
                kind, payload = self.events.get_nowait()
            except queue.Empty:
                break

            if kind == "status":
                self.status_var.set(str(payload))
            elif kind == "error":
                self.status_var.set(str(payload))
                messagebox.showerror("OpenJK error", str(payload))
            elif kind == "devices":
                self._show_devices(payload)
            elif kind == "connected":
                self._handle_connected(payload)
            elif kind == "tx":
                self._log("TX", payload)
            elif kind == "rx_chunk":
                self._log("RX-CHUNK", payload)
            elif kind == "rx_frame":
                self._log(f"RX-FRAME type=0x{payload[4]:02X} len={len(payload)}", payload)
            elif kind == "settings":
                self.state.settings = payload
                self.state.last_update = dt.datetime.now().isoformat(timespec="seconds")
                self._display_settings(payload)
                self.backup_button.configure(state="normal")
                self._refresh_write_panel()
                self._check_pending_write(payload)
                self.status_var.set(f"Settings received: {len(settings_rows(payload))} decoded values")
            elif kind == "write_started":
                self.write_status_var.set(
                    f"Sent {payload['label']} = {payload['value']:.3f}; "
                    "waiting for fresh settings readback..."
                )
                self._append_transaction_log(
                    "TX",
                    payload,
                    result="SENT; verification pending",
                )
            elif kind == "device_info":
                self.state.device_info = payload
                self._display_identity(payload)
                self.status_var.set("Device identity received")
            elif kind == "live":
                self.state.live = payload
                self._display_live(payload)
                self._display_cells(payload.get("cells", []))
                self.status_var.set(
                    f"Live: {payload.get('pack_voltage', 0):.3f} V, "
                    f"{payload.get('pack_current', 0):+.3f} A"
                )
        self.root.after(75, self._drain_events)

    def _handle_connected(self, payload: dict[str, Any]) -> None:
        if payload.get("connected"):
            self.state.selected_device_name = payload.get("name", "")
            self.state.selected_device_address = payload.get("address", "")
            self.write_device_var.set(
                f"{self.state.selected_device_name} ({self.state.selected_device_address})"
            )
        else:
            self.write_device_var.set("Not connected")
            self.write_button.configure(state="disabled")
            self.restore_button.configure(state="disabled")
            self.status_var.set("Disconnected")

    def _show_devices(self, devices: list[DeviceRow]) -> None:
        self.devices = devices
        self.device_tree.delete(*self.device_tree.get_children())
        for index, row in enumerate(devices):
            rssi = "" if row.rssi is None else str(row.rssi)
            self.device_tree.insert("", "end", iid=str(index), values=(row.name, row.address, rssi))

    def _display_settings(self, values: dict[str, Any]) -> None:
        self.settings_tree.delete(*self.settings_tree.get_children())
        for category, label, value, unit, key in settings_rows(values):
            if isinstance(value, bool):
                shown = "On" if value else "Off"
            elif isinstance(value, float):
                shown = f"{value:.3f}".rstrip("0").rstrip(".")
            else:
                shown = value
            self.settings_tree.insert("", "end", values=(category, label, shown, unit, key))

    def _display_identity(self, values: dict[str, Any]) -> None:
        self.identity_tree.delete(*self.identity_tree.get_children())
        for key, value in values.items():
            self.identity_tree.insert("", "end", values=(key, value))

    def _display_live(self, values: dict[str, Any]) -> None:
        for key, var in self.live_values.items():
            value = values.get(key)
            if value is None:
                continue
            if key == "errors_bitmask":
                shown = f"0x{int(value):08X}"
            elif isinstance(value, bool):
                shown = "On" if value else "Off"
            elif isinstance(value, float):
                shown = f"{value:.3f}"
            else:
                shown = str(value)
            unit = self.live_units.get(key, "")
            var.set(f"{shown} {unit}".strip())

    def _display_cells(self, cells: list[dict[str, Any]]) -> None:
        self.cells_tree.delete(*self.cells_tree.get_children())
        for cell in cells:
            self.cells_tree.insert(
                "",
                "end",
                values=(
                    cell["number"],
                    f"{cell['voltage']:.3f} V",
                    f"{cell['wire_resistance']:.3f} Ω",
                ),
            )

    def _log(self, direction: str, payload: bytes) -> None:
        timestamp = dt.datetime.now().isoformat(timespec="milliseconds")
        line = f"{timestamp} {direction} {payload.hex(' ').upper()}\n"
        with self.raw_log.open("a", encoding="utf-8") as handle:
            handle.write(line)
        if direction.startswith("RX-FRAME") or direction == "TX":
            self.raw_text.insert("end", line)
            self.raw_text.see("end")

    def _close(self) -> None:
        self.worker.send("stop")
        self.root.after(150, self.root.destroy)


def main() -> None:
    root = tk.Tk()
    try:
        ttk.Style(root).theme_use("vista")
    except tk.TclError:
        pass
    OpenJKApp(root)
    root.mainloop()
