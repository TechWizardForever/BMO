#!/usr/bin/env python3
"""Which brain writes 3D parts best? (2026-10-22)

Each brain gets the same parts, the same short instructions and BMO's real make_cad_model tool (up to three
tries, seeing the tool's answer each time, as in BMO). Every STL is then judged here, not by the brain: the
outside size, the holes that go all the way through, and the volume against the ideal part.

  .venv/bin/python tools/bench_cad_models.py [engineering coding] [--parts 1,3] [--out results.json]

Uses the local Ollama brains only; files go to a temporary folder. Run it while BMO isn't using a brain.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config  # noqa: E402
from systems.engineering import EngineeringToolkit, engineering_tool_schemas  # noqa: E402

PI = math.pi
# (request as the owner would say it, outside size mm, holes through, ideal volume mm³)
PARTS = [
    ("make a 20 millimeter cube with a 5 millimeter hole through the middle",
     (20, 20, 20), 1, 8000 - PI * 2.5 ** 2 * 20),
    ("make a 20 millimeter cube with an 8 millimeter hole through the middle",
     (20, 20, 20), 1, 8000 - PI * 4 ** 2 * 20),
    ("make a round spacer 10 millimeters tall, 12 millimeters across, with a 5 millimeter hole through it",
     (12, 12, 10), 1, PI * (6 ** 2 - 2.5 ** 2) * 10),
    ("make a flat plate 60 by 40 by 3 millimeters with a 4 millimeter hole near each corner, "
     "the hole centers 5 millimeters in from both edges",
     (60, 40, 3), 4, 60 * 40 * 3 - 4 * PI * 2 ** 2 * 3),
    ("make an L-shaped bracket from two plates 3 millimeters thick and 20 millimeters wide: a base 40 millimeters "
     "long and an upright 40 millimeters tall standing on one end of the base",
     (40, 20, 40), 0, 40 * 20 * 3 + 37 * 20 * 3),
    ("make an open-top box 50 by 30 by 20 millimeters on the outside, with 2 millimeter walls and a "
     "2 millimeter floor",
     (50, 30, 20), 0, 50 * 30 * 20 - 46 * 26 * 18),
    # 13-14: cutouts of other shapes (2026-10-22).
    ("make a block 30 by 30 by 10 millimeters with a 10 millimeter square hole through the middle",
     (30, 30, 10), 1, 30 * 30 * 10 - 10 * 10 * 10),
    ("make a cylinder 20 millimeters across and 10 tall with a hexagon pocket 9 millimeters across the corners and "
     "4 millimeters deep in the middle of the top",
     (20, 20, 10), 0, PI * 10 ** 2 * 10 - 3 * 3 ** 0.5 / 2 * 4.5 ** 2 * 4),
    # Parts for the bigger template catalog (2026-10-22).
    ("make a standoff 10 millimeters tall and 6 millimeters across with a 3 millimeter hole through it",
     (6, 6, 10), 1, PI * (3 ** 2 - 1.5 ** 2) * 10),
    ("make a round container 40 millimeters across and 30 millimeters tall with 2 millimeter walls and a "
     "2 millimeter floor",
     (40, 40, 30), 0, PI * (20 ** 2 * 30 - 18 ** 2 * 28)),
    ("make a tray 100 by 60 by 20 millimeters with 2 millimeter walls and floor, divided into 3 compartments "
     "along its length",
     (100, 60, 20), 0, 100 * 60 * 20 - 92 * 56 * 18),
    ("make a block 40 by 30 by 20 millimeters with its four vertical edges rounded to a 5 millimeter radius",
     (40, 30, 20), 0, 40 * 30 * 20 - 4 * (5 ** 2 - PI * 5 ** 2 / 4) * 20),
    ("make a lid for a box that is 50 by 30 millimeters on the outside with 2 millimeter walls: a 2 millimeter "
     "top with a 3 millimeter lip that fits inside the walls with 0.3 millimeters of clearance",
     (50, 30, 5), 0, 50 * 30 * 2 + (45.4 * 25.4 - 41.4 * 21.4) * 3),
    ("make a pipe clamp for a 20 millimeter pipe, 15 millimeters wide and 3 millimeters thick, with a "
     "10 millimeter foot on each side and a 4 millimeter screw hole in each foot",
     (46, 15, 13), 2, PI / 2 * (13 ** 2 - 10 ** 2) * 15 + 2 * 10 * 3 * 15 - 2 * PI * 2 ** 2 * 3),
]
# Parts made of several pieces (2026-10-22).
COMPLEX = [
    ("make a project box 80 by 60 by 30 millimeters with 2 millimeter walls and floor, with four standoffs standing "
     "on the floor inside, each 6 millimeters across and 8 millimeters tall above the floor with a 2.5 millimeter "
     "hole, centered 10 millimeters in from each corner",
     (80, 60, 30), 0, 80 * 60 * 30 - 76 * 56 * 28 + 4 * PI * (3 ** 2 - 1.25 ** 2) * 8),
    ("make a mounting plate 60 by 40 by 3 millimeters with an L-bracket standing on top of it: the bracket is 20 "
     "millimeters wide, 3 thick, with a 30 millimeter base and 30 millimeters tall above the plate, placed 5 "
     "millimeters from the left edge and centered front to back; plus a 6 millimeter hole through the plate, "
     "10 millimeters from the right edge and centered front to back",
     (60, 40, 33), 1, 60 * 40 * 3 - PI * 3 ** 2 * 3 + 30 * 20 * 3 + 3 * 20 * 27),
    ("make a block 50 by 30 by 20 millimeters with a slot 10 millimeters wide and 10 millimeters deep cut into the "
     "top, running the full 30 millimeter depth, centered along the 50 millimeter length",
     (50, 30, 20), 0, 50 * 30 * 20 - 10 * 10 * 30),
    ("make a round container 40 millimeters across and 30 millimeters tall with 2 millimeter walls and floor, "
     "standing in the middle of a square base plate 50 by 50 by 3 millimeters",
     (50, 50, 33), 0, 50 * 50 * 3 + PI * (20 ** 2 * 30 - 18 ** 2 * 28)),
    ("make a plate 100 by 40 by 4 millimeters with three standoffs on top, each 8 millimeters across and 10 "
     "millimeters tall above the plate, evenly spaced along the middle at 25, 50 and 75 millimeters, each with a "
     "3 millimeter hole going all the way through the standoff and the plate",
     (100, 40, 14), 3, 100 * 40 * 4 - 3 * PI * 1.5 ** 2 * 4 + 3 * PI * (4 ** 2 - 1.5 ** 2) * 10),
]
SYSTEM = ("You are BMO's 3D-part maker. Write OpenSCAD in millimeters and call make_cad_model with the complete "
          "source, a short name, size_mm (the outside size asked for) and through_holes (how many holes go all "
          "the way through; cut holes out with "
          "difference()). Read the tool's answer: if it refused the part or the size is wrong, fix it and call "
          "again. Finish with one short sentence giving the measured size.")


def judge(made: dict, size, holes, volume) -> list[str]:
    """What is wrong with the STL, judged here; [] when it is the part that was asked for."""
    wrong = []
    got = sorted(made.get("size_mm") or [])
    if len(got) != 3 or any(abs(a - b) > 0.5 for a, b in zip(got, sorted(size))):
        wrong.append(f"size {made.get('size_mm')} (wanted {list(size)})")
    if made.get("through_holes") != holes:
        wrong.append(f"{made.get('through_holes')} holes through (wanted {holes})")
    vol = made.get("volume_mm3")
    if vol is None or abs(vol - volume) > 0.03 * volume:
        wrong.append(f"volume {vol} (ideal {volume:.0f})")
    return wrong


SHAPES = ("For a part one of make_cad_part's shapes can make, call make_cad_part with the shape and its numbers; "
          "make_cad_model is only for anything else. ")


def run_part(ollama, profile: dict, kit: EngineeringToolkit, request: str, size, holes, volume, tries=3,
             templates=False) -> dict:
    wanted = {"make_cad_model", "make_cad_part"} if templates else {"make_cad_model"}
    tools = [t for t in engineering_tool_schemas() if t["function"]["name"] in wanted]
    system = SYSTEM.replace("Read the tool's answer", SHAPES + "Read the tool's answer") if templates else SYSTEM
    messages = [{"role": "system", "content": system}, {"role": "user", "content": request}]
    options = {"num_ctx": 8192, "num_thread": int(config.OLLAMA_NUM_THREAD),
               "temperature": float(profile.get("temperature", 0.2)), "num_predict": int(profile.get("num_predict", 4096))}
    result = {"request": request, "attempts": [], "seconds": 0.0, "first_try_right": False, "right": False}
    started = time.monotonic()
    for _ in range(tries + 1):
        try:
            reply = ollama.chat(model=profile["model"], messages=messages, tools=tools, options=options, stream=False)
        except ollama.ResponseError as exc:  # e.g. a tool call Ollama couldn't parse: a failed try, not a crash
            result["attempts"].append({"right": False, "wrong": [f"ollama: {str(exc)[:160]}"], "tool": None, "source": ""})
            messages.append({"role": "user", "content": "That tool call was not valid JSON. Try again."})
            if len(result["attempts"]) >= tries:
                break
            continue
        message = reply["message"]
        calls = message.get("tool_calls") or []
        messages.append({"role": "assistant", "content": message.get("content") or "", "tool_calls": calls})
        result["last_reply"] = (message.get("content") or "")[-600:]
        # Why the brain stopped, and how much it "thought" first (2026-10-22: the bracket twice came back empty).
        result.setdefault("rounds", []).append({"done_reason": reply.get("done_reason"),
                                                "output_tokens": reply.get("eval_count"),
                                                "thinking_chars": len(message.get("thinking") or "")})
        if not calls:
            break
        for call in calls[:1]:
            args = dict(call["function"].get("arguments") or {})
            tool_name = call["function"].get("name")
            try:
                made = kit.make_cad_part(args) if tool_name == "make_cad_part" else kit.make_cad_model(args)
                wrong = judge(made, size, holes, volume)
                answer = json.dumps(made)
            except Exception as exc:  # the tool's refusal, as BMO would pass it back
                made, wrong, answer = None, [f"tool: {exc}"], f"Tool error: {exc}"
            result["attempts"].append({"right": not wrong, "wrong": wrong, "tool": tool_name,
                                       "source": args.get("source", "")[:1500] or json.dumps(args)})
            messages.append({"role": "tool", "content": answer})
            if not wrong:
                result["right"] = True
                result["first_try_right"] = len(result["attempts"]) == 1
        if result["right"] or len(result["attempts"]) >= tries:
            break
    result["seconds"] = round(time.monotonic() - started, 1)
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("brains", nargs="*", default=["engineering", "coding"])
    parser.add_argument("--parts", default="", help="comma-separated part numbers (1-based); default all")
    parser.add_argument("--out", default="")
    parser.add_argument("--templates", "--shapes", dest="templates", action="store_true",
                        help="also offer make_cad_part (six basic shapes filled in by number)")
    parser.add_argument("--complex", action="store_true", help="the parts made of several pieces")
    parser.add_argument("--no-think", action="store_true", help="the brain answers without thinking first")
    args = parser.parse_args(argv)
    import ollama
    if args.no_think:
        # 2026-10-23: live, Engineering thought for 3 minutes (1,900 words) before filling in a 20 mm cube.
        real = ollama

        class _NoThink:
            ResponseError = real.ResponseError
            generate = staticmethod(real.generate)

            @staticmethod
            def chat(**kwargs):
                return real.chat(think=False, **kwargs)
        ollama = _NoThink
    pool = COMPLEX if args.complex else PARTS
    chosen = [pool[int(n) - 1] for n in args.parts.split(",") if n.strip()] or pool
    results = {}
    for brain in args.brains:
        profile = config.MODEL_PROFILES[brain]
        with tempfile.TemporaryDirectory() as tmp:
            kit = EngineeringToolkit(Path(tmp) / "engineering", cad_output_dir=Path(tmp) / "out")
            rows = []
            for i, (request, size, holes, volume) in enumerate(chosen, 1):
                row = run_part(ollama, profile, kit, request, size, holes, volume, templates=args.templates)
                rows.append(row)
                print(f"{brain:12s} part {i}: {'RIGHT' if row['right'] else 'WRONG'}"
                      f"{' first try' if row['first_try_right'] else ''}, {len(row['attempts'])} tries, "
                      f"{row['seconds']:.0f} s  {row['attempts'][-1]['wrong'] if row['attempts'] and not row['right'] else ''}",
                      flush=True)
                if args.out:
                    Path(args.out).write_text(json.dumps({**results, brain: rows}, indent=2))
            results[brain] = rows
        # One brain at a time, as BMO does: two 18 GB brains don't fit in 28 GB (2026-10-22 the second load was
        # killed for lack of memory).
        ollama.generate(model=profile["model"], prompt="", keep_alive=0)
    print()
    for brain, rows in results.items():
        right = sum(r["right"] for r in rows)
        first = sum(r["first_try_right"] for r in rows)
        secs = sum(r["seconds"] for r in rows)
        print(f"{brain:12s} right {right}/{len(rows)}, first try {first}/{len(rows)}, "
              f"{secs / 60:.1f} min in all ({secs / max(1, len(rows)):.0f} s a part)")
    if args.out:
        Path(args.out).write_text(json.dumps(results, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
