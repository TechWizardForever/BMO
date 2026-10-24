"""Sound settings by voice (settings plan S1): volume, mute, and which output plays sound.

Deterministic, no model. Linux backend: pactl (PulseAudio, or PipeWire's
PulseAudio interface). Every change is read back before BMO reports it.
Microphone levels are left alone on purpose: turning BMO's own microphone
down would stop him hearing the owner.
"""
from __future__ import annotations

import difflib
import json
import re
import shutil
import subprocess
from dataclasses import dataclass

STEP = 10          # "volume up" / "down"
SMALL_STEP = 5     # "a little louder"
BIG_STEP = 20      # "a lot louder"
ASK_ABOVE = 85     # approved: going above this asks first
ASK_JUMP = 30      # approved: a jump bigger than this asks first


@dataclass(frozen=True)
class Output:
    name: str
    description: str
    volume: int
    muted: bool
    default: bool
    port: str = ""   # e.g. analog-output-headphones / analog-output-speaker / hdmi-output-0

    @property
    def spoken(self) -> str:
        """'Scarlett Solo (3rd Gen.) Analog Stereo' -> 'Scarlett Solo'; HDMI -> 'the HDMI output (TV or monitor)'."""
        desc = self.description
        if "hdmi" in desc.lower() or "displayport" in desc.lower():
            return "the HDMI output (TV or monitor)"
        desc = re.sub(r"\s*\([^)]*\)", "", desc)
        desc = re.sub(r"\b(?:analog|digital|stereo|mono|surround [\d.]+|output|audio|controller)\b", "", desc, flags=re.I)
        return re.sub(r"\s+", " ", desc).strip() or self.description

    def matches(self, spoken: str) -> float:
        words = set(re.findall(r"[a-z0-9]+", spoken.lower())) - {"the", "my", "a", "for", "sound", "audio", "output"}
        if not words:
            return 0.0
        desc = self.description.lower()
        aliases = set(re.findall(r"[a-z0-9]+", desc))
        port = self.port.lower()
        if "hdmi" in desc or "displayport" in desc or "hdmi" in port:
            aliases |= {"tv", "television", "hdmi", "monitor", "screen", "crt", "displayport"}
        elif "headphone" in port or "headset" in port:  # the port says what it is: no guessing
            aliases |= {"headphones", "headphone", "headset"}
        elif "speaker" in port or "lineout" in port:
            aliases |= {"speakers", "speaker"}
        else:  # USB interfaces etc. report a plain "analog-output": either word fits
            aliases |= {"speakers", "speaker", "headphones", "headphone", "headset", "analog"}
        # Close spellings count: Whisper wrote "Scarlet" for Scarlett (live 2026-10-18).
        hits = sum(1 for w in words if w in aliases or any(
            len(w) >= 4 and difflib.SequenceMatcher(None, w, a).ratio() >= 0.8 for a in aliases))
        return hits / len(words)


class PactlSound:
    """Volume, mute and output through pactl."""

    def __init__(self, runner=subprocess.run):
        self._run = runner

    @staticmethod
    def available() -> bool:
        return bool(shutil.which("pactl"))

    def _pactl(self, *args: str) -> str:
        done = self._run(["pactl", *args], capture_output=True, text=True, timeout=5, check=False)
        if done.returncode != 0:
            raise RuntimeError((done.stderr or done.stdout or "pactl failed").strip()[:200])
        return done.stdout

    def outputs(self) -> list[Output]:
        default = self._pactl("get-default-sink").strip()
        found = []
        for sink in json.loads(self._pactl("-f", "json", "list", "sinks") or "[]"):
            levels = [int(str(ch.get("value_percent", "0")).rstrip("%")) for ch in (sink.get("volume") or {}).values()]
            found.append(Output(sink["name"], sink.get("description") or sink["name"],
                                round(sum(levels) / len(levels)) if levels else 0, bool(sink.get("mute")),
                                sink["name"] == default, str(sink.get("active_port") or "")))
        return found

    def inputs(self) -> list[Output]:
        """Microphones (sources), leaving out the 'Monitor of …' loopbacks of each output."""
        default = self._pactl("get-default-source").strip()
        found = []
        for src in json.loads(self._pactl("-f", "json", "list", "sources") or "[]"):
            desc = src.get("description") or src["name"]
            if src["name"].endswith(".monitor") or desc.lower().startswith("monitor of"):
                continue
            found.append(Output(src["name"], desc, 0, bool(src.get("mute")), src["name"] == default,
                                str(src.get("active_port") or "")))
        return found

    def current(self) -> Output | None:
        return next((o for o in self.outputs() if o.default), None)

    def use_input(self, name: str) -> None:
        """Make this microphone the default; BMO records from the default (first boot, installer plan I2)."""
        self._pactl("set-default-source", name)

    def set_volume(self, percent: int) -> Output | None:
        self._pactl("set-sink-volume", "@DEFAULT_SINK@", f"{max(0, min(150, int(percent)))}%")
        return self.current()

    def set_mute(self, muted: bool) -> Output | None:
        self._pactl("set-sink-mute", "@DEFAULT_SINK@", "1" if muted else "0")
        return self.current()

    def use_output(self, output: Output) -> Output | None:
        """Make it the default and move everything that is playing (BMO's voice too)."""
        self._pactl("set-default-sink", output.name)
        for stream in json.loads(self._pactl("-f", "json", "list", "sink-inputs") or "[]"):
            try:
                self._pactl("move-sink-input", str(stream["index"]), output.name)
            except RuntimeError:
                pass  # a stream that ended meanwhile
        return self.current()


