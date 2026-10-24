#!/usr/bin/env python3
"""Record a wake-word sample exactly as BMO's standby listener hears it.

Same microphone, same thresholds and the same phrase cutting as standby_wake.py,
so a later comparison of speech models sees what standby really transcribes.
Part 1: you say "Hey BMO" ten times. Part 2: two minutes of normal room sound.
Everything is saved only on this PC, in ~/.bemo/wake-samples/<time>/ (private).
Stop BMO's standby service first, or your "Hey BMO"s will start him:

    systemctl --user stop bemo.service
    .venv/bin/python tools/record_wake_sample.py
    systemctl --user start bemo.service
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

import config  # noqa: E402
import speech_recognition as sr  # noqa: E402
from standby_wake import StandbyWakeListener  # noqa: E402

WAKE_TRIES = 10
ROOM_SECONDS = 120


def save(folder: Path, name: str, audio: sr.AudioData) -> dict:
    path = folder / f"{name}.wav"
    path.write_bytes(audio.get_wav_data(convert_rate=int(config.STT_SAMPLE_RATE), convert_width=2))
    seconds = len(audio.frame_data) / float(audio.sample_rate * audio.sample_width)
    return {"file": path.name, "seconds": round(seconds, 2)}


def main() -> int:
    out = Path(config.DATA_ROOT) / "wake-samples" / datetime.now().strftime("%Y%m%d-%H%M%S")
    (out / "wake").mkdir(parents=True)
    (out / "room").mkdir()
    os.chmod(out.parent, 0o700)
    listener = StandbyWakeListener(should_listen=lambda: False, on_wake=lambda: None)
    rec = listener._make_recognizer()
    labels = {"device_index": config.DEVICE_INDEX, "wake": [], "room": []}
    with sr.Microphone(device_index=config.DEVICE_INDEX) as source:
        print("Measuring the room for half a second, stay quiet...", flush=True)
        rec.adjust_for_ambient_noise(source, duration=float(config.STANDBY_WAKE_AMBIENT_CALIBRATION_SECONDS))

        print(f"\nPART 1: say \"Hey BMO\" the way you normally would, {WAKE_TRIES} times.", flush=True)
        n = 0
        while n < WAKE_TRIES:
            print(f"  ({n + 1}/{WAKE_TRIES}) Say \"Hey BMO\" now...", flush=True)
            try:
                audio = rec.listen(source, timeout=10.0, phrase_time_limit=float(config.STANDBY_WAKE_PHRASE_TIME_LIMIT))
            except sr.WaitTimeoutError:
                print("     didn't hear anything, try again", flush=True)
                continue
            n += 1
            item = save(out / "wake", f"{n:02d}", audio)
            labels["wake"].append(item)
            print(f"     got {item['seconds']} s", flush=True)
            time.sleep(1.5)

        print(f"\nPART 2: {ROOM_SECONDS // 60} minutes of normal room sound. Carry on as usual; "
              "don't say BMO's name.", flush=True)
        end = time.monotonic() + ROOM_SECONDS
        n = 0
        while time.monotonic() < end:
            try:
                audio = rec.listen(source, timeout=float(config.STANDBY_WAKE_LISTEN_TIMEOUT),
                                   phrase_time_limit=float(config.STANDBY_WAKE_PHRASE_TIME_LIMIT))
            except sr.WaitTimeoutError:
                continue
            n += 1
            labels["room"].append(save(out / "room", f"{n:03d}", audio))
            left = int(end - time.monotonic())
            print(f"  captured sound #{n} ({left} s left)", flush=True)
    (out / "labels.json").write_text(json.dumps(labels, indent=2))
    print(f"\nDone. Saved {len(labels['wake'])} wake tries and {len(labels['room'])} room sounds in {out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
