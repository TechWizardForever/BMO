"""Going to sleep (2026-11-24): BMO says so, his eyes close slowly, Zzz's float up, then the window goes to standby."""
import os
from pathlib import Path
import sys
import time
import unittest

os.environ["SDL_VIDEODRIVER"] = "dummy"
os.environ["SDL_AUDIODRIVER"] = "dummy"
ROOT = Path(os.environ.get("BEMO_TEST_CODE_ROOT", Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
import pygame
import config
import visual_smoke_test as smoke
from systems.render import FaceRenderer

SOURCE = (ROOT / "systems" / "chat.py").read_text()


class SleepAnimationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        pygame.init()

    @classmethod
    def tearDownClass(cls):
        pygame.quit()

    def frame_at(self, renderer, app, seconds):
        app.chat.status["falling_asleep"] = time.monotonic() - seconds
        renderer.render()
        return app._screen.copy()

    def setUp(self):
        self.app = smoke.FakeApp()
        self.app.SCREEN_WIDTH, self.app.SCREEN_HEIGHT = 1024, 768
        self.app._screen = pygame.Surface((1024, 768))
        self.renderer = FaceRenderer(self.app)
        self.renderer._saver_guard = type("Quiet", (), {"hold_off": lambda self: None})()
        self.renderer.render()

    def eye(self, surface):
        rect = self.renderer.LEFT_EYE_OPEN.get_rect(center=self.renderer.LEFT_EYE_POSE)
        return pygame.image.tostring(surface.subsurface(rect), "RGB")

    def zzz_pixels(self, surface):
        r = self.renderer
        area = pygame.Rect(int(r.RIGHT_EYE_POSE[0] + r.RIGHT_EYE_OPEN.get_width() * 0.6), 0,
                           int(surface.get_height() * 0.35), int(r.RIGHT_EYE_POSE[1]))
        background = tuple(r.BACKGROUND_COLOR)
        return sum(surface.get_at((x, y))[:3] != background
                   for x in range(area.left, min(area.right, surface.get_width()), 3) for y in range(area.top, area.bottom, 3))

    def test_the_eyes_close_slowly_then_the_zzzs_come(self):
        frames = {s: self.frame_at(self.renderer, self.app, s) for s in (0.2, 1.0, 3.0, 4.0)}
        self.assertNotEqual(self.eye(frames[0.2]), self.eye(frames[1.0]))  # open, then drowsy
        self.assertNotEqual(self.eye(frames[1.0]), self.eye(frames[3.0]))  # then shut
        self.assertEqual(self.zzz_pixels(frames[1.0]), 0)                  # no Z's while his eyes are still open
        self.assertGreater(self.zzz_pixels(frames[3.0]), 0)
        self.assertGreater(self.zzz_pixels(frames[4.0]), 0)

    def test_the_window_waits_for_the_animation(self):
        branch = SOURCE[SOURCE.index('in set(getattr(config, "VOICE_SLEEP_PHRASES"'):]
        branch = branch[:branch.index("return True")]
        order = [branch.index(s) for s in ("self.speak(config.VOICE_SLEEP_ACKNOWLEDGEMENT)",
                                           "self._falling_asleep_since = time.monotonic()",
                                           "self.stop_event.wait(float(config.SLEEP_ANIMATION_SECONDS))",
                                           "self.supervisor.stop_bemo()")]
        self.assertEqual(order, sorted(order))
        self.assertGreater(config.SLEEP_ANIMATION_SECONDS, FaceRenderer.ZZZ_START + FaceRenderer.ZZZ_EVERY)
        self.assertLessEqual(config.SLEEP_ANIMATION_SECONDS, 6)  # a short goodbye, not a wait
        self.assertIn('status["falling_asleep"] = getattr(self, "_falling_asleep_since", None)', SOURCE)


if __name__ == "__main__":
    unittest.main()
