"""The face, the CRT saver and the model-swap loader render headless (untouched-systems check, 2026-10-19).

No overlays on BMO's face (a listening dot was tried and dropped, 2026-10-18): with no sound playing, every
state must draw exactly the same face.
"""
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
from systems.chat import State
from systems.render import FaceRenderer


class FaceRenderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        pygame.init()

    @classmethod
    def tearDownClass(cls):
        pygame.quit()

    def frame(self, renderer, app, times=3):
        for _ in range(times):
            renderer.render()
        return pygame.image.tostring(app._screen, "RGB")

    def test_no_overlays_in_any_state(self):
        app = smoke.FakeApp()
        renderer = FaceRenderer(app)
        frames = {}
        for state in State:
            app.chat.state = state
            frames[state.name] = self.frame(renderer, app)
        first = frames["READY"]
        for name, pixels in frames.items():
            self.assertEqual(pixels, first, f"the face in state {name} differs from READY (an overlay?)")

    def test_saver_moves_and_loader_draws(self):
        app = smoke.FakeApp()
        renderer = FaceRenderer(app)
        # Nothing on this desktop (a video in a browser) may hold the saver off in a test.
        renderer._saver_guard = type("QuietGuard", (), {"hold_off": lambda self: None})()
        face = self.frame(renderer, app)
        app.chat.status["idle_seconds"] = float(config.CRT_SCREENSAVER_IDLE_SECONDS) + 1.0
        saver_a = self.frame(renderer, app)
        time.sleep(0.5)
        saver_b = self.frame(renderer, app, times=20)
        self.assertNotEqual(saver_a, face)
        self.assertNotEqual(saver_a, saver_b, "the saver must move (no burn-in on the CRT)")
        app.chat.status.update({"idle_seconds": 0.0, "model_switch_generation": 1, "model_switch_active": True,
                                "model_switch_started": time.monotonic()})
        loader = self.frame(renderer, app)
        self.assertNotEqual(loader, face)

    def test_a_running_windows_saver_is_not_closed_by_its_own_full_screen_window(self):
        # Live 2026-10-23: the guard was asked again while the Wine saver ran, saw the saver itself
        # full screen and closed it within 5 s.
        class Wine:
            running, stops = False, 0
            def tick(self, now=None):
                self.running = True
                return {"running": True, "user_exit": False, "current": "Underwater.scr"}
            def stop(self):
                self.running, self.stops = False, self.stops + 1
        class Guard:
            def hold_off(self):
                return "full screen: underwater.scr" if wine.running else None
        app = smoke.FakeApp()
        wine = Wine()
        app.chat.windows_screensaver = wine
        renderer = FaceRenderer(app)
        renderer._saver_guard = Guard()
        app.chat.status["idle_seconds"] = float(config.CRT_SCREENSAVER_IDLE_SECONDS) + 1.0
        old = config.ENABLE_WINDOWS_SCR_SCREENSAVERS
        config.ENABLE_WINDOWS_SCR_SCREENSAVERS = True
        try:
            self.frame(renderer, app, times=10)
        finally:
            config.ENABLE_WINDOWS_SCR_SCREENSAVERS = old
        self.assertTrue(wine.running)
        self.assertEqual(wine.stops, 0)

    def test_static_refresh_timing(self):
        renderer = FaceRenderer(smoke.FakeApp())
        renderer._reset_crt_static_refresh(1000.0)
        interval = float(config.CRT_STATIC_REFRESH_INTERVAL_SECONDS)
        duration = float(config.CRT_STATIC_REFRESH_DURATION_SECONDS)
        self.assertFalse(renderer._crt_static_refresh_active(1000.0 + interval - 1, allowed=True))
        self.assertTrue(renderer._crt_static_refresh_active(1000.0 + interval, allowed=True))
        self.assertTrue(renderer._crt_static_refresh_active(1000.0 + interval + duration - 0.5, allowed=True))
        self.assertFalse(renderer._crt_static_refresh_active(1000.0 + interval + duration + 0.1, allowed=True))
        # Not while BMO is busy (or anything else that disallows it), and it stops at once.
        self.assertTrue(renderer._crt_static_refresh_active(5000.0, allowed=True))
        self.assertFalse(renderer._crt_static_refresh_active(5000.5, allowed=False))
        self.assertFalse(renderer._crt_static_refresh_active(5001.0, allowed=True))


if __name__ == "__main__":
    unittest.main()
