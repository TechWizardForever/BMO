"""The mouse pointer stays off BMO's face until it's used (owner 2026-11-01)."""
import os
from pathlib import Path
import sys
import unittest

ROOT = Path(os.environ.get("BEMO_TEST_CODE_ROOT", Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(ROOT))
from systems.pointer import HIDE_AFTER_SECONDS, SETTLE_SECONDS, PointerHider


class PointerTests(unittest.TestCase):
    def setUp(self):
        self.now, self.shown = 100.0, []
        self.pointer = PointerHider(self.shown.append, clock=lambda: self.now)

    def settle(self):
        self.now += SETTLE_SECONDS + 0.1

    def test_hidden_from_the_start(self):
        self.assertEqual(self.shown, [False])

    def test_the_window_opening_is_not_a_move(self):
        self.pointer.moved()
        self.pointer.tick()
        self.assertEqual(self.shown, [False])

    def test_moving_shows_it_then_being_still_hides_it(self):
        self.settle()
        self.pointer.moved()
        self.assertEqual(self.shown, [False, True])
        self.now += HIDE_AFTER_SECONDS - 0.1
        self.pointer.tick()
        self.assertEqual(self.shown, [False, True])
        self.now += 0.2
        self.pointer.tick()
        self.assertEqual(self.shown, [False, True, False])

    def test_still_moving_keeps_it(self):
        self.settle()
        for _ in range(4):
            self.pointer.moved()
            self.now += HIDE_AFTER_SECONDS - 0.5
            self.pointer.tick()
        self.assertEqual(self.shown, [False, True])

    def test_wired_in(self):
        src = (ROOT / "app.py").read_text()
        self.assertIn("PointerHider(pygame.mouse.set_visible)", src)
        self.assertIn("self._pointer.moved()", src)
        self.assertIn("self._pointer.tick()", src)


if __name__ == "__main__":
    unittest.main()
