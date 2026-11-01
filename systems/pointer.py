"""The mouse pointer over BMO's face (owner 2026-11-01: it sat in the middle of his face on boot). Hidden until the
mouse really moves, and hidden again once it has been still a few seconds."""
from __future__ import annotations

import time

HIDE_AFTER_SECONDS = 3.0
SETTLE_SECONDS = 2.0  # the window opening under a still mouse reports it as a move; that doesn't count


class PointerHider:
    def __init__(self, set_visible, clock=time.monotonic):
        self._set_visible, self._clock = set_visible, clock
        self._ignore_until = clock() + SETTLE_SECONDS
        self._hide_at = None
        set_visible(False)

    def moved(self) -> None:
        now = self._clock()
        if now < self._ignore_until:
            return
        if self._hide_at is None:
            self._set_visible(True)
        self._hide_at = now + HIDE_AFTER_SECONDS

    def tick(self) -> None:
        if self._hide_at is not None and self._clock() >= self._hide_at:
            self._hide_at = None
            self._set_visible(False)
