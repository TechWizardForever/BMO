"""BMO Jump's look (2026-11-17): green plains under a bright sky, hills moving at two speeds, boulders and birds to
get past, all pixel art, at 60 frames a second."""
import os
from pathlib import Path
import sys
import unittest

os.environ["SDL_VIDEODRIVER"] = "dummy"
os.environ["SDL_AUDIODRIVER"] = "dummy"
ROOT = Path(os.environ.get("BEMO_TEST_CODE_ROOT", Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
import pygame
import visual_smoke_test as smoke
from systems import runner_game as runner
from systems import runner_world as world
from systems.render import FaceRenderer


class TheLook(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        pygame.init()

    def test_the_sprites_are_whole(self):
        shapes = {"block": (8, 12), "tall": (12, 20), "wide": (16, 8), "bug": (8, 6)}  # the shapes' proportions
        for shape, frames in world.OBSTACLE_ART.items():
            for rows in frames:
                self.assertEqual(len({len(row) for row in rows}), 1, shape)
                self.assertLessEqual(set("".join(rows)) - {"."}, set(world.COLORS), shape)
                self.assertEqual((len(rows[0]), len(rows)), shapes[shape], shape)
        for kind in runner.OBSTACLES:
            self.assertTrue(world.obstacle_frame(kind, 1.0), kind)

    def test_the_same_plains_at_every_level(self):
        app = smoke.FakeApp()
        app.SCREEN_WIDTH, app.SCREEN_HEIGHT = 640, 480
        app._screen = pygame.Surface((640, 480))
        renderer = FaceRenderer(app)
        play = pygame.Rect(64, 154, 512, 269)
        renderer._runner_scenery(play, play.bottom - 38, 309, 1.0, 5.0)
        made = renderer._runner_plains
        renderer._runner_scenery(play, play.bottom - 38, 309, 9.0, 6.0)
        self.assertIs(renderer._runner_plains, made)  # drawn once, then reused
        self.assertGreater(app._screen.get_at((play.centerx, play.bottom - 10))[1], 100)  # green underfoot

    def test_smoother_while_playing(self):
        src = (ROOT / "systems" / "render.py").read_text()
        self.assertIn("self.GAME_FPS = 60", src)
        self.assertIn("self.GAME_FPS if switch_visible", src)

    def test_every_size_and_every_moment(self):
        for size in ((640, 480), (1024, 768), (1920, 1080)):
            app = smoke.FakeApp()
            app.SCREEN_WIDTH, app.SCREEN_HEIGHT = size
            app._screen = pygame.Surface(size)
            renderer = FaceRenderer(app)
            game = renderer._runner = runner.RunnerGame(None)
            renderer._seen_switch_generation, renderer._game_last_update = 0, 0.0
            status = {"model_switch_active": True, "model_switch_generation": 0, "model_switch_from": "normal",
                      "model_switch_to": "coding", "model_switch_started": 0.0}
            renderer._render_model_switch(status, 0.1)  # ready to run
            game.press_jump()
            game.obstacles = [runner.Obstacle(kind, 0.5 + n * 0.3, h, w, ht)
                              for n, (kind, (w, ht, h, _)) in enumerate(runner.OBSTACLES.items())]
            for frame in range(1, 40):
                renderer._render_model_switch(status, 0.1 + frame / 30)
            game.state = runner.OVER
            renderer._render_model_switch(status, 2.0)

    def test_the_ground_moves_with_the_game(self):
        app = smoke.FakeApp()
        app.SCREEN_WIDTH, app.SCREEN_HEIGHT = 1024, 768
        app._screen = pygame.Surface((1024, 768))
        renderer = FaceRenderer(app)
        play = pygame.Rect(100, 240, 820, 430)
        shots = []
        for distance in (0.0, 0.5):
            renderer._runner_scenery(play, play.bottom - 60, 500, distance, 1.0)
            shots.append(pygame.image.tobytes(app._screen.subsurface(pygame.Rect(100, 500, 820, 170)), "RGB"))
        self.assertNotEqual(shots[0], shots[1])


if __name__ == "__main__":
    unittest.main()
