#!/usr/bin/env python3
"""Lightweight always-on wake listener used while the full Bemo UI is stopped.

Only the canonical wake phrase is accepted here.  The full conversational stack,
large Ollama model, TTS, renderer, project state, and normal Whisper model are not
loaded until the wake phrase is heard.
"""
from __future__ import annotations

import gc
import logging
import re
import threading
import time
from typing import Callable

import config

LOG = logging.getLogger(__name__)


_STANDBY_HEY = re.compile(r"^\W*(?:hey|hay|he'd)\b", re.IGNORECASE)
# The standby model sometimes drops or mangles the "Hey" (live 2026-10-19: "8 Bemo", "A Bemo").
# A filler sound right before the name counts, in a short phrase.
_STANDBY_FILLER = re.compile(r"^\W*(?:a|uh|ah|eh|oh|um|8|hey|hay|he'd)\W+(?:bemo|beemo|bmo|be mo)\b", re.IGNORECASE)
# Spellings of the name in the owner's recorded "Hey BMO"s (2026-10-19 sample, tiny.en and base.en):
# "Bima", "Beema", "Beemow", "B-M-O", "b-mo". "Hey <name>" may also come after a first try
# ("Bemo Hey Bemo"), in a short phrase.
_NAME = r"(?:bemo|beemo|bmo|be mo|bima|beema|beemow|beemo|b mo|bee mo)"
# Only the name or a filler may come before it: "random Hey Bemo in the middle" is not a wake.
_HEY_NAME_ANYWHERE = re.compile(rf"^\W*(?:(?:{_NAME}|a|uh|um|oh|hey|hay)\W+){{0,3}}(?:hey|hay|a|8)\W+{_NAME}\b",
                                re.IGNORECASE)


def _spelled_name(text: str) -> str:
    """'B-M-O' / 'B.M.O.' / 'b-mo' -> 'bmo' / 'b mo' so the patterns above see one word."""
    text = re.sub(r"\b[Bb]\W?[Mm]\W?[Oo]\b\.?", "BMO", text)
    return re.sub(r"\b([Bb])-([Mm]o)\b", r"\1 \2", text)


def is_standby_wake_phrase(text: str) -> bool:
    """Return True when speech begins with a robust Hey-Bemo wake variant."""
    from voice_commands import VoiceSessionGate

    cleaned = _spelled_name(" ".join(str(text or "").strip().split()))
    if len(cleaned.split()) <= 6 and _HEY_NAME_ANYWHERE.match(cleaned):
        return True
    # Starting BMO from standby needs a "Hey …" form (decided 2026-10-19): the bare name
    # "Bemo" heard in 2.4 s of room speech started the whole BMO unasked. Once he is running, the
    # bare name still works (VoiceSessionGate in chat).
    if _STANDBY_FILLER.match(cleaned) and len(cleaned.split()) <= 6:
        return True
    if not _STANDBY_HEY.match(cleaned):
        return False
    gate = VoiceSessionGate(wake_aliases=config.VOICE_WAKE_ALIASES, timeout_seconds=1.0)
    return gate.has_wake_prefix(cleaned)


