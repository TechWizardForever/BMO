"""'Hey BMO, go to sleep.' with Whisper's full stop is the sleep command (live 2026-10-20: it went to Main)."""
import ast
import os
from pathlib import Path
import re
import sys
import unittest

ROOT = Path(os.environ.get("BEMO_TEST_CODE_ROOT", Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(ROOT))
import config

SOURCE = (ROOT / "systems" / "chat.py").read_text()


def mini(names):
    chat = next(n for n in ast.parse(SOURCE).body if isinstance(n, ast.ClassDef) and n.name == "Chat")
    keep = [n for n in chat.body if isinstance(n, ast.FunctionDef) and n.name in names]
    ns = {"config": config, "re": re, "logger": type("L", (), {"info": staticmethod(lambda *a, **k: None)})}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[ast.ClassDef(
        name="Mini", bases=[], keywords=[], body=keep, decorator_list=[])], type_ignores=[])), "m", "exec"), ns)
    return ns["Mini"]()


class SleepTests(unittest.TestCase):
    def test_the_sleep_check_ignores_a_full_stop(self):
        self.assertIn('if normalized.strip(" .!?") in set(getattr(config, "VOICE_SLEEP_PHRASES"', SOURCE)
        for heard in ("go to sleep.", "go to sleep!", "go back to sleep."):
            normalized = re.sub(r"\s+", " ", re.sub(r"[^a-z0-9.\- ]+", " ", heard.lower())).strip()
            self.assertIn(normalized.strip(" .!?"), set(config.VOICE_SLEEP_PHRASES), heard)

    def test_sleep_means_standby(self):
        # 2026-10-20: sends BMO back to standby, after saying so.
        branch = SOURCE[SOURCE.index('in set(getattr(config, "VOICE_SLEEP_PHRASES"'):]
        branch = branch[:branch.index("return True")]
        self.assertLess(branch.index("self.speak(config.VOICE_SLEEP_ACKNOWLEDGEMENT)"),
                        branch.index("self.supervisor.stop_bemo()"))

    def test_a_sentence_that_differs_only_by_punctuation_gets_the_second_try(self):
        m = mini({"_handle_local_command", "_normalize_command_text"})
        tried = []

        def once(command):
            tried.append(command)
            return command == "go to sleep"  # a handler that knows only the bare words
        m._handle_local_command_once = once
        self.assertTrue(m._handle_local_command("go to sleep."))
        self.assertEqual(tried, ["go to sleep.", "go to sleep"])


if __name__ == "__main__":
    unittest.main()
