"""A stopwatch for every voice turn (2026-10-31): when each step happened, one JSON line per turn in
~/.bemo/logs/turn-trace.jsonl. Times, counts and names only, never the words said or answered.

Marks, in seconds from the end of speech: speech_start (negative), speech_end (0), stt_done, accepted, ollama_sent,
ollama_first_event, first_sentence, first_audio, ready_again. Notes: the turn's kind (local command or brain), model,
context, threads, whether a warm-up was still running, Ollama's own load / prefill / decode numbers, each tool call's
time, and how long BMO waited for a yes or no (that's the owner's time, not the machine's).
tools/summarize_turn_trace.py prints the table.
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

MAX_BYTES = 5 * 1024 * 1024  # then the file starts over, keeping the last one as .1


class NoTrace:
    """Stands in when there's no stopwatch (tests, or switched off)."""
    def __getattr__(self, name):
        return lambda *args, **kwargs: None


class TurnTrace:
    def __init__(self, path, clock=time.monotonic, wall=time.time):
        self.path = Path(path)
        self._clock, self._wall = clock, wall
        self._lock = threading.Lock()
        self._pending: dict = {}
        self._turn: dict | None = None
        self._count = 0

    # --- while BMO listens: marks that become a turn's if the phrase was for him -----------------------------
    def listen_started(self) -> None:
        with self._lock:
            self._pending = {}

    def heard(self, name: str) -> None:
        with self._lock:
            self._pending.setdefault(name, self._clock())

    # --- a turn ----------------------------------------------------------------------------------------------
    def begin(self, **notes) -> None:
        with self._lock:
            self._count += 1
            marks = dict(self._pending)
            marks["accepted"] = self._clock()
            self._turn = {"turn": self._count, "at": round(self._wall(), 1), "marks": marks, "notes": dict(notes),
                          "tools": [], "approval_s": 0.0}

    def mark(self, name: str) -> None:
        with self._lock:
            if self._turn is not None:
                self._turn["marks"].setdefault(name, self._clock())

    def note(self, **values) -> None:
        with self._lock:
            if self._turn is not None:
                self._turn["notes"].update(values)

    def tool(self, name: str, seconds: float) -> None:
        with self._lock:
            if self._turn is not None:
                self._turn["tools"].append({"name": str(name), "s": round(float(seconds), 3)})

    def approval(self, seconds: float) -> None:
        with self._lock:
            if self._turn is not None:
                self._turn["approval_s"] = round(self._turn["approval_s"] + float(seconds), 3)

    def finish(self) -> dict | None:
        """The turn is over (BMO listens again): its line is written. None when there was no turn."""
        with self._lock:
            turn, self._turn = self._turn, None
            if turn is None:
                return None
            marks = turn["marks"]
            marks.setdefault("ready_again", self._clock())
            base = marks.get("speech_end", marks["accepted"])
            turn["marks"] = {name: round(at - base, 3) for name, at in sorted(marks.items(), key=lambda kv: kv[1])}
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            if self.path.exists() and self.path.stat().st_size > MAX_BYTES:
                self.path.replace(self.path.with_suffix(self.path.suffix + ".1"))
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(turn, sort_keys=True) + "\n")
        except OSError:
            pass
        return turn
