
from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Any, Callable

SERVICE_UUID = "0000ffe0-0000-1000-8000-00805f9b34fb"
CHAR_UUID = "0000ffe1-0000-1000-8000-00805f9b34fb"
RESPONSE_HEADER = b"\x55\xAA\xEB\x90"
MIN_FRAME = 300
MAX_FRAME = 320


def crc8_sum(data: bytes) -> int:
    return sum(data) & 0xFF


def make_command(command: int, counter: int = 0) -> bytes:
    frame = bytearray(20)
    frame[0:4] = b"\xAA\x55\x90\xEB"
    frame[4] = command & 0xFF
    frame[5] = 0
    frame[16] = counter & 0xFF
    frame[19] = crc8_sum(frame[:19])
    return bytes(frame)


def make_write_command(register: int, value: int, length: int = 4) -> bytes:
    """Build a JK BLE holding-register write frame.

    Signed values are encoded using two's complement, which is required for
    negative temperature thresholds.
    """
    if not 0 <= register <= 0xFF:
        raise ValueError("register must be in the range 0x00..0xFF")
    if length not in (1, 2, 4):
        raise ValueError("length must be 1, 2, or 4 bytes")

    signed = value < 0
    minimum = -(1 << (length * 8 - 1)) if signed else 0
    maximum = (1 << (length * 8 - (1 if signed else 0))) - 1
    if not minimum <= value <= maximum:
        raise ValueError(f"value does not fit in {length} byte(s)")

    frame = bytearray(20)
    frame[0:4] = b"\xAA\x55\x90\xEB"
    frame[4] = register
    frame[5] = length
    encoded = int(value).to_bytes(length, "little", signed=signed)
    frame[6 : 6 + length] = encoded
    frame[19] = crc8_sum(frame[:19])
    return bytes(frame)


@dataclass(frozen=True)
class WritableParameter:
    key: str
    label: str
    category: str
    register: int
    factor: float
    length: int
    minimum: float
    maximum: float
    unit: str
    step: float
    kind: str = "number"
    critical: bool = False
    note: str = ""

    def encode(self, value: float) -> tuple[int, bytes]:
        if self.kind == "bool":
            value = 1.0 if bool(value) else 0.0
        if not self.minimum <= value <= self.maximum:
            raise ValueError(
                f"{self.label} must be between {self.minimum:g} and "
                f"{self.maximum:g} {self.unit}".rstrip()
            )
        raw = round(value * self.factor)
        return raw, make_write_command(self.register, raw, self.length)


def _p(
    key: str,
    label: str,
    category: str,
    register: int,
    factor: float,
    length: int,
    minimum: float,
    maximum: float,
    unit: str,
    step: float,
    *,
    kind: str = "number",
    critical: bool = False,
    note: str = "",
) -> WritableParameter:
    return WritableParameter(
        key, label, category, register, factor, length,
        minimum, maximum, unit, step, kind, critical, note
    )


