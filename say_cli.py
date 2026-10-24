#!/usr/bin/env python3
"""Say one line in BMO's voice, after a two-note chime: timers and reminders that come due while BMO sleeps.

  .venv/bin/python say_cli.py "Ding ding! Your 10 minute timer is done."

Started by the Supervisor (systems/reminders.py); it loads the Piper voice, speaks, and exits.
"""
from __future__ import annotations

import sys

import numpy as np

import config


def chime(rate: int) -> np.ndarray:
    """Two soft notes, like a kitchen timer's ding-ding."""
    notes = []
    for freq in (880.0, 1174.7):
        t = np.arange(int(rate * 0.22)) / rate
        tone = 0.25 * np.sin(2 * np.pi * freq * t) * np.exp(-6 * t)
        notes += [tone, np.zeros(int(rate * 0.06))]
    return np.concatenate(notes).astype(np.float32)


def main(argv=None) -> int:
    text = " ".join(argv if argv is not None else sys.argv[1:]).strip()
    if not text:
        return 2
    import pyaudio
    from systems.piper_streaming import PiperSpeechEngine
    engine = PiperSpeechEngine(config.PIPER_MODEL_PATH)
    audio = [chime(engine.sample_rate)] + [chunk.audio for chunk in engine.stream(text)]
    pcm = np.clip(np.concatenate(audio), -1.0, 1.0)
    pa = pyaudio.PyAudio()
    try:
        stream = pa.open(format=pyaudio.paFloat32, channels=1, rate=engine.sample_rate, output=True)
        stream.write(pcm.tobytes())
        stream.stop_stream()
        stream.close()
    finally:
        pa.terminate()
    return 0


if __name__ == "__main__":
    sys.exit(main())
