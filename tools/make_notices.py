#!/usr/bin/env python3
"""Write THIRD_PARTY_NOTICES.md: what BMO is built from and under which licence (installer plan I6).

The brains, voice and tools are listed by hand below; the Python packages come from the exact package list
(installer/requirements.lock) and each package's own metadata in BMO's environment. --check compares.
"""
from __future__ import annotations

import argparse
import importlib.metadata as md
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "THIRD_PARTY_NOTICES.md"

BY_HAND = """# Third-party notices

BMO's own code is licensed under the GNU General Public License, version 3 (`LICENSE`). BMO is a free,
non-commercial fan project; it is not affiliated with or endorsed by the owners of Adventure Time. BMO's
name and look belong to them.

## Brains, voice and speech (downloaded or bundled)

| Part | Where it comes from | Licence |
|---|---|---|
| Main, Engineering, Coding brains (Qwen3 30B-A3B, Qwen3-Coder 30B-A3B) | Alibaba Qwen, via Ollama | Apache-2.0 |
| Emergency brain (Qwen3 4B) | Alibaba Qwen, via Ollama | Apache-2.0 |
| Deep Vision brain (InternVL3.5 30B-A3B, GGUF by bartowski) | OpenGVLab, via Hugging Face | to confirm from the model card before release |
| Ollama 0.40.5 | ollama/ollama on GitHub | MIT |
| Whisper base.en / tiny.en (faster-whisper conversions) | OpenAI; Systran | MIT |
| Silero VAD (shipped inside faster-whisper) | Silero | MIT |
| Piper 1.8 (`piper-tts`) | OHF-Voice/piper1-gpl | GPL-3.0; source: https://github.com/OHF-Voice/piper1-gpl |
| BMO's Piper voice (`en_US-bemo-best-v3`, release voice-1) | fan-made by BMO's maker for this non-commercial project | see the note above about the character |
| openWakeWord and its feature models | dscripka/openWakeWord | Apache-2.0 |
| BMO's "Hey BMO" detector (`assets/wakeword/hey_bmo.onnx`) | trained for this project with openWakeWord from synthetic voices; the training data includes ACAV100M features | CC BY-NC-SA 4.0 (non-commercial, credit, same licence for changes) |

## Python packages (exact versions from installer/requirements.lock)

| Package | Version | Licence (from the package's metadata) |
|---|---|---|
"""


def licence(name: str) -> str:
    meta = md.metadata(name)
    text = (meta.get("License-Expression") or "").strip()
    if not text:
        classifiers = [c.split("::")[-1].strip() for c in meta.get_all("Classifier") or [] if c.startswith("License ::")]
        text = ", ".join(c for c in classifiers if c != "OSI Approved")
    if not text:
        raw = (meta.get("License") or "").strip().splitlines()
        text = raw[0][:60] if raw else ""
    return (text or "see the package").replace("|", "/")


def render() -> str:
    rows = []
    for line in (ROOT / "installer" / "requirements.lock").read_text().splitlines():
        if line and not line.startswith("#") and "==" in line:
            name, version = line.split("==", 1)
            rows.append(f"| {name} | {version} | {licence(name)} |")
    return BY_HAND + "\n".join(rows) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    text = render()
    if args.check:
        same = OUT.is_file() and OUT.read_text() == text
        print("THIRD_PARTY_NOTICES.md is up to date." if same else "THIRD_PARTY_NOTICES.md is out of date.")
        return 0 if same else 1
    OUT.write_text(text)
    print(f"Wrote {OUT.name}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
