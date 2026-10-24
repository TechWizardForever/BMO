"""A file action on a path that isn't there says nothing was done, with the closest names (live 2026-10-20:
"delete trashtest.txt in the Bemo code folder" -> wrong path -> "it was already gone. Clean sweep!")."""
import ast
import os
from pathlib import Path
import re
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(os.environ.get("BEMO_TEST_CODE_ROOT", Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(ROOT))
import config
from systems.computer import ComputerController, ComputerControlError

SOURCE = (ROOT / "systems" / "chat.py").read_text()


def mini():
    chat = next(n for n in ast.parse(SOURCE).body if isinstance(n, ast.ClassDef) and n.name == "Chat")
    keep = [n for n in chat.body if (isinstance(n, ast.FunctionDef) and n.name in ("_missing_path_reply", "_similar_paths"))
            or (isinstance(n, ast.Assign) and any(getattr(t, "id", "") == "_PATH_ACTIONS" for t in n.targets))]
    ns = {"re": re, "Path": Path, "logger": type("L", (), {"info": staticmethod(lambda *a, **k: None)})}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[ast.ClassDef(
        name="Mini", bases=[], keywords=[], body=keep, decorator_list=[])], type_ignores=[])), "m", "exec"), ns)
    return ns["Mini"]()


class MissingPathTests(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        (self.home / "BMO-code").mkdir()
        (self.home / "BMO-code" / "trash-test.txt").write_text("x")
        (self.home / "Documents").mkdir()
        (self.home / "Documents" / "unrelated.txt").write_text("x")

    def test_the_owners_words_find_the_real_file_and_nothing_is_done(self):
        with mock.patch("pathlib.Path.home", return_value=self.home):
            reply = mini()._missing_path_reply("delete", {"path": str(self.home / "Bemo_code" / "trashtest.txt")})
        self.assertIn("doesn't exist, so nothing was deleted", reply)
        self.assertIn(str(self.home / "BMO-code" / "trash-test.txt"), reply)
        self.assertIn("Never say it was done or already gone.", reply)
        self.assertNotIn("unrelated", reply)

    def test_existing_paths_and_other_actions_pass_through(self):
        m = mini()
        self.assertIsNone(m._missing_path_reply("delete", {"path": str(self.home / "BMO-code" / "trash-test.txt")}))
        self.assertIsNone(m._missing_path_reply("write_text", {"path": str(self.home / "new.txt")}))
        with mock.patch("pathlib.Path.home", return_value=self.home):
            reply = m._missing_path_reply("read_text", {"path": str(self.home / "nothing-like-it.pdf")})
        self.assertIn("Ask the owner where the file is.", reply)

    def test_it_runs_before_the_supervisor_is_asked(self):
        self.assertLess(SOURCE.index("missing = self._missing_path_reply(action, payload)"),
                        SOURCE.index("result = self.supervisor.computer_action("))

    def test_a_plain_yes_is_left_for_main_when_no_display_test_waits(self):
        # Live 2026-10-20: Main asked "Did you mean ~/BMO-code/trash-test.txt?", the owner said "yes", and the
        # display command answered "There isn't a display test waiting for approval."
        branch = SOURCE[SOURCE.index("# Approval/rejection phrases that name the display"):]
        branch = branch[:branch.index("return True")]
        self.assertIn('re.search(r"\\b(?:display|screen)\\b", normalized)', branch)

    def test_a_short_answer_keeps_the_tools_of_the_question(self):
        # Live 2026-10-20: "Yes" to "Did you mean …?" got no tools; Main said "Deleted …" without deleting.
        chat = next(n for n in ast.parse(SOURCE).body if isinstance(n, ast.ClassDef) and n.name == "Chat")
        pattern = next(n for n in chat.body if isinstance(n, ast.Assign)
                       and any(getattr(t, "id", "") == "_SHORT_ANSWER" for t in n.targets))
        short = eval(compile(ast.Expression(pattern.value), "p", "eval"), {"re": re})
        for said in ("yes", "yes.", "yeah do it", "do it", "that one", "the first one", "go ahead"):
            self.assertTrue(short.match(said), said)
        for said in ("what's the weather", "open firefox", "yesterday was fun"):
            self.assertFalse(short.match(said), said)
        self.assertIn("allowed |= set(last[1])", SOURCE)
        self.assertIn("self._last_turn_tools = (time.monotonic()", SOURCE)

    def test_claims_of_file_actions_without_a_tool_are_caught(self):
        from systems.grounding import HONEST_REPLIES, fabricated_live_claim
        for said in ("Deleted `/home/bemo/BMO-code/trash-test.txt`. No more trash.",
                     "File not found — it was already gone. Clean sweep!", "I moved the file to Documents."):
            self.assertEqual(fabricated_live_claim(said), "undone", said)
        for said in ("Saved snake_game.py in ~/BMO-code.", "Your file is in the trash folder.",
                     "Wood glue is removed with warm water."):
            self.assertIsNone(fabricated_live_claim(said), said)
        self.assertIn("didn't actually do that", HONEST_REPLIES["undone"])

    def test_deleting_a_missing_file_is_not_a_success(self):
        cc = ComputerController(screenshot_dir=tempfile.mkdtemp(), guarded_paths=())
        with self.assertRaises(ComputerControlError):
            cc.delete({"path": str(self.home / "gone.txt")})


if __name__ == "__main__":
    unittest.main()
