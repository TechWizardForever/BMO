"""Hardware detection and adaptive CPU/RAM tuning for Bemo.

Bemo does not need per-CPU profiles. Runtime defaults come from the actual
physical/logical topology reported by the host. When a valid empirical hardware
benchmark is present, the measured efficient Ollama thread count replaces the
simple topology fallback. Manual overrides remain available in config.py for
controlled benchmarking/recovery.
"""

from __future__ import annotations

import json
import os
import platform
from dataclasses import dataclass
from pathlib import Path

try:
    import psutil
except ImportError:  # Keeps config import readable before dependencies are installed.
    psutil = None


GIB = 1024 ** 3
AUTOTUNE_ALGORITHM_VERSION = 3


def _data_root() -> Path:
    return Path(os.environ.get("BEMO_DATA_DIR", str(Path.home() / ".bemo"))).expanduser()


def autotune_path() -> Path:
    return _data_root() / "hardware" / "autotune.json"


@dataclass(frozen=True)
class HardwareProfile:
    cpu_name: str
    profile_name: str
    physical_cores: int
    logical_cores: int
    ram_gb: float
    ollama_threads: int
    stt_threads: int
    torch_threads: int
    torch_interop_threads: int
    default_context: int

    def as_lines(self) -> list[str]:
        return [
            f"CPU: {self.cpu_name}",
            f"Tuning: {self.profile_name}",
            f"Cores: {self.physical_cores} physical / {self.logical_cores} logical",
            f"RAM: {self.ram_gb:.1f} GiB",
            f"Ollama threads: {self.ollama_threads}",
            f"Whisper threads: {self.stt_threads}",
            f"Torch/TTS threads: {self.torch_threads} (+ {self.torch_interop_threads} inter-op)",
            f"Auto context: {self.default_context} tokens",
        ]


def _read_cpu_name() -> str:
    cpuinfo = Path("/proc/cpuinfo")
    if cpuinfo.exists():
        try:
            for line in cpuinfo.read_text(errors="ignore").splitlines():
                if line.lower().startswith("model name") and ":" in line:
                    name = line.split(":", 1)[1].strip()
                    if name:
                        return name
        except OSError:
            pass

    for value in (
        platform.processor(),
        os.environ.get("PROCESSOR_IDENTIFIER", ""),
        platform.machine(),
    ):
        value = (value or "").strip()
        if value:
            return value
    return "Unknown CPU"


def _linux_sysfs_physical_cores() -> int | None:
    """Count unique package/core pairs from Linux sysfs without model presets."""
    root = Path("/sys/devices/system/cpu")
    if not root.exists():
        return None
    pairs: set[tuple[str, str]] = set()
    try:
        for cpu_dir in root.glob("cpu[0-9]*"):
            online_path = cpu_dir / "online"
            if online_path.exists():
                try:
                    if online_path.read_text().strip() == "0":
                        continue
                except OSError:
                    pass
            topology = cpu_dir / "topology"
            core_path = topology / "core_id"
            package_path = topology / "physical_package_id"
            if not core_path.exists():
                continue
            core_id = core_path.read_text().strip()
            package_id = package_path.read_text().strip() if package_path.exists() else "0"
            pairs.add((package_id, core_id))
    except OSError:
        return None
    return len(pairs) or None


def _proc_cpuinfo_physical_cores() -> int | None:
    cpuinfo = Path("/proc/cpuinfo")
    if not cpuinfo.exists():
        return None
    try:
        blocks = cpuinfo.read_text(errors="ignore").split("\n\n")
    except OSError:
        return None
    pairs: set[tuple[str, str]] = set()
    for block in blocks:
        fields = {}
        for line in block.splitlines():
            if ":" in line:
                key, value = line.split(":", 1)
                fields[key.strip().lower()] = value.strip()
        if "core id" in fields:
            pairs.add((fields.get("physical id", "0"), fields["core id"]))
    return len(pairs) or None


def _detected_core_counts() -> tuple[int, int]:
    logical = max(1, int(os.cpu_count() or 1))
    physical = None

    if psutil is not None:
        try:
            physical = psutil.cpu_count(logical=False)
            logical = int(psutil.cpu_count(logical=True) or logical)
        except Exception:
            physical = None

    if not physical:
        physical = _linux_sysfs_physical_cores()
    if not physical:
        physical = _proc_cpuinfo_physical_cores()
    if not physical:
        # Last-resort behavior is intentionally portable: if topology is
        # unknowable, use the logical count rather than a CPU-model guess.
        physical = logical

    physical = max(1, min(int(physical), logical))
    logical = max(1, int(logical))
    return physical, logical


