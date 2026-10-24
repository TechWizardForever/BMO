import os

# SDL keeps the PC "awake" by resetting X's idle timer when the window opens and every 30 s after, so with
# BMO awake the 4-minute screensaver could never start (seen on the CRT, 2026-10-22). BMO runs his own saver.
os.environ.setdefault("SDL_VIDEO_ALLOW_SCREENSAVER", "1")

import pygame
from pygame import K_ESCAPE, K_F11

import config
from systems.chat import Chat
from systems.display import DisplayManager
from systems.render import FaceRenderer


class App:
    def __init__(self, screen_width, screen_height):
        self._running = True
        self.reference_width = int(screen_width)
        self.reference_height = int(screen_height)
        self.SCREEN_WIDTH = int(screen_width)
        self.SCREEN_HEIGHT = int(screen_height)
        self.fullscreen = False
        self._display = None
        self._screen = None
        self.viewport_rect = pygame.Rect(0, 0, self.SCREEN_WIDTH, self.SCREEN_HEIGHT)
        self.face_renderer = None
        self.chat = None
        self._display_generation = -1
        self.display_manager = DisplayManager(
            config.DISPLAY_SETTINGS_PATH,
            fallback_width=self.reference_width,
            fallback_height=self.reference_height,
            preview_timeout_seconds=config.DISPLAY_PREVIEW_TIMEOUT_SECONDS,
        )

    def _display_flags(self):
        return pygame.FULLSCREEN if self.fullscreen else pygame.NOFRAME

    def _apply_display_state(self, *, force=False):
        generation = self.display_manager.generation
        if not force and generation == self._display_generation:
            return False

        state = self.display_manager.current_state()
        # Keep Bemo borderless and anchored at the upper-left of the selected X11
        # output. A window matching the monitor mode behaves like the appliance
        # surface without asking SDL to perform another mode switch; F11 still
        # provides SDL's true fullscreen mode when desired.
        os.environ.setdefault("SDL_VIDEO_WINDOW_POS", "0,0")
        self._display = pygame.display.set_mode(
            (int(state.width), int(state.height)), self._display_flags()
        )
        pygame.display.set_caption("Bemo / WIB")
        self._display.fill((0, 0, 0))

        if getattr(state, "fit", "app") == "desktop":
            # The whole desktop is already scaled into the safe area by an xrandr
            # transform; BMO fills his window like any other app.
            rect = pygame.Rect(0, 0, int(state.width), int(state.height))
        else:
            rect = pygame.Rect(
                int(state.crop_left),
                int(state.crop_top),
                int(state.viewport_width),
                int(state.viewport_height),
            )
        # DisplayManager already validates this; clamp once more before creating
        # a Pygame subsurface so a malformed hand-edited settings file cannot
        # bring down the renderer.
        rect.clamp_ip(self._display.get_rect())
        rect.width = max(1, min(rect.width, self._display.get_width() - rect.left))
        rect.height = max(1, min(rect.height, self._display.get_height() - rect.top))
        self.viewport_rect = rect
        self._screen = self._display.subsurface(rect)
        self.SCREEN_WIDTH = rect.width
        self.SCREEN_HEIGHT = rect.height
        self._display_generation = generation

        if self.face_renderer is not None:
            self.face_renderer.on_viewport_changed()
        return True

    def prepare_frame(self):
        if self._display is not None:
            # Crop margins are intentionally black. On an overscan-prone CRT or
            # converter they disappear offscreen while Bemo stays in the safe area.
            self._display.fill((0, 0, 0))

    def present(self):
        pygame.display.flip()

    def on_init(self):
        # Approved settings are reapplied on startup when the current monitor
        # supports them. A different monitor safely falls back to its live mode.
        self.display_manager.apply_startup()
        pygame.init()
        self._apply_display_state(force=True)
        self.face_renderer = FaceRenderer(self)
        self.chat = Chat(display_manager=self.display_manager)
        self._running = True
        return True

    def on_render(self):
        self.face_renderer.render()

    def on_event(self, event):
        if self.chat and event.type in (
            pygame.KEYDOWN,
            pygame.KEYUP,
            pygame.MOUSEMOTION,
            pygame.MOUSEBUTTONDOWN,
            pygame.MOUSEBUTTONUP,
        ):
            self.chat.note_user_activity()

        if event.type == pygame.QUIT:
            # A window close, or a stop signal SDL turned into one (logged: live 2026-10-20 BMO quit on his own).
            print("BMO window: quit requested (window closed or stop signal)", flush=True)
            self._running = False
        elif event.type == pygame.KEYDOWN:
            if event.key == K_F11:
                self.fullscreen = not self.fullscreen
                self._apply_display_state(force=True)
            elif event.key == K_ESCAPE:
                print("BMO window: Esc pressed", flush=True)
                self._running = False
            elif event.key == pygame.K_BACKSPACE and self.chat:
                # Stop the current reply only; Bemo keeps running and listening.
                self.chat.cancel_generation()

        if self.face_renderer:
            self.face_renderer.handle_event(event)

    def on_cleanup(self):
        if self.chat:
            self.chat.stop()
        pygame.quit()

    def on_execute(self):
        if not self.on_init():
            return

        self.chat.init()
        try:
            while self._running:
                # Timeout rollback is owned by the UI loop, so it still happens
                # even if the LLM is busy or unavailable.
                self.display_manager.tick()
                self._apply_display_state()
                for event in pygame.event.get():
                    self.on_event(event)
                self.on_render()
        finally:
            self.on_cleanup()
