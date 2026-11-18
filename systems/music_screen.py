"""What the CRT shows while music plays (2026-11-17): pixel-art music notes bouncing around a black screen, each one
changing colour when it hits an edge. Always moving, so nothing burns in. BMO's window draws them (systems/render.py)
whenever the music is on and he isn't listening, thinking or talking; going to sleep stops the music.
"""
from __future__ import annotations

import math
import random

# One character per pixel: "#" the note's colour, "+" a highlight, "." nothing.
NOTES = {
    "eighth": (
        ".....#...",
        ".....##..",
        ".....#+#.",
        ".....#..#",
        ".....#..#",
        ".....#.#.",
        ".....#...",
        ".....#...",
        ".....#...",
        ".....#...",
        "..####...",
        ".#+###...",
        "######...",
        "#####....",
        ".###.....",
    ),
    "quarter": (
        ".....#",
        ".....#",
        ".....#",
        ".....#",
        ".....#",
        ".....#",
        ".....#",
        ".....#",
        ".....#",
        ".....#",
        "..####",
        ".#+###",
        "######",
        "#####.",
        ".###..",
    ),
    "pair": (
        ".....#########",
        ".....#########",
        ".....#.......#",
        ".....#.......#",
        ".....#.......#",
        ".....#.......#",
        ".....#.......#",
        ".....#.......#",
        ".....#.......#",
        ".....#.......#",
        "..####....####",
        ".#+###...#+###",
        "######..######",
        "#####...#####.",
        ".###.....###..",
    ),
    "sixteenth": (
        ".....#...",
        ".....##..",
        ".....#+#.",
        ".....#..#",
        ".....##.#",
        ".....#+#.",
        ".....#..#",
        ".....#..#",
        ".....#.#.",
        ".....#...",
        "..####...",
        ".#+###...",
        "######...",
        "#####....",
        ".###.....",
    ),
}
COLORS = ((255, 85, 120), (255, 160, 60), (255, 230, 90), (110, 240, 120), (90, 220, 255), (110, 140, 255),
          (200, 110, 255), (255, 120, 220))
COUNT = 9
SPEED = 0.16  # of the screen's height per second


class Notes:
    """The bouncing notes. update() moves them; draw() puts them on a surface of any size."""

    def __init__(self, count: int = COUNT, seed: int | None = None):
        self._random = random.Random(seed)
        self._count = count
        self._notes: list[dict] = []
        self._size = None
        self._sprites: dict = {}

    def _place(self, w: int, h: int) -> None:
        self._notes = []
        kinds = list(NOTES)
        for k in range(self._count):
            angle = self._random.uniform(0.35, 1.2) * self._random.choice((1, -1))
            speed = SPEED * h * self._random.uniform(0.75, 1.3)
            self._notes.append({
                "kind": kinds[k % len(kinds)], "big": k % 3 == 0, "color": k % len(COLORS),
                "x": self._random.uniform(0.1, 0.8) * w, "y": self._random.uniform(0.1, 0.75) * h,
                "dx": speed * self._random.choice((1, -1)) * abs(math.cos(angle)),
                "dy": speed * (1 if angle > 0 else -1) * abs(math.sin(angle)),
            })
        self._size = (w, h)

    def pixel(self, h: int, big: bool) -> int:
        base = max(2, h // 96)
        return base + base // 2 if big else base

    def sprite(self, kind: str, color: int, pixel: int):
        """The note drawn at `pixel` screen pixels per art pixel, kept between frames."""
        import pygame
        key = (kind, color, pixel)
        if key not in self._sprites:
            art = NOTES[kind]
            main = COLORS[color]
            light = tuple(min(255, c + 90) for c in main)
            small = pygame.Surface((len(art[0]), len(art)), pygame.SRCALPHA)
            for y, row in enumerate(art):
                for x, ch in enumerate(row):
                    if ch != ".":
                        small.set_at((x, y), light if ch == "+" else main)
            self._sprites[key] = pygame.transform.scale(small, (small.get_width() * pixel, small.get_height() * pixel))
        return self._sprites[key]

    def update(self, dt: float, w: int, h: int) -> None:
        if self._size != (w, h):
            self._place(w, h)
            return
        dt = max(0.0, min(0.1, dt))
        for note in self._notes:
            pixel = self.pixel(h, note["big"])
            art = NOTES[note["kind"]]
            nw, nh = len(art[0]) * pixel, len(art) * pixel
            note["x"] += note["dx"] * dt
            note["y"] += note["dy"] * dt
            bounced = False
            if note["x"] < 0 or note["x"] + nw > w:
                note["dx"] = abs(note["dx"]) if note["x"] < 0 else -abs(note["dx"])
                note["x"] = min(max(note["x"], 0), max(0, w - nw))
                bounced = True
            if note["y"] < 0 or note["y"] + nh > h:
                note["dy"] = abs(note["dy"]) if note["y"] < 0 else -abs(note["dy"])
                note["y"] = min(max(note["y"], 0), max(0, h - nh))
                bounced = True
            if bounced:  # a new colour at every edge
                note["color"] = (note["color"] + self._random.randint(1, len(COLORS) - 1)) % len(COLORS)

    def draw(self, surface) -> None:
        w, h = surface.get_size()
        if self._size != (w, h):
            self._place(w, h)
        surface.fill((0, 0, 0))
        for note in self._notes:
            surface.blit(self.sprite(note["kind"], note["color"], self.pixel(h, note["big"])),
                         (int(note["x"]), int(note["y"])))