_NUM = r"(?P<num>\d{1,3})"
_WORD_LEVELS = {"max": 100, "maximum": 100, "full": 100, "half": 50, "zero": 0, "minimum": 0}
_SOUND = r"(?:the\s+)?(?:volume|sound|audio)"


def parse_sound_command(text: str) -> dict | None:
    """'volume up' -> {'kind': 'change', 'delta': 10}; 'set the volume to 40' -> {'kind': 'set', 'level': 40}."""
    t = re.sub(r"[?!.,]+", " ", str(text or "").lower())
    t = re.sub(r"^(?:(?:can|could|would|will) you |please )+", "", re.sub(r"\s+", " ", t).strip()).strip()
    t = re.sub(r"\s+(?:please|for me|a bit|now)$", "", t)
    size = BIG_STEP if re.search(r"\b(?:a lot|much|way)\b", t) else SMALL_STEP if re.search(r"\b(?:a little|a bit|slightly|a tad)\b", t) else STEP
    by = re.search(r"\bby\s+" + _NUM + r"\b", t)
    if by:
        size = int(by.group("num"))
    up = (rf"^(?:turn\s+(?:it|{_SOUND})\s+up|turn\s+up\s+{_SOUND}|{_SOUND}\s+up|raise\s+{_SOUND}|increase\s+{_SOUND}|"
          r"(?:make it\s+|a little\s+|a lot\s+|much\s+|way\s+|bit\s+)*louder)\b")
    down = (rf"^(?:turn\s+(?:it|{_SOUND})\s+down|turn\s+down\s+{_SOUND}|{_SOUND}\s+down|lower\s+{_SOUND}|"
            rf"decrease\s+{_SOUND}|reduce\s+{_SOUND}|(?:make it\s+|a little\s+|a lot\s+|much\s+|way\s+|bit\s+)*"
            r"(?:quieter|softer))\b")
    if re.match(up, t):
        return {"kind": "change", "delta": size}
    if re.match(down, t):
        return {"kind": "change", "delta": -size}
    level = re.match(rf"^(?:set\s+|change\s+|put\s+)?{_SOUND}\s+(?:to\s+|at\s+)?(?:{_NUM}(?:\s*(?:percent|%))?|"
                     r"(?P<word>max|maximum|full|half|zero|minimum))$", t)
    if level:
        return {"kind": "set", "level": int(level.group("num")) if level.group("num") else _WORD_LEVELS[level.group("word")]}
    if re.match(rf"^(?:mute|silence)(?:\s+(?:{_SOUND}|the speakers|the computer|everything))?$", t):
        return {"kind": "mute"}
    if re.match(rf"^(?:unmute|un-mute)(?:\s+(?:{_SOUND}|the speakers|the computer|everything))?$", t):
        return {"kind": "unmute"}
    if re.match(rf"^(?:what(?:'s| is)\s+{_SOUND}(?:\s+(?:at|level|set to))?|how loud is (?:it|the sound|the volume))$", t):
        return {"kind": "query"}
    if re.match(r"^(?:list|what are)(?:\s+the)?\s+(?:sound|audio)\s+(?:outputs|devices)$|^where is (?:the )?sound (?:going|coming out)$", t):
        return {"kind": "list"}
    # "back" is common when undoing a switch ("switch sound back to the Scarlett", live 2026-10-18).
    target = (re.match(r"^(?:use|switch(?: back)? to|change(?: back)? to|go back to)\s+(?P<target>.+?)\s+for\s+"
                       r"(?:the\s+)?(?:sound|audio)$", t)
              or re.match(r"^(?:switch|change|move|send|play|put)\s+(?:the\s+)?(?:sound|audio)\s+(?:back\s+)?"
                          r"(?:to|through|on|onto|over|out of)\s+(?P<target>.+)$", t)
              or re.match(r"^(?:switch|change|move|put)\s+(?:it\s+)?back\s+to\s+(?P<target>.+?)(?:\s+for\s+(?:the\s+)?"
                          r"(?:sound|audio))?$", t))
    if target:
        # Without "sound"/"audio" ("switch back to the speakers") it is only a sound
        # request if the target names an output; "switch back to coding" is a model switch.
        explicit = bool(re.search(r"\b(?:sound|audio)\b", t))
        return {"kind": "output", "target": target.group("target").strip(), "explicit": explicit}
    return None


def plan_change(current: int, parsed: dict) -> int:
    if parsed["kind"] == "set":
        return max(0, min(100, int(parsed["level"])))
    return max(0, min(100, int(current) + int(parsed["delta"])))


def needs_ok(current: int, target: int) -> bool:
    """approved: big jumps and very loud levels are asked first (a CRT TV can be loud)."""
    return target - current > ASK_JUMP or (target > ASK_ABOVE and target > current)


def host_sound():
    return PactlSound() if PactlSound.available() else None
