"""The world of BMO Jump (2026-11-17): green plains under a bright sky, with rolling hills moving at two speeds and
clouds drifting over. All pixel art, drawn on a small grid and scaled up so it stays crisp on the CRT;
systems/render.py puts it on the screen.

The obstacles keep the game's shapes (systems/runner_game.py OBSTACLES): "block" small on the ground, "tall", "wide",
"low_bug" to duck under and "high_bug" to stay under.
"""
from __future__ import annotations

import math
import random

import pygame

COLORS = {
    "h": (222, 220, 212), "a": (178, 176, 170), "m": (138, 136, 134), "d": (98, 96, 100), "D": (64, 62, 70),  # rock
    "k": (44, 60, 86),  # bird
}

# Boulders on the ground and birds in the air, in the shapes' proportions (block 8x12, tall 12x20, wide 16x8, bird 8x6).
OBSTACLE_ART = {
    "block": (("...aa...", "..aham..", ".aahamm.", ".aaammd.", "aaammmmd", "ahammmdd", "aaammmdd", "aammmddd",
               "ammmmddd", "ammmdddD", ".mmddddD", "..DDDDD."),),
    "tall": (("...aa....m..", "..ahamm.mmd.", "..ahammmmdd.", ".aahammmDdd.", ".ahaammmdDdd", "aaaammmmddDd",
              "ahaaDmmmdddd", "aaaamDmmmddd", "aahaammDmddd", "aaaammmmDddD", ".aaammmmdddD", ".ahammDmmddd",
              "aaaamDmmmddd", "ahaammmmdddD", "aaaammmmDddd", "aahammmmdDdD", "aaammmmmdddD", "aammmmmddddD",
              "ammmmmdddddD", ".DDDDDDDDDD."),),
    "wide": (("....aaaammm.....", "..aahaammmmdd...", ".aahhaammmmmddd.", "aaahaaammmmmmddd", "aaaaammmmmmddddd",
              "aaammmmmmmdddddD", ".mmmmmmddddddDD.", "..DDDDDDDDDDDD.."),),
    "bug": (("k......k", "kk....kk", ".kk..kk.", "..kkkk..", "...kk...", "........"),
            ("........", "...kk...", "..kkkk..", ".kk..kk.", "kk....kk", "k......k")),
}


def obstacle_frame(kind: str, now: float):
    """The art for one obstacle at this moment."""
    shape = "bug" if kind.endswith("bug") else ("block" if kind == "pair" else kind)
    frames = OBSTACLE_ART[shape]
    return frames[int(now * (8.0 if shape == "bug" else 5.0)) % len(frames)]


def sprite(cache: dict, rows, size):
    """The rows drawn at `size` pixels, kept between frames."""
    key = (rows, size)
    if key not in cache:
        grid = pygame.Surface((len(rows[0]), len(rows)), pygame.SRCALPHA)
        for r, row in enumerate(rows):
            for c, cell in enumerate(row):
                if cell != ".":
                    grid.set_at((c, r), (*COLORS[cell], 255))
        if len(cache) > 64:
            cache.clear()
        cache[key] = pygame.transform.scale(grid, (max(1, size[0]), max(1, size[1])))
    return cache[key]


# --- The scenery, on a small grid ------------------------------------------------------------------------

SKY = ((92, 166, 236), (116, 186, 242), (146, 206, 246), (182, 224, 250), (218, 240, 248))
_BAYER = ((0, 8, 2, 10), (12, 4, 14, 6), (3, 11, 1, 9), (15, 7, 13, 5))
_LIGHT = (-0.55, -0.65, 0.52)  # from the upper left, towards the viewer


def _tone(x, y, value, tones):
    """`value` from 0 (darkest) to 1 (lightest) as one of `tones`, dithered where it falls between two."""
    v = max(0.0, min(0.999, value)) * (len(tones) - 1)
    low = int(v)
    return tones[min(len(tones) - 1, low + (1 if v - low > (_BAYER[y % 4][x % 4] + 0.5) / 16 else 0))]


def _billows(surface, circles, tones):
    """Round puffs (clouds, bushes): each circle (x, y, r) lit as a ball; where they overlap the nearest shows."""
    w, h = surface.get_size()
    for y in range(max(0, int(min(c[1] - c[2] for c in circles))), min(h, int(max(c[1] + c[2] for c in circles)) + 2)):
        for x in range(max(0, int(min(c[0] - c[2] for c in circles))), min(w, int(max(c[0] + c[2] for c in circles)) + 2)):
            best = None
            for cx, cy, r in circles:
                dx, dy = (x + 0.5 - cx) / r, (y + 0.5 - cy) / r
                if dx * dx + dy * dy < 1.0:
                    z = math.sqrt(1.0 - dx * dx - dy * dy)
                    if best is None or z * r > best[0]:
                        best = (z * r, dx, dy, z)
            if best:
                _, nx, ny, nz = best
                lum = 0.25 + 0.75 * max(0.0, nx * _LIGHT[0] + ny * _LIGHT[1] + nz * _LIGHT[2])
                surface.set_at((x, y), _tone(x, y, lum, tones))


