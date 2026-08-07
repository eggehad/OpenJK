
from __future__ import annotations

import datetime as dt
from decimal import Decimal
import json
import queue
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any

from .engine import BMSState, BleWorker, DeviceRow
from .protocol import SETTINGS, settings_rows

APP_VERSION = "0.4.4"


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
        self.max_write_attempts = 3

        # Cell laboratory state
        self.latest_cells: list[dict[str, Any]] = []
        self.display_cells: list[dict[str, Any]] = []
        self.cell_color_mode = tk.StringVar(value="Deviation")
        self.cell_band_mv = tk.DoubleVar(value=3.0)
        self.history_enabled = tk.BooleanVar(value=True)
        self.history_samples: list[dict[str, Any]] = []
        self.history_playing = False
        self.history_after_id: str | None = None
        self.hover_cell_number: int | None = None
        # Slow visual state for the deviation/color strip.  Numerical values stay live;
        # only the color presentation is deliberately damped so sub-mV jitter does not flash.
        self.cell_color_tau_seconds = 25.0
        self.cell_smoothed_metrics: dict[int, float] = {}
        self.cell_smoothing_mode = self.cell_color_mode.get()
        self.cell_smoothing_last_time: float | None = None
        # Short rolling voltage history used by the H (60 s drift) display mode.
        self.cell_trend_history: list[tuple[float, dict[int, float]]] = []

        stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        self.raw_log = Path.cwd() / f"openjk_raw_{stamp}.log"
        self.gui_diag_log = Path.cwd() / f"openjk_gui_diag_{stamp}.log"
        self.gui_drain_cycles = 0
        self.gui_events_processed = 0

        self._build_ui()
        self._bind_cell_mode_keys()
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

        # Physical cell laboratory
        cell_toolbar = ttk.Frame(cells_tab)
        cell_toolbar.pack(fill="x", pady=(0, 8))

        ttk.Label(cell_toolbar, text="Color mode:").pack(side="left")
        self.cell_mode_combo = ttk.Combobox(
            cell_toolbar,
            textvariable=self.cell_color_mode,
            values=("Deviation", "Absolute voltage", "Wire resistance", "Trend (60 s)"),
            state="readonly",
            width=18,
        )
        self.cell_mode_combo.pack(side="left", padx=(6, 14))
        self.cell_mode_combo.bind(
            "<<ComboboxSelected>>", lambda _event: self._draw_cell_map()
        )

        ttk.Label(cell_toolbar, text="Band:").pack(side="left")
        self.cell_band_spin = ttk.Spinbox(
            cell_toolbar,
            from_=1.0,
            to=20.0,
            increment=1.0,
            textvariable=self.cell_band_mv,
            width=5,
            command=self._draw_cell_map,
        )
        self.cell_band_spin.pack(side="left", padx=(6, 2))
        ttk.Label(cell_toolbar, text="mV").pack(side="left")

        ttk.Checkbutton(
            cell_toolbar,
            text="Capture history",
            variable=self.history_enabled,
        ).pack(side="left", padx=(18, 0))

        ttk.Label(
            cell_toolbar,
            text="  Keys: D deviation   V voltage   R resistance   H 60 s drift",
            foreground="#5d6772",
        ).pack(side="left", padx=(14, 0))

        ttk.Button(
            cell_toolbar,
            text="Load history…",
            command=self._load_history,
        ).pack(side="right")
        ttk.Button(
            cell_toolbar,
            text="Live",
            command=self._show_live_cells,
        ).pack(side="right", padx=(0, 8))

        map_frame = ttk.Frame(cells_tab)
        map_frame.pack(fill="both", expand=True)

        self.cell_canvas = tk.Canvas(
            map_frame,
            background="#f7f8fa",
            highlightthickness=1,
            highlightbackground="#c8cdd3",
        )
        self.cell_canvas.pack(side="left", fill="both", expand=True)
        self.cell_canvas.bind("<Configure>", lambda _event: self._draw_cell_map())
        self.cell_canvas.bind("<Motion>", self._cell_hover)
        self.cell_canvas.bind("<Leave>", self._cell_leave)

        details = ttk.Frame(map_frame, padding=(12, 4))
        details.pack(side="right", fill="y")
        ttk.Label(details, text="Cell details", font=("Segoe UI", 11, "bold")).pack(anchor="w")
        self.cell_detail_var = tk.StringVar(
            value="Move the pointer over a cell."
        )
        ttk.Label(
            details,
            textvariable=self.cell_detail_var,
            justify="left",
            width=29,
            wraplength=230,
        ).pack(anchor="w", pady=(8, 18))

        ttk.Separator(details, orient="horizontal").pack(fill="x", pady=4)
        ttk.Label(details, text="Pack statistics", font=("Segoe UI", 11, "bold")).pack(anchor="w", pady=(8, 4))
        self.cell_stats_var = tk.StringVar(value="Waiting for live cell data.")
        ttk.Label(
            details,
            textvariable=self.cell_stats_var,
            justify="left",
            width=29,
        ).pack(anchor="w")

        history_frame = ttk.LabelFrame(cells_tab, text="Time-lapse", padding=8)
        history_frame.pack(fill="x", pady=(8, 0))
        self.history_status_var = tk.StringVar(value="Live view")
        ttk.Label(
            history_frame,
            textvariable=self.history_status_var,
        ).pack(side="left")

        self.history_scale = ttk.Scale(
            history_frame,
            from_=0,
            to=1,
            orient="horizontal",
            command=self._history_scrub,
        )
        self.history_scale.pack(side="left", fill="x", expand=True, padx=12)
        self.history_scale.configure(state="disabled")

        self.history_play_button = ttk.Button(
            history_frame,
            text="▶ Play",
            command=self._toggle_history_play,
            state="disabled",
        )
        self.history_play_button.pack(side="right")

        from .protocol import SAFE_WRITABLE_PARAMETERS

        ttk.Label(
            writes_tab,
            text=(
                "All decoded JK02_32S settings use the same guarded write engine. "
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
        ordered = sorted(
            SAFE_WRITABLE_PARAMETERS.items(),
            key=lambda item: (item[1].category, item[1].label),
        )
        self.write_parameter_labels = {
            f"{definition.category} — {definition.label}": key
            for key, definition in ordered
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
                "v0.4.4 keeps the bounded GUI event drain, reduces routine logging, "
                "and combines live signed deviation bars with the slow color-memory strip."
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

        if definition.kind == "bool":
            shown = "On" if bool(value) else "Off"
            self.write_current_var.set(shown)
            self.write_new_var.set("1" if bool(value) else "0")
            self.write_unit_var.set("0 = Off, 1 = On")
        else:
            decimals = max(0, -Decimal(str(definition.step)).as_tuple().exponent)
            self.write_current_var.set(
                f"{float(value):.{decimals}f} {definition.unit}".strip()
            )
            self.write_new_var.set(f"{float(value):.{decimals}f}")
            self.write_unit_var.set(definition.unit)
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
            entered = self.write_new_var.get().strip().lower()
            if definition.kind == "bool":
                aliases = {
                    "0": 0.0, "off": 0.0, "false": 0.0, "no": 0.0,
                    "1": 1.0, "on": 1.0, "true": 1.0, "yes": 1.0,
                }
                if entered not in aliases:
                    raise ValueError("Enter 0/Off or 1/On for this switch.")
                new_value = aliases[entered]
            else:
                new_value = float(entered)
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
                + (
                    "CRITICAL SETTING: verify the selected BMS and value carefully.\n"
                    if definition.critical else ""
                )
                + (f"{definition.note}\n" if definition.note else "")
                + "\nOpenJK will save a complete JSON backup, transmit the write, "
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
            "attempt": 1,
            "max_attempts": self.max_write_attempts,
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

        self._append_transaction_log(
            "VERIFY",
            {
                **pending,
                "actual": float(actual),
            },
            result="PASS" if passed else "NOT_ACCEPTED",
        )

        if passed:
            attempts = int(pending.get("attempt", 1))
            self.write_status_var.set(
                f"PASS after {attempts} attempt"
                f"{'' if attempts == 1 else 's'}: {pending['label']} "
                f"read back as {float(actual):.3f} {pending['unit']}. "
                "The original value is available for restoration."
            )
            self.restore_button.configure(state="normal")
            self.write_current_var.set(
                f"{float(actual):.3f} {pending['unit']}"
            )
            self.pending_write = None
            self.write_button.configure(state="normal")
            return

        attempt = int(pending.get("attempt", 1))
        maximum = int(pending.get("max_attempts", self.max_write_attempts))

        if attempt < maximum:
            next_attempt = attempt + 1
            pending["attempt"] = next_attempt
            self.write_status_var.set(
                f"NOT ACCEPTED on attempt {attempt}/{maximum}: expected "
                f"{float(pending['expected']):.3f} {pending['unit']}, "
                f"read back {float(actual):.3f} {pending['unit']}. "
                f"Retrying automatically in 1.5 seconds..."
            )
            self._append_transaction_log(
                "RETRY",
                {
                    **pending,
                    "actual": float(actual),
                    "next_attempt": next_attempt,
                },
                result="RETRY_SCHEDULED",
            )
            self.root.after(
                1500,
                lambda: self.worker.send(
                    "write_parameter",
                    {
                        "key": pending["key"],
                        "value": pending["expected"],
                    },
                ),
            )
            return

        self.write_status_var.set(
            f"FAILED after {maximum} attempts: expected "
            f"{float(pending['expected']):.3f} {pending['unit']}, "
            f"read back {float(actual):.3f} {pending['unit']}. "
            "The BMS did not accept the value. No additional write was sent."
        )
        self._append_transaction_log(
            "FINAL",
            {
                **pending,
                "actual": float(actual),
            },
            result="FAILED_AFTER_RETRIES",
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
            "attempt": 1,
            "max_attempts": self.max_write_attempts,
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
        # Keep GUI work bounded so a continuous BLE stream can never monopolize
        # Tk/Windows message processing.  v0.4.4 keeps the v0.4.2 fix but only
        # writes diagnostics when something is actually abnormal.
        cycle_start = time.perf_counter()
        processed = 0
        max_events = 32
        max_seconds = 0.012
        self.gui_drain_cycles += 1

        while processed < max_events and (time.perf_counter() - cycle_start) < max_seconds:
            try:
                kind, payload = self.events.get_nowait()
            except queue.Empty:
                break

            event_start = time.perf_counter()
            try:
                self._handle_gui_event(kind, payload)
            except Exception as exc:
                self._diag(f"EVENT ERROR kind={kind} {type(exc).__name__}: {exc}")
                raise
            finally:
                elapsed_ms = (time.perf_counter() - event_start) * 1000.0
                processed += 1
                self.gui_events_processed += 1
                if elapsed_ms >= 100.0:
                    self._diag(
                        f"SLOW EVENT kind={kind} elapsed={elapsed_ms:.3f}ms "
                        f"queue={self.events.qsize()}"
                    )

        cycle_ms = (time.perf_counter() - cycle_start) * 1000.0
        remaining = self.events.qsize()
        if cycle_ms >= 50.0 or remaining >= 64:
            self._diag(
                f"GUI BACKLOG cycle={self.gui_drain_cycles} processed={processed} "
                f"elapsed={cycle_ms:.3f}ms remaining={remaining}"
            )

        self.root.after(1 if remaining else 75, self._drain_events)

    def _handle_gui_event(self, kind: str, payload: Any) -> None:
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
            # Retained for compatibility with older workers, but intentionally
            # ignored in v0.4.4.  Complete RX-FRAME records contain the useful
            # protocol bytes without duplicating every BLE fragment.
            pass
        elif kind == "rx_frame":
            self._log(f"RX-FRAME type=0x{payload[4]:02X} len={len(payload)}", payload)
        elif kind == "settings":
            self.state.settings = payload
            self.state.last_update = dt.datetime.now().isoformat(timespec="seconds")
            self._timed_gui_call("settings.display", self._display_settings, payload)
            self.backup_button.configure(state="normal")
            self._timed_gui_call("settings.refresh_write", self._refresh_write_panel)
            self._timed_gui_call("settings.pending_write", self._check_pending_write, payload)
            self.status_var.set(f"Settings received: {len(settings_rows(payload))} decoded values")
        elif kind == "write_started":
            attempt = (
                self.pending_write.get("attempt", 1)
                if self.pending_write
                else 1
            )
            self.write_status_var.set(
                f"Attempt {attempt}/{self.max_write_attempts}: sent "
                f"{payload['label']} = {payload['value']:.3f}; "
                "waiting for fresh settings readback..."
            )
            self._append_transaction_log(
                "TX",
                payload,
                result="SENT; verification pending",
            )
        elif kind == "device_info":
            self.state.device_info = payload
            self._timed_gui_call("identity.display", self._display_identity, payload)
            self.status_var.set("Device identity received")
        elif kind == "live":
            self.state.live = payload
            self._timed_gui_call("live.display", self._display_live, payload)
            cells = payload.get("cells", [])
            self.latest_cells = [dict(cell) for cell in cells]
            self._record_cell_trend_sample(self.latest_cells)
            if not self.history_samples:
                self.display_cells = [dict(cell) for cell in cells]
                self._timed_gui_call("live.draw_cells", self._draw_cell_map)
            if self.history_enabled.get():
                self._capture_history_sample(payload)
            self.status_var.set(
                f"Live: {payload.get('pack_voltage', 0):.3f} V, "
                f"{payload.get('pack_current', 0):+.1f} A"
            )

    def _timed_gui_call(self, label: str, function, *args, **kwargs):
        started = time.perf_counter()
        try:
            return function(*args, **kwargs)
        finally:
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            if elapsed_ms >= 100.0:
                self._diag(f"SLOW GUI {label} elapsed={elapsed_ms:.3f}ms")

    def _diag(self, message: str) -> None:
        # Sparse field diagnostics only.  Normal operation writes nothing here;
        # the file is created only if an error, >100 ms GUI call, or queue backlog
        # is observed.
        timestamp = dt.datetime.now().isoformat(timespec="milliseconds")
        try:
            with self.gui_diag_log.open("a", encoding="utf-8") as handle:
                handle.write(f"{timestamp} {message}\n")
        except OSError:
            pass

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

    def _bind_cell_mode_keys(self) -> None:
        for key in ("d", "v", "r", "h"):
            self.root.bind(f"<{key}>", self._cell_mode_key)
            self.root.bind(f"<{key.upper()}>", self._cell_mode_key)

    def _cell_mode_key(self, event: tk.Event) -> None:
        # Do not steal letters while the user is typing into an editable control.
        if isinstance(event.widget, (tk.Entry, ttk.Entry, ttk.Spinbox, ttk.Combobox)):
            return
        modes = {
            "d": "Deviation",
            "v": "Absolute voltage",
            "r": "Wire resistance",
            "h": "Trend (60 s)",
        }
        mode = modes.get(str(event.keysym).lower())
        if mode:
            self.cell_color_mode.set(mode)
            self.cell_smoothed_metrics.clear()
            self.cell_smoothing_last_time = None
            self.cell_smoothing_mode = mode
            self._draw_cell_map()

    def _record_cell_trend_sample(self, cells: list[dict[str, Any]]) -> None:
        now = time.monotonic()
        snapshot = {int(c["number"]): float(c["voltage"]) for c in cells}
        self.cell_trend_history.append((now, snapshot))
        cutoff = now - 300.0
        while self.cell_trend_history and self.cell_trend_history[0][0] < cutoff:
            self.cell_trend_history.pop(0)

    def _trend_metric(self, cell: dict[str, Any], average: float) -> float:
        if not self.cell_trend_history:
            return 0.0
        target = time.monotonic() - 60.0
        then_time, then_cells = min(
            self.cell_trend_history, key=lambda sample: abs(sample[0] - target)
        )
        # Until there is useful history, leave the trend neutral instead of inventing motion.
        if time.monotonic() - then_time < 20.0:
            return 0.0
        number = int(cell["number"])
        if number not in then_cells:
            return 0.0
        then_average = sum(then_cells.values()) / len(then_cells)
        now_delta = (float(cell["voltage"]) - average) * 1000.0
        then_delta = (then_cells[number] - then_average) * 1000.0
        return now_delta - then_delta

    def _smoothed_metrics(self, raw: dict[int, float]) -> dict[int, float]:
        mode = self.cell_color_mode.get()
        now = time.monotonic()
        if mode != self.cell_smoothing_mode or self.cell_smoothing_last_time is None:
            self.cell_smoothed_metrics = dict(raw)
            self.cell_smoothing_mode = mode
            self.cell_smoothing_last_time = now
            return dict(raw)
        elapsed = max(0.0, now - self.cell_smoothing_last_time)
        self.cell_smoothing_last_time = now
        # Exponential fade with ~25 s time constant: fast enough to follow real pack movement,
        # slow enough to suppress the one-frame red/blue confetti seen in v0.4.0.
        alpha = 1.0 - pow(2.718281828459045, -elapsed / self.cell_color_tau_seconds)
        for number, target in raw.items():
            old = self.cell_smoothed_metrics.get(number, target)
            self.cell_smoothed_metrics[number] = old + (target - old) * alpha
        return dict(self.cell_smoothed_metrics)

    @staticmethod
    def _mix_color(left: str, right: str, fraction: float) -> str:
        fraction = max(0.0, min(1.0, fraction))
        a = tuple(int(left[index:index + 2], 16) for index in (1, 3, 5))
        b = tuple(int(right[index:index + 2], 16) for index in (1, 3, 5))
        rgb = tuple(round(x + (y - x) * fraction) for x, y in zip(a, b))
        return "#{:02x}{:02x}{:02x}".format(*rgb)

    @classmethod
    def _heat_color(cls, fraction: float) -> str:
        stops = (
            (0.00, "#2775d8"),
            (0.25, "#36bfc6"),
            (0.50, "#8dce62"),
            (0.70, "#e5d33f"),
            (0.85, "#f29335"),
            (1.00, "#eb4545"),
        )
        fraction = max(0.0, min(1.0, fraction))
        for (x0, c0), (x1, c1) in zip(stops, stops[1:]):
            if x0 <= fraction <= x1:
                return cls._mix_color(c0, c1, (fraction - x0) / (x1 - x0))
        return stops[-1][1]

    def _physical_positions(self, count: int) -> list[tuple[int, int, int]]:
        """Return one continuous row of BMS channels; channel 9 starts after a midpoint gap."""
        return [(number, 0, number - 1) for number in range(1, count + 1)]

    def _cell_metric(self, cell: dict[str, Any], average: float) -> float:
        mode = self.cell_color_mode.get()
        if mode == "Wire resistance":
            return float(cell.get("wire_resistance", 0.0))
        if mode == "Absolute voltage":
            return float(cell.get("voltage", 0.0))
        if mode == "Trend (60 s)":
            return self._trend_metric(cell, average)
        return (float(cell.get("voltage", 0.0)) - average) * 1000.0

    def _draw_cell_map(self) -> None:
        if not hasattr(self, "cell_canvas"):
            return
        canvas = self.cell_canvas
        canvas.delete("all")
        cells = self.display_cells or self.latest_cells
        width = max(canvas.winfo_width(), 700)
        height = max(canvas.winfo_height(), 340)

        if not cells:
            canvas.create_text(
                width / 2, height / 2,
                text="Waiting for live cell data…", fill="#58606b",
                font=("Segoe UI", 14),
            )
            return

        cells = sorted(cells, key=lambda item: int(item["number"]))
        voltages = [float(cell["voltage"]) for cell in cells]
        average = sum(voltages) / len(voltages)
        low_voltage = min(voltages)
        high_voltage = max(voltages)
        low_number = int(min(cells, key=lambda item: float(item["voltage"]))["number"])
        high_number = int(max(cells, key=lambda item: float(item["voltage"]))["number"])

        # Color memory follows the selected mode and is deliberately slow.
        raw_metrics = {
            int(cell["number"]): self._cell_metric(cell, average) for cell in cells
        }
        metrics = self._smoothed_metrics(raw_metrics)
        mode = self.cell_color_mode.get()
        metric_values = list(metrics.values())
        if mode in ("Deviation", "Trend (60 s)"):
            band = max(float(self.cell_band_mv.get()), 0.5)
            metric_min, metric_max = -band, band
        else:
            metric_min, metric_max = min(metric_values), max(metric_values)
            if abs(metric_max - metric_min) < 1e-12:
                metric_max = metric_min + 1.0

        # The signed bars always show CURRENT deviation from pack average,
        # independent of the selected color-memory mode.
        bar_band = max(float(self.cell_band_mv.get()), 0.5)

        margin_x = 34
        top = 48
        block_gap = 4
        midpoint_gap = 22
        count = len(cells)
        total_gaps = max(0, count - 1) * block_gap + (midpoint_gap if count > 8 else 0)
        available_w = width - 2 * margin_x - total_gaps
        block_w = max(34.0, available_w / max(count, 1))
        block_h = max(72.0, min(96.0, height * 0.28))

        bar_top = top + block_h + 12
        bar_h = max(64.0, min(94.0, height * 0.26))
        bar_center = bar_top + bar_h / 2
        strip_y0 = bar_top + bar_h + 8
        strip_h = 12

        canvas.create_text(
            margin_x, 18, text="Battery cell map", anchor="w",
            fill="#1f2933", font=("Segoe UI", 12, "bold"),
        )
        canvas.create_text(
            width - margin_x, 18,
            text=f"{count} BMS groups / {count * 2} physical cells   •   color: {mode}",
            anchor="e", fill="#66717e", font=("Segoe UI", 9),
        )

        # Precompute the x positions so the zero line and all bars share exactly
        # the same geometry as the compound-cell row above.
        positions: list[tuple[dict[str, Any], float, float]] = []
        x = margin_x
        for index, cell in enumerate(cells):
            if index == 8:
                x += midpoint_gap
            positions.append((cell, x, x + block_w))
            x += block_w + block_gap

        zero_x0 = positions[0][1]
        zero_x1 = positions[-1][2]
        canvas.create_line(zero_x0, bar_center, zero_x1, bar_center, fill="#77818c", width=1)
        canvas.create_text(
            margin_x - 5, bar_top, text=f"+{bar_band:g}", anchor="e",
            fill="#66717e", font=("Consolas", 7),
        )
        canvas.create_text(
            margin_x - 5, bar_center, text="0", anchor="e",
            fill="#66717e", font=("Consolas", 7),
        )
        canvas.create_text(
            margin_x - 5, bar_top + bar_h, text=f"-{bar_band:g}", anchor="e",
            fill="#66717e", font=("Consolas", 7),
        )

        self.cell_hitboxes = []
        for index, (cell, x0, x1) in enumerate(positions):
            number = int(cell["number"])
            y0, y1 = top, top + block_h

            if index == 8:
                split_x = x0 - midpoint_gap / 2
                canvas.create_line(
                    split_x, top - 7, split_x, strip_y0 + strip_h + 5,
                    fill="#a9b0b8", dash=(2, 4),
                )

            outline = "#59636e"
            outline_width = 1
            if number == high_number:
                outline, outline_width = "#d51f2b", 3
            elif number == low_number:
                outline, outline_width = "#087fd0", 3

            canvas.create_rectangle(
                x0, y0, x1, y1, fill="#eef1f4", outline=outline, width=outline_width
            )
            pair_a = 2 * number - 1
            pair_b = 2 * number
            canvas.create_text(
                (x0 + x1) / 2, y0 + 15, text=f"{pair_a} | {pair_b}",
                fill="#111820", font=("Segoe UI", 8, "bold"),
            )
            canvas.create_line(
                (x0 + x1) / 2, y0 + 4, (x0 + x1) / 2, y0 + 25,
                fill="#aab1b8", width=1,
            )
            canvas.create_text(
                (x0 + x1) / 2, y0 + 43,
                text=f"{float(cell['voltage']):.3f}",
                fill="#111820", font=("Consolas", 10, "bold"),
            )
            delta_mv = (float(cell["voltage"]) - average) * 1000.0
            canvas.create_text(
                (x0 + x1) / 2, y1 - 12, text=f"{delta_mv:+.1f} mV",
                fill="#25313d", font=("Consolas", 8),
            )

            # Live signed deviation bar.  Positive rises above zero; negative falls below.
            clipped = max(-bar_band, min(bar_band, delta_mv))
            half_h = bar_h / 2 - 3
            magnitude = abs(clipped) / bar_band * half_h
            bar_w = max(5.0, min(12.0, block_w * 0.24))
            bx0 = (x0 + x1) / 2 - bar_w / 2
            bx1 = bx0 + bar_w
            if clipped >= 0:
                by0, by1 = bar_center - magnitude, bar_center
            else:
                by0, by1 = bar_center, bar_center + magnitude
            if magnitude > 0.5:
                canvas.create_rectangle(
                    bx0, by0, bx1, by1, fill="#747d87", outline=""
                )
                # A small colored tip keeps hue as a secondary cue while bar height
                # remains the dominant quantitative signal.
                tip = min(4.0, magnitude)
                frac = (clipped + bar_band) / (2.0 * bar_band)
                tip_color = self._heat_color(frac)
                if clipped >= 0:
                    canvas.create_rectangle(bx0, by0, bx1, by0 + tip, fill=tip_color, outline="")
                else:
                    canvas.create_rectangle(bx0, by1 - tip, bx1, by1, fill=tip_color, outline="")

            # Slow color-memory strip: selected metric, approximately 25 s time constant.
            metric = metrics[number]
            fraction = (metric - metric_min) / (metric_max - metric_min)
            canvas.create_rectangle(
                x0, strip_y0, x1, strip_y0 + strip_h,
                fill=self._heat_color(fraction), outline="",
            )
            self.cell_hitboxes.append((x0, y0, x1, strip_y0 + strip_h, number))

        canvas.create_text(
            margin_x, strip_y0 + strip_h + 14,
            text="live signed Δ bars  •  slow color memory ~25 s", anchor="w",
            fill="#66717e", font=("Segoe UI", 8),
        )

        # Compact color legend.  Deviation bars use the ±Band scale printed beside them.
        legend_y = max(strip_y0 + strip_h + 30, height - 24)
        legend_x0 = margin_x + 80
        legend_x1 = width - margin_x - 80
        segments = 64
        for i in range(segments):
            xa = legend_x0 + (legend_x1 - legend_x0) * i / segments
            xb = legend_x0 + (legend_x1 - legend_x0) * (i + 1) / segments
            canvas.create_rectangle(
                xa, legend_y, xb + 1, legend_y + 8,
                fill=self._heat_color(i / (segments - 1)), outline="",
            )
        canvas.create_text(legend_x0 - 8, legend_y + 4, text="Low", anchor="e",
                           fill="#4d5965", font=("Segoe UI", 8))
        canvas.create_text(legend_x1 + 8, legend_y + 4, text="High", anchor="w",
                           fill="#4d5965", font=("Segoe UI", 8))

        delta = (high_voltage - low_voltage) * 1000.0
        self.cell_stats_var.set(
            f"Average:     {average:.3f} V\n"
            f"Highest:    Group {high_number}  {high_voltage:.3f} V\n"
            f"Lowest:     Group {low_number}  {low_voltage:.3f} V\n"
            f"Delta:       {delta:.1f} mV\n"
            f"BMS groups:  {len(cells)}\n"
            f"Physical:    {len(cells) * 2} cells\n"
            f"Mode:        {mode}"
        )

    def _cell_hover(self, event: tk.Event) -> None:
        for x0, y0, x1, y1, number in getattr(self, "cell_hitboxes", []):
            if x0 <= event.x <= x1 and y0 <= event.y <= y1:
                if number == self.hover_cell_number:
                    return
                self.hover_cell_number = number
                cells = self.display_cells or self.latest_cells
                cell = next(
                    (item for item in cells if int(item["number"]) == number),
                    None,
                )
                if not cell:
                    return
                voltages = [float(item["voltage"]) for item in cells]
                average = sum(voltages) / len(voltages)
                delta_mv = (float(cell["voltage"]) - average) * 1000.0
                pair_a, pair_b = 2 * number - 1, 2 * number
                half = "second half" if number > 8 else "first half"
                self.cell_detail_var.set(
                    f"BMS group {number}  •  physical cells {pair_a}/{pair_b}\n\n"
                    f"Voltage:          {float(cell['voltage']):.3f} V\n"
                    f"From average:     {delta_mv:+.1f} mV\n"
                    f"Wire resistance:  {float(cell.get('wire_resistance', 0.0)):.3f} Ω\n\n"
                    f"Pack position:    {half}"
                )
                return
        self._cell_leave()

    def _cell_leave(self, _event: tk.Event | None = None) -> None:
        self.hover_cell_number = None
        self.cell_detail_var.set("Move the pointer over a cell.")

    def _history_path(self) -> Path:
        history_dir = Path.cwd() / "history"
        history_dir.mkdir(exist_ok=True)
        serial = (
            self.state.device_info.get("serial_number")
            or self.state.selected_device_name
            or "jkbms"
        )
        safe_serial = "".join(
            char if char.isalnum() or char in "-_" else "_"
            for char in str(serial)
        )
        day = dt.datetime.now().strftime("%Y%m%d")
        return history_dir / f"{safe_serial}_{day}.jsonl"

    def _capture_history_sample(self, payload: dict[str, Any]) -> None:
        cells = payload.get("cells", [])
        if not cells:
            return
        sample = {
            "timestamp": dt.datetime.now().isoformat(timespec="milliseconds"),
            "pack_voltage": payload.get("pack_voltage"),
            "pack_current": payload.get("pack_current"),
            "soc": payload.get("soc"),
            "cell_average": payload.get("cell_average"),
            "cell_delta": payload.get("cell_delta"),
            "balance_current": payload.get("balance_current"),
            "temperatures": [
                payload.get("mos_temperature"),
                payload.get("temperature_1"),
                payload.get("temperature_2"),
            ],
            "cells": [
                {
                    "number": int(cell["number"]),
                    "voltage": float(cell["voltage"]),
                    "wire_resistance": float(cell.get("wire_resistance", 0.0)),
                }
                for cell in cells
            ],
        }
        with self._history_path().open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(sample, separators=(",", ":")) + "\n")

    def _load_history(self) -> None:
        filename = filedialog.askopenfilename(
            title="Load OpenJK cell history",
            initialdir=str(Path.cwd() / "history"),
            filetypes=[
                ("OpenJK history", "*.jsonl"),
                ("All files", "*.*"),
            ],
        )
        if not filename:
            return
        samples: list[dict[str, Any]] = []
        try:
            with Path(filename).open("r", encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if line:
                        samples.append(json.loads(line))
        except (OSError, json.JSONDecodeError) as exc:
            messagebox.showerror("OpenJK history", str(exc))
            return
        if not samples:
            messagebox.showinfo("OpenJK history", "The selected file contains no samples.")
            return

        self.history_samples = samples
        self.history_scale.configure(
            from_=0,
            to=max(1, len(samples) - 1),
            state="normal",
        )
        self.history_scale.set(0)
        self.history_play_button.configure(state="normal")
        self._show_history_index(0)

    def _history_scrub(self, value: str) -> None:
        if not self.history_samples:
            return
        self._show_history_index(round(float(value)))

    def _show_history_index(self, index: int) -> None:
        if not self.history_samples:
            return
        index = max(0, min(len(self.history_samples) - 1, index))
        sample = self.history_samples[index]
        self.display_cells = [dict(cell) for cell in sample.get("cells", [])]
        self._draw_cell_map()
        timestamp = sample.get("timestamp", "")
        current = sample.get("pack_current")
        current_text = "—" if current is None else f"{float(current):+.1f} A"
        self.history_status_var.set(
            f"{index + 1:,}/{len(self.history_samples):,}  "
            f"{timestamp}  Current {current_text}"
        )

    def _show_live_cells(self) -> None:
        self._stop_history_play()
        self.history_samples = []
        self.display_cells = [dict(cell) for cell in self.latest_cells]
        self.history_scale.configure(state="disabled")
        self.history_play_button.configure(state="disabled")
        self.history_status_var.set("Live view")
        self._draw_cell_map()

    def _toggle_history_play(self) -> None:
        if not self.history_samples:
            return
        if self.history_playing:
            self._stop_history_play()
        else:
            self.history_playing = True
            self.history_play_button.configure(text="Ⅱ Pause")
            self._advance_history()

    def _advance_history(self) -> None:
        if not self.history_playing or not self.history_samples:
            return
        current = round(float(self.history_scale.get()))
        next_index = current + 1
        if next_index >= len(self.history_samples):
            next_index = 0
        self.history_scale.set(next_index)
        self._show_history_index(next_index)
        self.history_after_id = self.root.after(90, self._advance_history)

    def _stop_history_play(self) -> None:
        self.history_playing = False
        if hasattr(self, "history_play_button"):
            self.history_play_button.configure(text="▶ Play")
        if self.history_after_id is not None:
            self.root.after_cancel(self.history_after_id)
            self.history_after_id = None

    def _log(self, direction: str, payload: bytes) -> None:
        timestamp = dt.datetime.now().isoformat(timespec="milliseconds")
        line = f"{timestamp} {direction} {payload.hex(' ').upper()}\n"
        with self.raw_log.open("a", encoding="utf-8") as handle:
            handle.write(line)
        if direction.startswith("RX-FRAME") or direction == "TX":
            self.raw_text.insert("end", line)
            self.raw_text.see("end")

    def _close(self) -> None:
        self._stop_history_play()
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
