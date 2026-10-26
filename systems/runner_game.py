"""The game while BMO swaps brains (2026-10-25, in place of Data Catch): BMO runs, you jump and duck.

One button jumps (hold it for a higher jump, tap it for a hop), a second one ducks, like Chrome's dinosaur.
A brain swap takes about the same minute every time, so the game is about how far you get in it: every
level is faster, and new obstacles come in (bugs to duck under from level 2, bugs to stay under from level
3). The best score and level are kept in config.RUNNER_SCORES_PATH. Plain Python, so the tests need no
screen; systems/render.py draws it.

Sizes are in play heights (the height of the game's box), so it plays the same on the CRT and a wide screen.
"""
from __future__ import annotations

import json
import os
import random
from dataclasses import dataclass
from pathlib import Path

READY, RUNNING, OVER = "ready", "running", "over"

GRAVITY = 3.2        # play heights per second per second
JUMP_SPEED = 1.45    # about a third of the box high, 0.9 s in the air
CUT_JUMP = 0.45      # letting go early keeps this much of the upward speed: a hop
FAST_FALL = 6.0      # ducking in the air drops faster
BASE_SPEED = 0.95    # play heights per second at level 1
LEVEL_SPEEDUP = 0.12
MAX_SPEED = 2.6
LEVEL_POINTS = 200   # about 20 s at level 1
POINTS_PER_HEIGHT = 10.0

RUNNER_X, RUNNER_W, RUNNER_H, DUCK_W, DUCK_H = 0.30, 0.09, 0.15, 0.12, 0.08
FORGIVE = 0.15       # hit boxes are this much smaller than they look

# kind: width, height, height above the ground, first level it appears in
OBSTACLES = {
    "block": (0.06, 0.09, 0.0, 1),
    "tall": (0.055, 0.15, 0.0, 1),
    "wide": (0.15, 0.075, 0.0, 2),
    "low_bug": (0.08, 0.06, 0.105, 2),    # duck under it (or jump it)
    "pair": (0.13, 0.10, 0.0, 3),
    "high_bug": (0.08, 0.06, 0.20, 3),    # stay on the ground: a jump runs into it
}


@dataclass
class Obstacle:
    kind: str
    x: float       # left edge, from the box's left
    y: float       # bottom, above the ground
    w: float
    h: float


def _overlap(a, b) -> bool:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return ax < bx + bw and bx < ax + aw and ay < by + bh and by < ay + ah


class RunnerGame:
    def __init__(self, scores_path: Path | None = None, rng: random.Random | None = None, width: float = 1.6):
        self.scores_path = Path(scores_path) if scores_path else None
        self.rng = rng or random.Random()
        self.width = float(width)          # the box's width in play heights
        self.best, self.best_level = self._load()
        self.new_best = False
        self.reset()

    # --- saved best ---------------------------------------------------------------------------------
    def _load(self) -> tuple[int, int]:
        try:
            data = json.loads(self.scores_path.read_text(encoding="utf-8")) if self.scores_path else {}
            return max(0, int(data.get("best", 0))), max(1, int(data.get("best_level", 1)))
        except (OSError, ValueError, TypeError, AttributeError):
            return 0, 1

    def _save(self) -> None:
        if not self.scores_path:
            return
        try:
            self.scores_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.scores_path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"best": self.best, "best_level": self.best_level}) + "\n", encoding="utf-8")
            os.replace(tmp, self.scores_path)
        except OSError:
            pass

    # --- a run --------------------------------------------------------------------------------------
    def reset(self) -> None:
        self.state = READY
        self.y = self.vy = 0.0
        self.jump_held = self.ducking = False
        self.distance = self.score = 0.0
        self.level = 1
        self.level_shown_at = None   # distance when the level banner went up
        self.obstacles: list[Obstacle] = []
        self.next_gap = 0.9
        self.over_for = 0.0          # seconds since the crash: a jump still held doesn't restart at once

    @property
    def speed(self) -> float:
        return min(MAX_SPEED, BASE_SPEED * (1 + LEVEL_SPEEDUP * (self.level - 1)))

    @property
    def on_ground(self) -> bool:
        return self.y <= 0.0

    def press_jump(self) -> None:
        if self.state == OVER and self.over_for < 0.5:
            return
        if self.state != RUNNING:
            self.reset()
            self.state = RUNNING
            self.new_best = False
            return
        self.jump_held = True
        if self.on_ground and not self.ducking:
            self.vy = JUMP_SPEED

    def release_jump(self) -> None:
        self.jump_held = False
        if self.vy > 0:
            self.vy *= CUT_JUMP

    def set_duck(self, down: bool) -> None:
        self.ducking = bool(down) and self.state == RUNNING

    def hitbox(self) -> tuple[float, float, float, float]:
        w, h = (DUCK_W, DUCK_H) if self.ducking and self.on_ground else (RUNNER_W, RUNNER_H)
        inset_w, inset_h = w * FORGIVE / 2, h * FORGIVE / 2
        return (RUNNER_X + inset_w, self.y + inset_h, w - 2 * inset_w, h - 2 * inset_h)

    def update(self, dt: float) -> None:
        dt = max(0.0, min(0.05, float(dt)))
        if self.state == OVER:
            self.over_for += dt
        if self.state != RUNNING:
            return
        # BMO
        if not self.on_ground or self.vy > 0:
            self.vy -= (GRAVITY + (FAST_FALL if self.ducking else 0.0)) * dt
            self.y += self.vy * dt
            if self.y <= 0.0:
                self.y, self.vy = 0.0, 0.0
        # The world goes by
        step = self.speed * dt
        self.distance += step
        self.score += step * POINTS_PER_HEIGHT
        level = 1 + int(self.score // LEVEL_POINTS)
        if level != self.level:
            self.level, self.level_shown_at = level, self.distance
        for obstacle in self.obstacles:
            obstacle.x -= step
        self.obstacles = [o for o in self.obstacles if o.x + o.w > -0.2]
        self._spawn()
        mine = self.hitbox()
        for o in self.obstacles:
            inset = o.w * FORGIVE / 2
            if _overlap(mine, (o.x + inset, o.y + o.h * FORGIVE / 2, o.w - 2 * inset, o.h * (1 - FORGIVE))):
                self._end()
                return

    def _spawn(self) -> None:
        last_right = max((o.x + o.w for o in self.obstacles), default=-1.0)
        if self.width - last_right < self.next_gap:
            return
        kinds = [k for k, spec in OBSTACLES.items() if spec[3] <= self.level]
        kind = self.rng.choice(kinds)
        w, h, above, _first = OBSTACLES[kind]
        self.obstacles.append(Obstacle(kind, self.width + 0.02, above, w, h))
        # The gap after it, in seconds of running: shorter at higher levels, but always long enough to land
        # from one jump and take off for the next.
        seconds = max(0.85, 1.25 - 0.06 * (self.level - 1)) + self.rng.uniform(0.0, 0.9)
        self.next_gap = seconds * self.speed

    def _end(self) -> None:
        self.state = OVER
        self.jump_held = self.ducking = False
        self.finish()

    def finish(self) -> None:
        """A run ended (a crash, or the brain is ready): keep it if it's the best."""
        score = int(self.score)
        if score > self.best:
            self.best, self.new_best = score, True
            self.best_level = max(self.best_level, self.level)
            self._save()
        elif self.level > self.best_level:
            self.best_level = self.level
            self._save()
