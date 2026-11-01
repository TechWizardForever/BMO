"""Every request to a brain sends the same load settings (2026-11-01 on the 5700U: a request with a smaller context
reloaded the brain in 21 s and the next one reloaded it again in 24 s; one without use_mmap reloaded it in 35 s)."""
import os
from pathlib import Path
import re
import sys
import unittest

ROOT = Path(os.environ.get("BEMO_TEST_CODE_ROOT", Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(ROOT))
import config


class LoadSettingTests(unittest.TestCase):
    def test_mapping_is_on_by_default(self):
        self.assertTrue(config.OLLAMA_USE_MMAP)

    def test_bmo_sends_it(self):
        src = (ROOT / "systems" / "chat.py").read_text()
        self.assertIn('"use_mmap": bool(config.OLLAMA_USE_MMAP)', src)
        self.assertNotIn("min(self._effective_context(), 4096)", src)  # the classifier uses the brain's own

    def test_the_supervisor_preload_sends_it(self):
        src = (ROOT / "supervisor.py").read_text()
        start = src.index("def _preload_ollama_model")
        body = src[start:src.index("\n    def ", start + 10)]
        options = re.findall(r"options=\{(.*?)\}", body, re.S)
        self.assertEqual(len(options), 2)
        for block in options:
            self.assertIn("use_mmap", block)
            self.assertIn("num_ctx", block)
            self.assertIn("num_thread", block)


if __name__ == "__main__":
    unittest.main()
