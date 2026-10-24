"""Read-only local hardware and OS diagnostics for Bemo.

All subprocesses are fixed allowlisted commands with bounded output. No shell is
used and no state-changing diagnostics command is exposed.
"""
from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import psutil


class DiagnosticsError(ValueError):
    pass


def _run(argv: list[str], timeout: float = 8.0, max_chars: int = 30_000) -> dict[str, Any]:
    exe = shutil.which(argv[0])
    if not exe:
        return {"available": False, "command": argv[0], "reason": "not-installed"}
    try:
        proc = subprocess.run([exe, *argv[1:]], capture_output=True, text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        return {"available": True, "command": argv[0], "error": "timeout"}
    text = (proc.stdout or "") + (("\n" + proc.stderr) if proc.stderr else "")
    return {"available": True, "command": argv[0], "returncode": proc.returncode, "output": text[:max_chars], "truncated": len(text) > max_chars}


def _safe_device(raw: str) -> str:
    path = Path(raw).resolve()
    # Diagnostics may inspect only normal block-device paths.
    value = str(path)
    if not (value.startswith("/dev/sd") or value.startswith("/dev/nvme") or value.startswith("/dev/mmcblk")):
        raise DiagnosticsError("Only /dev/sd*, /dev/nvme*, or /dev/mmcblk* block devices are accepted")
    return value


@dataclass
class HardwareDiagnostics:
    max_processes: int = 40

    def overview(self) -> dict[str, Any]:
        vm = psutil.virtual_memory()
        swap = psutil.swap_memory()
        disk = psutil.disk_usage("/")
        freqs = psutil.cpu_freq()
        load = os.getloadavg() if hasattr(os, "getloadavg") else None
        return {
            "platform": platform.platform(),
            "kernel": platform.release(),
            "machine": platform.machine(),
            "cpu_logical": psutil.cpu_count(logical=True),
            "cpu_physical": psutil.cpu_count(logical=False),
            "cpu_percent": psutil.cpu_percent(interval=0.15),
            "cpu_freq_mhz": {"current": getattr(freqs, "current", None), "min": getattr(freqs, "min", None), "max": getattr(freqs, "max", None)} if freqs else None,
            "load_average": list(load) if load else None,
            "memory": {"total": vm.total, "available": vm.available, "used": vm.used, "percent": vm.percent},
            "swap": {"total": swap.total, "used": swap.used, "percent": swap.percent},
            "root_disk": {"total": disk.total, "used": disk.used, "free": disk.free, "percent": disk.percent},
            "boot_time_epoch": psutil.boot_time(),
        }

    def sensors(self) -> dict[str, Any]:
        result: dict[str, Any] = {}
        try:
            temps = psutil.sensors_temperatures(fahrenheit=False)
            result["temperatures_c"] = {
                group: [{"label": x.label, "current": x.current, "high": x.high, "critical": x.critical} for x in entries]
                for group, entries in temps.items()
            }
        except Exception as exc:
            result["temperatures_error"] = f"{type(exc).__name__}: {exc}"
        try:
            fans = psutil.sensors_fans()
            result["fans_rpm"] = {group: [{"label": x.label, "current": x.current} for x in entries] for group, entries in fans.items()}
        except Exception as exc:
            result["fans_error"] = f"{type(exc).__name__}: {exc}"
        result["lm_sensors"] = _run(["sensors", "-j"])
        return result

    def storage(self, device: str | None = None) -> dict[str, Any]:
        partitions = []
        for p in psutil.disk_partitions(all=False):
            try:
                usage = psutil.disk_usage(p.mountpoint)
                used = {"total": usage.total, "used": usage.used, "free": usage.free, "percent": usage.percent}
            except Exception:
                used = None
            partitions.append({"device": p.device, "mountpoint": p.mountpoint, "fstype": p.fstype, "usage": used})
        result: dict[str, Any] = {"partitions": partitions}
        if device:
            dev = _safe_device(device)
            result["smartctl_json"] = _run(["smartctl", "-a", "-j", dev], timeout=15)
            if dev.startswith("/dev/nvme"):
                result["nvme_smart_json"] = _run(["nvme", "smart-log", "-o", "json", dev], timeout=10)
        else:
            result["lsblk_json"] = _run(["lsblk", "-J", "-o", "NAME,PATH,SIZE,TYPE,FSTYPE,MOUNTPOINT,MODEL,SERIAL,ROTA,TRAN"])
        return result

    def buses(self) -> dict[str, Any]:
        return {"pci": _run(["lspci", "-mm"]), "usb": _run(["lsusb"])}

    def network(self) -> dict[str, Any]:
        interfaces = {}
        stats = psutil.net_if_stats()
        for name, addrs in psutil.net_if_addrs().items():
            interfaces[name] = {
                "isup": getattr(stats.get(name), "isup", None),
                "speed_mbps": getattr(stats.get(name), "speed", None),
                "addresses": [{"family": str(a.family), "address": a.address, "netmask": a.netmask} for a in addrs],
            }
        return {"interfaces": interfaces, "io": psutil.net_io_counters(pernic=True)._asdict() if False else {k: v._asdict() for k, v in psutil.net_io_counters(pernic=True).items()}}

    def processes(self, query: str = "", limit: int | None = None) -> dict[str, Any]:
        needle = query.lower().strip()
        rows = []
        for proc in psutil.process_iter(["pid", "name", "username", "cpu_percent", "memory_percent", "cmdline"]):
            try:
                info = proc.info
                blob = " ".join([str(info.get("name") or ""), *[str(x) for x in (info.get("cmdline") or [])]]).lower()
                if needle and needle not in blob:
                    continue
                rows.append({
                    "pid": info.get("pid"), "name": info.get("name"), "cpu_percent": info.get("cpu_percent"),
                    "memory_percent": round(float(info.get("memory_percent") or 0.0), 3),
                    "cmdline": (info.get("cmdline") or [])[:20],
                })
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        rows.sort(key=lambda x: (float(x.get("cpu_percent") or 0.0), float(x.get("memory_percent") or 0.0)), reverse=True)
        return {"processes": rows[: max(1, min(100, int(limit or self.max_processes)))]}

    def logs(self, priority: str = "warning", since: str = "-1h", unit: str = "") -> dict[str, Any]:
        priorities = {"emerg": "0", "alert": "1", "crit": "2", "err": "3", "warning": "4", "notice": "5", "info": "6"}
        p = priorities.get(priority.lower(), "4")
        argv = ["journalctl", "--no-pager", "--output=short-iso", f"--priority={p}", f"--since={since}", "-n", "300"]
        if unit:
            # Unit name only; no arbitrary journalctl args.
            if not all(ch.isalnum() or ch in "@_.-" for ch in unit):
                raise DiagnosticsError("Invalid systemd unit name")
            argv.extend(["--unit", unit])
        return {"journal": _run(argv, timeout=10, max_chars=40_000)}

    def models(self) -> dict[str, Any]:
        return {"ollama_ps": _run(["ollama", "ps"], timeout=8), "ollama_list": _run(["ollama", "list"], timeout=8)}

    def execute(self, arguments: dict[str, Any]) -> str:
        try:
            action = str(arguments.get("action", "overview")).lower()
            if action == "overview": result = self.overview()
            elif action == "sensors": result = self.sensors()
            elif action == "storage": result = self.storage(arguments.get("device"))
            elif action == "buses": result = self.buses()
            elif action == "network": result = self.network()
            elif action == "processes": result = self.processes(str(arguments.get("query", "")), arguments.get("limit"))
            elif action == "logs": result = self.logs(str(arguments.get("priority", "warning")), str(arguments.get("since", "-1h")), str(arguments.get("unit", "")))
            elif action == "models": result = self.models()
            else: raise DiagnosticsError(f"Unknown diagnostics action: {action}")
            return json.dumps(result, indent=2, default=str)
        except Exception as exc:
            return f"Tool error: {type(exc).__name__}: {exc}"


def diagnostics_tool_schema() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "hardware_diagnostics",
            "description": "Read actual telemetry for Bemo's LOCAL LINUX COMPUTER hardware/OS only. Never use this tool for a person's body, anatomy, injuries, health, medical statements, figurative language, or ordinary conversation. Read-only actions: overview, sensors, storage, buses, network, processes, logs, models. storage may inspect a specific /dev/sd*, /dev/nvme*, or /dev/mmcblk* device with SMART/NVMe tools when permissions allow.",
            "parameters": {
                "type": "object", "required": ["action"],
                "properties": {
                    "action": {"type": "string", "enum": ["overview", "sensors", "storage", "buses", "network", "processes", "logs", "models"]},
                    "device": {"type": "string"}, "query": {"type": "string"}, "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                    "priority": {"type": "string", "enum": ["emerg", "alert", "crit", "err", "warning", "notice", "info"]},
                    "since": {"type": "string", "description": "journalctl since expression, e.g. -1h or today"}, "unit": {"type": "string"},
                },
            },
        },
    }
