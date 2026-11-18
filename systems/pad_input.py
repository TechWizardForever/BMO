"""Lists and the carousels by controller (2026-11-17): left and right (or up and down) move, A picks, B closes, Y
shuffles the music.
The d-pad, or the left stick, which counts once per push so holding it doesn't race through the list."""
from __future__ import annotations

import pygame

PUSH, LET_GO = 0.6, 0.3  # how far the stick goes before it counts, and back before it can count again
SELECT, BACK, SHUFFLE = 0, 1, 3  # SDL's usual numbers for A, B and Y


class PadNavigator:
    def __init__(self):
        self._pushed: dict[tuple, bool] = {}

    def action(self, event) -> str | None:
        """'next', 'previous', 'select', 'back', 'shuffle' or None."""
        if event.type == pygame.JOYHATMOTION:
            x, y = event.value
            if x:
                return "next" if x > 0 else "previous"
            if y:
                return "previous" if y > 0 else "next"
            return None
        if event.type == pygame.JOYAXISMOTION and event.axis in (0, 1):
            key = (getattr(event, "instance_id", 0), event.axis)
            if abs(event.value) < LET_GO:
                self._pushed[key] = False
                return None
            if abs(event.value) >= PUSH and not self._pushed.get(key):
                self._pushed[key] = True
                return "next" if event.value > 0 else "previous"
            return None
        if event.type == pygame.JOYBUTTONDOWN:
            return {SELECT: "select", BACK: "back", SHUFFLE: "shuffle"}.get(event.button)
        return None
