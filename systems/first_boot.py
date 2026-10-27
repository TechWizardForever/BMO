"""BMO's first boot: the install screen (installer plan I2-I4, rebuilt 2026-10-25).

Runs only while ~/.bemo/install/first-boot.json is open: the disk image's first start or install.sh opens it
(`first_boot_cli.py start` for a rehearsal); installs from before it never get one. BMO's window lists every
step top to bottom with check marks and progress bars (systems/render.py draws SetupView):

  internet      first, because everything after it downloads
  updates       Linux's own updates, before the big downloads so a new kernel can't land half way through
                (it did on 10-24); a new kernel restarts the PC, and the setup carries on after
  downloads     Main, the game player, the screensavers, the other brains, then the speed test, each with
                its own bar (systems/downloads.py does them in a background process)
  microphone, voice, screen
                last, once the person presses Enter: they need someone there

A step that fails is tried once more, then left with its problem on the screen while the rest go on. At
the end BMO goes back to it, and what still fails waits for the person: Enter tries again, S skips it for
now. Every step is saved as it finishes, so a power cut or a restart carries on where it was, with the
steps already done ticked straight away. (On 10-24 one failed microphone check held up the whole install.)

Two places in BMO's start run the steps:
  "before_listening"  speech recognition is loaded, the microphone isn't open yet (all but the screen)
  "listening"         the main brain is warm and the microphone is open (the screen)

Nothing heard is logged or kept: only whether the phrase matched and how loud it was.
"""
from __future__ import annotations

import json
import math
import os
import queue
import re
import subprocess
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

RATE = 16000
RECORD_SECONDS = 5.0
VOICE_SNR_DB = 6.0    # below this, a recording has no speech worth transcribing
QUIET_SNR_DB = 15.0   # heard, but the voice is barely above the room
DONE, FAILED, PENDING, SKIPPED = "done", "failed", "pending", "skipped"
RUNNING = "running"   # on the screen only; never saved
STEP_ORDER = ("internet", "updates", "downloads", "microphone", "voice", "screen")
PERSON_STEPS = ("microphone", "voice", "screen")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --- Saved state --------------------------------------------------------------------------------------

class FirstBootState:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.data = self._load()

    @classmethod
    def default(cls) -> "FirstBootState":
        import config
        return cls(Path(config.DATA_ROOT) / "install" / "first-boot.json")

    def _load(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) and isinstance(data.get("steps"), list) else {}
        except (OSError, ValueError):
            return {}

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, self.path)

    def active(self) -> bool:
        return bool(self.data) and not self.data.get("completed_at")

    def begin(self, names=STEP_ORDER, options: dict | None = None) -> None:
        """options: per step, e.g. {"internet": {"practice_wifi": True}} for a rehearsal on a wired PC."""
        self.data = {"version": 1, "created_at": _now(), "completed_at": None, "options": options or {},
                     "steps": [{"name": n, "status": PENDING, "attempts": 0, "result": {}} for n in names]}
        self._save()

    def steps(self) -> list[dict]:
        return list(self.data.get("steps") or [])

    def started(self) -> bool:
        return any(step.get("attempts") for step in self.steps())

    def mark(self, name: str, status: str, result: dict | None = None) -> None:
        for step in self.data.get("steps", []):
            if step["name"] == name:
                step.update(status=status, attempts=int(step.get("attempts", 0)) + 1, result=result or {},
                            updated_at=_now())
        if all(step["status"] in (DONE, SKIPPED) for step in self.data.get("steps", [])):
            self.data["completed_at"] = _now()
        self._save()


@dataclass
class StepResult:
    status: str
    result: dict = field(default_factory=dict)


# --- Microphone ---------------------------------------------------------------------------------------

@dataclass
class Level:
    noise_rms: float
    speech_rms: float

    @property
    def snr_db(self) -> float:
        return 20 * math.log10(max(self.speech_rms, 1.0) / max(self.noise_rms, 1.0))