# JK02_32S / PB / V19 holding-register map.
#
# This table is the write engine's source of truth. The transaction code does
# not contain parameter-specific branches.
WRITABLE_PARAMETERS: dict[str, WritableParameter] = {
    # Voltage and SOC
    "smart_sleep_voltage": _p("smart_sleep_voltage", "Smart sleep voltage", "Voltage", 0x01, 1000, 1, 0.003, 0.255, "V", 0.001),
    "cell_uvp": _p("cell_uvp", "Cell UVP", "Voltage", 0x02, 1000, 4, 1.2, 4.35, "V", 0.001, critical=True),
    "cell_uvpr": _p("cell_uvpr", "Cell UVPR", "Voltage", 0x03, 1000, 4, 1.2, 4.35, "V", 0.001, critical=True),
    "cell_ovp": _p("cell_ovp", "Cell OVP", "Voltage", 0x04, 1000, 4, 1.2, 4.35, "V", 0.001, critical=True),
    "cell_ovpr": _p("cell_ovpr", "Cell OVPR", "Voltage", 0x05, 1000, 4, 1.2, 4.35, "V", 0.001, critical=True),
    "balance_trigger_voltage": _p("balance_trigger_voltage", "Balance trigger voltage", "Balancing", 0x06, 1000, 4, 0.003, 1.0, "V", 0.001),
    "soc_100_voltage": _p("soc_100_voltage", "SOC 100% voltage", "SOC", 0x07, 1000, 4, 0.003, 3.65, "V", 0.001),
    "soc_0_voltage": _p("soc_0_voltage", "SOC 0% voltage", "SOC", 0x08, 1000, 4, 0.003, 3.65, "V", 0.001),
    "cell_rcv": _p("cell_rcv", "Cell request charge voltage", "Charging", 0x09, 1000, 4, 0.003, 3.65, "V", 0.001),
    "cell_rfv": _p("cell_rfv", "Cell request float voltage", "Charging", 0x0A, 1000, 4, 0.003, 3.65, "V", 0.001),
    "power_off_voltage": _p("power_off_voltage", "Power-off voltage", "Voltage", 0x0B, 1000, 4, 1.2, 4.35, "V", 0.01, critical=True),

    # Current, timing, balancing and pack
    "max_charge_current": _p("max_charge_current", "Maximum charge current", "Current", 0x0C, 1000, 4, 1.0, 600.1, "A", 0.1, critical=True),
    "charge_ocp_delay": _p("charge_ocp_delay", "Charge OCP delay", "Protection", 0x0D, 1, 4, 2, 600, "s", 1),
    "charge_ocp_recovery": _p("charge_ocp_recovery", "Charge OCP recovery time", "Protection", 0x0E, 1, 4, 2, 600, "s", 1),
    "max_discharge_current": _p("max_discharge_current", "Maximum discharge current", "Current", 0x0F, 1000, 4, 1.0, 1200.1, "A", 0.1, critical=True),
    "discharge_ocp_delay": _p("discharge_ocp_delay", "Discharge OCP delay", "Protection", 0x10, 1, 4, 2, 600, "s", 1),
    "discharge_ocp_recovery": _p("discharge_ocp_recovery", "Discharge OCP recovery time", "Protection", 0x11, 1, 4, 2, 600, "s", 1),
    "scp_recovery": _p("scp_recovery", "Short-circuit recovery time", "Protection", 0x12, 1, 4, 2, 600, "s", 1),
    "max_balance_current": _p("max_balance_current", "Maximum balance current", "Balancing", 0x13, 1000, 4, 0.3, 15.0, "A", 0.1),
    "cell_count": _p("cell_count", "Cell count", "Pack", 0x1C, 1, 4, 2, 32, "", 1, critical=True, note="Changing cell count can disable or misconfigure protection."),
    "nominal_capacity": _p("nominal_capacity", "Nominal battery capacity", "Pack", 0x20, 1000, 4, 2, 20000, "Ah", 1),
    "scp_delay": _p("scp_delay", "Short-circuit protection delay", "Protection", 0x21, 1, 4, 0, 1000000, "µs", 1, critical=True),
    "start_balance_voltage": _p("start_balance_voltage", "Start balance voltage", "Balancing", 0x22, 1000, 4, 1.2, 4.25, "V", 0.01),
    "precharge_time": _p("precharge_time", "Precharge time", "Controls", 0x25, 1, 4, 0, 255, "s", 1),

    # Temperature
    "charge_otp": _p("charge_otp", "Charge OTP", "Temperature", 0x14, 10, 4, 30, 80, "°C", 0.1, critical=True),
    "charge_otp_recovery": _p("charge_otp_recovery", "Charge OTP recovery", "Temperature", 0x15, 10, 4, 30, 80, "°C", 0.1),
    "discharge_otp": _p("discharge_otp", "Discharge OTP", "Temperature", 0x16, 10, 4, 30, 80, "°C", 0.1, critical=True),
    "discharge_otp_recovery": _p("discharge_otp_recovery", "Discharge OTP recovery", "Temperature", 0x17, 10, 4, 30, 80, "°C", 0.1),
    "charge_utp": _p("charge_utp", "Charge UTP", "Temperature", 0x18, 10, 4, -45, 20, "°C", 0.1, critical=True),
    "charge_utp_recovery": _p("charge_utp_recovery", "Charge UTP recovery", "Temperature", 0x19, 10, 4, -45, 20, "°C", 0.1),
    "mos_otp": _p("mos_otp", "MOS OTP", "Temperature", 0x1A, 10, 4, 50, 110, "°C", 0.1, critical=True),
    "mos_otp_recovery": _p("mos_otp_recovery", "MOS OTP recovery", "Temperature", 0x1B, 10, 4, 50, 110, "°C", 0.1),
    "heating_start_temperature": _p("heating_start_temperature", "Heating start temperature", "Heating", 0x37, 1, 1, -40, 100, "°C", 1),
    "heating_stop_temperature": _p("heating_stop_temperature", "Heating stop temperature", "Heating", 0x38, 1, 1, -40, 100, "°C", 1),
    "smart_sleep_hours": _p("smart_sleep_hours", "Smart sleep delay", "Sleep", 0x39, 1, 1, 1, 100, "h", 1),
    "discharge_utp": _p("discharge_utp", "Discharge UTP", "Temperature", 0x3A, 1, 1, -40, 100, "°C", 1, critical=True),
    "discharge_utp_recovery": _p("discharge_utp_recovery", "Discharge UTP recovery", "Temperature", 0x3B, 1, 1, -40, 100, "°C", 1),

    # Primary MOS and feature controls
    "charge_switch": _p("charge_switch", "Charge MOS", "Controls", 0x1D, 1, 4, 0, 1, "", 1, kind="bool", critical=True),
    "discharge_switch": _p("discharge_switch", "Discharge MOS", "Controls", 0x1E, 1, 4, 0, 1, "", 1, kind="bool", critical=True),
    "balancer_switch": _p("balancer_switch", "Balancer", "Controls", 0x1F, 1, 4, 0, 1, "", 1, kind="bool"),
    "heating_enabled": _p("heating_enabled", "Heating enabled", "Controls", 0x27, 1, 4, 0, 1, "", 1, kind="bool"),
    "temperature_sensors_disabled": _p("temperature_sensors_disabled", "Disable temperature sensors", "Controls", 0x28, 1, 4, 0, 1, "", 1, kind="bool", critical=True),
    "display_always_on": _p("display_always_on", "Display always on", "Controls", 0x2B, 1, 4, 0, 1, "", 1, kind="bool"),
    "smart_sleep_enabled": _p("smart_sleep_enabled", "Smart sleep enabled", "Controls", 0x2D, 1, 4, 0, 1, "", 1, kind="bool"),
    "pcl_module_disabled": _p("pcl_module_disabled", "Disable PCL module", "Controls", 0x2E, 1, 4, 0, 1, "", 1, kind="bool"),
    "timed_stored_data": _p("timed_stored_data", "Timed stored data", "Controls", 0x2F, 1, 4, 0, 1, "", 1, kind="bool"),
    "charging_float_mode": _p("charging_float_mode", "Charging float mode", "Controls", 0x30, 1, 4, 0, 1, "", 1, kind="bool"),
}

