"""A brain that says nothing still gets an answer from BMO (2026-10-22: Engineering thought for about 7 minutes
about an L-bracket, ran out of its 4,096-token answer and said nothing; twice)."""
import ast
import os
from pathlib import Path
import sys
import unittest

ROOT = Path(os.environ.get("BEMO_TEST_CODE_ROOT", Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(ROOT))
SOURCE = (ROOT / "systems" / "chat.py").read_text()


def line_for(reason):
    chat = next(n for n in ast.parse(SOURCE).body if isinstance(n, ast.ClassDef) and n.name == "Chat")
    keep = [n for n in chat.body if isinstance(n, ast.FunctionDef) and n.name == "_empty_answer_line"]
    ns = {"logger": type("L", (), {"warning": staticmethod(lambda *a, **k: None)})}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[ast.ClassDef(
        name="Mini", bases=[], keywords=[], body=keep, decorator_list=[])], type_ignores=[])), "m", "exec"), ns)
    m = ns["Mini"]()
    m._last_done_reason, m.active_profile_name = reason, "engineering"
    return m._empty_answer_line()


class EmptyAnswerTests(unittest.TestCase):
    def test_ran_out_of_room(self):
        self.assertIn("too complicated for me to work out in one go", line_for("length"))

    def test_said_nothing_for_another_reason(self):
        self.assertIn("didn't come up with an answer", line_for("stop"))

    def test_the_turn_speaks_it_instead_of_silence(self):
        self.assertIn("full_response = self._empty_answer_line()", SOURCE)
        self.assertIn("self._last_done_reason = None", SOURCE)  # never a stale reason from an earlier turn


if __name__ == "__main__":
    unittest.main()
