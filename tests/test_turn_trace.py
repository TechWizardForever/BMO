"""The per-turn stopwatch (handoff 2026-10-31 #1): marks relative to the end of speech, tools and the owner's yes/no
wait kept apart, one JSON line per turn with no words in it, and the summary table."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(os.environ.get("BEMO_TEST_CODE_ROOT", Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(ROOT))
from systems.turn_trace import NoTrace, TurnTrace


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


def a_turn(trace, clock, kind="brain", approval=0.0):
    trace.listen_started()
    clock.now += 1.0
    trace.heard("speech_start")
    clock.now += 2.0
    trace.heard("speech_end")
    clock.now += 0.5
    trace.heard("stt_done")
    clock.now += 0.1
    trace.begin(kind="local", requests=1, words=4)
    if kind == "brain":
        trace.note(kind="brain", model="qwen3-main:30b", threads=12)
        clock.now += 0.2
        trace.mark("ollama_sent")
        clock.now += 1.5
        trace.mark("ollama_first_event")
        clock.now += 1.0
        trace.mark("first_sentence")
        trace.note(prefill_s=1.2, prefill_tokens=80, load_s=0.0, decode_s=2.0)
    trace.tool("find_files", 0.4)
    if approval:
        trace.approval(approval)
        clock.now += approval
    clock.now += 0.3
    trace.mark("first_audio")
    trace.mark("first_audio")  # only the first counts
    clock.now += 3.0
    return trace.finish()


class TurnTraceTests(unittest.TestCase):
    def setUp(self):
        self.path = Path(tempfile.mkdtemp()) / "logs" / "turn-trace.jsonl"
        self.clock = Clock()
        self.trace = TurnTrace(self.path, clock=self.clock, wall=lambda: 1793500000.0)

    def test_one_line_per_turn_timed_from_the_end_of_speech(self):
        turn = a_turn(self.trace, self.clock)
        self.assertEqual(turn["marks"], {"speech_start": -2.0, "speech_end": 0.0, "stt_done": 0.5, "accepted": 0.6,
                                         "ollama_sent": 0.8, "ollama_first_event": 2.3, "first_sentence": 3.3,
                                         "first_audio": 3.6, "ready_again": 6.6})
        self.assertEqual(turn["tools"], [{"name": "find_files", "s": 0.4}])
        self.assertEqual((turn["notes"]["kind"], turn["notes"]["words"]), ("brain", 4))
        line = json.loads(self.path.read_text())
        self.assertEqual(line["marks"]["first_audio"], 3.6)
        self.assertIsNone(self.trace.finish())  # nothing open any more

    def test_no_words_are_kept(self):
        self.trace.begin(kind="local", requests=1, words=len("play spirited away".split()))
        self.trace.finish()
        self.assertNotIn("spirited", self.path.read_text())

    def test_listening_with_nobody_talking_is_no_turn(self):
        self.trace.listen_started()
        self.trace.heard("speech_start")
        self.assertIsNone(self.trace.finish())
        self.assertFalse(self.path.exists())

    def test_the_summary_table(self):
        from tools import summarize_turn_trace as summary
        for _ in range(3):
            a_turn(self.trace, self.clock)
        a_turn(self.trace, self.clock, kind="local", approval=5.0)
        table = summary.table(summary.load(self.path))
        self.assertIn("brain turns: 3", table)
        self.assertIn("local turns: 1", table)
        brain = table.split("brain turns")[1]
        line = next(l for l in brain.splitlines() if "end of speech to first sound" in l)
        self.assertEqual(line.split()[-3:], ["3.60", "3.60", "3"])
        whole_local = [l for l in table.split("local turns")[1].splitlines() if "whole turn" in l][0]
        self.assertEqual(whole_local.split()[-3], "3.90")  # 8.9 s less the 5 s yes/no wait: the owner's, not BMO's

    def test_no_trace_does_nothing(self):
        NoTrace().begin(kind="x")
        NoTrace().mark("y")
        self.assertIsNone(NoTrace().finish())

    def test_wired_in(self):
        chat = (ROOT / "systems" / "chat.py").read_text()
        for hook in ('self._tracer().heard("speech_end")', 'self._tracer().heard("stt_done")',
                     'self._tracer().begin(kind="local"', 'self._tracer().mark("ollama_sent")',
                     'self._tracer().mark("ollama_first_event")', 'self._tracer().mark("first_sentence")',
                     'self.chat._tracer().mark("first_audio")', 'self._tracer().tool(name,',
                     'self.turn_trace.note(kind="brain")', 'self._tracer().finish()'):
            self.assertIn(hook, chat)


if __name__ == "__main__":
    unittest.main()
