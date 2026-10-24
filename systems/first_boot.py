"""BMO's spoken first boot (installer plan, milestone I2).

Runs only while ~/.bemo/install/first-boot.json is open: the installer (or `first_boot_cli.py start`
for a rehearsal) creates it; installs from before it never get one. Each step is saved as soon as it
finishes, so a power cut or a restart picks up at the first step that isn't done. A step that fails
(no microphone found, nothing heard) is tried again at the next start.

Two places in BMO's start run the steps, in order:
  "before_listening"  speech recognition is loaded, the microphone isn't open yet (microphone, voice)
  "listening"         the microphone is open and calibrated (screen, internet, downloads)
The first start (greeting, examples) comes next.

Nothing heard is logged or kept: only whether the phrase matched and how loud it was.
"""
from __future__ import annotations

import json
import math
import os
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
STEP_ORDER = ("microphone", "voice", "screen", "internet", "downloads")


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

class VoiceStep:
    """Like Siri's setup: "Hey BMO" six times, then ten seconds of normal talk. The recordings stay in
    memory; only the small personal check is saved (systems/wake_enrollment.py). Never blocks the setup:
    if too little was heard, BMO keeps his usual settings and says how to try again."""
    name, phase = "voice", "before_listening"
    options: dict = {}
    WAKES, WAKE_SECONDS, TALK_SECONDS = 6, 2.5, 10.0

    def run(self, io) -> StepResult:
        io.say("Now let me learn your voice, so I wake up for you and not for the TV. Each time I say go, "
               f"say: Hey BMO. We'll do it {self.WAKES} times.")
        wakes, misses = [], 0
        while len(wakes) < self.WAKES and misses < 4:
            io.say("Go." if not wakes and not misses else "Again.")
            pcm = io.record_voice(self.WAKE_SECONDS)
            if level(pcm).snr_db >= VOICE_SNR_DB:
                wakes.append(pcm)
            else:
                misses += 1
                io.say("I didn't hear that.")
        if len(wakes) < 3:
            io.say("I couldn't hear enough. I'll keep my usual settings. You can say, Hey BMO, learn my voice, "
                   "any time to try again.")
            return StepResult(DONE, {"enrolled": False, "reason": "too little heard", "heard": len(wakes)})
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
    name, phase = "screen", "listening"
    QUESTION = "Now the screen. Can you see all of my face, with nothing cut off at the edges? Say yes or no."
    AGAIN = "Please say yes or no. Does my face fit the screen?"

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

_NONE = re.compile(r"\b(?:none|neither|not there|not listed|skip|cancel)\b", re.I)


class InternetStep:
    """Online already: nothing to do. Otherwise Wi-Fi: BMO reads the nearby networks, the owner says the
    number, and Mint's own window asks for the password, which the owner types (decision B)."""
    name, phase = "internet", "listening"
    options: dict = {}

    def run(self, io) -> StepResult:
        practice = bool(self.options.get("practice_wifi"))
        if not practice and io.online():
            io.say("I'm connected to the internet.")
            return StepResult(DONE, {"online": True, "how": "already"})
        device = io.wifi.device()
        if device is None:
            io.say("I'm not connected to the internet, and I can't find Wi-Fi on this computer. "
                   "Please plug in a network cable.")
            if io.wait_until_online(120):
                io.say("Thanks, I'm online now.")
                return StepResult(DONE, {"online": True, "how": "cable"})
            io.say("I'll check the internet again the next time I start.")
            return StepResult(FAILED, {"reason": "no wifi adapter, no cable"})
        if not practice:
            io.say("I'm not connected to the internet yet. Let's set up the Wi-Fi.")
        from systems.wifi import pick_network, spoken_reason
        for attempt in range(3):
            networks = io.wifi.networks()
            if not networks:
                io.say("I can't see any Wi-Fi networks. Check the router is on, or plug in a network cable. "
                       "I'll try again the next time I start.")
                return StepResult(FAILED, {"reason": "no networks"})
            listing = ". ".join(f"{i}: {n.ssid}" for i, n in enumerate(networks, 1))
            chosen = None
            for question in (f"I can see these Wi-Fi networks. {listing}. Which one is yours? Say its number.",
                             "Please say the number of your network, or say none."):
                answer = io.ask(question)
                if answer and _NONE.search(answer):
                    break
                chosen = pick_network(answer, networks)
                if chosen:
                    break
            if chosen is None:
                io.say("Okay. I'll ask about the Wi-Fi again the next time I start.")
                return StepResult(FAILED, {"reason": "no network chosen"})
            if chosen.secured:
                io.say(f"A window will ask for the password of {chosen.ssid}. Type it with the keyboard, "
                       "then press Enter.")
            else:
                io.say(f"Connecting to {chosen.ssid}.")
            ok, message = io.wifi.connect(chosen.ssid)
            if ok and (practice or io.wait_until_online(30)):
                io.say(f"I'm online, through {chosen.ssid}.")
                return StepResult(DONE, {"online": True, "how": "wifi", "ssid": chosen.ssid})
            io.say(spoken_reason(message) + (" Let's try again." if attempt < 2 else ""))
        io.say("I'll try the internet again the next time I start.")
        return StepResult(FAILED, {"reason": "wifi did not connect"})