# Compatibility alias used by the v0.3 transaction and GUI code.
SAFE_WRITABLE_PARAMETERS = WRITABLE_PARAMETERS


GET_SETTINGS = make_command(0x96)
GET_DEVICE_INFO = make_command(0x97)


def clean_ascii(data: bytes) -> str:
    return data.split(b"\x00", 1)[0].decode("ascii", errors="replace").strip()


def unpack_le(fmt: str, data: bytes, offset: int, default: Any = None) -> Any:
    try:
        return struct.unpack_from("<" + fmt, data, offset)[0]
    except (struct.error, IndexError):
        return default


def frame_crc_valid(frame: bytes) -> bool:
    return len(frame) >= 2 and crc8_sum(frame[:-1]) == frame[-1]


class FrameAssembler:
    def __init__(self) -> None:
        self.buffer = bytearray()
        self.capturing = False

    def feed(self, chunk: bytes) -> list[bytes]:
        frames: list[bytes] = []

        start = chunk.find(RESPONSE_HEADER)
        if start >= 0:
            self.buffer = bytearray(chunk[start:])
            self.capturing = True
        elif self.capturing:
            self.buffer.extend(chunk)
        else:
            return frames

        if len(self.buffer) > MAX_FRAME:
            self.buffer.clear()
            self.capturing = False
            return frames

        if len(self.buffer) >= MIN_FRAME:
            for length in range(MIN_FRAME, min(len(self.buffer), MAX_FRAME) + 1):
                candidate = bytes(self.buffer[:length])
                if frame_crc_valid(candidate):
                    frames.append(candidate)
                    remainder = self.buffer[length:]
                    self.buffer = bytearray(remainder)
                    self.capturing = False
                    break
        return frames


