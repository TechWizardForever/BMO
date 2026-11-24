"""The computer-actions log says what BMO did, never what a file or program contained (review 2026-11-24:
a file read and three commands had left up to 2,000 characters of their output in it)."""
import os
from pathlib import Path
import sys
import unittest

ROOT = Path(os.environ.get("BEMO_TEST_CODE_ROOT", Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(ROOT))
from supervisor import SupervisorService


class ActionLogPrivacyTests(unittest.TestCase):
    def test_results_keep_sizes_not_text(self):
        result = {"ok": True, "path": "/home/pat/notes.txt", "returncode": 0,
                  "lines": ["secret line one", "secret line two"], "stdout": "private output",
                  "stderr": "x" * 5000, "entries": [{"name": "diary.txt"}], "text": "typed words"}
        clean = SupervisorService._safe_result_for_log(result)
        logged = repr(clean)
        for private in ("secret line", "private output", "diary.txt", "typed words", "xxxx"):
            self.assertNotIn(private, logged)
        self.assertEqual(clean["lines"], "[2 items]")
        self.assertEqual(clean["stdout"], "[redacted 14 chars]")
        self.assertEqual(clean["stderr"], "[redacted 5000 chars]")
        self.assertEqual((clean["ok"], clean["path"], clean["returncode"]), (True, "/home/pat/notes.txt", 0))

    def test_payload_text_was_already_hidden(self):
        clean = SupervisorService._safe_payload_for_log({"action": "type", "text": "hello there"})
        self.assertNotIn("hello there", repr(clean))


if __name__ == "__main__":
    unittest.main()
