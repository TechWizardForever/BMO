"""BMO's brains, downloaded in a set order.

Main first, the game player and screensavers, then the other brains smallest first, then the simple
benchmark (hardware_autotune.py). One background process does it (`downloads_cli.py run`), so it carries
on after a restart or reboot: Ollama keeps partly downloaded files and resumes them. Progress is saved in
~/.bemo/install/downloads.json, which the install screen draws its bars from (systems/first_boot.py) and
BMO reads to answer "Hey BMO, how are the downloads?".

A download that fails is tried once more, then left with its problem saved while the rest carry on; the
downloader comes back to it once at the end (2026-10-25: one stuck step used to hold up the whole install).
"""
from __future__ import annotations

import fcntl
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

GB = 1e9
HEADROOM_GB = 10
WAITING, DOWNLOADING, DONE, FAILED = "waiting", "downloading", "done", "failed"
SPOKEN_ROLES = {"qwen3-main:30b": "main brain", "qwen3-emergency:4b": "emergency brain",
                "internvl3.5-deep:30b": "deep vision brain", "qwen3-engineering:30b": "engineering brain",
                "qwen3-coder:30b": "coding brain", "game-player": "game player",
                "screensavers": "screensavers"}

# Extras fetched on the owner's PC right after the main brain, never shipped in the disk image (2026-10-24): the
# game player (Dolphin, from Flathub, for this account only, so no password) and the Windows 95/98 screensaver pack
# (Microsoft's files; the image used to carry them). No games or videos are ever downloaded or shipped.
GAME_PLAYER, SCREENSAVERS = "game-player", "screensavers"
EXTRA_GB = {GAME_PLAYER: 1.6, SCREENSAVERS: 0.02}  # Dolphin 55 MB + the KDE and graphics runtimes (2026-10-24)


def data_dir() -> Path:
    import config
    return Path(config.DATA_ROOT) / "install"


def roster():
    """Main first, then the rest smallest first."""
    from model_installer import MODELS
    main = [m for m in MODELS if m.alias == "qwen3-main:30b"]
    return main + sorted((m for m in MODELS if m not in main), key=lambda m: m.approx_gb)


def spoken(alias: str) -> str:
    return SPOKEN_ROLES.get(alias, alias.split(":")[0].replace("-", " ") + " brain")


def game_player_installed() -> bool:
    from systems.games import dolphin_available
    return dolphin_available()


def install_game_player() -> None:
    import config
    from systems.games import install_dolphin_flatpak
    install_dolphin_flatpak(Path(config.DATA_ROOT))


def screensavers_installed() -> bool:
    import fetch_legacy_screensavers as savers
    return any(Path(savers.DEFAULT_DEST).rglob("*.scr")) if Path(savers.DEFAULT_DEST).is_dir() else False


def install_screensavers() -> None:
    import fetch_legacy_screensavers as savers
    result = savers.fetch()
    if not result.get("ok"):
        raise RuntimeError(str(result.get("error") or "the screensaver pack did not download"))


EXTRA_INSTALLED = {GAME_PLAYER: game_player_installed, SCREENSAVERS: screensavers_installed}
EXTRAS = {GAME_PLAYER: install_game_player, SCREENSAVERS: install_screensavers}
# Flatpak shows no progress BMO can read, so the game player's bar follows the space it takes up on disk
# (up to 99 % until it's really done).
EXTRA_DISK = {GAME_PLAYER: Path.home() / ".local" / "share" / "flatpak"}


def queue(state=None):
    """What the downloader works through: the brains, with the extras on the list right after Main."""
    brains = list(roster())
    extras = [alias for alias in EXTRA_GB if state is not None and alias in state.models()]
    if not extras:
        return brains
    return brains[:1] + [_extra_spec(a) for a in extras] + brains[1:]


def everything():
    """What the install screen lists: the queue with both extras, before the downloader has started too."""
    brains = list(roster())
    return brains[:1] + [_extra_spec(a) for a in EXTRA_GB] + brains[1:]


def _extra_spec(alias):
    from model_installer import ModelSpec
    return ModelSpec(spoken(alias).title(), f"extra:{alias}", alias, EXTRA_GB[alias])


# --- Saved progress -----------------------------------------------------------------------------------