@dataclass(frozen=True)
class SettingDefinition:
    key: str
    label: str
    category: str
    offset: int
    fmt: str
    scale: float = 1.0
    unit: str = ""
    display: str = "number"

    def decode(self, frame: bytes) -> Any:
        raw = unpack_le(self.fmt, frame, self.offset)
        if raw is None:
            return None
        if self.display == "bool":
            return bool(raw)
        return raw * self.scale


SETTINGS: tuple[SettingDefinition, ...] = (
    SettingDefinition("smart_sleep_voltage", "Smart sleep voltage", "Voltage", 6, "I", 0.001, "V"),
    SettingDefinition("cell_uvp", "Cell UVP", "Voltage", 10, "I", 0.001, "V"),
    SettingDefinition("cell_uvpr", "Cell UVPR", "Voltage", 14, "I", 0.001, "V"),
    SettingDefinition("cell_ovp", "Cell OVP", "Voltage", 18, "I", 0.001, "V"),
    SettingDefinition("cell_ovpr", "Cell OVPR", "Voltage", 22, "I", 0.001, "V"),
    SettingDefinition("balance_trigger_voltage", "Balance trigger voltage", "Balancing", 26, "I", 0.001, "V"),
    SettingDefinition("soc_100_voltage", "SOC 100% voltage", "SOC", 30, "I", 0.001, "V"),
    SettingDefinition("soc_0_voltage", "SOC 0% voltage", "SOC", 34, "I", 0.001, "V"),
    SettingDefinition("cell_rcv", "Cell request charge voltage", "Charging", 38, "I", 0.001, "V"),
    SettingDefinition("cell_rfv", "Cell request float voltage", "Charging", 42, "I", 0.001, "V"),
    SettingDefinition("power_off_voltage", "Power-off voltage", "Voltage", 46, "I", 0.001, "V"),
    SettingDefinition("max_charge_current", "Maximum charge current", "Current", 50, "I", 0.001, "A"),
    SettingDefinition("charge_ocp_delay", "Charge OCP delay", "Protection", 54, "I", 1.0, "s"),
    SettingDefinition("charge_ocp_recovery", "Charge OCP recovery time", "Protection", 58, "I", 1.0, "s"),
    SettingDefinition("max_discharge_current", "Maximum discharge current", "Current", 62, "I", 0.001, "A"),
    SettingDefinition("discharge_ocp_delay", "Discharge OCP delay", "Protection", 66, "I", 1.0, "s"),
    SettingDefinition("discharge_ocp_recovery", "Discharge OCP recovery time", "Protection", 70, "I", 1.0, "s"),
    SettingDefinition("scp_recovery", "Short-circuit recovery time", "Protection", 74, "I", 1.0, "s"),
    SettingDefinition("max_balance_current", "Maximum balance current", "Balancing", 78, "I", 0.001, "A"),
    SettingDefinition("charge_otp", "Charge OTP", "Temperature", 82, "I", 0.1, "°C"),
    SettingDefinition("charge_otp_recovery", "Charge OTP recovery", "Temperature", 86, "I", 0.1, "°C"),
    SettingDefinition("discharge_otp", "Discharge OTP", "Temperature", 90, "I", 0.1, "°C"),
    SettingDefinition("discharge_otp_recovery", "Discharge OTP recovery", "Temperature", 94, "I", 0.1, "°C"),
    SettingDefinition("charge_utp", "Charge UTP", "Temperature", 98, "i", 0.1, "°C"),
    SettingDefinition("charge_utp_recovery", "Charge UTP recovery", "Temperature", 102, "i", 0.1, "°C"),
    SettingDefinition("mos_otp", "MOS OTP", "Temperature", 106, "i", 0.1, "°C"),
    SettingDefinition("mos_otp_recovery", "MOS OTP recovery", "Temperature", 110, "i", 0.1, "°C"),
    SettingDefinition("cell_count", "Cell count", "Pack", 114, "B"),
    SettingDefinition("charge_switch", "Charge switch", "Controls", 118, "B", display="bool"),
    SettingDefinition("discharge_switch", "Discharge switch", "Controls", 122, "B", display="bool"),
    SettingDefinition("balancer_switch", "Balancer switch", "Controls", 126, "B", display="bool"),
    SettingDefinition("nominal_capacity", "Nominal battery capacity", "Pack", 130, "I", 0.001, "Ah"),
    SettingDefinition("scp_delay", "Short-circuit protection delay", "Protection", 134, "I", 1.0, "µs"),
    SettingDefinition("start_balance_voltage", "Start balance voltage", "Balancing", 138, "I", 0.001, "V"),
    SettingDefinition("device_address", "Device address", "Communications", 270, "B"),
    SettingDefinition("precharge_time", "Precharge time", "Controls", 274, "B", 1.0, "s"),
    SettingDefinition("heating_start_temperature", "Heating start temperature", "Heating", 284, "b", 1.0, "°C"),
    SettingDefinition("heating_stop_temperature", "Heating stop temperature", "Heating", 285, "b", 1.0, "°C"),
    SettingDefinition("smart_sleep_hours", "Smart sleep", "Sleep", 286, "B", 1.0, "h"),
    SettingDefinition("data_field_enable_0", "Data field enable control 0", "Advanced", 287, "B"),
    SettingDefinition("discharge_utp", "Discharge UTP", "Temperature", 296, "b", 1.0, "°C"),
    SettingDefinition("discharge_utp_recovery", "Discharge UTP recovery", "Temperature", 297, "b", 1.0, "°C"),
)


