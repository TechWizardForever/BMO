#!/usr/bin/env python3
"""Headless smoke test for Bemo's CRT saver and model-switch loader."""
from __future__ import annotations

import os
import time

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame

import config
from systems.chat import State
from systems.render import FaceRenderer


class FakeChat:
    def __init__(self):
        self.state = State.READY
        self.count = 0.0
        self.xf_list = []
        self.yf_list = []
        self.rms_list = []
        self.status = {
            "idle_seconds": 0.0,
            "model_switch_generation": 0,
            "model_switch_active": False,
            "model_switch_from": "MAIN",
            "model_switch_to": "ENGINEERING",
            "model_switch_started": 0.0,
            "model_switch_finished": 0.0,
            "model_switch_success": None,
        }

    def visual_status(self):
        return dict(self.status)


class FakeApp:
    def __init__(self):
        self.SCREEN_WIDTH = 640
        self.SCREEN_HEIGHT = 480
        self._screen = pygame.display.set_mode((self.SCREEN_WIDTH, self.SCREEN_HEIGHT))
        self.chat = FakeChat()


def main():
    pygame.init()
    app = FakeApp()
    renderer = FaceRenderer(app)

    renderer.render()  # normal face

    app.chat.status["idle_seconds"] = float(config.CRT_SCREENSAVER_IDLE_SECONDS) + 1.0
    for _ in range(3):
        renderer.render()  # CRT saver

    now = time.monotonic()
    app.chat.status.update(
        {
            "idle_seconds": 0.0,
            "model_switch_generation": 1,
            "model_switch_active": True,
            "model_switch_started": now,
        }
    )
    renderer.handle_event(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_RIGHT))
    for _ in range(4):
        renderer.render()  # loading / mini-game
    renderer.handle_event(pygame.event.Event(pygame.KEYUP, key=pygame.K_RIGHT))

    pygame.quit()
    print("Bemo CRT/model-loader visual smoke test: PASS")


if __name__ == "__main__":
    main()
