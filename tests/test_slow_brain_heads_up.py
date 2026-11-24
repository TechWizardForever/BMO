"""A brain that thinks before it talks says so when it takes over (2026-11-10: the engineering brain was silent
for 2 to 13 minutes on long questions), and points to Main for quick ones."""
import os
from pathlib import Path
import sys
import unittest

ROOT = Path(os.environ.get("BEMO_TEST_CODE_ROOT", Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(ROOT))
import config


class SlowBrainHeadsUpTests(unittest.TestCase):
    def test_only_the_thinking_brain_warns(self):
        self.assertEqual(set(config.SLOW_BRAIN_HEADS_UP), {"engineering"})
        said = config.SLOW_BRAIN_HEADS_UP["engineering"]
        self.assertIn("a few minutes", said)
        self.assertIn("switch to main", said)

    def test_said_right_after_the_ready_line(self):
        chat = (ROOT / "systems" / "chat.py").read_text()
        ready = chat.index('self.speak(f"{self._profile_spoken_name(profile_name)} here. I\'m loaded and ready.")')
        heads_up = chat.index('heads_up = getattr(config, "SLOW_BRAIN_HEADS_UP", {}).get(profile_name)')
        self.assertLess(ready, heads_up)
        self.assertLess(heads_up - ready, 200)

    def test_switch_to_main_is_a_real_command(self):
        chat = (ROOT / "systems" / "chat.py").read_text()
        self.assertIn('"switched to "', chat)  # the switch phrases the heads-up points to
        self.assertIn("main", config.MODEL_PROFILES[config.MAIN_MODEL_PROFILE]["aliases"])


if __name__ == "__main__":
    unittest.main()