CONTROL_BITS = {
    0: "heating_enabled",
    1: "temperature_sensors_disabled",
    2: "gps_heartbeat",
    3: "port_rs485",
    4: "display_always_on",
    5: "special_charger",
    6: "smart_sleep_enabled",
    7: "pcl_module_disabled",
    8: "timed_stored_data",
    9: "charging_float_mode",
}


def parse_settings(frame: bytes) -> dict[str, Any]:
    if len(frame) < 300 or frame[4] != 0x01:
        raise ValueError("Not a JK settings frame")

    values: dict[str, Any] = {}
    for definition in SETTINGS:
        values[definition.key] = definition.decode(frame)

    wire_resistance: list[float] = []
    for index in range(32):
        raw = unpack_le("I", frame, 142 + index * 4, 0)
        wire_resistance.append(raw * 0.001)
    values["wire_resistance"] = wire_resistance

    controls = unpack_le("H", frame, 282, 0) or 0
    values["controls_bitmask"] = controls
    values["controls"] = {
        name: bool(controls & (1 << bit))
        for bit, name in CONTROL_BITS.items()
    }
    values.update(values["controls"])

    values["_frame_counter"] = frame[5]
    values["_crc_valid"] = frame_crc_valid(frame)
    return values


def settings_rows(values: dict[str, Any]) -> list[tuple[str, str, Any, str, str]]:
    rows: list[tuple[str, str, Any, str, str]] = []
    definitions = {item.key: item for item in SETTINGS}
    for item in SETTINGS:
        rows.append((item.category, item.label, values.get(item.key), item.unit, item.key))

    for index, value in enumerate(values.get("wire_resistance", []), start=1):
        rows.append(("Wire resistance", f"Cell {index} wire resistance", value, "Ω", f"wire_resistance_{index}"))

    for key, value in values.get("controls", {}).items():
        rows.append(("Control flags", key.replace("_", " ").title(), value, "", key))
    return rows


def parse_device_info(frame: bytes) -> dict[str, Any]:
    if len(frame) < 300 or frame[4] != 0x03:
        raise ValueError("Not a JK device information frame")
    return {
        "vendor_id": clean_ascii(frame[6:22]),
        "hardware_version": clean_ascii(frame[22:30]),
        "software_version": clean_ascii(frame[30:38]),
        "device_uptime_s": unpack_le("I", frame, 38, 0),
        "power_on_count": unpack_le("I", frame, 42, 0),
        "device_name": clean_ascii(frame[46:62]),
        "device_passcode": clean_ascii(frame[62:78]),
        "manufacturing_date": clean_ascii(frame[78:86]),
        "serial_number": clean_ascii(frame[86:102]),
        "user_data": clean_ascii(frame[102:118]),
        "setup_passcode": clean_ascii(frame[118:134]),
        "uart1_protocol": unpack_le("B", frame, 184, 0),
        "can_protocol": unpack_le("B", frame, 185, 0),
        "uart2_protocol": unpack_le("B", frame, 218, 0),
        "uart2_enabled": bool(unpack_le("B", frame, 219, 0)),
        "data_stored_period": unpack_le("I", frame, 262, 0),
        "rcv_time_h": (unpack_le("B", frame, 266, 0) or 0) * 0.1,
        "rfv_time_h": (unpack_le("B", frame, 267, 0) or 0) * 0.1,
        "_frame_counter": frame[5],
        "_crc_valid": frame_crc_valid(frame),
    }


