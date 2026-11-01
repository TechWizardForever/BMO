"""After "go to sleep" the main brain stays loaded for a while (2026-10-31: a wake took 49 s, 36 s of it reloading
the brain and 12 s warming it up). It unloads when its time is up, or at once if memory runs low."""
import os
from pathlib import Path
import sys
import types
import unittest
from unittest import mock

ROOT = Path(os.environ.get("BEMO_TEST_CODE_ROOT", Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(ROOT))
import config
import supervisor


class StandbyBrainTests(unittest.TestCase):
    def setUp(self):
        self.unloads = []
        self.service = types.SimpleNamespace(unload_model=lambda *a: self.unloads.append(1))

    def test_kept_then_unloaded_when_its_time_is_up(self):
        with mock.patch.object(config, "STANDBY_KEEP_BRAIN_MINUTES", 60.0), \
                mock.patch.object(supervisor.time, "monotonic", return_value=1000.0):
            supervisor._enter_standby(self.service)
        self.assertEqual(self.unloads, [])                       # still loaded
        self.assertEqual(self.service.standby_unload_at, 1000.0 + 3600)
        self.assertFalse(supervisor._standby_brain_check(self.service, now=2000.0, free_gb=10.0))
        self.assertTrue(supervisor._standby_brain_check(self.service, now=4601.0, free_gb=10.0))
        self.assertEqual(self.unloads, [1])
        self.assertIsNone(self.service.standby_unload_at)
        self.assertFalse(supervisor._standby_brain_check(self.service, now=9999.0, free_gb=10.0))  # once

    def test_low_memory_unloads_at_once(self):
        with mock.patch.object(config, "STANDBY_KEEP_BRAIN_MINUTES", 60.0):
            supervisor._enter_standby(self.service)
        self.assertTrue(supervisor._standby_brain_check(self.service, free_gb=2.0))
        self.assertEqual(self.unloads, [1])

    def test_zero_minutes_is_the_old_way(self):
        with mock.patch.object(config, "STANDBY_KEEP_BRAIN_MINUTES", 0):
            supervisor._enter_standby(self.service)
        self.assertEqual(self.unloads, [1])

    def test_wired_in(self):
        src = (ROOT / "supervisor.py").read_text()
        self.assertEqual(src.count("_enter_standby(service)"), 2)        # "go to sleep", and Esc
        self.assertIn("_standby_brain_check(service)", src)              # checked while he sleeps
        self.assertIn("service.standby_unload_at = None  # awake again", src)
        self.assertIn("service.standby_unload_at = None  # after a crash loop", src)


if __name__ == "__main__":
    unittest.main()
