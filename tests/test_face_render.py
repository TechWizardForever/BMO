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
import json
import tempfile
import pygame
import config
import visual_smoke_test as smoke
from systems import first_boot as fb
from systems import runner_game as runner
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


    def test_the_install_screen_comes_first_and_frames_the_screen_for_the_last_question(self):
        app = smoke.FakeApp()
        renderer = FaceRenderer(app)
        face = self.frame(renderer, app)
        view = fb.SetupView(blocking=False)
        view.open()
        view.row("internet", fb.DONE, "connected")
        view.row("qwen3-main:30b", fb.RUNNING, "41%", 0.41)
        app.chat.status["setup"] = view.snapshot()
        app.chat.status["idle_seconds"] = float(config.CRT_SCREENSAVER_IDLE_SECONDS) + 1.0  # no saver over it
        installing = self.frame(renderer, app)
        self.assertNotEqual(installing, face)
        self.assertIsNone(renderer._saver_started)
        self.assertNotEqual(tuple(app._screen.get_at((1, 1)))[:3], (255, 255, 255))
        view.frame = True  # "can you see the whole white frame?"
        view.show_problems(["Microphone: I couldn't hear you."])
        app.chat.status["setup"] = view.snapshot()
        self.frame(renderer, app)
        w, h = app.SCREEN_WIDTH, app.SCREEN_HEIGHT
        for corner in ((1, 1), (w - 2, 1), (1, h - 2), (w - 2, h - 2)):
            self.assertEqual(tuple(app._screen.get_at(corner))[:3], (255, 255, 255), corner)

    def test_the_brain_swap_game_plays_and_keeps_a_best_score_when_the_brain_is_ready(self):
        old = config.RUNNER_SCORES_PATH
        config.RUNNER_SCORES_PATH = Path(tempfile.mkdtemp()) / "runner-game.json"
        try:
            app = smoke.FakeApp()
            renderer = FaceRenderer(app)
            app.chat.status.update({"model_switch_generation": 1, "model_switch_active": True,
                                    "model_switch_started": time.monotonic()})
            self.frame(renderer, app, times=1)
            renderer.handle_event(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_SPACE))
            self.assertEqual(renderer._runner.state, runner.RUNNING)
            renderer.handle_event(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_DOWN))
            self.assertTrue(renderer._runner.ducking)
            playing = self.frame(renderer, app, times=2)
            renderer._runner.score = 77.0
            app.chat.status.update({"model_switch_active": False, "model_switch_started": time.monotonic() - 10,
                                    "model_switch_finished": time.monotonic()})
            self.assertNotEqual(self.frame(renderer, app, times=1), playing)  # back to the face
            self.assertEqual(json.loads(config.RUNNER_SCORES_PATH.read_text())["best"], 77)
            score = renderer._runner.score
            renderer.handle_event(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_SPACE))
            self.assertEqual(renderer._runner.score, score)  # Space is nothing to the game under BMO's face
        finally:
            config.RUNNER_SCORES_PATH = old

    def test_the_runner_bmo_has_arms_his_yellow_and_red_buttons_and_a_crash_face(self):
        app = smoke.FakeApp()
        renderer = FaceRenderer(app)
        game = runner.RunnerGame(None)
        rect = pygame.Rect(200, 150, 56, 93)
        legs = int(rect.height * 0.18)

        def draw(state, y=0.0):
            app._screen.fill((0, 0, 0))
            game.state, game.y = state, y
            renderer._draw_little_bmo(rect, game, False)
            return pygame.image.tostring(app._screen, "RGB")

        draw(runner.RUNNING)
        colours = {tuple(app._screen.get_at((x, y)))[:3] for x in range(rect.left, rect.right)
                   for y in range(rect.top, rect.bottom - legs)}
        self.assertIn(renderer._BMO_COLORS["Y"], colours)
        self.assertIn(renderer._BMO_COLORS["R"], colours)
        arms = [x for x in range(rect.left - 30, rect.left) for y in range(rect.top, rect.bottom)
                if tuple(app._screen.get_at((x, y)))[:3] == renderer._BMO_LIMBS]
        self.assertTrue(arms, "an arm outside his body")
        running = draw(runner.RUNNING)
        self.assertNotEqual(draw(runner.OVER), running)  # the flat crash mouth
        self.assertNotEqual(draw(runner.RUNNING, y=0.2), draw(runner.RUNNING))  # arms up mid-jump

if __name__ == "__main__":
    unittest.main()