def level(pcm16: bytes, frame: int = 480) -> Level:
    """Room level (quiet 20 % of 30 ms frames) and voice level (loudest 5 %) of one recording.

    This PC's motherboard input (nothing plugged in) gives up to 0.41 s of full-scale noise as it opens,
    then exact digital silence (measured 2026-10-19). So the first half second is left out, and a
    recording whose quiet part is digital silence, or that is still clipped, counts as nothing: a real
    microphone always hears a little room.
    """
    samples = np.frombuffer(pcm16, dtype="<i2").astype(np.float32)[int(0.5 * RATE):]
    if not samples.size or np.mean(np.abs(samples) >= 32000) > 0.05:
        return Level(0.0, 0.0)
    frames = samples[: len(samples) // frame * frame].reshape(-1, frame) if len(samples) >= frame else samples[None, :]
    rms = np.sqrt(np.mean(frames ** 2, axis=1))
    noise = float(np.percentile(rms, 20))
    if noise < 0.5:
        return Level(0.0, 0.0)
    return Level(noise, float(np.percentile(rms, 95)))


def heard_phrase(text: str | None) -> bool:
    """'Hey BMO, hello' (or most of it): hello, or a wake phrase BMO would take."""
    words = re.sub(r"[^a-z ]+", " ", str(text or "").lower())
    if re.search(r"\b(?:hello|hallo|hullo|hi there)\b", words):
        return True
    from standby_wake import is_standby_wake_phrase
    return is_standby_wake_phrase(str(text or ""))


def spoken_device(description: str) -> str:
    """'Scarlett Solo (3rd Gen.) Analog Stereo' -> 'Scarlett Solo'; 'ATR4650-USB Mono' -> 'ATR4650 USB';
    the motherboard's 'HD Audio Controller' -> its microphone socket."""
    if re.search(r"\b(?:hd audio|built-in|internal)\b", str(description or ""), re.I):
        return "computer's own microphone socket"
    name = re.sub(r"\([^)]*\)", " ", str(description or "microphone"))
    name = re.sub(r"\b(?:analog|digital|stereo|mono|fallback|input|multichannel)\b", " ", name, flags=re.I)
    name = re.sub(r"[-_]+", " ", name)
    return re.sub(r"\s+", " ", name).strip() or "microphone"


class MicrophoneStep:
    name, phase = "microphone", "before_listening"
    FIRST = "First, your microphone. After this sentence, please say: Hey BMO, hello."
    AGAIN = "I didn't catch that. Please say it once more: Hey BMO, hello."

    def run(self, io) -> StepResult:
        inputs = io.list_inputs()
        if not inputs:
            io.say("I can't find a microphone. Plug one in, and I'll try again the next time I start.")
            return StepResult(FAILED, {"reason": "no microphone"})
        by_name = {i.name: i for i in inputs}
        tried = []
        for prompt in (self.FIRST, self.AGAIN):
            io.say(prompt)
            recordings = io.record_inputs(list(by_name), RECORD_SECONDS)
            scores = []
            for name, pcm in recordings.items():
                lv = level(pcm)
                matched = lv.snr_db >= VOICE_SNR_DB and (heard_phrase(io.transcribe(pcm)) or io.heard_wake(pcm))
                scores.append((matched, lv.snr_db, name))
            tried.append({name: round(snr, 1) for _m, snr, name in scores})
            heard = [s for s in scores if s[0]]
            if not heard:
                continue
            _matched, snr, best = max(heard, key=lambda s: s[1])
            chosen = by_name[best]
            changed = not chosen.default
            if changed:
                io.set_default_input(best)
            message = f"Got it. I can hear you on the {spoken_device(chosen.description)}."
            if snr < QUIET_SNR_DB:
                message += " It's a little noisy, or the microphone is far away, so speak up or move it closer."
            io.say(message)
            return StepResult(DONE, {"input": best, "description": chosen.description, "snr_db": round(snr, 1),
                                     "changed_default": changed})
        io.say("I still can't hear you. Check that the microphone is plugged in and switched on. "
               "I'll ask again the next time I start.")
        return StepResult(FAILED, {"reason": "not heard", "snr_db": tried})


# --- Learning this person's "Hey BMO" (installer plan I7) ------------------------------------------------

def _show(io, row, status=None, detail=None, progress=None):
    """A step's progress on the install screen (the tests' fakes have no screen)."""
    view = getattr(io, "view", None)
    if view is not None:
        view.row(row, status, detail, progress)


class VoiceStep:
    """Like Siri's setup: "Hey BMO" six times, then ten seconds of normal talk. The recordings stay in
    memory; only the small personal check is saved (systems/wake_enrollment.py). Too little heard is a
    problem the install screen comes back to (and the person can skip); a voice the detector hardly
    knows still finishes, with BMO's usual settings."""
    name, phase = "voice", "before_listening"
    options: dict = {}
    WAKES, WAKE_SECONDS, TALK_SECONDS = 6, 2.5, 10.0

    def run(self, io) -> StepResult:
        io.say("Now let me learn your voice, so I wake up for you and not for the TV. Each time I say go, "
               f"say: Hey BMO. We'll do it {self.WAKES} times.")
        wakes, misses = [], 0
        while len(wakes) < self.WAKES and misses < 4:
            _show(io, self.name, RUNNING, f"say Hey BMO ({len(wakes) + 1} of {self.WAKES})",
                  len(wakes) / (self.WAKES + 1))
            io.say("Go." if not wakes and not misses else "Again.")
            pcm = io.record_voice(self.WAKE_SECONDS)
            if level(pcm).snr_db >= VOICE_SNR_DB:
                wakes.append(pcm)
            else:
                misses += 1
                io.say("I didn't hear that.")
        if len(wakes) < 3:
            io.say("I couldn't hear enough.")
            return StepResult(FAILED, {"enrolled": False, "reason": "too little heard", "heard": len(wakes)})
        _show(io, self.name, RUNNING, "talk for ten seconds", self.WAKES / (self.WAKES + 1))
        io.say("Now talk to me normally for about ten seconds. Tell me about your day, or anything at all. Go.")
        talk = io.record_voice(self.TALK_SECONDS)
        io.say("Thanks. Give me a moment.")
        result = io.enroll(wakes, [talk])
        if result.ok:
            io.say("Got it. I've learnt how you say my name.")
        else:
            io.say("Your voice is new to me, so I'll keep my usual settings and listen with my other ears too. "
                   "You can say, Hey BMO, learn my voice, to try again.")
        return StepResult(DONE, {"enrolled": result.ok, "personal_check": result.verifier,
                                 "threshold": result.threshold, "heard": len(wakes)})


# --- Screen -------------------------------------------------------------------------------------------

def yes_or_no(text: str | None):
    """True, False, or None when it's neither."""
    from systems.voice_intents import canonical
    words = canonical(str(text or ""))
    words = re.sub(r"^(?:oh|um|uh|well|hmm)\s+", "", words)
    if not words:
        return None
    if re.match(r"^(?:no|nope|nah|not really)\b", words) or re.search(
            r"\b(?:cut off|cannot see|can not see|doesn t|does not|isn t|is not|too big|too small|missing)\b", words):
        return False
    if re.match(r"^(?:yes|yeah|yep|yup|sure|correct|right|it does|it fits|i can|all good|looks good|perfect)\b",
                words) or re.search(r"\b(?:fits|looks good|looks fine|see all)\b", words):
        return True
    return None


class ScreenStep:
    """The install screen has a white frame round its edge: can the person see all of it? Yes or no, said
    or typed (Y / N), since the microphone may be the step that didn't work."""
    name, phase = "screen", "listening"
    QUESTION = ("Last one, the screen. Can you see the whole white frame around the edge, with nothing cut off? "
                "Say yes or no, or press Y or N.")
    AGAIN = "Please say yes or no, or press Y or N. Can you see the whole white frame?"

    def run(self, io) -> StepResult:
        for question in (self.QUESTION, self.AGAIN):
            answer = yes_or_no(io.ask(question))
            if answer is True:
                io.say("Great.")
                return StepResult(DONE, {"fits": True})
            if answer is False:
                io.calibrate_screen()
                return StepResult(DONE, {"fits": False, "calibration_offered": True})
        io.say("I'll leave the screen as it is. You can say, Hey BMO, calibrate the screen, any time.")
        return StepResult(DONE, {"fits": None})


# --- Internet -----------------------------------------------------------------------------------------

class InternetStep:
    """Everything after this downloads, so the setup waits here until there's a connection: a network
    cable, or a Wi-Fi network picked on the screen with the arrow keys (or its number) and Enter. Mint's
    own window asks for the password, which the person types (BMO never sees it, decision B). It never
    gives up: the screen says what to do, and BMO carries on by himself once he's online."""
    name, phase = "internet", "before_listening"
    options: dict = {}
    RESCAN_SECONDS = 15.0
    CHECK_SECONDS = 3.0

    def run(self, io) -> StepResult:
        practice = bool(self.options.get("practice_wifi"))  # the Wi-Fi part on a PC that is online already
        if not practice and io.online():
            link = io.wifi.link()
            return StepResult(DONE, dict({"online": True, "how": "already"}, **({"link": link} if link else {})))
        return self.wait(io, practice=practice)

    def wait(self, io, *, lost=False, practice=False) -> StepResult:
        """Until online. lost: the connection went away during the downloads."""
        from systems.wifi import spoken_reason
        device = io.wifi.device()
        view = io.view
        if lost:
            io.say("I lost the internet. I'll carry on as soon as it's back.")
        elif device is None:
            io.say("I need the internet first, and I can't find Wi-Fi on this computer. Please plug in a network cable.")
        elif not practice:
            io.say("I need the internet first. Plug in a network cable, or pick your Wi-Fi on the screen.")
        view.row(self.name, FAILED if lost else RUNNING, "lost the connection" if lost else "not connected")
        networks, scanned, selected = [], None, 0
        try:
            while True:
                if not practice and io.online():
                    io.say("I'm online again." if lost else "I'm online.")
                    return StepResult(DONE, {"online": True, "how": "cable" if device is None else "connected"})
                if device is not None and (scanned is None or io.now() - scanned >= self.RESCAN_SECONDS):
                    networks, scanned = io.wifi.networks(limit=9), io.now()
                    selected = min(selected, max(0, len(networks) - 1))
                if device is None:
                    view.message("Plug in a network cable. I'll carry on by myself once I'm online.", "")
                elif networks:
                    view.message("Plug in a network cable, or pick your Wi-Fi:",
                                 "Up / Down to pick, Enter to connect")
                else:
                    view.message("I can't see any Wi-Fi networks. Check the router is on, or plug in a network "
                                 "cable.", "")
                view.show_choices([n.ssid for n in networks], selected)
                key = view.key(self.CHECK_SECONDS)
                if key == "up":
                    selected = max(0, selected - 1)
                elif key == "down":
                    selected = min(max(0, len(networks) - 1), selected + 1)
                elif key and key.isdigit() and 1 <= int(key) <= len(networks):
                    selected, key = int(key) - 1, "enter"
                if key != "enter" or not networks:
                    continue
                chosen = networks[selected]
                if chosen.secured:
                    view.message(f"Type the password for {chosen.ssid} in the window that opens, then press Enter.", "")
                    io.say(f"A window will ask for the password of {chosen.ssid}. Type it with the keyboard, "
                           "then press Enter.")
                else:
                    view.message(f"Connecting to {chosen.ssid}.", "")
                    io.say(f"Connecting to {chosen.ssid}.")
                ok, message = io.wifi.connect(chosen.ssid)
                if ok and (practice or io.wait_until_online(30)):
                    io.say(f"I'm online, through {chosen.ssid}.")
                    return StepResult(DONE, {"online": True, "how": "wifi", "ssid": chosen.ssid})
                reason = spoken_reason(message)
                view.message(f"{reason} Pick it again to have another go.", "Up / Down to pick, Enter to connect")
                io.say(f"{reason} Pick it again to have another go.")
                scanned = None
        finally:
            view.show_choices([], 0)
            view.message("", "")


# --- Linux updates ------------------------------------------------------------------------------------

class UpdatesStep:
    """Linux's own updates, through the disk image's bemo-system-update.service: a root-owned helper that
    runs only apt-get update and upgrade (tools/image/system_update.py). This account may start that one
    service without a password (a polkit rule) and reads its progress. An install without it (install.sh)
    skips the step: its owner does the updates."""
    name, phase = "updates", "before_listening"
    options: dict = {}
    START_SECONDS = 60.0   # the service should have written its first progress by then

    def run(self, io) -> StepResult:
        if not io.update_service_installed():
            return StepResult(SKIPPED, {"reason": "no update service"})
        before = int((io.system_update_status() or {}).get("run", 0))
        ok, message = io.start_system_update()
        if not ok:
            return StepResult(FAILED, {"reason": "not started", "error": message[-160:]})
        io.say("First, Linux's own updates. This takes a few minutes.")
        began = io.now()
        while True:
            status = io.system_update_status() or {}
            mine = int(status.get("run", 0)) > before
            if mine:
                percent = float(status.get("percent") or 0.0)
                phase = status.get("phase") or ""
                _show(io, self.name, RUNNING, f"{phase} {int(percent)}%".strip(), percent / 100)
                if status.get("state") in ("done", "failed") and not io.system_update_running():
                    break
            elif io.now() - began > self.START_SECONDS and not io.system_update_running():
                return StepResult(FAILED, {"reason": "not started", "error": "the update service didn't run"})
            io.pause(1.0)
        if status.get("state") == "done":
            return StepResult(DONE, {"reboot": bool(status.get("reboot_needed"))})
        return StepResult(FAILED, {"reason": "apt", "error": str(status.get("error") or "")[-160:]})


# --- Downloads ----------------------------------------------------------------------------------------

class DownloadsStep:
    """Starts the background downloader and follows it on the screen until it stops; systems/downloads.py
    does the tries. If the internet goes, the screen says so and the downloader waits for it. A download
    that still failed is left as a problem while the rest of the setup goes on (BMO comes back to it)."""
    name, phase = "downloads", "before_listening"
    options: dict = {}
    GRACE_SECONDS = 30.0   # a just-started downloader takes a moment to show up
    ONLINE_CHECK_SECONDS = 10.0
    internet: "InternetStep | None" = None   # waits when the connection goes (Setup sets it)

    def run(self, io) -> StepResult:
        from systems import downloads as dl
        state = io.downloads_state()
        if state.finished() and dl.benchmark_needed(state):
            # Everything is here and only the speed test didn't work: the downloader runs it again.
            state.data.pop("finished_at", None)
            state.data.pop("benchmark", None)
            state.save()
        if not state.finished():
            need = dl.still_needed_gb(state, already=io.model_installed)
            if need > 0:
                free = io.free_gb()
                if free < need + dl.HEADROOM_GB:
                    more = need + dl.HEADROOM_GB - free
                    io.say(f"My brains need about {more:.0f} more gigabytes of free disk space. Please free some "
                           "space, and I'll try again.")
                    return StepResult(FAILED, {"reason": "disk", "need_gb": round(need + dl.HEADROOM_GB),
                                               "free_gb": round(free)})
                if need > 2 and not state.data.get("started_at"):
                    io.say(f"Now I'm downloading my brains. It's about {need:.0f} gigabytes, so it takes a while, "
                           "and you don't need to stay. I'll call you when I need you.")
            io.start_downloads()
        started, checked, offline = io.now(), io.now(), False
        while not state.reload().finished():
            show_downloads(io.view, state)
            if io.now() - checked >= self.ONLINE_CHECK_SECONDS:
                checked, was_offline = io.now(), offline
                offline = not io.online()
                if offline and not was_offline:
                    io.view.row("internet", FAILED, "lost the connection")
                    io.view.message("I lost the internet. I'll carry on as soon as it's back.", "")
                    io.say("I lost the internet. I'll carry on as soon as it's back.")
                elif was_offline and not offline:
                    io.view.row("internet", DONE, "connected")
                    io.view.message("", "")
            if not io.downloader_running() and io.now() - started > self.GRACE_SECONDS:
                if not io.online() and self.internet is not None:
                    self.internet.wait(io, lost=True)
                    io.view.row("internet", DONE, "connected")
                    io.start_downloads()
                    started = io.now()
                    continue
                failed = {alias: _plain(record.get("error")) for alias, record in state.models().items()
                          if record.get("status") == dl.FAILED}
                return StepResult(FAILED, {"reason": "download", "failed": failed})
            io.pause(1.0)
        show_downloads(io.view, state)
        state.data["announced"] = True  # the screen showed it all; no spoken report at the next start
        state.save()
        bench = state.data.get("benchmark") or {}
        if not bench.get("ok"):
            return StepResult(FAILED, {"reason": "speed test", "error": str(bench.get("error") or "")[:120]})
        return StepResult(DONE, {"threads": bench.get("threads")})


def _plain(error) -> str:
    from systems.downloads import _plain_error
    return _plain_error(error).rstrip(".")


def _size(gb: float) -> str:
    return f"{gb * 1000:.0f} MB" if gb < 1 else f"{gb:g} GB"


def _left(seconds: float) -> str:
    minutes = round(seconds / 60)
    if minutes < 1:
        return "under a minute left"
    if minutes < 60:
        return f"{minutes} min left"
    return f"{minutes // 60} h {minutes % 60:02d} min left"


def show_downloads(view, state) -> None:
    """Each download's row from systems/downloads.py's saved progress, and the speed test's."""
    from systems import downloads as dl
    for spec in dl.everything():
        record = state.model(spec.alias)
        status = record.get("status")
        if status == dl.DONE:
            view.row(spec.alias, DONE, "already here" if record.get("already") else _size(spec.approx_gb), 1.0)
        elif status == dl.DOWNLOADING:
            total, completed, rate = record.get("total") or 0, record.get("completed") or 0, record.get("rate") or 0
            fraction = completed / total if total else None
            detail = f"{int(100 * fraction)}%" if fraction is not None else "starting"
            if rate > 0 and total:
                detail += "  " + _left((total - completed) / rate)
            view.row(spec.alias, RUNNING, detail, fraction)
        elif status == dl.FAILED:
            view.row(spec.alias, FAILED, _plain(record.get("error")), None)
        else:
            view.row(spec.alias, PENDING, _size(spec.approx_gb), None)
    bench = state.data.get("benchmark")
    if not bench:
        view.row("benchmark", PENDING, "about 5 min", None)
    elif bench.get("running"):
        view.row("benchmark", RUNNING, bench.get("label") or "", bench.get("step", 0) / max(1, bench.get("steps", 1)))
    elif bench.get("ok"):
        view.row("benchmark", DONE, f"{bench['threads']} threads" if bench.get("threads") else "done", 1.0)
    else:
        view.row("benchmark", FAILED, "didn't finish", None)


# --- What the install screen shows --------------------------------------------------------------------

class SetupView:
    """The install screen's contents. The steps' thread writes it; BMO's window draws a copy each frame
    (snapshot) and hands it the keys pressed (press). blocking=False is for tests: key() never waits."""
    WEIGHTS = {"internet": 1.0, "updates": 2.0, "benchmark": 3.0, "microphone": 1.0, "voice": 1.0, "brain": 1.0, "screen": 1.0}

    def __init__(self, blocking: bool = True):
        self._lock = threading.Lock()
        self._keys: "queue.Queue[str]" = queue.Queue()
        self._blocking = blocking
        self._empty_reads = 0
        self.active = False
        self._rows: list[dict] = []
        self._weights: dict[str, float] = {}
        self.text, self.hint = "", ""
        self.choices: list[str] = []
        self.selected = 0
        self.problems: list[str] = []
        self.frame = False

    def open(self) -> None:
        """Every row, in order; the statuses come from what's saved (Setup.show)."""
        from systems import downloads as dl
        rows = [("internet", "Internet"), ("updates", "Linux updates")]
        rows += [(spec.alias, dl.spoken(spec.alias)[:1].upper() + dl.spoken(spec.alias)[1:]) for spec in dl.everything()]
        rows += [("benchmark", "Speed test"), ("microphone", "Microphone"), ("voice", "Learn your voice"),
                 ("brain", "Start main brain"), ("screen", "Screen fit")]
        sizes = {spec.alias: spec.approx_gb for spec in dl.everything()}
        with self._lock:
            known = {row["key"]: row for row in self._rows}
            self._rows = [known.get(key) or {"key": key, "label": label, "status": PENDING, "detail": "",
                                              "progress": None} for key, label in rows]
            self._weights = {key: max(0.5, sizes.get(key, self.WEIGHTS.get(key, 1.0))) for key, _ in rows}
        self.active = True

    def row(self, key, status=None, detail=None, progress=None) -> None:
        with self._lock:
            for row in self._rows:
                if row["key"] == key:
                    if status is not None:
                        row["status"] = status
                    if detail is not None:
                        row["detail"] = str(detail)
                    row["progress"] = progress if progress is None else max(0.0, min(1.0, float(progress)))
                    return

    def status(self, key) -> str | None:
        with self._lock:
            return next((row["status"] for row in self._rows if row["key"] == key), None)

    def message(self, text: str, hint: str | None = None) -> None:
        with self._lock:
            self.text = str(text or "")
            if hint is not None:
                self.hint = str(hint)

    def show_choices(self, names, selected: int = 0) -> None:
        with self._lock:
            self.choices, self.selected = list(names), int(selected)

    def show_problems(self, lines) -> None:
        with self._lock:
            self.problems = list(lines)

    def overall(self) -> float:
        with self._lock:
            return self._overall()

    def _overall(self) -> float:
        total = sum(self._weights.values()) or 1.0
        got = 0.0
        for row in self._rows:
            weight = self._weights.get(row["key"], 1.0)
            if row["status"] in (DONE, SKIPPED):
                got += weight
            elif row["status"] == RUNNING and row["progress"] is not None:
                got += weight * row["progress"]
        return got / total

    def snapshot(self) -> dict:
        with self._lock:
            return {"rows": [dict(row) for row in self._rows], "overall": self._overall(), "text": self.text,
                    "hint": self.hint, "choices": list(self.choices), "selected": self.selected,
                    "problems": list(self.problems), "frame": self.frame,
                    "done": sum(row["status"] in (DONE, SKIPPED) for row in self._rows), "total": len(self._rows)}

    def press(self, name: str) -> None:
        """From BMO's window: enter, skip, yes, no, up, down or a digit."""
        if self.active and name:
            self._keys.put(str(name))

    def key(self, timeout: float = 0.0) -> str | None:
        try:
            if self._blocking and timeout > 0:
                return self._keys.get(timeout=timeout)
            return self._keys.get_nowait()
        except queue.Empty:
            if not self._blocking:
                self._empty_reads += 1
                if self._empty_reads > 2000:
                    raise RuntimeError("a test waited for a key that was never pressed")
            return None


# --- The runner ---------------------------------------------------------------------------------------

STEPS = {step.name: step for step in (InternetStep(), UpdatesStep(), DownloadsStep(), MicrophoneStep(), VoiceStep(),
                                      ScreenStep())}
STEPS["downloads"].internet = STEPS["internet"]
GREETING = "Hi! I'm BMO. Let's get me set up. I'll show you each step on the screen."
FINISHED = "That's the setup done!"
LABELS = {"internet": "the internet", "updates": "the Linux updates", "downloads": "my downloads", "microphone": "the microphone",
          "voice": "learning your voice", "screen": "the screen"}
PERSON_WORDS = {"microphone": "the microphone", "voice": "your voice", "screen": "the screen"}
REMIND_SECONDS = 20 * 60
RETRY_DOWNLOADS_SECONDS = 10 * 60


def _join(words: list[str]) -> str:
    return words[0] if len(words) == 1 else ", ".join(words[:-1]) + " and " + words[-1]


def problem_lines(name: str, result: dict) -> list[str]:
    """What the "these need you" panel says about one failed step."""
    from systems import downloads as dl
    reason = (result or {}).get("reason")
    if name == "downloads":
        if reason == "disk":
            return [f"Brains: I need about {max(1, result.get('need_gb', 0) - result.get('free_gb', 0))} GB more "
                    "free disk space."]
        if reason == "speed test":
            return ["Speed test: it didn't finish. Until it does, I use a safe setting."]
        return [f"{dl.spoken(alias)[:1].upper() + dl.spoken(alias)[1:]}: {why}."
                for alias, why in sorted((result.get("failed") or {}).items())] or ["Downloads: they stopped."]
    if name == "updates":
        why = (result or {}).get("error") or "they didn't finish"
        return [f"Linux updates: {why}"]
    if name == "microphone":
        if reason == "no microphone":
            return ["Microphone: I can't find one. Plug one in."]
        return ["Microphone: I couldn't hear you. Check it's plugged in, switched on and turned up."]
    if name == "voice":
        if reason == "needs the microphone":
            return ["Learn your voice: it needs the microphone first."]
        return ["Learn your voice: I couldn't hear enough."]
    return [f"{name.title()}: it didn't work."]


def _spoken_problems(failed: list[str]) -> str:
    return f"I still need help with {_join([LABELS.get(n, n) for n in failed])}."


def describe(name: str, outcome: StepResult) -> str:
    """The row's short note once a step has run."""
    result = outcome.result or {}
    if name == "internet":
        return (result.get("ssid") or result.get("link")
                or {"cable": "cable", "already": "connected"}.get(result.get("how"), "connected"))
    if name == "updates":
        if outcome.status == SKIPPED:
            return "skipped for now" if result.get("skipped") else "you do these yourself"
        if outcome.status == DONE:
            return "restarting" if result.get("reboot") else "up to date"
        return "didn't finish"
    if name == "microphone":
        if outcome.status == DONE:
            return spoken_device(result.get("description", "")).replace("computer's own microphone socket",
                                                                        "built-in")
        return "no microphone found" if result.get("reason") == "no microphone" else "couldn't hear you"
    if name == "voice":
        if outcome.status != DONE:
            return "needs the microphone" if result.get("reason") == "needs the microphone" else "couldn't hear enough"
        return "learnt" if result.get("enrolled") else "usual settings"
    if name == "screen":
        return {True: "fits", False: "adjusted"}.get(result.get("fits"), "left as it is")
    return ""


class Setup:
    """The install screen's steps in order (see the top of this file)."""

    def __init__(self, state: FirstBootState, io, view: SetupView | None = None, steps: dict | None = None, log=None):
        self.state, self.io, self.steps = state, io, steps or STEPS
        self.view = view or getattr(io, "view", None) or SetupView(blocking=False)
        self.log = log or (lambda *args: None)

    def _entry(self, name):
        return next((entry for entry in self.state.steps() if entry["name"] == name), None)

    def _status(self, name):
        entry = self._entry(name)
        return entry["status"] if entry else None

    def _failed(self) -> list[str]:
        return [n for n in STEP_ORDER if n != "screen" and self._status(n) == FAILED]

    def show(self) -> bool:
        """The screen with what's saved: ticks for the steps already done. False: no setup open."""
        if not self.state.active():
            self.view.active = False
            return False
        for entry in self.state.steps():
            if entry["name"] not in self.steps:  # an older first boot's step that no longer exists
                self.log("First boot: step %s isn't built yet; skipped", entry["name"])
                self.state.mark(entry["name"], SKIPPED, {"reason": "not built yet"})
        self.view.open()
        for entry in self.state.steps():
            name, status = entry["name"], entry["status"]
            if name == "downloads":
                show_downloads(self.view, self.io.downloads_state())
            elif name in self.steps and status != PENDING:
                outcome = StepResult(status, entry.get("result") or {})
                self.view.row(name, SKIPPED if status == SKIPPED else status, describe(name, outcome))
        return self.state.active()

    def run(self, phase: str) -> bool:
        """Run this phase's steps. Always True: a failure never stops BMO starting (the screen shows it)."""
        if not self.show():
            return True
        if phase == "before_listening":
            if not self.state.started():
                self.io.say(GREETING)
            for name in ("internet", "updates", "downloads"):
                self._step(name)
            if any(self._status(n) in (PENDING, FAILED) for n in PERSON_STEPS):
                self._wait_for_person()
            for name in ("microphone", "voice"):
                self._step(name)
            self._go_back()
            if self.state.active():
                self.view.row("brain", RUNNING, "about a minute")
                self.view.message("Starting my main brain. This takes about a minute.", "")
            else:  # nothing was left for the listening part (the screen was done before)
                self.io.say(FINISHED)
            return True
        self.view.row("brain", DONE, "")
        self.view.message("", "")
        self._step("screen")
        if not self.state.active():
            self.view.message("All done!", "")
            self.io.say(FINISHED)
        self.view.active = False
        return True

    def _step(self, name: str, again: bool = False) -> bool:
        entry = self._entry(name)
        if entry is None or entry["status"] in (DONE, SKIPPED):
            return True
        if name == "voice" and self._status("microphone") not in (DONE, SKIPPED, None):
            outcome = StepResult(FAILED, {"reason": "needs the microphone"})
        else:
            step = self.steps[name]
            step.options = (self.state.data.get("options") or {}).get(name, {})
            self.view.row(name, RUNNING, "trying again" if again else "")
            self.view.frame = name == "screen"
            try:
                outcome = step.run(self.io)
            finally:
                self.view.frame = False
        self.state.mark(name, outcome.status, outcome.result)
        self.log("First boot: %s %s %s", name, outcome.status, outcome.result)
        if name != "downloads":
            self.view.row(name, outcome.status, describe(name, outcome))
        if name == "updates" and outcome.status == DONE and outcome.result.get("reboot"):
            self._restart_computer()
        return outcome.status != FAILED

    def _restart_computer(self) -> None:
        """A new kernel came in: restart now, before the long downloads. The step is saved as done, so the
        setup carries on with the downloads after the restart."""
        self.view.message("Linux put in a new kernel, so I'm restarting the computer. I'll carry on by myself "
                          "afterwards.", "")
        self.io.say("Linux put in a new kernel, so I'm restarting the computer. I'll carry on by myself afterwards.")
        if self.io.restart_computer():
            self.io.pause(120)  # the restart takes BMO with it

    def _wait_for_person(self) -> None:
        downloads_left = self._status("downloads") == FAILED
        left = [PERSON_WORDS[n] for n in PERSON_STEPS if self._status(n) in (PENDING, FAILED)]
        line = ("Some of my downloads need another go; I'll come back to them. " if downloads_left
                else "My brains are all here. ") + \
            f"The last part needs you for a couple of minutes: {_join(left)}. Press Enter when you're ready."
        self.view.message("Ready for the last part? It needs you for a couple of minutes.",
                          "Press Enter when you're ready")
        self.io.say(line)
        reminders = 0
        while self.view.key(REMIND_SECONDS) != "enter":
            if reminders < 3:
                reminders += 1
                self.io.say("I'm ready for the last part of my setup. Press Enter when you're here.")
        self.view.message("", "")

    def _go_back(self) -> None:
        """Once more for what failed; then what still fails waits for the person."""
        failed = self._failed()
        if not failed:
            return
        self.io.say(f"Let me go back to {_join([LABELS.get(n, n) for n in failed])}.")
        for name in failed:
            self._step(name, again=True)
        told = False
        while True:
            failed = self._failed()
            if not failed:
                self.view.show_problems([])
                self.view.message("", "")
                return
            lines = [line for name in failed for line in problem_lines(name, self._entry(name).get("result") or {})]
            self.view.show_problems(lines)
            self.view.message("", "Enter: try again      S: skip for now")
            if not told:
                self.io.say(_spoken_problems(failed) + " The screen says what's wrong. Press Enter to try again, "
                            "or S to skip it for now.")
                told = True
            key = self.view.key(RETRY_DOWNLOADS_SECONDS if "downloads" in failed else REMIND_SECONDS)
            if key == "skip":
                for name in failed:
                    entry = self._entry(name)
                    self.state.mark(name, SKIPPED, dict(entry.get("result") or {}, skipped="for now"))
                    self.view.row(name, SKIPPED, "skipped for now")
                self.view.show_problems([])
                self.view.message("", "")
                self.io.say("Okay, I'll skip that for now." + (
                    " My downloads keep trying in the background." if "downloads" in failed else ""))
                return
            if key == "enter":
                self.view.show_problems([])
                self.view.message("Trying again.", "")
                for name in failed:
                    self._step(name, again=True)
            elif key is None and "downloads" in failed and self.io.online():
                self._step("downloads", again=True)  # the internet may be back by itself


# --- BMO's side: speaking, listening, recording -------------------------------------------------------

def record_sources(names: list[str], seconds: float, popen=subprocess.Popen) -> dict[str, bytes]:
    """Record every input at once (16 kHz mono), so one "Hey BMO, hello" tests them all."""
    wanted = int(RATE * seconds) * 2
    results: dict[str, bytes] = {}

    def capture(name):
        proc = popen(["parec", f"--device={name}", "--rate=16000", "--channels=1", "--format=s16le", "--raw"],
                     stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        data = bytearray()
        deadline = time.monotonic() + seconds + 2.0
        try:
            while len(data) < wanted and time.monotonic() < deadline:
                chunk = proc.stdout.read(min(8192, wanted - len(data)))
                if not chunk:
                    break
                data.extend(chunk)
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
        results[name] = bytes(data)

    threads = [threading.Thread(target=capture, args=(n,), daemon=True) for n in names]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(seconds + 5)
    return results


class ChatIO:
    """The first-boot steps' view of a running BMO (systems/chat.py)."""

    def __init__(self, chat, sound=None, wifi=None):
        from systems.sound import PactlSound
        from systems.wifi import Wifi
        self.chat = chat
        self.sound = sound or PactlSound()
        self.wifi = wifi or Wifi()
        self.view = getattr(chat, "setup_view", None) or SetupView()

    def online(self) -> bool:
        from systems.wifi import online
        return online()

    # Linux updates (tools/image/system_update.py, through its service)
    UPDATE_UNIT = "bemo-system-update.service"
    UPDATE_STATUS = Path("/var/lib/bemo/system-update.json")

    def update_service_installed(self) -> bool:
        return Path("/etc/systemd/system", self.UPDATE_UNIT).is_file()

    def system_update_status(self) -> dict:
        try:
            data = json.loads(self.UPDATE_STATUS.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def start_system_update(self) -> tuple[bool, str]:
        try:
            done = subprocess.run(["systemctl", "start", "--no-block", self.UPDATE_UNIT], capture_output=True,
                                  text=True, timeout=30, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return False, str(exc)
        return done.returncode == 0, (done.stderr or done.stdout or "").strip()

    def system_update_running(self) -> bool:
        try:
            done = subprocess.run(["systemctl", "is-active", self.UPDATE_UNIT], capture_output=True, text=True,
                                  timeout=10, check=False)
        except (OSError, subprocess.TimeoutExpired):
            return False
        return done.stdout.strip() in ("activating", "active", "reloading")

    def restart_computer(self) -> bool:
        try:
            return subprocess.run(["systemctl", "reboot"], capture_output=True, timeout=30, check=False).returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            return False

    # Downloads (I4)
    def downloads_state(self):
        from systems.downloads import DownloadState
        return DownloadState()

    def model_installed(self, alias: str) -> bool:
        from systems.downloads import installed
        return installed(alias)

    def free_gb(self) -> float:
        from systems.downloads import free_gb, model_dir
        return free_gb(model_dir())

    def start_downloads(self) -> None:
        from systems.downloads import start_in_background
        start_in_background()

    def downloader_running(self) -> bool:
        from systems.downloads import running
        return running()

    def now(self) -> float:
        return time.monotonic()

    def pause(self, seconds: float) -> None:
        time.sleep(seconds)

    def wait_until_online(self, seconds: float) -> bool:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if self.online():
                return True
            time.sleep(3)
        return False

    def say(self, text: str) -> None:
        from systems.chat import State
        self.chat.state = State.RESPONDING
        self.chat.speak(text)
        self.chat.state = State.THINKING

    def list_inputs(self):
        try:
            return self.sound.inputs() if self.sound.available() else []
        except RuntimeError:
            return []

    def set_default_input(self, name: str) -> None:
        self.sound.use_input(name)

    def record_inputs(self, names, seconds):
        from systems.chat import State
        self.chat.state = State.LISTENING
        try:
            return record_sources(names, seconds)
        finally:
            self.chat.state = State.THINKING

    def record_voice(self, seconds: float):
        """The default microphone, for voice enrollment (kept in memory only)."""
        from systems.chat import State
        self.chat.state = State.LISTENING
        try:
            pcm = record_sources(["@DEFAULT_SOURCE@"], seconds).get("@DEFAULT_SOURCE@", b"")
        finally:
            self.chat.state = State.THINKING
        return pcm

    def enroll(self, wakes, talk):
        import config
        from systems.wake_enrollment import Enrollment, enroll, personal_dir
        if not getattr(config, "ENABLE_WAKEWORD_MODEL", False) or not Path(config.WAKEWORD_MODEL_PATH).is_file():
            return Enrollment(False, float(config.WAKEWORD_THRESHOLD), False, len(wakes), [], "no detector")
        as_array = lambda pcm: np.frombuffer(pcm, dtype="<i2")
        return enroll([as_array(w) for w in wakes], [as_array(t) for t in talk],
                      model_path=config.WAKEWORD_MODEL_PATH, melspec=config.WAKEWORD_MELSPEC_PATH,
                      embedding=config.WAKEWORD_EMBEDDING_PATH, base_threshold=float(config.WAKEWORD_THRESHOLD),
                      out_dir=personal_dir(config))

    def transcribe(self, pcm16: bytes):
        """Whisper directly: BMO's usual path logs and saves rejected audio, and a second microphone may
        have picked up the room. Nothing here is logged or kept."""
        import config
        samples = np.frombuffer(pcm16, dtype="<i2").astype(np.float32) / 32768.0
        if not samples.size:
            return ""
        segments, _ = self.chat.stt.transcribe(samples, language=config.STT_LANGUAGE, beam_size=config.STT_BEAM_SIZE,
                                               temperature=0.0, condition_on_previous_text=False,
                                               hotwords="Bemo", max_new_tokens=48)
        return " ".join(segment.text.strip() for segment in segments).strip()

    def heard_wake(self, pcm16: bytes) -> bool:
        import config
        from systems.wakeword import detector
        found = detector(config)
        return bool(found and found.heard(pcm16))

    def ask(self, question: str, seconds: float = 10.0):
        """The spoken answer (without a leading "Hey BMO"), or "yes" / "no" from the Y and N keys."""
        self.say(question)
        source = getattr(self.chat, "_active_microphone", None)
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            key = self.view.key(0)
            if key in ("yes", "no"):
                return key
            if source is None:
                time.sleep(0.2)
                continue
            heard = self.chat.listen(source)  # about a second when nobody speaks
            if heard:
                return self.chat.voice_gate.clipped_wake(heard)[1]
        return None

    def calibrate_screen(self) -> None:
        self.chat._calibration_session("calibrate the screen")