class StandbyWakeListener:
    """Listen for Hey Bemo while the full Bemo process is not running.

    The listener uses config.STANDBY_WAKE_STT_MODEL (base.en since 2026-10-19,
    measured on the owner's recording; tiny.en missed most real wakes).  It releases both microphone and model before asking
    the Supervisor to launch full Bemo, preventing audio-device contention and
    keeping standby RAM/CPU use small relative to the normal stack.
    """

    def __init__(
        self,
        *,
        should_listen: Callable[[], bool],
        on_wake: Callable[[], None],
        on_status: Callable[[bool, str], None] | None = None,
    ):
        self.should_listen = should_listen
        self.on_wake = on_wake
        self.on_status = on_status or (lambda ready, error: None)
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None

    def start(self) -> None:
        if self.thread and self.thread.is_alive():
            return
        self.stop_event.clear()
        self.thread = threading.Thread(target=self._run, name="bemo-standby-wake", daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=3.0)
        self.thread = None
        self.on_status(False, "")

    @staticmethod
    def _detector_heard(audio, text) -> bool:
        """BMO's own "Hey BMO" detector, beside Whisper (chosen 2026-10-19).

        Only for speech: if Whisper heard no words at all, it was a noise. Live 2026-10-20, the public
        detector woke BMO at 0.83 on a sound with no words; every real detector wake had some (6, 2)."""
        if not str(text or "").split():
            return False
        from systems.wakeword import detector
        found = detector(config)
        if found is None:
            return False
        try:
            scored = found.check(audio.get_raw_data(convert_rate=16000, convert_width=2))
        except Exception:
            LOG.exception("Wake-word detector failed; using Whisper only")
            return False
        hit = scored if scored.score >= found.threshold else None
        if hit is None and scored.score >= 0.15:
            # Near misses, as numbers only (live 2026-10-20: "Hey BMO" not heard with a video playing).
            LOG.info("Standby detector near miss: score %.2f (needs %.2f)", scored.score, found.threshold)
        if hit is not None:
            LOG.info("Standby wake by the detector (score %.2f); Whisper heard %d words", hit.score,
                     len(str(text or "").split()))
        return hit is not None

    def _wake_decision(self, text: str, now: float) -> bool:  # noqa: ARG002 (kept for replay tests)
        # The bare name, once or repeated, does NOT start BMO (removed 2026-10-19): standby transcribed
        # background sound as "Bemo ... Bemo" and started him three times in 15 minutes.
        return is_standby_wake_phrase(text)

    def _make_recognizer(self):
        import speech_recognition as sr
        rec = sr.Recognizer()
        rec.pause_threshold = float(config.STANDBY_WAKE_PAUSE_THRESHOLD)
        rec.energy_threshold = float(config.STANDBY_WAKE_ENERGY_THRESHOLD)
        rec.dynamic_energy_threshold = bool(config.STANDBY_WAKE_DYNAMIC_ENERGY_THRESHOLD)
        return rec

    @staticmethod
    def _audio_to_float32(audio):
        import numpy as np
        raw = audio.get_raw_data(convert_rate=int(config.STT_SAMPLE_RATE), convert_width=2)
        if not raw:
            return np.empty(0, dtype=np.float32)
        return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0

    def _transcribe(self, model, audio) -> str:
        samples = self._audio_to_float32(audio)
        if samples.size == 0:
            return ""
        kwargs = dict(
            language=config.STT_LANGUAGE,
            beam_size=max(1, int(getattr(config, "STT_BEAM_SIZE", 1))),
            temperature=0.0,
            initial_prompt=getattr(config, "STT_INITIAL_PROMPT", None),
            vad_filter=bool(getattr(config, "STANDBY_WAKE_VAD_FILTER", False)),
            vad_parameters=(
                {"min_silence_duration_ms": int(config.STANDBY_WAKE_MIN_SILENCE_MS)}
                if bool(getattr(config, "STANDBY_WAKE_VAD_FILTER", False))
                else None
            ),
            condition_on_previous_text=False,
        )
        # Standby's own hint (config.STANDBY_WAKE_HOTWORDS): with base.en it lifts the owner's "Hey BMO"
        # from 7/10 to 9/10 and hears no room sound as "Bemo"; with tiny.en it heard 4 (2026-10-19).
        hotwords = str(getattr(config, "STANDBY_WAKE_HOTWORDS", "") or "").strip()
        if hotwords:
            kwargs["hotwords"] = hotwords
        try:
            segments, _ = model.transcribe(samples, **kwargs)
        except TypeError as exc:
            if "hotwords" not in str(exc):
                raise
            kwargs.pop("hotwords", None)
            segments, _ = model.transcribe(samples, **kwargs)
        return " ".join(segment.text.strip() for segment in segments if segment.text.strip()).strip()

    def _run(self) -> None:
        model = None
        while not self.stop_event.is_set():
            if not self.should_listen():
                self.on_status(False, "")
                self.stop_event.wait(0.25)
                continue

            try:
                if model is None:
                    from systems.whisper_local import load_whisper

                    model = load_whisper(
                        config.STANDBY_WAKE_STT_MODEL,
                        device="cpu",
                        compute_type=config.STANDBY_WAKE_STT_COMPUTE_TYPE,
                        cpu_threads=int(config.STANDBY_WAKE_STT_CPU_THREADS),
                        num_workers=1,
                    )

                import speech_recognition as sr
                rec = self._make_recognizer()
                triggered = False
                with sr.Microphone(device_index=config.DEVICE_INDEX) as source:
                    if float(config.STANDBY_WAKE_AMBIENT_CALIBRATION_SECONDS) > 0:
                        rec.adjust_for_ambient_noise(
                            source,
                            duration=float(config.STANDBY_WAKE_AMBIENT_CALIBRATION_SECONDS),
                        )
                    self.on_status(True, "")

                    while (
                        not self.stop_event.is_set()
                        and self.should_listen()
                    ):
                        try:
                            audio = rec.listen(
                                source,
                                timeout=float(config.STANDBY_WAKE_LISTEN_TIMEOUT),
                                phrase_time_limit=float(config.STANDBY_WAKE_PHRASE_TIME_LIMIT),
                            )
                        except sr.WaitTimeoutError:
                            continue

                        try:
                            text = self._transcribe(model, audio)
                            if text and len(text.split()) <= 5 and (
                                    re.search(r"\b(?:bemo|beemo|bmo|bima|beema|be mo|b mo)\b", text, re.I)
                                    or self._wake_decision(text, time.monotonic())):
                                LOG.info("Standby wake transcript: %r", text)
                            elif text:
                                # Room speech is not BMO's to keep (2026-10-19: phone calls ended up in
                                # the logs word for word). Only its size is logged.
                                LOG.info("Standby heard %d words (not a wake)", len(text.split()))
                        except Exception as exc:
                            self.on_status(True, f"standby transcription error: {type(exc).__name__}: {exc}")
                            continue

                        if self._wake_decision(text, time.monotonic()) or self._detector_heard(audio, text):
                            triggered = True
                            self.on_status(False, "")
                            break

                if triggered:
                    # Release the tiny model before full Bemo starts so the
                    # normal base.en STT model gets the cleanest possible handoff.
                    del model
                    model = None
                    gc.collect()
                    self.on_wake()
                    self.stop_event.wait(0.35)

            except Exception as exc:
                self.on_status(False, f"{type(exc).__name__}: {exc}")
                if model is not None:
                    del model
                    model = None
                    gc.collect()
                self.stop_event.wait(float(config.STANDBY_WAKE_RETRY_SECONDS))

        if model is not None:
            del model
            gc.collect()
        self.on_status(False, "")
