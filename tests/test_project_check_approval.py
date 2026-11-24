"""The coding brain asks before a project's tests run (review 2026-11-24): new files are saved without asking,
and pytest or npm test would run them, so a check that runs code needs a spoken yes."""
import ast
import os
from pathlib import Path
import re
import sys
import time
import types
import unittest

ROOT = Path(os.environ.get("BEMO_TEST_CODE_ROOT", Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(ROOT))
import config

SOURCE = (ROOT / "systems" / "chat.py").read_text()


class FakeProjects:
    active_slug = "robot-arm"

    def __init__(self):
        self.ran = []

    def _read_metadata(self, slug):
        return {"name": "Robot Arm"}

    def run_project_check(self, kind, timeout):
        self.ran.append(kind)
        return "2 passed"


def mini(answers):
    chat = next(n for n in ast.parse(SOURCE).body if isinstance(n, ast.ClassDef) and n.name == "Chat")
    keep = [n for n in chat.body if isinstance(n, ast.FunctionDef)
            and n.name in ("_execute_project_tool", "_project_check_approved")]
    ns = {"re": re, "time": time, "config": config,
          "logger": types.SimpleNamespace(exception=lambda *a, **k: None)}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[ast.ClassDef(
        name="Mini", bases=[], keywords=[], body=keep, decorator_list=[])], type_ignores=[])), "m", "exec"), ns)
    chat = ns["Mini"]()
    chat.project_manager = FakeProjects()
    chat.asked = []

    def confirm(question, **kwargs):
        chat.asked.append(question)
        return answers.pop(0)
    chat._confirm_by_voice = confirm
    return chat


class ProjectCheckApprovalTests(unittest.TestCase):
    def test_no_means_the_tests_do_not_run(self):
        chat = mini([False])
        reply = chat._execute_project_tool("run_project_check", {"kind": "pytest"})
        self.assertTrue(reply.startswith("Not run"))
        self.assertEqual(chat.project_manager.ran, [])
        self.assertIn("Robot Arm", chat.asked[0])
        self.assertIn("Hey BMO, yes", chat.asked[0])

    def test_one_yes_covers_the_task(self):
        chat = mini([True])
        self.assertEqual(chat._execute_project_tool("run_project_check", {"kind": "pytest"}), "2 passed")
        self.assertEqual(chat._execute_project_tool("run_project_check", {"kind": "pytest"}), "2 passed")
        self.assertEqual(len(chat.asked), 1)

    def test_the_yes_expires_and_belongs_to_one_project(self):
        chat = mini([True, False, False])
        chat._execute_project_tool("run_project_check", {"kind": "pytest"})
        chat._project_check_ok["until"] = time.monotonic() - 1
        self.assertTrue(chat._execute_project_tool("run_project_check", {"kind": "pytest"}).startswith("Not run"))
        chat._project_check_ok = {"slug": "other-project", "until": time.monotonic() + 600}
        self.assertTrue(chat._execute_project_tool("run_project_check", {"kind": "npm-test"}).startswith("Not run"))
        self.assertEqual(len(chat.asked), 3)

    def test_compiling_runs_no_code_and_needs_no_yes(self):
        chat = mini([])
        self.assertEqual(chat._execute_project_tool("run_project_check", {"kind": "python-compile"}), "2 passed")
        self.assertEqual(chat.asked, [])


if __name__ == "__main__":
    unittest.main()
