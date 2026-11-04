"""BMO's own "Hey BMO" detector: an openWakeWord model trained on this PC (tools/wakeword/make_hey_bmo.py).

Whisper has to *spell* an invented name, and spelled it "B-mount", "Bima", "Pidmino", "Hit Bemo" … on
2026-10-19. The detector listens for the sound instead. It is used beside Whisper, never instead of it:
BMO wakes if either hears "Hey BMO". The detector also says where the phrase ends, so the command can be
transcribed on its own, without a garbled name in front.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path

import numpy as np

RATE = 16000
FRAME = 1280  # 80 ms, openWakeWord's step


@dataclass(frozen=True)
class WakeHit:
    score: float
    end_sample: int  # about where the wake phrase ends: the first frame over the threshold (else the best)


class WakeWordDetector:
    def __init__(self, model_path, melspec_path, embedding_path, threshold: float, verifier_path=None,
                 verifier_gate: float = 0.2):
        self.paths = (Path(model_path), Path(melspec_path), Path(embedding_path))
        self.threshold = float(threshold)
        # This person's check from voice enrollment (systems/wake_enrollment.py, installer plan I7): asked
        # whenever the detector scores at least verifier_gate; its probability then becomes the score.
        self.verifier_path = Path(verifier_path) if verifier_path else None
        self.verifier_gate = float(verifier_gate)
        self._model = None
        self._lock = threading.Lock()

    def available(self) -> bool:
        if not all(path.is_file() for path in self.paths):
            return False
        try:
            import openwakeword  # noqa: F401
        except ImportError:
            return False
        return True

    def _load(self):
        if self._model is None:
            from openwakeword.model import Model
            model, melspec, embedding = (str(p) for p in self.paths)
            extra = {}
            if self.verifier_path and self.verifier_path.is_file():
                extra = {"custom_verifier_models": {self.paths[0].stem: str(self.verifier_path)},
                         "custom_verifier_threshold": self.verifier_gate}
            self._model = Model(wakeword_models=[model], inference_framework="onnx",
                                melspec_model_path=melspec, embedding_model_path=embedding, **extra)
        return self._model

    def check(self, pcm16: bytes | np.ndarray) -> WakeHit:
        """Score one captured phrase (16 kHz mono int16) the way live audio is streamed."""
        samples = np.frombuffer(pcm16, dtype="<i2") if isinstance(pcm16, (bytes, bytearray)) else np.asarray(pcm16)
        lead = np.zeros(RATE // 2, dtype=np.int16)  # half a second of quiet, like the stream before speech
        audio = np.concatenate([lead, samples.astype(np.int16), np.zeros(FRAME * 3, dtype=np.int16)])
        best, best_end, first = 0.0, 0, None
        with self._lock:
            model = self._load()
            model.reset()
            for start in range(0, len(audio) - FRAME + 1, FRAME):
                score = float(next(iter(model.predict(audio[start:start + FRAME]).values())))
                if score >= self.threshold and first is None:
                    # The first crossing matches the end of the name to ~0.2 s; the peak comes later and
                    # cut into the command ("Twitch to me" for "switch to main", bench 2026-10-19).
                    first = start + FRAME - len(lead)
                if score > best:
                    best, best_end = score, start + FRAME - len(lead)
        end = first if first is not None else best_end
        return WakeHit(best, max(0, min(len(samples), end)))

    def heard(self, pcm16) -> WakeHit | None:
        hit = self.check(pcm16)
        return hit if hit.score >= self.threshold else None


class LiveWake:
    """Listens for "Hey BMO" on the live microphone while BMO captures a phrase and calls on_wake once: the ear on
    his face (2026-10-31: the ear means he's been woken). Its own copy of the detector's model, with the
    owner's voice check when there is one, so it never disturbs the detector's own checks."""

    def __init__(self, source: WakeWordDetector, on_wake):
        self.source = source
        self._twin = WakeWordDetector(*source.paths, source.threshold, verifier_path=source.verifier_path,
                                      verifier_gate=source.verifier_gate)
        self.threshold = source.threshold
        self.on_wake = on_wake
        self.fired = False
        self._buffer = np.zeros(0, dtype=np.int16)

    def reset(self) -> None:
        self.fired = False
        self._buffer = np.zeros(0, dtype=np.int16)
        if self._twin._model is not None:
            self._twin._model.reset()

    def feed(self, raw: bytes, rate: int, width: int) -> None:
        if self.fired or width != 2:
            return
        samples = np.frombuffer(raw, dtype="<i2")
        if rate != RATE:
            from scipy.signal import resample_poly
            samples = resample_poly(samples.astype(np.float32), RATE, rate).astype(np.int16)
        self._buffer = np.concatenate([self._buffer, samples])
        model = self._twin._load()
        while len(self._buffer) >= FRAME and not self.fired:
            frame, self._buffer = self._buffer[:FRAME], self._buffer[FRAME:]
            if float(next(iter(model.predict(frame).values()))) >= self.threshold:
                self.fired = True
                self.on_wake()


_DETECTOR = None
_PERSONAL_STAMP = None


def _personal_stamp(directory: Path):
    try:
        return (directory / "personal.json").stat().st_mtime
    except OSError:
        return None


def detector(config) -> WakeWordDetector | None:
    """The configured detector, or None when it is off or its files aren't there. A new voice enrollment
    (personal.json changed) is picked up at the next call, also by the standby listener."""
    global _DETECTOR, _PERSONAL_STAMP
    if not getattr(config, "ENABLE_WAKEWORD_MODEL", False):
        return None
    directory = Path(config.WAKEWORD_MODEL_PATH).parent
    stamp = _personal_stamp(directory)
    if _DETECTOR is None or stamp != _PERSONAL_STAMP:
        from systems.wake_enrollment import load_personal
        personal = load_personal(directory)
        verifier = directory / personal["verifier"] if personal.get("verifier") else None
        candidate = WakeWordDetector(config.WAKEWORD_MODEL_PATH, config.WAKEWORD_MELSPEC_PATH,
                                     config.WAKEWORD_EMBEDDING_PATH,
                                     float(personal.get("threshold") or config.WAKEWORD_THRESHOLD),
                                     verifier_path=verifier, verifier_gate=float(personal.get("verifier_gate", 0.2)))
        _DETECTOR = candidate if candidate.available() else False
        _PERSONAL_STAMP = stamp
    return _DETECTOR or None
