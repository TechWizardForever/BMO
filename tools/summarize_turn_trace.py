"""Where a voice turn's time goes, from ~/.bemo/logs/turn-trace.jsonl (systems/turn_trace.py): median and p95 of each
step, for BMO's own commands and for turns that went to a brain.

    .venv/bin/python tools/summarize_turn_trace.py [path] [--since 2026-11-01]
"""
from __future__ import annotations

import json
import statistics
import sys
import time
from pathlib import Path

STEPS = (
    ("speech recognition", "speech_end", "stt_done"),
    ("routing", "stt_done", "accepted"),
    ("to Ollama", "accepted", "ollama_sent"),
    ("Ollama first event", "ollama_sent", "ollama_first_event"),
    ("to first sentence", "ollama_first_event", "first_sentence"),
    ("first sentence to sound", "first_sentence", "first_audio"),
    ("end of speech to first sound", "speech_end", "first_audio"),
    ("whole turn", "speech_end", "ready_again"),
)


def p95(values):
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(round(0.95 * (len(ordered) - 1))))]


def load(path: Path, since: float = 0.0) -> list[dict]:
    turns = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            turn = json.loads(line)
        except ValueError:
            continue
        if turn.get("at", 0) >= since:
            turns.append(turn)
    return turns


def table(turns: list[dict]) -> str:
    lines = []
    for kind in ("local", "brain"):
        group = [t for t in turns if t.get("notes", {}).get("kind") == kind]
        if not group:
            continue
        lines.append(f"{kind} turns: {len(group)}")
        lines.append(f"  {'step':30} {'median':>8} {'p95':>8} {'n':>4}")
        for label, start, end in STEPS:
            pairs = [(t["marks"][end] - t["marks"][start], t) for t in group
                     if start in t["marks"] and end in t["marks"]]
            # the owner's time answering a yes/no isn't the machine's
            values = [v - (t.get("approval_s", 0.0) if label == "whole turn" else 0.0) for v, t in pairs]
            if values:
                lines.append(f"  {label:30} {statistics.median(values):8.2f} {p95(values):8.2f} {len(values):4}")
        notes = [t["notes"] for t in group]
        for key in ("load_s", "prefill_s", "prefill_tokens", "decode_s"):
            values = [n[key] for n in notes if isinstance(n.get(key), (int, float))]
            if values:
                lines.append(f"  ollama {key:23} {statistics.median(values):8.2f} {p95(values):8.2f} {len(values):4}")
        waits = [n for n in notes if n.get("warmup_running")]
        if waits:
            lines.append(f"  turns sent while a warm-up was still running: {len(waits)}")
    return "\n".join(lines) or "no turns yet"


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    since = 0.0
    if "--since" in args:
        at = args.index("--since")
        since = time.mktime(time.strptime(args[at + 1], "%Y-%m-%d"))
        del args[at:at + 2]
    path = Path(args[0]) if args else Path.home() / ".bemo" / "logs" / "turn-trace.jsonl"
    if not path.exists():
        print(f"No turns traced yet ({path}).")
        return 1
    print(table(load(path, since)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
