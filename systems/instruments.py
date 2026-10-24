"""Read-only serial/SCPI instrument access for Bemo Engineering.

Mutating bench commands are intentionally excluded. Device-specific telemetry can
be configured in ~/.bemo/instruments.json as named, allowlisted queries. This
supports streamed Arduino/Teensy telemetry, many BMS/USB meters, and SCPI-capable
power supplies/scopes without exposing arbitrary serial writes to the model.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class InstrumentError(ValueError):
    pass


def _extract_value(text: str, regex: str = "", group: int = 1) -> Any:
    if not regex:
        return text.strip()
    match = re.search(regex, text, flags=re.I | re.M)
    if not match:
        raise InstrumentError("Configured parse_regex did not match the instrument response")
    value = match.group(group)
    try:
        return float(value)
    except ValueError:
        return value


@dataclass
class InstrumentManager:
    config_path: Path
    max_read_bytes: int = 64 * 1024

    def __post_init__(self):
        self.config_path = Path(self.config_path).expanduser().resolve()
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.config_path.exists():
            self.config_path.write_text(json.dumps({"profiles": {}}, indent=2), encoding="utf-8")

    def _profiles(self) -> dict[str, Any]:
        try:
            data = json.loads(self.config_path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise InstrumentError(f"Could not read instrument config: {exc}") from exc
        profiles = data.get("profiles") or {}
        if not isinstance(profiles, dict):
            raise InstrumentError("Instrument config profiles must be an object")
        return profiles

    def list_devices(self) -> dict[str, Any]:
        devices = []
        try:
            from serial.tools import list_ports
            for port in list_ports.comports():
                devices.append({
                    "device": port.device, "description": port.description, "hwid": port.hwid,
                    "vid": port.vid, "pid": port.pid, "serial_number": port.serial_number,
                    "manufacturer": port.manufacturer, "product": port.product,
                })
        except Exception as exc:
            return {"serial_devices": [], "error": f"{type(exc).__name__}: {exc}", "profiles": list(self._profiles())}
        return {"serial_devices": devices, "profiles": list(self._profiles())}

    @staticmethod
    def _port_allowed(port: str) -> str:
        value = str(port).strip()
        if not (value.startswith("/dev/tty") or value.startswith("/dev/serial/")):
            raise InstrumentError("Only local /dev/tty* or /dev/serial/* ports are allowed")
        return value

    def serial_read(self, port: str, baud: int = 115200, lines: int = 10, timeout: float = 2.0) -> dict[str, Any]:
        import serial
        port = self._port_allowed(port)
        lines = max(1, min(200, int(lines)))
        timeout = max(0.1, min(10.0, float(timeout)))
        output = []
        total = 0
        with serial.Serial(port=port, baudrate=int(baud), timeout=timeout) as ser:
            for _ in range(lines):
                data = ser.readline(self.max_read_bytes - total)
                if not data:
                    break
                total += len(data)
                output.append(data.decode("utf-8", errors="replace").rstrip("\r\n"))
                if total >= self.max_read_bytes:
                    break
        return {"port": port, "baud": int(baud), "lines": output, "bytes_read": total, "mode": "passive-read"}

    def scpi_query(self, resource: str, query: str, timeout_ms: int = 3000) -> dict[str, Any]:
        # Only query-style SCPI is allowed. Semicolon chaining is blocked because
        # a query and a state-changing command could otherwise be combined.
        q = query.strip()
        if not q.endswith("?") or ";" in q or "\n" in q or "\r" in q:
            raise InstrumentError("Only a single read-only SCPI query ending in '?' is allowed")
        import pyvisa
        rm = pyvisa.ResourceManager("@py")
        inst = rm.open_resource(resource)
        try:
            inst.timeout = max(100, min(10_000, int(timeout_ms)))
            response = inst.query(q)
        finally:
            inst.close(); rm.close()
        return {"resource": resource, "query": q, "response": response.strip(), "mode": "read-only-scpi-query"}

    def _serial_profile_query(self, profile: dict[str, Any], query_def: dict[str, Any]) -> dict[str, Any]:
        import serial
        port = self._port_allowed(str(profile.get("port", "")))
        baud = int(profile.get("baud", 115200))
        timeout = max(0.1, min(10.0, float(query_def.get("timeout", profile.get("timeout", 2.0)))))
        command = query_def.get("command")
        passive = bool(query_def.get("passive", False))
        encoding = str(profile.get("encoding", "utf-8"))
        with serial.Serial(port=port, baudrate=baud, timeout=timeout) as ser:
            if not passive:
                if command is None:
                    raise InstrumentError("Profile query requires command or passive=true")
                # The exact command is loaded from the user's local allowlist,
                # never invented by the LLM at runtime.
                payload = str(command).encode(encoding)
                line_ending = str(query_def.get("line_ending", profile.get("line_ending", "\\n")))
                line_ending = bytes(line_ending, "utf-8").decode("unicode_escape").encode(encoding)
                ser.write(payload + line_ending)
                ser.flush()
            read_mode = str(query_def.get("read_mode", "line"))
            if read_mode == "bytes":
                count = max(1, min(self.max_read_bytes, int(query_def.get("read_bytes", 64))))
                data = ser.read(count)
            else:
                data = ser.readline(self.max_read_bytes)
        text = data.decode(encoding, errors="replace").strip()
        value = _extract_value(text, str(query_def.get("parse_regex", "")), int(query_def.get("parse_group", 1)))
        return {"transport": "serial", "port": port, "raw": text, "value": value, "unit": query_def.get("unit"), "profile_read_only_allowlist": True}

    def profile_query(self, profile_name: str, query_name: str) -> dict[str, Any]:
        profiles = self._profiles()
        if profile_name not in profiles:
            raise InstrumentError(f"Unknown instrument profile: {profile_name}")
        profile = profiles[profile_name]
        queries = profile.get("queries") or {}
        if query_name not in queries:
            raise InstrumentError(f"Query {query_name!r} is not allowlisted for profile {profile_name!r}")
        query_def = queries[query_name]
        transport = str(profile.get("transport", "serial")).lower()
        if transport == "serial":
            result = self._serial_profile_query(profile, query_def)
        elif transport == "scpi":
            command = str(query_def.get("command", ""))
            result = self.scpi_query(str(profile.get("resource", "")), command, int(query_def.get("timeout_ms", 3000)))
            result["value"] = _extract_value(result["response"], str(query_def.get("parse_regex", "")), int(query_def.get("parse_group", 1)))
            result["unit"] = query_def.get("unit")
        else:
            raise InstrumentError("Profile transport must be serial or scpi")
        result.update({"profile": profile_name, "query_name": query_name})
        return result

    @staticmethod
    def _vesc_crc16(data: bytes) -> int:
        crc = 0
        for byte in data:
            crc ^= byte << 8
            for _ in range(8):
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF if (crc & 0x8000) else (crc << 1) & 0xFFFF
        return crc

    @classmethod
    def _vesc_packet(cls, payload: bytes) -> bytes:
        length = len(payload)
        if length < 256:
            header = bytes([2, length])
        else:
            header = bytes([3, (length >> 8) & 0xFF, length & 0xFF])
        crc = cls._vesc_crc16(payload)
        return header + payload + bytes([(crc >> 8) & 0xFF, crc & 0xFF, 3])

    @classmethod
    def _vesc_read_packet(cls, ser, timeout: float) -> bytes:
        deadline = time.time() + timeout
        while time.time() < deadline:
            start = ser.read(1)
            if not start:
                continue
            if start[0] == 2:
                raw = ser.read(1)
                if len(raw) != 1: continue
                length = raw[0]
            elif start[0] == 3:
                raw = ser.read(2)
                if len(raw) != 2: continue
                length = (raw[0] << 8) | raw[1]
            else:
                continue
            if length <= 0 or length > 4096:
                continue
            payload = ser.read(length)
            trailer = ser.read(3)
            if len(payload) != length or len(trailer) != 3 or trailer[-1] != 3:
                continue
            received_crc = (trailer[0] << 8) | trailer[1]
            if cls._vesc_crc16(payload) != received_crc:
                raise InstrumentError("VESC packet CRC mismatch")
            return payload
        raise InstrumentError("Timed out waiting for VESC response")

    def vesc_values(self, port: str, baud: int = 115200, timeout: float = 2.0) -> dict[str, Any]:
        """Read common COMM_GET_VALUES telemetry from a VESC-compatible controller."""
        import serial, struct
        port = self._port_allowed(port)
        with serial.Serial(port=port, baudrate=int(baud), timeout=max(0.1, min(10.0, float(timeout)))) as ser:
            ser.reset_input_buffer()
            ser.write(self._vesc_packet(bytes([4])))  # COMM_GET_VALUES
            ser.flush()
            payload = self._vesc_read_packet(ser, max(0.1, min(10.0, float(timeout))))
        if not payload or payload[0] != 4:
            raise InstrumentError("Unexpected VESC response command")
        data = memoryview(payload)[1:]
        # Stable prefix used by common VESC firmware generations. Newer firmware
        # appends fields, so parse the known prefix and leave extras untouched.
        if len(data) < 53:
            raise InstrumentError(f"VESC GET_VALUES response too short ({len(data)} bytes)")
        off = 0
        def i16(scale=1.0):
            nonlocal off
            v = struct.unpack_from(">h", data, off)[0]; off += 2; return v / scale
        def i32(scale=1.0):
            nonlocal off
            v = struct.unpack_from(">i", data, off)[0]; off += 4; return v / scale
        values = {
            "temp_mos_c": i16(10.0),
            "temp_motor_c": i16(10.0),
            "motor_current_a": i32(100.0),
            "input_current_a": i32(100.0),
            "id_current_a": i32(100.0),
            "iq_current_a": i32(100.0),
            "duty_cycle": i16(1000.0),
            "electrical_rpm": i32(1.0),
            "input_voltage_v": i16(10.0),
            "amp_hours": i32(10000.0),
            "amp_hours_charged": i32(10000.0),
            "watt_hours": i32(10000.0),
            "watt_hours_charged": i32(10000.0),
            "tachometer": i32(1.0),
            "tachometer_abs": i32(1.0),
        }
        if off < len(data):
            values["fault_code"] = int(data[off])
            off += 1
        return {"transport": "vesc-uart", "port": port, "baud": int(baud), "values": values, "extra_response_bytes": max(0, len(data)-off), "warning": "Field prefix follows common VESC COMM_GET_VALUES framing; verify unusual/custom firmware telemetry against that firmware's protocol."}

    def profile_examples(self) -> dict[str, Any]:
        return {
            "config_path": str(self.config_path),
            "example": {
                "profiles": {
                    "bench_scope": {"transport": "scpi", "resource": "USB0::...::INSTR", "queries": {"frequency": {"command": "MEAS:FREQ?", "unit": "Hz"}}},
                    "teensy_telemetry": {"transport": "serial", "port": "/dev/ttyACM0", "baud": 115200, "queries": {"latest": {"passive": True, "parse_regex": "voltage=([0-9.]+)", "unit": "V"}}},
                }
            },
            "note": "Only commands present in this user-controlled allowlist can be transmitted by profile_query.",
        }

    def execute(self, arguments: dict[str, Any]) -> str:
        try:
            action = str(arguments.get("action", "list")).lower()
            if action == "list": result = self.list_devices()
            elif action == "serial_read": result = self.serial_read(str(arguments.get("port", "")), int(arguments.get("baud", 115200)), int(arguments.get("lines", 10)), float(arguments.get("timeout", 2.0)))
            elif action == "scpi_query": result = self.scpi_query(str(arguments.get("resource", "")), str(arguments.get("query", "")), int(arguments.get("timeout_ms", 3000)))
            elif action == "profile_query": result = self.profile_query(str(arguments.get("profile", "")), str(arguments.get("query_name", "")))
            elif action == "vesc_values": result = self.vesc_values(str(arguments.get("port", "")), int(arguments.get("baud", 115200)), float(arguments.get("timeout", 2.0)))
            elif action == "profile_examples": result = self.profile_examples()
            else: raise InstrumentError(f"Unknown instrument action: {action}")
            return json.dumps(result, indent=2, ensure_ascii=False, default=str)
        except Exception as exc:
            return f"Tool error: {type(exc).__name__}: {exc}"


def instrument_tool_schema() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "instrument_telemetry",
            "description": "Read local bench/embedded telemetry without arbitrary writes. list finds serial devices/profiles; serial_read passively reads Arduino/Teensy/BMS-style serial streams; vesc_values reads standard VESC COMM_GET_VALUES telemetry; scpi_query permits only one SCPI query ending in '?'; profile_query runs only a query explicitly allowlisted by the user in ~/.bemo/instruments.json. State-changing instrument commands are intentionally unavailable.",
            "parameters": {
                "type": "object", "required": ["action"],
                "properties": {
                    "action": {"type": "string", "enum": ["list", "serial_read", "vesc_values", "scpi_query", "profile_query", "profile_examples"]},
                    "port": {"type": "string"}, "baud": {"type": "integer"}, "lines": {"type": "integer"}, "timeout": {"type": "number"},
                    "resource": {"type": "string"}, "query": {"type": "string"}, "timeout_ms": {"type": "integer"},
                    "profile": {"type": "string"}, "query_name": {"type": "string"},
                },
            },
        },
    }
