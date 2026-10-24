"""Background experiment/data logger for Bemo Engineering."""
from __future__ import annotations

import csv
import json
import statistics
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import psutil


class TelemetryError(ValueError):
    pass


def _dig(value: Any, field_path: str) -> Any:
    if not field_path:
        return value
    current = value
    for piece in field_path.split("."):
        if isinstance(current, dict):
            current = current.get(piece)
        elif isinstance(current, (list, tuple)) and piece.isdigit():
            current = current[int(piece)]
        else:
            return None
    return current


@dataclass
class _Session:
    id: str
    path: Path
    sources: list[dict[str, Any]]
    interval: float
    started: float
    stop_event: threading.Event = field(default_factory=threading.Event)
    thread: threading.Thread | None = None
    samples: int = 0
    last_error: str = ""


class TelemetryLogger:
    def __init__(self, root: Path, instrument_manager=None):
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.instrument_manager = instrument_manager
        self._sessions: dict[str, _Session] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _system_value(metric: str) -> Any:
        metric = metric.lower()
        if metric == "cpu_percent": return psutil.cpu_percent(interval=None)
        if metric == "memory_percent": return psutil.virtual_memory().percent
        if metric == "memory_available_bytes": return psutil.virtual_memory().available
        if metric == "cpu_freq_mhz":
            f = psutil.cpu_freq(); return f.current if f else None
        if metric == "disk_root_percent": return psutil.disk_usage("/").percent
        if metric == "net_bytes_sent": return psutil.net_io_counters().bytes_sent
        if metric == "net_bytes_recv": return psutil.net_io_counters().bytes_recv
        if metric.startswith("temperature:"):
            _, group, *label_parts = metric.split(":")
            label = ":".join(label_parts).lower()
            temps = psutil.sensors_temperatures(fahrenheit=False)
            for entry in temps.get(group, []):
                if not label or label in (entry.label or "").lower():
                    return entry.current
            return None
        raise TelemetryError(f"Unsupported system metric: {metric}")

    def _sample_source(self, src: dict[str, Any]) -> Any:
        kind = str(src.get("kind", "system")).lower()
        if kind == "system":
            return self._system_value(str(src.get("metric", "")))
        if kind == "profile":
            if self.instrument_manager is None:
                raise TelemetryError("Instrument manager unavailable")
            result = self.instrument_manager.profile_query(str(src.get("profile", "")), str(src.get("query", "")))
            return _dig(result, str(src.get("field", "value")))
        if kind == "vesc":
            if self.instrument_manager is None:
                raise TelemetryError("Instrument manager unavailable")
            result = self.instrument_manager.vesc_values(str(src.get("port", "")), int(src.get("baud", 115200)), float(src.get("timeout", 1.0)))
            return _dig(result, str(src.get("field", "values.input_voltage_v")))
        raise TelemetryError(f"Unsupported telemetry source kind: {kind}")

    def _worker(self, session: _Session):
        names = [str(src.get("name") or src.get("metric") or src.get("query") or f"value_{i}") for i, src in enumerate(session.sources)]
        try:
            with session.path.open("w", newline="", encoding="utf-8") as fh:
                writer = csv.writer(fh)
                writer.writerow(["timestamp_epoch", "elapsed_s", *names])
                while not session.stop_event.is_set():
                    started = time.time()
                    row = [started, started - session.started]
                    for src in session.sources:
                        try:
                            row.append(self._sample_source(src))
                        except Exception as exc:
                            session.last_error = f"{type(exc).__name__}: {exc}"
                            row.append("")
                    writer.writerow(row); fh.flush()
                    session.samples += 1
                    elapsed = time.time() - started
                    session.stop_event.wait(max(0.0, session.interval - elapsed))
        except Exception as exc:
            session.last_error = f"{type(exc).__name__}: {exc}"

    def start(self, sources: list[dict[str, Any]], interval_s: float = 1.0, filename: str = "") -> dict[str, Any]:
        if not sources or len(sources) > 24:
            raise TelemetryError("Provide 1-24 sources")
        interval = max(0.1, min(3600.0, float(interval_s)))
        sid = uuid.uuid4().hex[:10]
        safe = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in (filename or f"telemetry-{sid}.csv"))
        if not safe.lower().endswith(".csv"): safe += ".csv"
        path = self.root / safe
        if path.exists():
            base = path.stem; suffix = path.suffix; counter = 2
            while (self.root / f"{base}-{counter}{suffix}").exists():
                counter += 1
            path = self.root / f"{base}-{counter}{suffix}"
        session = _Session(sid, path, [dict(x) for x in sources], interval, time.time())
        thread = threading.Thread(target=self._worker, args=(session,), daemon=True, name=f"BemoTelemetry-{sid}")
        session.thread = thread
        with self._lock: self._sessions[sid] = session
        thread.start()
        return {"session_id": sid, "path": str(path), "interval_s": interval, "sources": session.sources, "status": "logging"}

    def stop(self, sid: str) -> dict[str, Any]:
        with self._lock: session = self._sessions.get(sid)
        if not session: raise TelemetryError("Unknown logger session")
        session.stop_event.set()
        if session.thread: session.thread.join(timeout=min(5.0, session.interval + 1.0))
        return {"session_id": sid, "path": str(session.path), "samples": session.samples, "last_error": session.last_error, "status": "stopped"}

    def status(self, sid: str = "") -> dict[str, Any]:
        with self._lock:
            sessions = [self._sessions[sid]] if sid and sid in self._sessions else list(self._sessions.values()) if not sid else []
        if sid and not sessions: raise TelemetryError("Unknown logger session")
        return {"sessions": [{"session_id": s.id, "path": str(s.path), "interval_s": s.interval, "samples": s.samples, "elapsed_s": time.time()-s.started, "running": bool(s.thread and s.thread.is_alive() and not s.stop_event.is_set()), "last_error": s.last_error} for s in sessions]}

    def summarize(self, path: str) -> dict[str, Any]:
        file = Path(path).expanduser().resolve()
        try: file.relative_to(self.root)
        except ValueError as exc: raise TelemetryError("Summary path must be inside Bemo telemetry directory") from exc
        if not file.exists(): raise TelemetryError("Telemetry file not found")
        with file.open("r", newline="", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
        if not rows: return {"path": str(file), "samples": 0, "columns": {}}
        columns: dict[str, Any] = {}
        for key in rows[0]:
            if key in {"timestamp_epoch", "elapsed_s"}: continue
            values = []
            for row in rows:
                try: values.append(float(row[key]))
                except (TypeError, ValueError): pass
            if values:
                columns[key] = {"count": len(values), "min": min(values), "max": max(values), "mean": statistics.fmean(values), "median": statistics.median(values)}
        return {"path": str(file), "samples": len(rows), "duration_s": float(rows[-1].get("elapsed_s") or 0), "columns": columns}


    def plot(self, path: str, columns: list[str] | None = None) -> dict[str, Any]:
        file = Path(path).expanduser().resolve()
        try: file.relative_to(self.root)
        except ValueError as exc: raise TelemetryError("Plot path must be inside Bemo telemetry directory") from exc
        if not file.exists(): raise TelemetryError("Telemetry file not found")
        with file.open("r", newline="", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
        if not rows: raise TelemetryError("Telemetry file has no samples")
        available = [k for k in rows[0].keys() if k not in {"timestamp_epoch", "elapsed_s"}]
        selected = [c for c in (columns or available) if c in available][:12]
        if not selected: raise TelemetryError("No numeric columns selected")
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        x = []
        series = {c: [] for c in selected}
        valid_rows = []
        for row in rows:
            try: elapsed = float(row.get("elapsed_s") or 0)
            except ValueError: continue
            x.append(elapsed); valid_rows.append(row)
        fig, ax = plt.subplots()
        plotted = []
        for c in selected:
            ys=[]; xs=[]
            for elapsed,row in zip(x,valid_rows):
                try: ys.append(float(row.get(c,""))); xs.append(elapsed)
                except (TypeError,ValueError): pass
            if ys:
                ax.plot(xs,ys,label=c); plotted.append(c)
        if not plotted:
            plt.close(fig); raise TelemetryError("Selected columns contain no numeric data")
        ax.set_xlabel("Elapsed time (s)"); ax.set_ylabel("Value"); ax.grid(True); ax.legend()
        out=file.with_suffix(".png")
        fig.tight_layout(); fig.savefig(out,dpi=140); plt.close(fig)
        return {"path":str(out),"source_csv":str(file),"columns":plotted}

    def execute(self, arguments: dict[str, Any]) -> str:
        try:
            action = str(arguments.get("action", "status")).lower()
            if action == "start": result = self.start(list(arguments.get("sources") or []), float(arguments.get("interval_s", 1.0)), str(arguments.get("filename", "")))
            elif action == "stop": result = self.stop(str(arguments.get("session_id", "")))
            elif action == "status": result = self.status(str(arguments.get("session_id", "")))
            elif action == "summarize": result = self.summarize(str(arguments.get("path", "")))
            elif action == "plot": result = self.plot(str(arguments.get("path", "")), list(arguments.get("columns") or []))
            else: raise TelemetryError(f"Unknown logger action: {action}")
            return json.dumps(result, indent=2, default=str)
        except Exception as exc:
            return f"Tool error: {type(exc).__name__}: {exc}"


def telemetry_tool_schema() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "data_logger",
            "description": "Start/stop/status/summarize/plot a background CSV experiment logger. Sources can be system metrics (cpu_percent, memory_percent, memory_available_bytes, cpu_freq_mhz, disk_root_percent, net_bytes_sent/recv, temperature:<group>:<label>), an allowlisted instrument profile query, or read-only VESC telemetry. Use this for real experiments instead of manually sampling values.",
            "parameters": {
                "type": "object", "required": ["action"],
                "properties": {
                    "action": {"type": "string", "enum": ["start", "stop", "status", "summarize", "plot"]},
                    "sources": {"type": "array", "items": {"type": "object"}},
                    "interval_s": {"type": "number", "minimum": 0.1, "maximum": 3600}, "filename": {"type": "string"},
                    "session_id": {"type": "string"}, "path": {"type": "string"}, "columns": {"type": "array", "items": {"type": "string"}},
                },
            },
        },
    }