class DownloadState:
    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path else data_dir() / "downloads.json"
        self.data = self.load()

    def load(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def reload(self) -> "DownloadState":
        self.data = self.load()
        return self

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, self.path)

    def models(self) -> dict:
        return self.data.setdefault("models", {})

    def model(self, alias: str) -> dict:
        return self.models().get(alias, {})

    def set(self, alias: str, **fields) -> None:
        self.models().setdefault(alias, {}).update(fields, updated_at=time.time())
        self.data["updated_at"] = time.time()
        self.save()

    def done(self, alias: str) -> bool:
        return self.model(alias).get("status") == DONE

    def finished(self) -> bool:
        return bool(self.data.get("finished_at"))


# --- Downloading --------------------------------------------------------------------------------------

def installed(alias: str, client=None) -> bool:
    """In model_installer's roster as complete, and Ollama knows the name."""
    import model_installer
    record = model_installer._load_state().get("models", {}).get(alias, {})
    if not record.get("complete"):
        return False
    try:
        (client or _client()).show(alias)
        return True
    except Exception:
        return False


def _client():
    import ollama
    return ollama.Client()


def download(spec, state: DownloadState, client=None, *, clock=time.monotonic) -> None:
    """One brain: Ollama's pull with progress, then BMO's stable name for it. Raises on failure."""
    import model_installer
    client = client or _client()
    parts: dict[str, tuple[int, int]] = {}
    started, last_save = clock(), 0.0
    state.set(spec.alias, status=DOWNLOADING, started_at=time.time(), error=None)
    for update in client.pull(spec.source, stream=True):
        digest, total, completed = getattr(update, "digest", None), getattr(update, "total", None), \
            getattr(update, "completed", None)
        if digest and total:
            parts[digest] = (int(total), int(completed or 0))
        if clock() - last_save >= 2.0 and parts:
            last_save = clock()
            total_bytes = sum(t for t, _ in parts.values())
            done_bytes = sum(c for _, c in parts.values())
            rate = done_bytes / max(1.0, clock() - started)
            state.set(spec.alias, status=DOWNLOADING, total=total_bytes, completed=done_bytes, rate=rate)
    client.copy(spec.source, spec.alias)
    client.show(spec.alias)
    roster_state = model_installer._load_state()
    roster_state.setdefault("models", {})[spec.alias] = {
        "role": spec.role, "source": spec.source, "alias": spec.alias, "approx_gb": spec.approx_gb,
        "complete": True, "completed_at_unix": int(time.time())}
    model_installer._save_state(roster_state)
    size = sum(t for t, _ in parts.values()) or int(spec.approx_gb * GB)
    state.set(spec.alias, status=DONE, total=size, completed=size)


NO_INTERNET = "no internet connection"


def run_all(state: DownloadState, client=None, *, benchmark=None, retries: int = 2, sleep=time.sleep,
            already=None, extras=None, online=None, offline_wait: float = 600.0) -> bool:
    """Every brain in order, the extras (alias -> installer) right after Main, then the benchmark.

    Each one gets `retries` tries. One that still fails keeps its problem in the saved state and the rest
    carry on; it gets one more try at the end. With no internet it waits up to `offline_wait` seconds
    instead of using up the tries, then stops, because nothing can download. False when something is
    still missing (the install screen says what, and starts this again when asked)."""
    already = already or (lambda alias: installed(alias, client))
    extras = extras or {}
    online = online or (lambda: True)
    state.data.setdefault("started_at", time.time())
    for alias in extras:
        if alias not in state.models():
            state.set(alias, status=WAITING)
    todo = []
    for spec in queue(state):
        if state.done(spec.alias):
            continue
        extra = spec.alias in EXTRA_GB
        if extra and spec.alias not in extras:
            continue
        if EXTRA_INSTALLED[spec.alias]() if extra else already(spec.alias):
            state.set(spec.alias, status=DONE, total=int(spec.approx_gb * GB), completed=int(spec.approx_gb * GB),
                      already=True)
            continue
        todo.append(spec)
    left = []
    for spec in todo:
        fetched = _fetch(spec, state, client, extras, retries, sleep, online, offline_wait)
        if fetched is None:
            return False
        if not fetched:
            left.append(spec)
    for spec in list(left):  # back to what failed, once
        sleep(10)
        fetched = _fetch(spec, state, client, extras, 1, sleep, online, offline_wait)
        if fetched is None:
            return False
        if fetched:
            left.remove(spec)
    if benchmark is not None and state.done(roster()[0].alias) and benchmark_needed(state):
        _run_benchmark(state, benchmark)
    if left:
        return False
    state.data["finished_at"] = time.time()
    state.save()
    return True


