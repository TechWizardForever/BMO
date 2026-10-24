"""Vision fallback for the GUI controller (milestone 4): MAI-UI-2B finds a point on screen.

Used only when a control is not in the accessibility tree (web pages, apps not
on the accessibility bus). The model runs in a llama.cpp server that is
started on first use and stopped after GUI_VISION_IDLE_SECONDS, and only on a
PC with enough memory (GUI_VISION_MIN_TOTAL_GIB installed and
GUI_VISION_MIN_AVAILABLE_GIB free at that moment). Everything stays local.

Measured 2026-10-18 (docs/GUI_BENCHMARK_2026-10-18.md): 8-bit MAI-UI-2B hits
89-92% of targets, ~27 s for a new screenshot, ~2.9-4.2 GB resident.
The point is a guess: the Supervisor clicks it only as a confirmed change and
reports whether anything changed.
"""
from __future__ import annotations

import atexit
import base64
import io
import json
import os
import re
import shutil
import socket
import subprocess
import threading
import time
import urllib.request
from pathlib import Path

# Verbatim from Tongyi-MAI/MAI-UI src/prompt.py (MAI_MOBILE_SYS_PROMPT_GROUNDING),
# the prompt used in the 2026-10-18 benchmark.
MAI_SYSTEM = """You are a GUI grounding agent.
## Task
Given a screenshot and the user's grounding instruction. Your task is to accurately locate a UI element based on the user's instructions.
First, you should carefully examine the screenshot and analyze the user's instructions,  translate the user's instruction into a effective reasoning process, and then provide the final coordinate.
## Output Format
Return a json object with a reasoning process in <grounding_think></grounding_think> tags, a [x,y] format coordinate within <answer></answer> XML tags:
<grounding_think>...</grounding_think>
<answer>
{"coordinate": [x,y]}
</answer>"""

MAX_IMAGE_SIDE = 1280  # the benchmark's frame width; larger images are slower, not better


def parse_point(reply: str) -> tuple[float, float] | None:
    """MAI-UI's answer in its 0-1000 frame (fitted on the bench: 1.28 / 0.726 per px at 1280x720)."""
    answer = re.search(r"<answer>(.*?)</answer>", reply or "", re.S)
    body = answer.group(1) if answer else (reply or "")
    pairs = re.findall(r"(-?\d+(?:\.\d+)?)\s*[, ]\s*(-?\d+(?:\.\d+)?)", body)
    if not pairs:
        return None
    x, y = float(pairs[-1][0]), float(pairs[-1][1])
    if not (0 <= x <= 1000 and 0 <= y <= 1000):
        return None
    return x, y


def to_screen(point: tuple[float, float], region: tuple[int, int, int, int]) -> tuple[int, int]:
    """0-1000 point in a captured region (x, y, w, h) -> screen pixels."""
    x, y, w, h = region
    return int(round(x + point[0] / 1000.0 * w)), int(round(y + point[1] / 1000.0 * h))


def window_regions(pid: int) -> list[tuple[str, tuple[int, int, int, int], str]]:
    """(window id, (x, y, w, h), title) for each window of a process (OS adapter)."""
    from systems.adapters import desktop
    return desktop().window_regions(pid)


def capture(region: tuple[int, int, int, int]):
    """Screenshot of a screen region (OS adapter). Returns a Pillow image."""
    from systems.adapters import desktop
    return desktop().capture(region)


def changed_pixels(before, after, around: tuple[int, int] | None = None, radius: int = 60) -> int:
    """How many pixels clearly changed, in the whole image or a box around a point."""
    from PIL import ImageChops
    if before.size != after.size:
        return before.width * before.height
    if around:
        cx, cy = around
        box = (max(0, cx - radius), max(0, cy - radius), min(before.width, cx + radius), min(before.height, cy + radius))
        before, after = before.crop(box), after.crop(box)
    diff = ImageChops.difference(before.convert("L"), after.convert("L")).point(lambda v: 255 if v > 24 else 0)
    return diff.histogram()[255]


def changed_fraction(before, after, around: tuple[int, int] | None = None, radius: int = 60) -> float:
    """Share of pixels that changed, in the whole image or a box around a point."""
    from PIL import ImageChops
    if before.size != after.size:
        return 1.0
    if around:
        cx, cy = around
        box = (max(0, cx - radius), max(0, cy - radius), min(before.width, cx + radius), min(before.height, cy + radius))
        before, after = before.crop(box), after.crop(box)
    diff = ImageChops.difference(before.convert("L"), after.convert("L")).point(lambda v: 255 if v > 24 else 0)
    hist = diff.histogram()
    total = max(1, before.width * before.height)
    return hist[255] / total


def signature(image) -> list[int]:
    """Tiny grayscale thumbnail: is this still the screen BMO looked at?"""
    return list(image.convert("L").resize((32, 18)).getdata())


def signature_distance(a: list[int], b: list[int]) -> float:
    """Mean difference (0-255) between two signatures; another window on top gives ~40+."""
    if not a or not b or len(a) != len(b):
        return 255.0
    return sum(abs(x - y) for x, y in zip(a, b)) / len(a)


def raise_window(window_id: str) -> None:
    """Bring a window to the front (looking only; the Supervisor re-checks before any click)."""
    from systems.adapters import desktop
    desktop().activate(window_id)


def active_window_id() -> int | None:
    from systems.adapters import desktop
    return desktop().active_window()