def _detected_ram_gb() -> float:
    if psutil is not None:
        try:
            return float(psutil.virtual_memory().total) / GIB
        except Exception:
            pass
    try:
        page_size = os.sysconf("SC_PAGE_SIZE")
        pages = os.sysconf("SC_PHYS_PAGES")
        return float(page_size * pages) / GIB
    except (AttributeError, ValueError, OSError):
        return 0.0


def _default_context_from_ram(ram_gb: float) -> int:
    if ram_gb <= 0:
        return 2048
    if ram_gb < 12:
        return 1536
    if ram_gb < 20:
        return 2048
    if ram_gb < 28:
        return 4096
    if ram_gb < 96:
        return 8192
    return 16384


def read_autotune_record(path: Path | None = None) -> dict | None:
    target = Path(path or autotune_path())
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    return data if isinstance(data, dict) else None


def autotune_record_matches(
    record: dict | None,
    *,
    cpu_name: str,
    physical_cores: int,
    logical_cores: int,
) -> bool:
    if not record:
        return False
    try:
        if int(record.get("algorithm_version", -1)) != AUTOTUNE_ALGORITHM_VERSION:
            return False
        if int(record.get("physical_cores", -1)) != int(physical_cores):
            return False
        if int(record.get("logical_cores", -1)) != int(logical_cores):
            return False
        if str(record.get("cpu_name", "")).strip() != str(cpu_name).strip():
            return False
        chosen = int(record.get("chosen_ollama_threads", 0))
        return 1 <= chosen <= int(logical_cores)
    except (TypeError, ValueError):
        return False


def saved_ollama_threads(
    *,
    cpu_name: str,
    physical_cores: int,
    logical_cores: int,
    path: Path | None = None,
) -> int | None:
    record = read_autotune_record(path)
    if not autotune_record_matches(
        record,
        cpu_name=cpu_name,
        physical_cores=physical_cores,
        logical_cores=logical_cores,
    ):
        return None
    try:
        return int(record["chosen_ollama_threads"])
    except (KeyError, TypeError, ValueError):
        return None


def profile_from_counts(
    *,
    cpu_name: str,
    physical_cores: int,
    logical_cores: int,
    ram_gb: float,
    measured_ollama_threads: int | None = None,
) -> HardwareProfile:
    """Build Bemo's tuning from topology, independent of CPU make/model."""
    logical = max(1, int(logical_cores))
    physical = max(1, min(int(physical_cores), logical))

    # Before empirical tuning exists, physical-core count is a conservative
    # fallback. A valid benchmark may choose any count up to the logical count.
    if measured_ollama_threads is None:
        ollama_threads = physical
        tuning_name = "automatic topology fallback (benchmark not yet applied)"
    else:
        ollama_threads = max(1, min(int(measured_ollama_threads), logical))
        tuning_name = "automatic measured efficiency benchmark"

    # Short STT/TTS bursts keep their own bounded pools. They do not inherit the
    # Ollama benchmark winner because the efficient LLM count can include SMT
    # threads. Reserve one physical core on 4+ core systems for UI/audio/OS.
    foreground_reserve = 1 if physical >= 4 else 0
    service_threads = max(1, physical - foreground_reserve)

    return HardwareProfile(
        cpu_name=str(cpu_name or "Unknown CPU"),
        profile_name=tuning_name,
        physical_cores=physical,
        logical_cores=logical,
        ram_gb=float(ram_gb),
        ollama_threads=ollama_threads,
        stt_threads=service_threads,
        torch_threads=service_threads,
        torch_interop_threads=1,
        default_context=_default_context_from_ram(float(ram_gb)),
    )


def detect_hardware() -> HardwareProfile:
    physical, logical = _detected_core_counts()
    cpu_name = _read_cpu_name()
    measured = saved_ollama_threads(
        cpu_name=cpu_name,
        physical_cores=physical,
        logical_cores=logical,
    )
    return profile_from_counts(
        cpu_name=cpu_name,
        physical_cores=physical,
        logical_cores=logical,
        ram_gb=_detected_ram_gb(),
        measured_ollama_threads=measured,
    )


def apply_thread_environment(
    profile: HardwareProfile, thread_override: int | None = None
) -> None:
    """Limit common math runtimes before NumPy/Torch are imported."""
    threads = str(max(1, int(thread_override or profile.torch_threads)))
    os.environ.setdefault("OMP_NUM_THREADS", threads)
    os.environ.setdefault("MKL_NUM_THREADS", threads)
    os.environ.setdefault("OPENBLAS_NUM_THREADS", threads)
    os.environ.setdefault("NUMEXPR_NUM_THREADS", threads)
    os.environ.setdefault("VECLIB_MAXIMUM_THREADS", threads)


def available_ram_gb() -> float:
    if psutil is None:
        return 0.0
    try:
        return float(psutil.virtual_memory().available) / GIB
    except Exception:
        return 0.0