def _fetch(spec, state: DownloadState, client, extras, tries: int, sleep, online, offline_wait) -> bool | None:
    """One brain or extra, `tries` times. None when the internet stayed away (stop everything)."""
    extra = spec.alias in EXTRA_GB
    for attempt in range(1, tries + 1):
        if not _wait_online(online, offline_wait, sleep):
            state.set(spec.alias, status=FAILED, error=NO_INTERNET, attempts=attempt)
            return None
        try:
            if extra:
                _install_extra(spec, extras[spec.alias], state)
            else:
                download(spec, state, client)
            return True
        except Exception as exc:
            state.set(spec.alias, status=FAILED, error=str(exc)[:200], attempts=attempt)
            if attempt < tries:
                sleep(min(300, 10 * 2 ** (attempt - 1)))
    return False


def _wait_online(online, seconds: float, sleep) -> bool:
    if online():
        return True
    for _ in range(max(0, int(seconds // 5))):
        sleep(5)
        if online():
            return True
    return False


def _install_extra(spec, installer, state: DownloadState) -> None:
    """The extra's own installer, with its bar following the disk space it takes (EXTRA_DISK)."""
    expected = int(spec.approx_gb * GB)
    where = EXTRA_DISK.get(spec.alias)
    start_free = free_gb(where) if where else None
    state.set(spec.alias, status=DOWNLOADING, started_at=time.time(), error=None, total=expected, completed=0)
    errors = []

    def work():
        try:
            installer()
        except Exception as exc:
            errors.append(exc)

    worker = threading.Thread(target=work, name=f"bemo-install-{spec.alias}", daemon=True)
    worker.start()
    while worker.is_alive():
        worker.join(2.0)
        if worker.is_alive() and start_free is not None:
            used = max(0.0, (start_free - free_gb(where)) * GB)
            state.set(spec.alias, status=DOWNLOADING, total=expected, completed=int(min(0.99 * expected, used)))
    if errors:
        raise errors[0]
    state.set(spec.alias, status=DONE, total=expected, completed=expected)


def benchmark_needed(state: DownloadState) -> bool:
    """Never run, cut off half way (a restart), or it failed: run it (again)."""
    bench = state.data.get("benchmark")
    return not bench or bool(bench.get("running")) or not bench.get("ok")


def _run_benchmark(state: DownloadState, benchmark) -> None:
    """The benchmark with its progress saved for the install screen, and a second go if it fails."""
    def progress(step, steps, label=""):
        state.data["benchmark"] = {"running": True, "step": int(step), "steps": max(1, int(steps)),
                                   "label": str(label)}
        state.save()

    result = {}
    for _attempt in range(2):
        progress(0, 1, "starting")
        result = benchmark(progress) or {}
        if result.get("ok"):
            break
    state.data["benchmark"] = result
    state.save()


def simple_benchmark(progress=None) -> dict:
    """The thread-count benchmark (about five minutes, Main brain), reduced to what BMO says.
    progress(step, steps, label) follows its stages for the install screen."""
    try:
        import hardware_autotune
        result = hardware_autotune.run_benchmark(force=False, progress=progress)
        record = result.get("record") or {}
        return {"ok": bool(result.get("ok")), "threads": record.get("chosen_ollama_threads"),
                "skipped": result.get("reason") if result.get("skipped") else None}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"[:200]}


# --- One background process ---------------------------------------------------------------------------

def lock_path() -> Path:
    return data_dir() / "downloads.lock"


def running(path: Path | None = None) -> bool:
    """True while a downloader holds the lock."""
    path = path or lock_path()
    if not path.exists():
        return False
    with open(path, "a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return True
        fcntl.flock(handle, fcntl.LOCK_UN)
        return False


def start_in_background(popen=subprocess.Popen) -> bool:
    """Start `downloads_cli.py run` detached unless one is already running or everything is done."""
    state = DownloadState()
    if state.finished() or running():
        return False
    root = Path(__file__).resolve().parents[1]
    log = data_dir() / "downloads.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    with open(log, "ab") as out:
        popen([sys.executable, str(root / "downloads_cli.py"), "run"], cwd=root, stdout=out, stderr=out,
              stdin=subprocess.DEVNULL, start_new_session=True)
    return True


def resume_if_unfinished(popen=subprocess.Popen) -> bool:
    """After a reboot: carry on with downloads that were started and not finished."""
    state = DownloadState()
    return bool(state.data) and not state.finished() and start_in_background(popen)


# --- Disk and progress, in words ---------------------------------------------------------------------

def model_dir() -> Path:
    return Path(os.environ.get("OLLAMA_MODELS") or "/usr/share/ollama/.ollama/models")


def free_gb(path: Path) -> float:
    path = Path(path)
    while not path.exists() and path != path.parent:
        path = path.parent
    usage = shutil.disk_usage(path)
    return usage.free / GB


def still_needed_gb(state: DownloadState, already=None) -> float:
    already = already or (lambda alias: False)
    total = 0.0
    for spec in queue(state):
        if state.done(spec.alias) or already(spec.alias):
            continue
        record = state.model(spec.alias)
        if record.get("total"):
            total += max(0, record["total"] - record.get("completed", 0)) / GB
        else:
            total += spec.approx_gb
    return total


def _minutes(seconds: float) -> str:
    minutes = max(1, round(seconds / 60))
    if minutes < 90:
        return f"about {minutes} minute{'s' if minutes != 1 else ''}"
    hours = round(minutes / 60, 1)
    return f"about {hours:g} hours"


def report(state: DownloadState) -> str:
    """'My main brain is ready. Coding brain: 40 percent, about 25 minutes left. 2 more after it.'"""
    if not state.data:
        return "All my brains are downloaded."
    if state.finished():
        bench = state.data.get("benchmark") or {}
        line = "All my brains are downloaded."
        if bench.get("threads"):
            line += f" My speed test says this computer works best with {bench['threads']} threads."
        return line
    specs = queue(state)
    current = next((s for s in specs if not state.done(s.alias)), None)
    if current is None:
        return "All my brains are downloaded. I'm testing how fast this computer is; it takes about five minutes."
    record = state.model(current.alias)
    parts = []
    main = specs[0]
    if current is not main:
        parts.append("My main brain is ready.")
    total, completed, rate = record.get("total"), record.get("completed", 0), record.get("rate") or 0
    if record.get("status") == FAILED:
        parts.append(f"Downloading my {spoken(current.alias)} stopped, and I'll keep trying. "
                     f"The last problem was: {_plain_error(record.get('error'))}")
    elif total:
        percent = int(100 * completed / total)
        line = f"My {spoken(current.alias)} is {percent} percent downloaded"
        if rate > 0:
            line += f", {_minutes((total - completed) / rate)} left"
        parts.append(line + ".")
    else:
        parts.append(f"I'm starting to download my {spoken(current.alias)}.")
    later = [s for s in specs if s is not current and not state.done(s.alias)]
    if later:
        remaining = sum(s.approx_gb for s in later)
        parts.append(f"{len(later)} more after that, about {remaining:.0f} gigabytes.")
    return " ".join(parts)


def _plain_error(error) -> str:
    text = str(error or "unknown").lower()
    if any(word in text for word in ("connection", "timeout", "temporary failure", "network", "resolve")):
        return "the internet connection."
    if "space" in text:
        return "the disk is full."
    return "something went wrong on the download server."


# While BMO waits for his main brain he can do nothing else, so any of these gets the progress.
_WAITING_QUESTION = re.compile(r"\b(?:downloads?|downloading|progress)\b|\bhow (?:long|much longer)\b|"
                               r"\bhow(?:'s| is) it going\b|\bare you (?:done|ready) yet\b", re.I)
# Any other time it must be a question about the downloads ("download the report" is not one).
_COMMAND = re.compile(r"^(?:how(?:'s| is| are)|what(?:'s| is)|are|is)\b.{0,25}\bdownloads?\b|"
                      r"\bdownloads? (?:status|progress)\b|\bhow long (?:until|till|for|will) (?:the |my |your )?"
                      r"downloads?\b|\bhow much (?:is |are )?left to download\b", re.I)


def is_progress_question(text: str | None, *, waiting: bool = False) -> bool:
    text = str(text or "").strip()
    return bool((_WAITING_QUESTION if waiting else _COMMAND).search(text))
