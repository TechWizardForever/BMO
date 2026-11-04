"""Woken from sleep with the brain still loaded, he doesn't announce a boot (2026-11-01)."""
import os
from pathlib import Path
import sys
import unittest
from unittest import mock

ROOT = Path(os.environ.get("BEMO_TEST_CODE_ROOT", Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(ROOT))
import config
from systems.chat import Chat


class QuickWakeTests(unittest.TestCase):
    """Woken with the brain loaded he doesn't say he's booting (2026-11-01)."""
    def woke(self, standby, loaded):
        c = Chat.__new__(Chat)
        c._booted_from_standby_wake = standby
        model = config.MODEL_PROFILES[config.MAIN_MODEL_PROFILE]["model"]
        with mock.patch("systems.chat.ollama.ps", return_value={"models": [{"model": model}] if loaded else []}):
            return c._woke_with_brain_loaded()

    def test_only_a_wake_with_the_brain_loaded(self):
        self.assertTrue(self.woke(True, True))
        self.assertFalse(self.woke(True, False))
        self.assertFalse(self.woke(False, True))

    def test_trouble_asking_gives_the_full_greeting(self):
        c = Chat.__new__(Chat)
        c._booted_from_standby_wake = True
        with mock.patch("systems.chat.ollama.ps", side_effect=RuntimeError("no")):
            self.assertFalse(c._woke_with_brain_loaded())

    def test_the_short_lines_dont_mention_booting(self):
        for line in (config.VOICE_QUICK_WAKE_ANNOUNCEMENT, config.VOICE_QUICK_WAKE_READY_ANNOUNCEMENT):
            self.assertNotIn("boot", line.lower())
            self.assertNotIn("loading", line.lower())


if __name__ == "__main__":
    unittest.main()