def parse_live_info(frame: bytes) -> dict[str, Any]:
    if len(frame) < 300 or frame[4] != 0x02:
        raise ValueError("Not a JK live-data frame")

    enabled_mask = unpack_le("I", frame, 70, 0) or 0
    cells: list[dict[str, Any]] = []
    for index in range(32):
        voltage = (unpack_le("H", frame, 6 + index * 2, 0) or 0) * 0.001
        resistance = (unpack_le("H", frame, 80 + index * 2, 0) or 0) * 0.001
        if enabled_mask & (1 << index):
            cells.append({
                "number": index + 1,
                "voltage": voltage,
                "wire_resistance": resistance,
            })

    current = (unpack_le("i", frame, 158, 0) or 0) * 0.001
    voltage = (unpack_le("I", frame, 150, 0) or 0) * 0.001
    return {
        "cells": cells,
        "cell_count": len(cells),
        "cell_average": (unpack_le("H", frame, 74, 0) or 0) * 0.001,
        "cell_delta": (unpack_le("H", frame, 76, 0) or 0) * 0.001,
        "highest_cell": (unpack_le("B", frame, 78, 0) or 0) + 1,
        "lowest_cell": (unpack_le("B", frame, 79, 0) or 0) + 1,
        "mos_temperature": (unpack_le("h", frame, 144, 0) or 0) * 0.1,
        "pack_voltage": voltage,
        "pack_power": voltage * current,
        "pack_current": current,
        "temperature_1": (unpack_le("h", frame, 162, 0) or 0) * 0.1,
        "temperature_2": (unpack_le("h", frame, 164, 0) or 0) * 0.1,
        "errors_bitmask": unpack_le("I", frame, 166, 0) or 0,
        "balance_current": (unpack_le("h", frame, 170, 0) or 0) * 0.001,
        "balance_action": unpack_le("B", frame, 172, 0),
        "soc": unpack_le("B", frame, 173, 0),
        "remaining_capacity": (unpack_le("I", frame, 174, 0) or 0) * 0.001,
        "nominal_capacity": (unpack_le("I", frame, 178, 0) or 0) * 0.001,
        "cycle_count": unpack_le("I", frame, 182, 0),
        "total_cycle_capacity": (unpack_le("I", frame, 186, 0) or 0) * 0.001,
        "soh": unpack_le("B", frame, 190, 0),
        "charge_mos": bool(unpack_le("B", frame, 198, 0)),
        "discharge_mos": bool(unpack_le("B", frame, 199, 0)),
        "heating": bool(unpack_le("B", frame, 215, 0)),
        "temperature_3": (unpack_le("h", frame, 258, 0) or 0) * 0.1,
        "temperature_4": (unpack_le("h", frame, 256, 0) or 0) * 0.1,
        "temperature_5": (unpack_le("h", frame, 254, 0) or 0) * 0.1,
        "_frame_counter": frame[5],
        "_crc_valid": frame_crc_valid(frame),
    }


def decode_frame(frame: bytes) -> tuple[str, dict[str, Any]]:
    if len(frame) < 6:
        raise ValueError("Frame too short")
    frame_type = frame[4]
    if frame_type == 0x01:
        return "settings", parse_settings(frame)
    if frame_type == 0x02:
        return "live", parse_live_info(frame)
    if frame_type == 0x03:
        return "device_info", parse_device_info(frame)
    return "unknown", {
        "frame_type": frame_type,
        "frame_counter": frame[5],
        "crc_valid": frame_crc_valid(frame),
    }
