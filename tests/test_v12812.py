"""v1.28.12 regressions from the v1.28.11 live test (2026-10-18)."""
import ast
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import re
import sys
import types
import unittest

ROOT = Path(os.environ.get("BEMO_TEST_CODE_ROOT", Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(ROOT))
import config
from systems.grounding import clock_reply, live_data_topic

SRC = (ROOT / "systems" / "chat.py").read_text()
CHAT = next(n for n in ast.parse(SRC).body if isinstance(n, ast.ClassDef) and n.name == "Chat")
PDT = timezone(timedelta(hours=-7), "PDT")


class ClockTests(unittest.TestCase):
    NOW = datetime(2026, 10, 18, 14, 3, tzinfo=PDT)

    def test_live_question_answered_from_the_clock(self):
        # Live: Main said "my clock is set to UTC" with no evidence.
        self.assertEqual(live_data_topic("what time zone are you in?"), "timezone")
        self.assertEqual(clock_reply("timezone", self.NOW), "This computer's clock is set to PDT, which is UTC minus 7.")
        self.assertEqual(clock_reply("time", self.NOW), "It's 2:03 PM.")
        self.assertEqual(clock_reply("date", self.NOW), "Today is Sunday, October 18, 2026.")

    def test_coding_and_general_questions_still_reach_a_model(self):
        for q in ("explain time zones", "write code that converts time zones",
                  "write a python script that prints the time", "what is time dilation"):
            self.assertIsNone(live_data_topic(q), q)

    def test_prompts_know_the_time_zone(self):
        self.assertIn(datetime.now().astimezone().tzname(), config.HOST_FACTS)


class ProfileNameTests(unittest.TestCase):
    def setUp(self):
        methods = [n for n in CHAT.body if isinstance(n, ast.FunctionDef)
                   and n.name in {"_match_profile_name", "_normalize_command_text"}]
        ns = {"config": config, "re": re, "logger": types.SimpleNamespace(info=lambda *a, **k: None)}
        exec(compile(ast.fix_missing_locations(ast.Module(body=[ast.ClassDef(
            name="Mini", bases=[], keywords=[], body=methods, decorator_list=[])], type_ignores=[])), "mini", "exec"), ns)
        self.chat = ns["Mini"]()

    def test_misheard_names_from_live_tests(self):
        self.assertEqual(self.chat._match_profile_name("codeine"), "coding")
        self.assertEqual(self.chat._match_profile_name("Maine"), "normal")
        self.assertEqual(self.chat._match_profile_name("coding brain"), "coding")

    def test_unrelated_words_do_not_match(self):
        for word in ("dinner", "ending", "pizza"):
            self.assertIsNone(self.chat._match_profile_name(word), word)


class DiagnosticsTests(unittest.TestCase):
    def test_near_misses_are_logged(self):
        listen = SRC[SRC.index("    def listen("):SRC.index("    def _recover_dropped_wake")]
        self.assertIn("VOICE near miss", listen)


if __name__ == "__main__":
    unittest.main()
