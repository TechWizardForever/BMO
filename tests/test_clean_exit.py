"""Closing BMO's window while he speaks is a clean exit, not a crash (live 2026-10-20: exit -6, and the
Supervisor started him again)."""
import os
from pathlib import Path
import subprocess
import sys
import textwrap
import unittest

ROOT = Path(os.environ.get("BEMO_TEST_CODE_ROOT", Path(__file__).resolve().parents[1]))


class CleanExitTests(unittest.TestCase):
    def test_the_entry_point_leaves_without_thread_teardown_and_keeps_the_exit_code(self):
        text = (ROOT / "__init__.py").read_text()
        self.assertIn("os._exit(exit_code)", text)
        self.assertIn("exit_code = 1", text)

    def test_a_busy_background_thread_does_not_change_the_exit_code(self):
        # The same pattern as __init__.py with a daemon thread still working.
        script = textwrap.dedent("""
            import os, sys, threading, time
            threading.Thread(target=lambda: time.sleep(30), daemon=True).start()
            exit_code = int(sys.argv[1])
            sys.stdout.flush()
            os._exit(exit_code)
        """)
        for code in (0, 1):
            done = subprocess.run([sys.executable, "-c", script, str(code)], timeout=20)
            self.assertEqual(done.returncode, code)


if __name__ == "__main__":
    unittest.main()
