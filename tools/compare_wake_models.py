#!/usr/bin/env python3
"""Compare standby speech models on a recording from tools/record_wake_sample.py.

Runs the standby listener's own transcription and wake rule on every clip, for
tiny.en and base.en, with and without the "Bemo" hint:

    .venv/bin/python tools/compare_wake_models.py ~/.bemo/wake-samples/<time> report.json
"""
import json, os, sys, time, wave
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); os.chdir(ROOT)
import speech_recognition as sr, config
from faster_whisper import WhisperModel
from standby_wake import StandbyWakeListener, is_standby_wake_phrase
D = sys.argv[1]
def load(path):
    with wave.open(path) as w:
        return sr.AudioData(w.readframes(w.getnframes()), w.getframerate(), w.getsampwidth())
wake = [load(f"{D}/wake/{f}") for f in sorted(os.listdir(f"{D}/wake"))]
room = [load(f"{D}/room/{f}") for f in sorted(os.listdir(f"{D}/room"))]
L = StandbyWakeListener(should_listen=lambda: False, on_wake=lambda: None)
results = {}
for name in ("tiny.en", "base.en"):
    m = WhisperModel(name, device="cpu", compute_type="int8", cpu_threads=2, num_workers=1)
    for hot in ("", "Bemo"):
        config.STANDBY_WAKE_HOTWORDS = hot
        t = time.monotonic(); wt = [L._transcribe(m, a) for a in wake]; wsec = (time.monotonic() - t) / len(wake)
        rt = [L._transcribe(m, a) for a in room]
        hits = sum(is_standby_wake_phrase(x) for x in wt)
        false = [x for x in rt if is_standby_wake_phrase(x)]
        bemo_room = [x for x in rt if "bemo" in x.lower() or "bmo" in x.lower()]
        key = f"{name} hint={'Bemo' if hot else 'none'}"
        results[key] = {"wake": wt, "false": false, "bemo_room": bemo_room}
        print(f"{key:24} woke {hits}/10   false starts {len(false)}/37   'Bemo' in room {len(bemo_room)}   {wsec:.2f} s/clip", flush=True)
json.dump(results, open(sys.argv[2], "w"), indent=1)
