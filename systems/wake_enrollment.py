"""Learning each person's "Hey BMO" (2026-10-20), like Siri's setup.

At first boot the person says "Hey BMO" several times and talks normally for a few seconds. From that,
BMO builds a small personal check for his "Hey BMO" detector (openWakeWord's custom verifier: a logistic
regression on the detector's own audio features) and a threshold that suits this voice. The recordings
are used in memory only and never saved; what is kept is the small model and a few numbers, in
~/.bemo/models/wakeword (personal.json, verifier.pkl), which never leave the computer.

If the detector barely reacts to this voice, there is nothing for a check to build on: then only a lower
threshold is kept (never below MIN_THRESHOLD), and Whisper keeps listening for "Hey BMO" as always.
"""
from __future__ import annotations

import json
import os
import pickle
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np

RATE = 16000
FRAME = 1280
MIN_THRESHOLD = 0.3
VERIFIER_THRESHOLD = 0.5     # on the personal check's probability
VERIFIER_GATE = 0.2          # the detector score at which the personal check is asked
MIN_CLIPS_FOR_CHECK = 3      # "Hey BMO"s the detector must recognise before a check is worth training


def personal_dir(config=None) -> Path:
    if config is None:
        import config
    return Path(config.WAKEWORD_MODEL_PATH).parent


@dataclass
class Enrollment:
    ok: bool
    threshold: float
    verifier: bool
    clips: int
    peaks: list
    reason: str = ""


def _model(model_path, melspec, embedding):
    from openwakeword.model import Model
    return Model(wakeword_models=[str(model_path)], inference_framework="onnx",
                 melspec_model_path=str(melspec), embedding_model_path=str(embedding))


def _stream(model, pcm: np.ndarray):
    """Score a clip the way live audio is streamed (half a second of quiet first, like the stream)."""
    model.reset()
    audio = np.concatenate([np.zeros(RATE // 2, np.int16), pcm.astype(np.int16), np.zeros(FRAME * 3, np.int16)])
    for start in range(0, len(audio) - FRAME + 1, FRAME):
        yield float(next(iter(model.predict(audio[start:start + FRAME]).values())))


def peak(model, pcm: np.ndarray) -> float:
    return max(_stream(model, pcm), default=0.0)


def windows(model, pcm: np.ndarray, threshold: float) -> list:
    """The detector's input windows wherever its score reaches `threshold` (openWakeWord's way)."""
    name = next(iter(model.models))
    found = []
    for score in _stream(model, pcm):
        if score >= threshold:
            found.append(model.preprocessor.get_features(model.model_inputs[name]))
    return found


def settings_from(peaks: list[float], base_threshold: float) -> tuple[float, bool]:
    """(threshold, train a check?) from how strongly the detector reacted to each "Hey BMO"."""
    strong = [p for p in peaks if p >= 0.5]
    if len(strong) >= MIN_CLIPS_FOR_CHECK:
        return VERIFIER_THRESHOLD, True
    heard = sorted(p for p in peaks if p >= MIN_THRESHOLD)
    if len(heard) >= MIN_CLIPS_FOR_CHECK:
        # The weaker of the recognised ones, with a margin: this voice scores lower than the voices
        # the detector learnt from.
        return round(max(MIN_THRESHOLD, min(base_threshold, 0.8 * heard[len(heard) // 3])), 2), False
    return base_threshold, False


def enroll(positives: list[np.ndarray], negatives: list[np.ndarray], *, model_path, melspec, embedding,
           base_threshold: float, out_dir: Path) -> Enrollment:
    """Build and save this person's settings. Nothing heard is written to disk."""
    model = _model(model_path, melspec, embedding)
    peaks = [round(peak(model, clip), 3) for clip in positives]
    threshold, train_check = settings_from(peaks, base_threshold)
    out_dir.mkdir(parents=True, exist_ok=True)
    verifier_path = out_dir / "verifier.pkl"
    verifier = False
    if train_check:
        from openwakeword.custom_verifier_model import train_verifier_model
        pos = [w for clip in positives for w in windows(model, clip, 0.5)]
        neg = [w for clip in negatives for w in windows(model, clip, 0.0)]
        if len(pos) >= MIN_CLIPS_FOR_CHECK and len(neg) >= 10:
            features = np.vstack(pos + neg)
            labels = np.array([1] * len(pos) + [0] * len(neg))
            check = train_verifier_model(features, labels)
            tmp = verifier_path.with_suffix(".tmp")
            with open(tmp, "wb") as handle:
                pickle.dump(check, handle)
            os.replace(tmp, verifier_path)
            verifier = True
        else:
            threshold = base_threshold
    if not verifier and verifier_path.exists():
        verifier_path.unlink()  # an older person's check must not judge this voice
    ok = verifier or threshold < base_threshold or sum(p >= base_threshold for p in peaks) >= MIN_CLIPS_FOR_CHECK
    record = {"enrolled_at": time.time(), "threshold": threshold, "verifier": "verifier.pkl" if verifier else None,
              "verifier_gate": VERIFIER_GATE, "clips": len(positives), "peaks": peaks, "ok": bool(ok)}
    tmp = out_dir / "personal.json.tmp"
    tmp.write_text(json.dumps(record, indent=2) + "\n")
    os.replace(tmp, out_dir / "personal.json")
    reason = "" if ok else "the detector hardly reacted to this voice"
    return Enrollment(bool(ok), threshold, verifier, len(positives), peaks, reason)


def load_personal(directory: Path) -> dict:
    try:
        data = json.loads((Path(directory) / "personal.json").read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}
