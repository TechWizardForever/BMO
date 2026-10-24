"""Rules in Main's instructions that came from live findings."""
import os
from pathlib import Path
import sys
import unittest

ROOT = Path(os.environ.get("BEMO_TEST_CODE_ROOT", Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(ROOT))
import config

MAIN = config.MODEL_PROFILES[config.MAIN_MODEL_PROFILE]["system_prompt"]


class MainRulesTests(unittest.TestCase):
    def test_no_invented_actions_in_greetings(self):
        # 2026-10-20: greetings like "Just finished calibrating the toaster oven" are to stop.
        self.assertIn("Never invent things you just did, even in greetings.", MAIN)

    def test_hardware_diagnostics_still_never_for_health(self):
        self.assertIn("never for bodies, injuries, health, or figures of speech", MAIN)

    def test_still_within_the_budget(self):
        self.assertLess(len(MAIN), 2200)


if __name__ == "__main__":
    unittest.main()
