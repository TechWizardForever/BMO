"""The brain-swap runner game (2026-10-25): jumping, ducking, levels, the best score, and that every level can
be beaten (a simple bot plays it)."""
import json
import os
from pathlib import Path
import random
import sys
import tempfile
import unittest

ROOT = Path(os.environ.get("BEMO_TEST_CODE_ROOT", Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(ROOT))
from systems import runner_game as rg


def ticks(game, seconds, dt=1 / 60, each=None):
    for _ in range(int(seconds / dt)):
        if each:
            each(game)
        game.update(dt)


def bot(game):
    """Jumps what's on the ground, ducks the low bugs, stays down for the high ones; lets go at the top."""
    ahead = sorted((o for o in game.obstacles if o.x + o.w > rg.RUNNER_X), key=lambda o: o.x)
    duck = False
    if ahead:
        o = ahead[0]
        gap = o.x - (rg.RUNNER_X + rg.RUNNER_W)
        if o.kind == "low_bug":
            duck = gap < 0.25
        elif o.kind != "high_bug" and gap < game.speed * 0.16 and game.on_ground:
            game.press_jump()
    if game.vy <= 0 and game.jump_held:
        game.release_jump()
    game.set_duck(duck)


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.path = Path(tempfile.mkdtemp()) / "runner-game.json"

    def game(self, seed=1):
        return rg.RunnerGame(self.path, rng=random.Random(seed))

    def test_jump_space_starts_and_a_held_jump_goes_higher_than_a_tap(self):
        game = self.game()
        self.assertEqual(game.state, rg.READY)
        game.press_jump()
        self.assertEqual(game.state, rg.RUNNING)
        game.obstacles = []
        game.next_gap = 99
        game.press_jump()
        top = 0.0
        for _ in range(120):
            game.update(1 / 60)
            top = max(top, game.y)
        self.assertGreater(top, 0.30)
        self.assertEqual(game.y, 0.0)  # back on the ground
        game.press_jump()
        game.update(1 / 60)
        game.release_jump()
        hop = 0.0
        for _ in range(120):
            game.update(1 / 60)
            hop = max(hop, game.y)
        self.assertLess(hop, top / 2)

    def test_ducking_gets_under_a_low_bug_and_standing_does_not(self):
        for duck, survives in ((True, True), (False, False)):
            game = self.game()
            game.press_jump()
            game.next_gap = 99
            w, h, above, _ = rg.OBSTACLES["low_bug"]
            game.obstacles = [rg.Obstacle("low_bug", 0.6, above, w, h)]
            game.set_duck(duck)
            ticks(game, 1.0)
            self.assertEqual(game.state == rg.RUNNING, survives, f"ducking={duck}")

    def test_a_jump_into_a_high_bug_ends_the_run(self):
        game = self.game()
        game.press_jump()
        game.next_gap = 99
        w, h, above, _ = rg.OBSTACLES["high_bug"]
        game.obstacles = [rg.Obstacle("high_bug", 0.55, above, w, h)]
        ticks(game, 0.1)
        game.press_jump()
        ticks(game, 1.0)
        self.assertEqual(game.state, rg.OVER)

    def test_levels_get_faster_and_bring_new_obstacles(self):
        game = self.game()
        game.press_jump()
        self.assertEqual(game.level, 1)
        kinds_at_1 = {k for k, spec in rg.OBSTACLES.items() if spec[3] <= 1}
        self.assertNotIn("low_bug", kinds_at_1)
        game.score = rg.LEVEL_POINTS * 2 + 1
        game.obstacles, game.next_gap = [], 99
        game.update(1 / 60)
        self.assertEqual(game.level, 3)
        self.assertAlmostEqual(game.speed, rg.BASE_SPEED * (1 + 2 * rg.LEVEL_SPEEDUP))
        self.assertIsNotNone(game.level_shown_at)  # the "LEVEL 3!" banner
        game.level = 99
        self.assertEqual(game.speed, rg.MAX_SPEED)

    def test_every_level_can_be_beaten(self):
        for seed in range(6):
            game = rg.RunnerGame(None, rng=random.Random(seed))
            game.press_jump()
            ticks(game, 150, each=bot)
            self.assertEqual(game.state, rg.RUNNING, f"seed {seed}: the bot crashed at level {game.level}")
            self.assertGreaterEqual(game.level, 10)

    def test_the_best_score_is_kept_and_a_new_run_starts_from_zero(self):
        game = self.game()
        game.press_jump()
        game.score, game.level = 420.0, 3
        game.finish()  # the brain was ready mid-run
        self.assertTrue(game.new_best)
        self.assertEqual(json.loads(self.path.read_text()), {"best": 420, "best_level": 3})
        again = rg.RunnerGame(self.path)
        self.assertEqual((again.best, again.best_level), (420, 3))
        again.press_jump()
        again.score = 100
        again.finish()
        self.assertFalse(again.new_best)
        self.assertEqual(rg.RunnerGame(self.path).best, 420)

    def test_a_broken_score_file_starts_fresh(self):
        self.path.write_text("{oops")
        self.assertEqual(rg.RunnerGame(self.path).best, 0)

    def test_after_a_crash_a_held_jump_does_not_restart_at_once(self):
        game = self.game()
        game.press_jump()
        game.next_gap = 99
        w, h, above, _ = rg.OBSTACLES["tall"]
        game.obstacles = [rg.Obstacle("tall", 0.42, above, w, h)]
        ticks(game, 0.3)
        self.assertEqual(game.state, rg.OVER)
        game.press_jump()
        self.assertEqual(game.state, rg.OVER)
        ticks(game, 0.6)
        game.press_jump()
        self.assertEqual((game.state, game.score), (rg.RUNNING, 0.0))


if __name__ == "__main__":
    unittest.main()