def memory_gate(min_total_gib: float, min_available_gib: float) -> tuple[bool, str]:
    import psutil
    vm = psutil.virtual_memory()
    total, available = vm.total / 2**30, vm.available / 2**30
    if total < min_total_gib:
        return False, f"this PC has {total:.0f} GB of memory and the vision helper needs {min_total_gib:.0f} GB"
    if available < min_available_gib:
        return False, (f"only {available:.1f} GB of memory is free right now and the vision helper needs "
                       f"{min_available_gib:.1f} GB; close something big and try again")
    return True, ""


class VisionLocator:
    """MAI-UI-2B in an on-demand llama.cpp server."""

    def __init__(self, *, model: Path, mmproj: Path, server: Path, threads: int, ctx: int = 4096,
                 idle_seconds: float = 600.0, min_total_gib: float = 28.0, min_available_gib: float = 6.0,
                 start_timeout: float = 180.0, logger=None):
        self.model, self.mmproj, self.server = Path(model), Path(mmproj), Path(server)
        self.threads, self.ctx, self.idle_seconds = int(threads), int(ctx), float(idle_seconds)
        self.min_total_gib, self.min_available_gib = float(min_total_gib), float(min_available_gib)
        self.start_timeout = float(start_timeout)
        self.logger = logger
        self.proc: subprocess.Popen | None = None
        self.port = 0
        self.last_used = 0.0
        self._lock = threading.Lock()
        self._watchdog: threading.Thread | None = None
        atexit.register(self.stop)

    def _log(self, message, *args):
        if self.logger is not None:
            self.logger.info(message, *args)

    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def available(self) -> tuple[bool, str]:
        """Can a point be located now? (files, display, memory)"""
        for path, what in ((self.model, "model"), (self.mmproj, "vision projector"), (self.server, "llama-server")):
            if not path.exists():
                return False, f"the vision helper's {what} is missing ({path})"
        if not os.environ.get("DISPLAY"):
            return False, "screen capture needs an X11 desktop"
        if self.running():
            return True, ""
        return memory_gate(self.min_total_gib, self.min_available_gib)

    def _free_port(self) -> int:
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            return int(s.getsockname()[1])

    def start(self) -> None:
        if self.running():
            return
        ok, reason = self.available()
        if not ok:
            raise RuntimeError(reason)
        self.port = self._free_port()
        env = dict(os.environ, LD_LIBRARY_PATH=str(self.server.parent))
        started = time.monotonic()
        self.proc = subprocess.Popen(
            [str(self.server), "-m", str(self.model), "--mmproj", str(self.mmproj), "--host", "127.0.0.1",
             "--port", str(self.port), "--threads", str(self.threads), "--ctx-size", str(self.ctx),
             "--temp", "0", "--no-warmup"],
            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        while time.monotonic() - started < self.start_timeout:
            if self.proc.poll() is not None:
                self.proc = None
                raise RuntimeError("the vision helper exited while starting")
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/health", timeout=2) as r:
                    if r.status == 200:
                        break
            except OSError:
                time.sleep(0.5)
        else:
            self.stop()
            raise RuntimeError("the vision helper did not start in time")
        self.last_used = time.monotonic()
        try:
            import psutil
            free = psutil.virtual_memory().available / 2**30
        except Exception:
            free = float("nan")
        self._log("GUI vision helper started on port {} in {:.1f}s ({:.1f} GB free)", self.port,
                  time.monotonic() - started, free)
        if self._watchdog is None or not self._watchdog.is_alive():
            self._watchdog = threading.Thread(target=self._idle_watch, name="gui-vision-idle", daemon=True)
            self._watchdog.start()

    def _idle_watch(self):
        while self.running():
            time.sleep(15)
            if self.running() and not self._lock.locked() and time.monotonic() - self.last_used > self.idle_seconds:
                self._log("GUI vision helper idle for {:.0f}s; stopping it to free memory", self.idle_seconds)
                self.stop()

    def stop(self) -> None:
        proc, self.proc = self.proc, None
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()

    def locate(self, image, instruction: str) -> tuple[tuple[float, float] | None, str]:
        """Point (0-1000 frame of `image`) for an instruction like 'Click the "Bold" button.'"""
        with self._lock:
            self.start()
            if max(image.size) > MAX_IMAGE_SIDE:
                scale = MAX_IMAGE_SIDE / max(image.size)
                image = image.resize((max(1, int(image.width * scale)), max(1, int(image.height * scale))))
            buf = io.BytesIO()
            image.convert("RGB").save(buf, format="PNG")
            url = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
            body = json.dumps({"messages": [
                {"role": "system", "content": MAI_SYSTEM},
                {"role": "user", "content": [{"type": "image_url", "image_url": {"url": url}},
                                             {"type": "text", "text": instruction}]}],
                "temperature": 0, "max_tokens": 256}).encode()
            request = urllib.request.Request(f"http://127.0.0.1:{self.port}/v1/chat/completions", data=body,
                                             headers={"Content-Type": "application/json"})
            started = time.monotonic()
            with urllib.request.urlopen(request, timeout=300) as r:
                reply = json.loads(r.read())["choices"][0]["message"]["content"]
            self.last_used = time.monotonic()
            self._log("GUI vision located in {:.1f}s: {!r}", time.monotonic() - started, instruction[:60])
            return parse_point(reply), reply
