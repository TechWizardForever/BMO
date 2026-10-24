"""Voice-activity endpointing for Bemo's microphone (local Silero VAD).

SpeechRecognition ends a phrase when loudness drops below a fixed energy
threshold. On the test host the room's background noise measured ~1,500
against a threshold capped at 420, so recordings ran until the noise happened
to dip: 10-30 s for a 3 s sentence (live tests, 2026-10-18). Silero VAD, which
ships offline with faster-whisper, scored that same noise at 0.05-0.07 speech
probability, so phrases here start and end on *voice*, not volume.

All timing uses audio time (samples read), so captures are deterministic and
testable with recorded audio.
"""
from __future__ import annotations

from collections import deque

import numpy as np
import speech_recognition as sr

_VAD_RATE = 16000
_VAD_WINDOW = 512  # Silero processes 32 ms windows at 16 kHz


class VoiceEndpointer:
    def __init__(
        self,
        vad_model,
        *,
        start_threshold: float = 0.5,
        end_threshold: float = 0.35,
        strong_start_threshold: float = 0.4,
        start_min_seconds: float = 0.12,
        end_silence_seconds: float = 1.0,
        preroll_seconds: float = 0.5,
        start_timeout_seconds: float = 1.0,
        max_phrase_seconds: float = 30.0,
        context_seconds: float = 0.8,
        step_seconds: float = 0.07,
    ):
        self.vad = vad_model
        self.start_threshold = float(start_threshold)
        # One step this sure is a voice even if very short ("nah" scored 0.46
        # for a single step and was missed; noise has peaked at 0.14).
        self.strong_start_threshold = max(float(strong_start_threshold), float(start_threshold))
        self.end_threshold = float(end_threshold)
        self.start_min_seconds = float(start_min_seconds)
        self.end_silence_seconds = float(end_silence_seconds)
        self.preroll_seconds = float(preroll_seconds)
        self.start_timeout_seconds = float(start_timeout_seconds)
        self.max_phrase_seconds = float(max_phrase_seconds)
        self.context_seconds = float(context_seconds)
        self.step_seconds = float(step_seconds)
        self.last_stats: dict = {}
        # Context carried across the 1 s idle checks while the stream stays
        # open: a cold start (little history) made Silero score plain noise
        # up to 0.22. No start decision is made with less than this context.
        self.min_context_seconds = 0.3
        self._recent = None
        self._preroll = None

    def _voice_probability(self, raw: bytes, rate: int, width: int) -> float:
        """Speech probability of the newest ~100 ms, judged with recent context."""
        pcm = sr.AudioData(raw, rate, width).get_raw_data(convert_rate=_VAD_RATE, convert_width=2)
        samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        usable = len(samples) // _VAD_WINDOW * _VAD_WINDOW
        if usable < _VAD_WINDOW:
            return 0.0
        probs = np.asarray(self.vad(samples[-usable:])).reshape(-1)
        return float(probs[-3:].max())

    on_audio = None  # callable(raw_bytes, rate, width) or None

    def reset(self):
        """Forget audio context; call when the microphone was paused."""
        self._recent = None
        self._preroll = None

    def capture(self, source):
        """Return AudioData for one spoken phrase, or None if nobody spoke in time."""
        rate, width, chunk = int(source.SAMPLE_RATE), int(source.SAMPLE_WIDTH), int(source.CHUNK)
        chunk_seconds = chunk / rate
        chunks_per_step = max(1, round(self.step_seconds / chunk_seconds))
        context_chunks = max(1, round(self.context_seconds / chunk_seconds))
        if self._recent is None or self._recent.maxlen != context_chunks:
            self._recent = deque(maxlen=context_chunks)
            self._preroll = deque(maxlen=max(1, round(self.preroll_seconds / chunk_seconds)))
        preroll, recent = self._preroll, self._recent
        frames: list[bytes] = []
        elapsed = 0.0
        voiced_run = 0.0
        started_at = None
        last_voice = 0.0
        peak = 0.0
        end_reason = "max length"
        while True:
            step = []
            for _ in range(chunks_per_step):
                data = source.stream.read(chunk)
                if not data:
                    break
                step.append(data)
            if not step:
                end_reason = "stream ended"
                break
            for data in step:
                recent.append(data)
                (frames if started_at is not None else preroll).append(data)
            if self.on_audio is not None:
                # Live audio for whoever needs it while a phrase is captured (the movie ducker, 2026-10-20).
                try:
                    self.on_audio(b"".join(step), rate, width)
                except Exception:
                    self.on_audio = None
            elapsed += len(step) * chunk_seconds
            if started_at is None and len(recent) * chunk_seconds < self.min_context_seconds:
                continue
            prob = self._voice_probability(b"".join(recent), rate, width)
            peak = max(peak, prob)

            if started_at is None:
                voiced_run = voiced_run + len(step) * chunk_seconds if prob >= self.start_threshold else 0.0
                if voiced_run >= self.start_min_seconds or prob >= self.strong_start_threshold:
                    started_at = elapsed
                    last_voice = elapsed
                    frames = list(preroll)
                elif elapsed >= self.start_timeout_seconds and voiced_run == 0.0:
                    # Never time out while a voice is just starting.
                    self.last_stats = {"started": False, "peak_voice_prob": round(peak, 2)}
                    return None
                continue

            if prob >= self.end_threshold:
                last_voice = elapsed
            if elapsed - last_voice >= self.end_silence_seconds:
                end_reason = "voice ended"
                break
            if elapsed - started_at >= self.max_phrase_seconds:
                break

        self.last_stats = {
            "started": started_at is not None,
            "phrase_seconds": round(elapsed - (started_at or elapsed), 2),
            "end_reason": end_reason,
            "peak_voice_prob": round(peak, 2),
        }
        if started_at is None:
            return None
        self.reset()  # the microphone pauses now; old context would be stale
        return sr.AudioData(b"".join(frames), rate, width)
