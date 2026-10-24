#!/usr/bin/env python3
"""Install, validate, and benchmark the locally trained Piper ONNX model.

Run with Bemo's own .venv/bin/python from the Bemo source directory.
No web calls or heavy models are needed to validate a transferred ONNX.
"""
import argparse
import hashlib
import shutil
import time
import wave
from pathlib import Path

import config


def install_model(source):
    src = Path(source).expanduser().resolve()
    cfg = Path(str(src) + ".json")
    if not src.is_file() or not cfg.is_file():
        raise SystemExit(f"Missing {src} or matching {cfg}")
    dst = config.PIPER_MODEL_PATH
    dst.parent.mkdir(parents=True, exist_ok=True)
    for source_file, dest in ((src, dst), (cfg, Path(str(dst) + ".json"))):
        if source_file == dest.resolve():
            continue
        tmp = dest.with_name(dest.name + ".incoming")
        shutil.copy2(source_file, tmp)
        tmp.replace(dest)
    print("Installed model:", dst)
    print("Model SHA256:", hashlib.sha256(dst.read_bytes()).hexdigest())
    return dst


def benchmark(output):
    from systems.piper_streaming import PiperSpeechEngine
    started = time.perf_counter()
    engine = PiperSpeechEngine(config.PIPER_MODEL_PATH)
    loaded = time.perf_counter()
    phrase = "Hi friend! I'm Beemo, and I'm all ready to talk to you. What would you like to work on today?"
    out = Path(output).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    first = None
    samples = 0
    with wave.open(str(out), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(engine.sample_rate)
        for chunk in engine.stream(phrase):
            if first is None:
                first = time.perf_counter()
            # Test WAV written only by this explicit benchmark, never during conversation.
            import numpy as np
            data = (np.clip(chunk.audio, -1, 1) * 32767).astype("<i2")
            wav.writeframes(data.tobytes())
            samples += data.size
    finished = time.perf_counter()
    audio_sec = samples / engine.sample_rate if engine.sample_rate else 0
    print(f"Model load: {loaded - started:.3f}s")
    print(f"First audio chunk after load: {(first-loaded):.3f}s" if first else "No audio generated")
    print(f"Synthesis (already loaded): {finished-loaded:.3f}s")
    print(f"Audio duration: {audio_sec:.3f}s")
    if finished > loaded:
        print(f"Realtime factor: {audio_sec/(finished-loaded):.2f}x")
    print("Test recording:", out)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    install = sub.add_parser("install", help="Copy exported .onnx and matching .json")
    install.add_argument("source", help="Path to en_US-bemo-best-v3.onnx on USB/disk")
    bench = sub.add_parser("benchmark", help="Synthesize offline to a diagnostic WAV")
    bench.add_argument("--output", default=str(config.DATA_ROOT / "voice" / "piper" / "benchmark.wav"))
    args = parser.parse_args()
    if args.action == "install":
        install_model(args.source)
    else:
        benchmark(args.output)


if __name__ == "__main__":
    main()
