"""What is inside this computer (portability plan P3): CPU, memory, GPU, disk, sound, screen.

Read on request ("Hey BMO, what hardware am I on?", print_hardware.py) and
saved to ~/.bemo/hardware-profile.json. It reports; it does not change
anything BMO uses (no silent replacements). The 28 GB minimum is stated
plainly (decided 2026-10-19: no smaller model tiers).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path

GIB = 1024 ** 3
VENDORS = {"0x1002": "AMD", "0x10de": "NVIDIA", "0x8086": "Intel"}


@dataclass
class Gpu:
    vendor: str
    model: str = ""
    memory_gib: float = 0.0
    integrated: bool = False


@dataclass
class HardwareReport:
    cpu: str = ""
    threads: int = 0
    ram_gib: float = 0.0
    meets_minimum: bool = False
    gpus: list = field(default_factory=list)
    models_use_gpu: bool | None = None   # None = no model loaded, can't tell
    disk_free_gib: dict = field(default_factory=dict)  # place -> GiB free
    sound_output: str = ""
    microphone: str = ""
    screen: str = ""
    speech_model: str = ""


def detect_gpus(drm=Path("/sys/class/drm"), runner=subprocess.run, which=shutil.which) -> list[Gpu]:
    """Graphics chips from the kernel (sysfs), names from lspci, NVIDIA memory from nvidia-smi."""
    gpus, seen = [], set()
    for card in sorted(drm.glob("card[0-9]")):
        device = card / "device"
        try:
            vendor_id = (device / "vendor").read_text().strip().lower()
        except OSError:
            continue
        slot = device.resolve().name  # PCI address, e.g. 0000:05:00.0
        if slot in seen:
            continue
        seen.add(slot)
        vendor = VENDORS.get(vendor_id, vendor_id)
        model = ""
        if which("lspci"):
            out = runner(["lspci", "-s", slot.split(":", 1)[-1]], capture_output=True, text=True, timeout=5, check=False)
            line = (out.stdout or "").strip().splitlines()[0] if (out.stdout or "").strip() else ""
            model = line.split(": ", 1)[-1].replace("Advanced Micro Devices, Inc. ", "")
            model = re.sub(r"\[AMD/ATI\]\s*|\s*\(rev [0-9a-f]+\)", "", model).strip()  # "Lucienne"
        memory = 0.0
        try:
            memory = int((device / "mem_info_vram_total").read_text()) / GIB  # amdgpu
        except (OSError, ValueError):
            pass
        if vendor == "NVIDIA" and which("nvidia-smi"):
            out = runner(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
                         capture_output=True, text=True, timeout=10, check=False)
            first = (out.stdout or "").strip().splitlines()[:1]
            if first and "," in first[0]:
                name, mib = first[0].rsplit(",", 1)
                model, memory = name.strip(), float(mib) / 1024
        integrated = vendor == "Intel" or (vendor == "AMD" and 0 < memory <= 4.0)
        gpus.append(Gpu(vendor, model, round(memory, 1), integrated))
    return gpus


def models_use_gpu(url="http://127.0.0.1:11434/api/ps", opener=urllib.request.urlopen) -> bool | None:
    """Does Ollama put any loaded model on a GPU? None when nothing is loaded (or Ollama is off)."""
    try:
        with opener(url, timeout=3) as r:
            models = json.loads(r.read()).get("models", [])
    except (OSError, ValueError):
        return None
    if not models:
        return None
    return any(int(m.get("size_vram") or 0) > 0 for m in models)


def ollama_models_dir() -> Path:
    env = os.environ.get("OLLAMA_MODELS")
    if env:
        return Path(env)
    system = Path("/usr/share/ollama/.ollama/models")  # Linux system service
    return system if system.exists() else Path.home() / ".ollama" / "models"


def disk_free(paths: dict) -> dict:
    free = {}
    for label, path in paths.items():
        path = Path(path)
        while not path.exists() and path != path.parent:
            path = path.parent
        try:
            free[label] = round(shutil.disk_usage(path).free / GIB, 1)
        except OSError:
            pass
    return free


def collect(config, sound=None, display=None, min_ram_gib=27.5) -> HardwareReport:
    """Everything at once; each part fails soft (an empty field), never the whole report."""
    from hardware import detect_hardware
    profile = detect_hardware()
    report = HardwareReport(cpu=profile.cpu_name, threads=profile.logical_cores, ram_gib=round(profile.ram_gb, 1),
                            meets_minimum=profile.ram_gb >= min_ram_gib)
    try:
        report.gpus = detect_gpus()
    except Exception:
        report.gpus = []
    report.models_use_gpu = models_use_gpu()
    report.disk_free_gib = disk_free({"BMO's data": config.DATA_ROOT, "the models": ollama_models_dir()})
    if sound is not None:
        try:
            current = sound.current()
            report.sound_output = current.spoken if current else ""
            mic = next((m for m in sound.inputs() if m.default), None)
            report.microphone = mic.spoken if mic else ""
        except Exception:
            pass
    if display is not None:
        try:
            state = display.current_state()
            rate = display.current_rate
            report.screen = f"{state.width} by {state.height}" + (f" at {rate:g} hertz" if rate else "")
        except Exception:
            pass
    report.speech_model = f"{config.STT_MODEL} on the CPU, {config.STT_CPU_THREADS} threads"
    return report


def save(report: HardwareReport, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = asdict(report)
    data["gpus"] = [asdict(g) if not isinstance(g, dict) else g for g in report.gpus]
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _spoken_cpu(name: str) -> str:
    name = re.sub(r"\((?:R|TM|tm)\)|\bCPU\b|@.*$|\s+with\s+.*graphics.*$", "", name, flags=re.I)
    name = re.sub(r"\s+", " ", name).strip() or "unknown processor"
    return ("an " if name[0].upper() in "AEIOU" else "a ") + name


def spoken(report: HardwareReport) -> str:
    gb = round(report.ram_gib)
    parts = [f"This computer has {_spoken_cpu(report.cpu)} with {report.threads} threads and {gb} gigabytes of "
             f"memory, " + ("enough for my models." if report.meets_minimum else "but my models need 28.")]
    if report.gpus:
        g = report.gpus[0]
        kind = "built-in" if g.integrated else "separate"
        chip = f"{kind} {g.vendor} graphics" + (f" with {g.memory_gib:g} gigabytes" if g.memory_gib else "")
        if report.models_use_gpu is True:
            parts.append(f"My models use its {chip}.")
        elif report.models_use_gpu is False:
            parts.append(f"It has {chip}, but my models run on the CPU.")
        else:
            parts.append(f"It has {chip}.")
    else:
        parts.append("It has no graphics chip I can see; my models run on the CPU.")
    if report.disk_free_gib:
        free = min(report.disk_free_gib.values())
        parts.append(f"There are {round(free)} gigabytes free on the disk.")
    if report.sound_output or report.microphone:
        parts.append(("Sound plays through " + report.sound_output if report.sound_output else "")
                     + (" and " if report.sound_output and report.microphone else "")
                     + (f"I listen through {report.microphone}" if report.microphone else "") + ".")
    if report.screen:
        parts.append("The screen is " + re.sub(r"(\d+)\.\d+ hertz", lambda m: f"{round(float(m.group(0).split()[0]))} hertz",
                                               report.screen) + ".")
    return " ".join(p[0].upper() + p[1:] for p in parts)


def printed(report: HardwareReport) -> list[str]:
    lines = [f"CPU: {report.cpu} ({report.threads} threads)",
             f"Memory: {report.ram_gib} GiB ({'meets' if report.meets_minimum else 'BELOW'} BMO's 28 GB minimum)"]
    for g in report.gpus:
        lines.append(f"GPU: {g.vendor} {g.model}".rstrip() + (f", {g.memory_gib:g} GiB" if g.memory_gib else "")
                     + (" (integrated)" if g.integrated else ""))
    if not report.gpus:
        lines.append("GPU: none detected")
    lines.append("Models on GPU: " + {True: "yes", False: "no (CPU)", None: "unknown (no model loaded)"}[report.models_use_gpu])
    for label, free in report.disk_free_gib.items():
        lines.append(f"Disk free for {label}: {free} GiB")
    lines.append(f"Sound output: {report.sound_output or 'unknown'}   Microphone: {report.microphone or 'unknown'}")
    lines.append(f"Screen: {report.screen or 'unknown'}")
    lines.append(f"Speech recognition: {report.speech_model}")
    return lines