# --- Downloads ----------------------------------------------------------------------------------------

class DownloadsStep:
    """Decision D: Main first, waited for here (BMO answers "how is it going?" meanwhile); the other
    brains and the simple benchmark carry on in the background (systems/downloads.py)."""
    name, phase = "downloads", "listening"
    options: dict = {}
    GRACE_SECONDS = 30   # a just-started downloader takes a moment to show up

    def run(self, io) -> StepResult:
        from systems import downloads as dl
        state = io.downloads_state()
        main = dl.roster()[0]
        need = dl.still_needed_gb(state, already=io.model_installed)
        if need > 0:
            free = io.free_gb()
            if free < need + dl.HEADROOM_GB:
                io.say(f"My brains need about {need + dl.HEADROOM_GB:.0f} gigabytes of free disk space, and there "
                       f"are only {free:.0f}. Please free some space, then restart me.")
                return StepResult(FAILED, {"need_gb": round(need + dl.HEADROOM_GB), "free_gb": round(free)})
        if state.done(main.alias) or io.model_installed(main.alias):
            io.start_downloads()
            if need > 0:
                io.say("My main brain is already here. I'll download my other brains in the background.")
            return StepResult(DONE, {"main": "already there", "still_needed_gb": round(need)})
        io.say(f"Now I'll download my brains. The main one comes first. It's about {main.approx_gb:.0f} gigabytes, "
               "so it can take a while. Ask me how it's going, any time.")
        io.start_downloads()
        started, restarts = io.now(), 0
        while True:
            state.reload()
            if state.done(main.alias):
                break
            if io.now() - started > self.GRACE_SECONDS and not io.downloader_running():
                restarts += 1
                if restarts > 3:
                    io.say(dl.report(state) + " I'll try again the next time I start.")
                    return StepResult(FAILED, {"reason": "main brain download kept stopping"})
                io.start_downloads()
                started = io.now()
            heard = io.hear()
            if heard and dl.is_progress_question(heard, waiting=True):
                io.say(dl.report(state.reload()))
            elif heard is None:
                io.pause(2)
        io.prepare_main()
        io.say("My main brain is ready, so we can talk now. My other brains keep downloading in the background.")
        return StepResult(DONE, {"main": "downloaded"})


# --- Runner -------------------------------------------------------------------------------------------

STEPS = {step.name: step for step in (MicrophoneStep(), VoiceStep(), ScreenStep(), InternetStep(),
                                      DownloadsStep())}
GREETING = "Hi! I'm BMO. Let's get me set up. It only takes a few minutes."
FINISHED = "That's the setup done."


class FirstBoot:
    def __init__(self, state: FirstBootState, io, steps: dict | None = None, log=None):
        self.state, self.io, self.steps = state, io, steps or STEPS
        self.log = log or (lambda *args: None)

    def run(self, phase: str) -> bool:
        """Run this phase's waiting steps in order. False when a step failed (the rest waits)."""
        if not self.state.active():
            return True
        for entry in self.state.steps():
            if entry["status"] in (DONE, SKIPPED):
                continue
            step = self.steps.get(entry["name"])
            if step is None:
                self.log("First boot: step %s isn't built yet; skipped", entry["name"])
                self.state.mark(entry["name"], SKIPPED, {"reason": "not built yet"})
                continue
            if step.phase != phase:
                return True  # a later phase; it runs when BMO gets there
            if not self.state.started():
                self.io.say(GREETING)
            step.options = (self.state.data.get("options") or {}).get(step.name, {})
            outcome = step.run(self.io)
            self.state.mark(step.name, outcome.status, outcome.result)
            self.log("First boot: %s %s %s", step.name, outcome.status, outcome.result)
            if outcome.status != DONE:
                return False
        if not self.state.active():
            self.io.say(FINISHED)
        return True


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

    def online(self) -> bool:
        from systems.wifi import online
        return online()

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

    def hear(self):
        """One listen (BMO's usual timeout); the words without a leading "Hey BMO", or None."""
        source = getattr(self.chat, "_active_microphone", None)
        if source is None:
            return None
        heard = self.chat.listen(source)
        if not heard:
            return None
        return self.chat.voice_gate.clipped_wake(heard)[1]

    def prepare_main(self) -> None:
        """The main brain just arrived: load and warm it, as a normal start would have."""
        self.chat._supervisor_switch_model(self.chat.active_model, preload=True)
        try:
            self.chat._warm_runtime_prompt()
        except Exception:
            pass

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

    def ask(self, question: str):
        self.say(question)
        source = getattr(self.chat, "_active_microphone", None)
        if source is None:
            return None
        heard = self.chat.listen(source)
        if not heard:
            return None
        _woke, rest = self.chat.voice_gate.clipped_wake(heard)
        return rest

    def calibrate_screen(self) -> None:
        self.chat._calibration_session("calibrate the screen")
