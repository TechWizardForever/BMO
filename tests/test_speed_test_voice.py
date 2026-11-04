"""'Hey BMO, run the speed test' (2026-11-01): a stopwatch on his face, he hears nothing until it's done, and he
uses the winner by himself."""
import os
from pathlib import Path
import sys
import threading
import types
import unittest
from unittest import mock

ROOT = Path(os.environ.get("BEMO_TEST_CODE_ROOT", Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(ROOT))
import config
import hardware_autotune
from systems.chat import Chat, State


class PhraseTests(unittest.TestCase):
    def test_asks(self):
        for said in ("Hey BMO, run the speed test.", "run a speed test", "do the speed test", "speed test",
                     "Hey BMO, test your speed", "run the benchmark", "please rerun the speed test"):
            self.assertTrue(hardware_autotune.is_request(said), said)

    def test_not_asks(self):
        for said in ("what's the speed of light", "how fast is an internet speed test", "test the speakers",
                     "run a hardware diagnostic", "what is a benchmark"):
            self.assertFalse(hardware_autotune.is_request(said), said)


class HandlerTests(unittest.TestCase):
    def chat(self):
        c = Chat.__new__(Chat)
        c.active_profile_name = config.MAIN_MODEL_PROFILE
        c.state = State.READY
        c.stop_event = threading.Event()
        c.managed_display_active = lambda: False
        c.note_user_activity = lambda: None
        c.said, c.warmed = [], []
        c.speak = lambda text, **kw: c.said.append(text)
        c._say_local = lambda command, text: c.said.append(text) or True
        c._warm_runtime_prompt = lambda: c.warmed.append(True)
        return c

    def run_test(self, c, result):
        seen = {}

        def benchmark(force=False):
            seen["force"] = force
            seen["symbol"] = c._indicator()
            if isinstance(result, Exception):
                raise result
            return result
        with mock.patch.object(hardware_autotune, "run_benchmark", benchmark), \
                mock.patch.object(config, "refresh_ollama_threads") as refresh:
            self.assertTrue(c._handle_speed_test("run the speed test"))
        seen["refreshed"] = refresh.called
        return seen

    def test_the_stopwatch_shows_and_he_uses_the_winner(self):
        c = self.chat()
        seen = self.run_test(c, {"ok": True, "record": {"chosen_ollama_threads": 8}})
        self.assertTrue(seen["force"])
        self.assertEqual(seen["symbol"], "testing")
        self.assertNotEqual(c._indicator(), "testing")   # gone afterwards
        self.assertTrue(seen["refreshed"])
        self.assertEqual(c.warmed, [True])               # on the winning thread count
        self.assertIn("won't hear you", c.said[0])       # said before it starts
        self.assertIn("8 threads", c.said[-1])

    def test_a_failed_test_keeps_what_he_had(self):
        for result in ({"ok": False, "reason": "no brain"}, RuntimeError("boom")):
            c = self.chat()
            seen = self.run_test(c, result)
            self.assertFalse(seen["refreshed"])
            self.assertEqual(c.warmed, [])
            self.assertIn("keeping the settings", c.said[-1])
            self.assertNotEqual(c._indicator(), "testing")

    def test_it_waits_for_the_main_brain_and_a_clear_screen(self):
        c = self.chat()
        c.active_profile_name = "coding"
        with mock.patch.object(hardware_autotune, "run_benchmark") as run:
            c._handle_speed_test("run the speed test")
        run.assert_not_called()
        self.assertIn("main brain", c.said[-1])
        c = self.chat()
        c.managed_display_active = lambda: True
        with mock.patch.object(hardware_autotune, "run_benchmark") as run:
            c._handle_speed_test("run the speed test")
        run.assert_not_called()
        self.assertIn("movie or game", c.said[-1])

    def test_other_requests_pass_through(self):
        self.assertFalse(self.chat()._handle_speed_test("play a movie"))

    def test_it_holds_the_turn(self):
        # Local commands run inside the turn: while this one runs, the loop isn't listening.
        src = (ROOT / "systems" / "chat.py").read_text()
        self.assertIn("if self._handle_speed_test(command):", src)
        start = src.index("def _handle_speed_test")
        body = src[start:src.index("\n    def ", start + 10)]
        self.assertIn("worker.join(", body)
        self.assertIn("self.note_user_activity()", body)  # no screensaver over the test


class SymbolTests(unittest.TestCase):
    def test_it_is_drawn(self):
        from systems.render import FaceRenderer
        self.assertIn("testing", FaceRenderer.INDICATOR_COLORS)
        self.assertEqual(len({len(row) for row in FaceRenderer._WATCH}), 1)

    def test_it_is_in_the_command_list(self):
        self.assertIn("speed test", (ROOT / "VOICE_COMMANDS.md").read_text())
        self.assertIn("speed test", (ROOT / "voice_commands.py").read_text())


if __name__ == "__main__":
    unittest.main()