def _hills(width, rows, horizon, tones, top, wobble, seed):
    """Rolling hills that repeat across `width`, lightest along their tops."""
    layer = pygame.Surface((width, rows), pygame.SRCALPHA)
    rng = random.Random(seed)
    phases = [rng.uniform(0, 6.28) for _ in range(3)]
    heights = []
    for x in range(width):
        t = x / width * 2 * math.pi
        y = int(horizon - top - sum(math.sin(t * k + p) / k for k, p in zip((2, 3, 5), phases)) * wobble)
        heights.append(y)
        for yy in range(y, rows):
            layer.set_at((x, yy), _tone(x, yy, 1.0 - (yy - y) / max(1.0, horizon - y + 6), tones))
    return layer, heights, rng


def layers(size, ground: int) -> dict:
    """The sky, the clouds and the two layers of hills for a play area of `size` with the ground `ground` pixels down,
    scaled up, and the pixel size."""
    width, height = size
    pixel = max(2, height // 110)
    cols, rows, horizon = width // pixel + 1, height // pixel + 1, max(8, ground // pixel)
    sky = pygame.Surface((cols, rows))
    for y in range(rows):
        for x in range(cols):
            sky.set_at((x, y), _tone(x, y, min(1.0, y / max(1, horizon)), SKY))
    sun = (int(cols * 0.14), int(horizon * 0.2))
    for r, color in ((9, (196, 228, 248)), (7, (255, 242, 176)), (5, (255, 251, 220))):
        pygame.draw.circle(sky, color, sun, r)

    clouds = pygame.Surface((cols * 2, rows), pygame.SRCALPHA)
    rng = random.Random(4)
    for k in range(6):
        x0, y0, size_ = (k + 0.3) * cols / 3, horizon * rng.uniform(0.1, 0.36), rng.uniform(0.7, 1.15)
        _billows(clouds, [(x0 + dx * size_, y0 + dy * size_, r * size_) for dx, dy, r in
                          ((0, 0, 5), (6, -2, 6), (12, 0, 5), (17, 1, 4), (-5, 1, 4), (6, 2, 5))],
                 ((190, 216, 240), (222, 236, 248), (244, 249, 253), (255, 255, 255)))

    far, heights, rng = _hills(cols * 2, rows, horizon, ((104, 164, 150), (126, 184, 158), (150, 202, 166)),
                               horizon * 0.3, horizon * 0.08, 3)
    for x in range(6, cols * 2 - 6, 9):  # far-off trees along the hilltops
        if rng.random() < 0.55:
            _billows(far, [(x, heights[x], 2.4), (x + 2.5, heights[x] + 0.5, 2)], ((70, 130, 106), (92, 154, 118), (120, 176, 130)))
    near, heights, rng = _hills(cols * 2, rows, horizon, ((54, 142, 70), (78, 170, 80), (108, 196, 92), (144, 218, 108)),
                                horizon * 0.12, horizon * 0.05, 8)
    for x in range(5, cols * 2 - 5, 3):
        roll, y = rng.random(), heights[x] + rng.randint(1, max(2, horizon - heights[x]))
        if roll < 0.35:  # grass
            near.set_at((x, y), (170, 230, 120))
            near.set_at((x, y - 1), (120, 204, 98))
        elif roll < 0.46:  # flowers
            near.set_at((x, y), rng.choice(((255, 255, 255), (255, 224, 90), (250, 140, 180))))
    made = {"sky": sky, "clouds": clouds, "far": far, "near": near}
    out = {name: pygame.transform.scale(layer, (layer.get_width() * pixel, layer.get_height() * pixel))
           for name, layer in made.items()}
    out["pixel"] = pixel
    return out


def birds(screen, play, pixel: int, now: float) -> None:
    for k in range(3):
        x = play.left + ((now * (14 + 5 * k) + k * 170) % (play.width + 40)) - 20
        y = play.top + play.height * (0.2 + 0.07 * k) + math.sin(now * 2 + k) * pixel * 2
        wing = pixel if int(now * 4 + k) % 2 else -pixel
        pygame.draw.line(screen, (44, 60, 86), (x - pixel * 2, y + wing), (x, y), max(1, pixel // 2 + 1))
        pygame.draw.line(screen, (44, 60, 86), (x, y), (x + pixel * 2, y + wing), max(1, pixel // 2 + 1))


def ground(screen, play, ground_y: int, pixel: int, scroll: int) -> None:
    """The grass BMO runs on, with tufts, darker patches and flowers going by."""
    top, edge, body, patch = (132, 220, 106), (84, 178, 84), (62, 150, 72), (50, 128, 64)
    screen.fill(body, pygame.Rect(play.left, ground_y, play.width, play.bottom - ground_y))
    screen.fill(top, pygame.Rect(play.left, ground_y - pixel, play.width, pixel * 2))
    screen.fill(edge, pygame.Rect(play.left, ground_y + pixel, play.width, pixel))
    step = pixel * 7
    for x in range(play.left - scroll % step, play.right, step):
        n = ((x + scroll) // step) * 2654435761 % 97
        screen.fill(top, pygame.Rect(x, ground_y - pixel * 2, pixel, pixel))
        if n % 3 == 0:
            screen.fill(patch, pygame.Rect(x + pixel * (n % 5), ground_y + pixel * (3 + n % 9), pixel * 3, pixel))
        if n % 5 == 0:
            screen.fill(edge, pygame.Rect(x + pixel * 4, ground_y + pixel * (4 + n % 6), pixel, pixel))
        if n % 7 == 0:
            screen.fill(((255, 255, 255), (255, 224, 90), (250, 140, 180))[n % 3],
                        pygame.Rect(x + pixel * 3, ground_y - pixel * 2, pixel, pixel))
