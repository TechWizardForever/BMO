#!/usr/bin/env python3
"""Headless soak: BMO's real reply path with the real Main model, for minutes or hours.

No microphone, no speaker, no Supervisor: speech is captured as text, the face
is a stub, and BMO's data goes to a throwaway folder (or --data-dir). Every turn
records the process's memory, threads and open files, so slow leaks show up as
a trend; errors and slow turns are listed at the end.

    .venv/bin/python tools/headless_soak.py --minutes 60
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Ordinary things the owner says: chat, local commands, follow-ups, a question about
# earlier turns (history), and one long answer. No computer actions (no Supervisor).
TURNS = [
    "hey bmo how are you today",
    "what time is it",
    "tell me a short joke",
    "what was the joke about",
    "give me three ideas for a weekend electronics project",
    "which of those is the easiest",
    "what can you do on this computer",
    "what hardware am I on",
    "explain what a capacitor does in two sentences",
    "what did I ask you about first",
    "what's the volume",
    "say something nice about adventure time",
]


def sample():
    import psutil
    me = psutil.Process()
    with me.oneshot():
        return {"rss_mb": round(me.memory_info().rss / 2**20, 1), "threads": me.num_threads(),
                "fds": me.num_fds()}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--minutes", type=float, default=30.0)
    parser.add_argument("--max-turns", type=int, default=10_000)
    parser.add_argument("--data-dir", default="")
    parser.add_argument("--report", default="")
    parser.add_argument("--trace", action="store_true",
                        help="record where Python memory grows between turn 20 and the end (tracemalloc)")
    args = parser.parse_args()
    data = Path(args.data_dir or tempfile.mkdtemp(prefix="bemo-soak-"))
    os.environ["BEMO_DATA_DIR"] = str(data)
    os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
    sys.path.insert(0, str(ROOT))
    os.chdir(ROOT)
    import config
    from systems.chat import Chat

    class Face:
        def __getattr__(self, name):
            return lambda *a, **k: None

    said = []
    chat = Chat(display_manager=Face())
    chat.supervisor = None
    chat.speak = lambda text, **kw: said.append(text)
    # Text mode: replies come back through speak() whole instead of the streaming voice (no speaker here).
    chat.interface_settings.set_tts_enabled(False)
    chat.active_profile_name = config.MAIN_MODEL_PROFILE
    rows, errors = [], []
    if args.trace:
        import tracemalloc
        tracemalloc.start(12)
    first_snapshot = None
    start = time.monotonic()
    base = sample()
    turn = 0
    while time.monotonic() - start < args.minutes * 60 and turn < args.max_turns:
        text = TURNS[turn % len(TURNS)]
        said.clear()
        t = time.monotonic()
        try:
            if not chat._handle_local_command(text):
                chat.respond(text)
        except Exception:
            errors.append({"turn": turn, "said": text, "error": traceback.format_exc()[-800:]})
        last = chat.chat_history[-1] if chat.chat_history else {}
        reply = " ".join(said) or (last.get("content", "") if last.get("role") == "assistant" else "")
        row = {"turn": turn, "said": text, "seconds": round(time.monotonic() - t, 1),
               "reply": str(reply)[:160], "history": len(chat.chat_history), **sample()}
        rows.append(row)
        print(json.dumps(row), flush=True)
        turn += 1
        if args.trace and turn == 20:
            import tracemalloc
            first_snapshot = tracemalloc.take_snapshot()
    end = sample()
    slow = [r for r in rows if r["seconds"] > 60]
    empty = [r for r in rows if not r["reply"]]
    summary = {
        "turns": len(rows), "minutes": round((time.monotonic() - start) / 60, 1), "errors": len(errors),
        "empty_replies": len(empty), "slow_turns_over_60s": len(slow),
        "rss_mb": [base["rss_mb"], end["rss_mb"]], "threads": [base["threads"], end["threads"]],
        "fds": [base["fds"], end["fds"]],
        # Growth over the second half only: the first turns load models and caches.
        "rss_growth_second_half_mb": round(rows[-1]["rss_mb"] - rows[len(rows) // 2]["rss_mb"], 1) if rows else 0,
        "data_dir": str(data),
    }
    try:  # Main is kept loaded forever (keep_alive -1); a soak shouldn't leave 18 GB behind.
        import httpx
        httpx.post("http://127.0.0.1:11434/api/generate", json={"model": chat.active_model, "keep_alive": 0},
                   timeout=60)
    except Exception as exc:
        print("Could not unload", chat.active_model, exc)
    print("SUMMARY", json.dumps(summary, indent=2))
    if args.trace and first_snapshot is not None:
        import tracemalloc
        growth = tracemalloc.take_snapshot().compare_to(first_snapshot, "traceback")
        print(f"MEMORY GROWTH after turn 20 (top 12 of {len(growth)}):")
        for stat in growth[:12]:
            print(f"  +{stat.size_diff / 1024:.0f} KiB in {stat.count_diff:+d} blocks")
            for line in [f for f in stat.traceback.format() if "site-packages" not in f][-6:]:
                print("     ", line.strip())
    for item in errors[:5]:
        print("ERROR in turn", item["turn"], repr(item["said"]), "\n", item["error"])
    if args.report:
        Path(args.report).write_text(json.dumps({"summary": summary, "rows": rows, "errors": errors}, indent=2))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
