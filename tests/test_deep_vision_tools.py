"""Deep Vision gets no tools (checklist B3, measured 2026-10-19).

Ollama answered HTTP 400 "internvl3.5-deep:30b does not support tools" whenever a tool was offered,
so every Deep Vision turn with tools failed; without tools it read a test image correctly in 47 s.
"""
import os
from pathlib import Path
import sys
import unittest

ROOT = Path(os.environ.get("BEMO_TEST_CODE_ROOT", Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(ROOT))
import config


class DeepVisionToolsTests(unittest.TestCase):
    def test_profile_says_no_tools(self):
        self.assertIs(config.MODEL_PROFILES["deep-vision"].get("tools_supported"), False)
        others = [name for name, p in config.MODEL_PROFILES.items() if p.get("tools_supported") is False]
        self.assertEqual(others, ["deep-vision"])

    def test_tool_selection_honours_it(self):
        src = (ROOT / "systems" / "chat.py").read_text()
        body = src[src.index("    def _tool_schemas_for_active_profile"):src.index("    def _execute_tool(")]
        guard = body.index('if not self.active_profile.get("tools_supported", True):')
        self.assertLess(guard, body.rindex("return selected"))
        self.assertIn("selected = []", body[guard:guard + 200])


if __name__ == "__main__":
    unittest.main()
