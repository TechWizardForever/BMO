"""The Supervisor's crash counting (untouched-systems check, 2026-10-19)."""
import ast
import os
from pathlib import Path
import unittest

ROOT = Path(os.environ.get("BEMO_TEST_CODE_ROOT", Path(__file__).resolve().parents[1]))


def load():
    tree = ast.parse((ROOT / "supervisor.py").read_text())
    keep = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_next_crash_count"
            or isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") == "STABLE_RUN_SECONDS"]
    ns = {}
    exec(compile(ast.Module(body=keep, type_ignores=[]), "supervisor", "exec"), ns)
    return ns["_next_crash_count"]


class CrashCountTests(unittest.TestCase):
    def test_quick_repeats_are_a_crash_loop(self):
        count = load()
        restarts = 0
        for _ in range(4):
            restarts = count(restarts, ran_seconds=5)
        self.assertEqual(restarts, 4)  # over the limit of 3: back to standby

    def test_crashes_days_apart_always_restart(self):
        count = load()
        restarts = 0
        for _ in range(10):
            restarts = count(restarts, ran_seconds=86_400)
        self.assertEqual(restarts, 1)

    def test_every_launch_records_its_start(self):
        src = (ROOT / "supervisor.py").read_text()
        self.assertEqual(src.count("child = _launch_bemo_child("), src.count("child_started = time.monotonic()") - 1)


if __name__ == "__main__":
    unittest.main()
