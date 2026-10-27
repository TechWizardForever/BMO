import math
from pathlib import Path
import random as rand
import time

import pygame

import config
from systems.chat import State
from systems import runner_game as runner


def ui_font(size, bold=False):
    """The lettering on BMO's own screens (setup, brain swap, parts, calibration, text mode): config.UI_FONT,
    a font file in BMO's assets or the first of a list of installed fonts' names, at config.UI_FONT_SCALE
    (pixel fonts run larger or smaller than ordinary ones)."""
    name = str(getattr(config, "UI_FONT", "DejaVu Sans"))
    size = max(6, int(round(size * float(getattr(config, "UI_FONT_SCALE", 1.0)))))
    if name.lower().endswith((".ttf", ".otf")):
        path = Path(name) if Path(name).is_absolute() else Path(__file__).resolve().parents[1] / name
        if path.is_file():
            return pygame.font.Font(str(path), size)  # no made-up bold on a pixel font
        name = "DejaVu Sans"
    return pygame.font.SysFont(name, size, bold=bold)


class FaceRenderer:
    def __init__(self, app):
        self._app = app
        self.i = 0
        self.mouth_frame_change_timer = time.time() + 0.1
        self.blink_timer = time.time() + 2
        self.frame_timer = time.monotonic()
        self.blinking = False
        self.fpsclock = pygame.time.Clock()
        self.FPS = config.FPS

        self.LEVER_FLIP = pygame.mixer.Sound("./lever_flip.mp3")
        self.LEVER_FLIP_BACK = pygame.mixer.Sound("./lever_flip_back.mp3")
        self.COMIC_SANS = pygame.font.SysFont("Comic Sans MS", 30)
        self.SMALL_FONT = ui_font(18)
        self.MEDIUM_FONT = ui_font(26, bold=True)
        self.BACKGROUND_COLOR = tuple(getattr(config, "FACE_BACKGROUND_COLOR", (163, 230, 202)))

        # ------------------------------------------------------------------
        # Original animation sprite structure preserved.
        # ------------------------------------------------------------------
        self._BASE_MOUTH_FRAMES = [
            pygame.transform.scale_by(pygame.image.load("./sprites/BMP.png"), 2),
            pygame.transform.scale_by(pygame.image.load("./sprites/Mouth_Small.png"), 2),
            pygame.transform.scale_by(pygame.image.load("./sprites/AE.png"), 2),
            pygame.transform.scale_by(pygame.image.load("./sprites/CD_GK_NS_TX_YZ.png"), 2),
            pygame.transform.scale_by(pygame.image.load("./sprites/F.png"), 2),
            pygame.transform.scale_by(pygame.image.load("./sprites/L.png"), 2),
            pygame.transform.scale_by(pygame.image.load("./sprites/O.png"), 2),
            pygame.transform.scale_by(pygame.image.load("./sprites/QW.png"), 2),
            pygame.transform.scale_by(pygame.image.load("./sprites/R.png"), 2),
            pygame.transform.scale_by(pygame.image.load("./sprites/TH.png"), 2),
        ]
        self._BASE_LEFT_EYE_OPEN = pygame.transform.scale_by(pygame.image.load("./sprites/Left_Eye_Open.png"), 2)
        self._BASE_LEFT_EYE_CLOSED = pygame.transform.scale_by(pygame.image.load("./sprites/Left_Eye_Closed.png"), 2)
        self._BASE_LEFT_EYE_HALF_CLOSED = pygame.transform.scale_by(pygame.image.load("./sprites/Eye_Half_Open_Left.png"), 2)
        self._BASE_RIGHT_EYE_OPEN = pygame.transform.scale_by(pygame.image.load("./sprites/Right_Eye_Open.png"), 2)
        self._BASE_RIGHT_EYE_CLOSED = pygame.transform.scale_by(pygame.image.load("./sprites/Right_Eye_Closed.png"), 2)
        self._BASE_RIGHT_EYE_HALF_CLOSED = pygame.transform.scale_by(pygame.image.load("./sprites/Eye_Half_Open_Right.png"), 2)
        self.MOUTH_FRAMES = list(self._BASE_MOUTH_FRAMES)
        self.LEFT_EYE_OPEN = self._BASE_LEFT_EYE_OPEN
        self.LEFT_EYE_CLOSED = self._BASE_LEFT_EYE_CLOSED
        self.LEFT_EYE_HALF_CLOSED = self._BASE_LEFT_EYE_HALF_CLOSED
        self.RIGHT_EYE_OPEN = self._BASE_RIGHT_EYE_OPEN
        self.RIGHT_EYE_CLOSED = self._BASE_RIGHT_EYE_CLOSED
        self.RIGHT_EYE_HALF_CLOSED = self._BASE_RIGHT_EYE_HALF_CLOSED
        self.current_mouth_frame = 1
        self.MOUTH_POSE = (0, 0)
        self.LEFT_EYE_POSE = (0, 0)
        self.RIGHT_EYE_POSE = (0, 0)
        self._face_scale = 1.0
        self._update_face_layout()

        # ------------------------------------------------------------------
        # CRT-safe idle animation state.
        # ------------------------------------------------------------------
        self._saver_started = None
        self._saver_mode_started = time.monotonic()
        self._saver_mode_index = 0
        self._stars = []
        self._rain = []
        self._bouncer = {
            "x": self._app.SCREEN_WIDTH * 0.35,
            "y": self._app.SCREEN_HEIGHT * 0.30,
            "vx": 95.0,
            "vy": 72.0,
        }
        self._reset_saver_particles()

        # Brief periodic full-screen CRT static refresh while the normal face is
        # otherwise sitting still. Frames are prebuilt once so the effect costs
        # very little CPU while Bemo is running.
        self._crt_static_started = None
        self._crt_static_last_ended = time.monotonic()
        self._crt_static_frames = self._build_crt_static_frames()

        # ------------------------------------------------------------------
        # Model-switch loading / runner game state (systems/runner_game.py).
        # ------------------------------------------------------------------
        self._seen_switch_generation = -1
        self._game_last_update = time.monotonic()
        self._runner = None
        self._switch_was_visible = False

    def _scaled_surface(self, surface, scale):
        if abs(scale - 1.0) < 0.001:
            return surface
        width = max(1, int(round(surface.get_width() * scale)))
        height = max(1, int(round(surface.get_height() * scale)))
        return pygame.transform.smoothscale(surface, (width, height))

    def _update_face_layout(self):
        # SCREEN_WIDTH/HEIGHT are the current safe viewport, not a fixed monitor
        # requirement. Scale the original 1280x800 composition to fit any mode.
        ref_w = max(1.0, float(getattr(config, "SCREEN_WIDTH", 1280)))
        ref_h = max(1.0, float(getattr(config, "SCREEN_HEIGHT", 800)))
        scale = min(self._app.SCREEN_WIDTH / ref_w, self._app.SCREEN_HEIGHT / ref_h)
        scale = max(0.25, min(2.5, float(scale)))
        self._face_scale = scale
        self.MOUTH_FRAMES = [self._scaled_surface(item, scale) for item in self._BASE_MOUTH_FRAMES]
        self.LEFT_EYE_OPEN = self._scaled_surface(self._BASE_LEFT_EYE_OPEN, scale)
        self.LEFT_EYE_CLOSED = self._scaled_surface(self._BASE_LEFT_EYE_CLOSED, scale)
        self.LEFT_EYE_HALF_CLOSED = self._scaled_surface(self._BASE_LEFT_EYE_HALF_CLOSED, scale)
        self.RIGHT_EYE_OPEN = self._scaled_surface(self._BASE_RIGHT_EYE_OPEN, scale)
        self.RIGHT_EYE_CLOSED = self._scaled_surface(self._BASE_RIGHT_EYE_CLOSED, scale)
        self.RIGHT_EYE_HALF_CLOSED = self._scaled_surface(self._BASE_RIGHT_EYE_HALF_CLOSED, scale)
        cx = self._app.SCREEN_WIDTH / 2.0
        cy = self._app.SCREEN_HEIGHT / 2.0
        self.MOUTH_POSE = (cx, cy + 130.0 * scale)
        self.LEFT_EYE_POSE = (cx - 220.0 * scale, cy - 80.0 * scale)
        self.RIGHT_EYE_POSE = (cx + 220.0 * scale, cy - 80.0 * scale)

    def on_viewport_changed(self):
        self._update_face_layout()
        self._reset_saver_particles()
        if hasattr(self, "_bouncer"):
            self._bouncer["x"] = min(self._bouncer.get("x", 0), max(0, self._app.SCREEN_WIDTH - 100))
            self._bouncer["y"] = min(self._bouncer.get("y", 0), max(0, self._app.SCREEN_HEIGHT - 80))

    # ----------------------------------------------------------------------
    # Input used only by the brain-swap runner game. App owns wake/quit/F11.
    # One button jumps (Space, Up, W, a click, pad A), one ducks (Down, S, pad B).
    # ----------------------------------------------------------------------
    _JUMP_KEYS = (pygame.K_SPACE, pygame.K_UP, pygame.K_w)
    _DUCK_KEYS = (pygame.K_DOWN, pygame.K_s)

    def handle_event(self, event):
        game = self._runner
        if not config.ENABLE_MODEL_SWITCH_MINIGAME or game is None or not self._switch_was_visible:
            return
        if event.type == pygame.KEYDOWN:
            if event.key in self._JUMP_KEYS:
                game.press_jump()
            elif event.key in self._DUCK_KEYS:
                game.set_duck(True)
        elif event.type == pygame.KEYUP:
            if event.key in self._JUMP_KEYS:
                game.release_jump()
            elif event.key in self._DUCK_KEYS:
                game.set_duck(False)
        elif event.type == pygame.JOYBUTTONDOWN:
            if event.button == 0:
                game.press_jump()
            elif event.button == 1:
                game.set_duck(True)
        elif event.type == pygame.JOYBUTTONUP:
            if event.button == 0:
                game.release_jump()
            elif event.button == 1:
                game.set_duck(False)
        elif event.type == pygame.JOYHATMOTION:
            up_down = event.value[1]
            if up_down > 0:
                game.press_jump()
            else:
                game.release_jump()
            game.set_duck(up_down < 0)
        elif event.type == pygame.MOUSEBUTTONDOWN:
            game.press_jump()
        elif event.type == pygame.MOUSEBUTTONUP:
            game.release_jump()

    # ----------------------------------------------------------------------
    # Original audio / face animation helpers.
    # ----------------------------------------------------------------------
    def visualizer(self, count, xf_list, yf_list):
        index = int(count)
        if index < 0 or index >= len(xf_list) or index >= len(yf_list):
            return None

        xf, yf = xf_list[index], yf_list[index]
        if not config.SHOW_DEBUG_OVERLAY or len(xf) == 0:
            return xf, yf

        plot_count = min(200, len(xf), len(yf))
        if plot_count <= 1:
            return xf, yf

        visible = yf[:plot_count]
        peak = max(float(np_max(visible)), 1e-8)
        for i in range(plot_count):
            val = min(140.0, (float(yf[i]) / peak) * 140.0)
            x = 10 + float(xf[i]) / 8.0
            pygame.draw.line(
                self._app._screen,
                (255, 255, 255),
                (x, 200 - val),
                (x, 200),
                2,
            )
        return xf, yf

    @staticmethod
    def band_energy(yf, xf, low_hz, high_hz):
        """Energy share inside a frequency band (0.0-1.0)."""
        if len(yf) == 0 or len(xf) == 0:
            return 0.0

        total = float(sum(float(v) for v in yf[1:]))
        if total <= 1e-9:
            return 0.0

        band = 0.0
        for frequency, magnitude in zip(xf, yf):
            if low_hz <= float(frequency) < high_hz:
                band += float(magnitude)
        return band / total

    def _choose_mouth_frame(self, xf, yf, rms):
        if rms < config.LIP_SYNC_SILENCE_RMS:
            return 1

        low = self.band_energy(yf, xf, 80, 300)
        low_mid = self.band_energy(yf, xf, 300, 700)
        mid = self.band_energy(yf, xf, 700, 1400)
        upper = self.band_energy(yf, xf, 1400, 2600)
        high = self.band_energy(yf, xf, 2600, 7000)

        if high > 0.24:
            return rand.choice((4, 9))
        if upper > 0.25:
            return 3
        if low > 0.26:
            return 6
        if low_mid > 0.28:
            return rand.choice((5, 7, 8))
        if mid > 0.22:
            return 2
        return rand.choice((2, 3, 6))


    def _build_crt_static_frames(self):
        frames = []
        count = max(2, int(getattr(config, "CRT_STATIC_REFRESH_FRAME_COUNT", 6)))
        width = max(32, int(getattr(config, "CRT_STATIC_REFRESH_FRAME_WIDTH", 160)))
        height = max(24, int(getattr(config, "CRT_STATIC_REFRESH_FRAME_HEIGHT", 100)))
        for _ in range(count):
            raw = bytearray(width * height * 3)
            j = 0
            for _pixel in range(width * height):
                value = rand.randrange(12, 244)
                raw[j] = value
                raw[j + 1] = value
                raw[j + 2] = value
                j += 3
            frame = pygame.image.frombuffer(bytes(raw), (width, height), "RGB").convert()
            frames.append(frame)
        return frames

    def _reset_crt_static_refresh(self, now=None):
        now = time.monotonic() if now is None else float(now)
        self._crt_static_started = None
        self._crt_static_last_ended = now

    def _crt_static_refresh_active(self, now, *, allowed):
        if not bool(getattr(config, "ENABLE_CRT_STATIC_REFRESH", False)):
            return False
        if not allowed:
            if self._crt_static_started is not None:
                self._reset_crt_static_refresh(now)
            return False
        duration = max(1.0, float(getattr(config, "CRT_STATIC_REFRESH_DURATION_SECONDS", 7.0)))
        interval = max(duration + 1.0, float(getattr(config, "CRT_STATIC_REFRESH_INTERVAL_SECONDS", 90.0)))
        if self._crt_static_started is not None:
            if now - self._crt_static_started < duration:
                return True
            self._reset_crt_static_refresh(now)
            return False
        if now - self._crt_static_last_ended >= interval:
            self._crt_static_started = now
            return True
        return False

    def _render_crt_static_refresh(self, now):
        if not self._crt_static_frames:
            self._app._screen.fill((0, 0, 0))
            return
        index = int(now * 18.0) % len(self._crt_static_frames)
        frame = pygame.transform.scale(
            self._crt_static_frames[index],
            (self._app.SCREEN_WIDTH, self._app.SCREEN_HEIGHT),
        )
        self._app._screen.blit(frame, (0, 0))

    # ----------------------------------------------------------------------
    # CRT screensavers.
    # ----------------------------------------------------------------------
    def _reset_saver_particles(self):
        w, h = self._app.SCREEN_WIDTH, self._app.SCREEN_HEIGHT
        self._stars = [
            {
                "x": rand.uniform(0, w),
                "y": rand.uniform(0, h),
                "speed": rand.uniform(24.0, 105.0),
                "size": rand.choice((1, 1, 1, 2, 2, 3)),
                "phase": rand.uniform(0, math.tau),
            }
            for _ in range(max(12, int(config.CRT_SCREENSAVER_STAR_COUNT)))
        ]
        columns = max(8, int(config.CRT_SCREENSAVER_RAIN_COLUMNS))
        self._rain = [
            {
                "x": ((i + 0.5) / columns) * w,
                "y": rand.uniform(-h, h),
                "speed": rand.uniform(45.0, 155.0),
                "length": rand.randint(3, 9),
                "phase": rand.uniform(0, math.tau),
            }
            for i in range(columns)
        ]

    def _saver_mode(self, now):
        modes = tuple(config.CRT_SCREENSAVER_MODES) or ("star_drift",)
        if now - self._saver_mode_started >= float(config.CRT_SCREENSAVER_MODE_SECONDS):
            steps = int(
                (now - self._saver_mode_started)
                // max(1.0, float(config.CRT_SCREENSAVER_MODE_SECONDS))
            )
            self._saver_mode_index = (self._saver_mode_index + max(1, steps)) % len(modes)
            self._saver_mode_started = now
        return modes[self._saver_mode_index % len(modes)]

    def _render_star_drift(self, dt, now):
        screen = self._app._screen
        w, h = self._app.SCREEN_WIDTH, self._app.SCREEN_HEIGHT
        screen.fill((0, 4, 8))
        for star in self._stars:
            star["x"] += star["speed"] * dt
            star["y"] += (star["speed"] * 0.24 + math.sin(now + star["phase"]) * 8.0) * dt
            if star["x"] > w + 4 or star["y"] > h + 4:
                star["x"] = rand.uniform(-80.0, -4.0)
                star["y"] = rand.uniform(0, h)
                star["speed"] = rand.uniform(24.0, 105.0)
            pulse = int(90 + 70 * (0.5 + 0.5 * math.sin(now * 1.7 + star["phase"])))
            color = (20, min(220, pulse + 35), min(255, pulse + 75))
            pygame.draw.circle(screen, color, (int(star["x"]), int(star["y"])), star["size"])

    def _render_bmo_bounce(self, dt, now):
        screen = self._app._screen
        w, h = self._app.SCREEN_WIDTH, self._app.SCREEN_HEIGHT
        screen.fill((0, 5, 3))
        b = self._bouncer
        bw, bh = max(92, int(w * 0.11)), max(72, int(h * 0.12))
        b["x"] += b["vx"] * dt
        b["y"] += b["vy"] * dt
        if b["x"] <= 8 or b["x"] + bw >= w - 8:
            b["vx"] *= -1
            b["x"] = max(8, min(w - bw - 8, b["x"]))
        if b["y"] <= 8 or b["y"] + bh >= h - 8:
            b["vy"] *= -1
            b["y"] = max(8, min(h - bh - 8, b["y"]))

        rect = pygame.Rect(int(b["x"]), int(b["y"]), bw, bh)
        pygame.draw.rect(screen, (27, 175, 132), rect, border_radius=max(8, bw // 10))
        pygame.draw.rect(screen, (90, 235, 185), rect, width=2, border_radius=max(8, bw // 10))
        eye_y = rect.top + int(bh * 0.38)
        eye_dx = int(bw * 0.22)
        eye_r = max(3, bw // 28)
        pygame.draw.circle(screen, (3, 18, 16), (rect.centerx - eye_dx, eye_y), eye_r)
        pygame.draw.circle(screen, (3, 18, 16), (rect.centerx + eye_dx, eye_y), eye_r)
        mouth_y = rect.top + int(bh * 0.66)
        mouth_w = int(bw * (0.18 + 0.04 * math.sin(now * 2.0)))
        pygame.draw.line(
            screen,
            (3, 18, 16),
            (rect.centerx - mouth_w, mouth_y),
            (rect.centerx + mouth_w, mouth_y),
            max(2, bw // 45),
        )
        # A handful of moving dots makes the otherwise dark screen visibly alive.
        for idx in range(8):
            x = int((now * (21 + idx * 5) + idx * 137) % max(1, w))
            y = int((idx * 89 + math.sin(now * 0.7 + idx) * 60 + h) % h)
            pygame.draw.circle(screen, (8, 75, 58), (x, y), 2)

    def _render_pixel_rain(self, dt, now):
        screen = self._app._screen
        w, h = self._app.SCREEN_WIDTH, self._app.SCREEN_HEIGHT
        screen.fill((0, 3, 2))
        step = max(8, int(h * 0.022))
        for col in self._rain:
            col["y"] += col["speed"] * dt
            if col["y"] - col["length"] * step > h:
                col["y"] = rand.uniform(-h * 0.45, -step)
                col["speed"] = rand.uniform(45.0, 155.0)
                col["length"] = rand.randint(3, 9)
            for j in range(col["length"]):
                y = col["y"] - j * step
                if -step <= y <= h + step:
                    fade = 1.0 - (j / max(1, col["length"]))
                    pulse = 0.72 + 0.28 * math.sin(now * 1.4 + col["phase"] + j * 0.4)
                    level = int(35 + 170 * fade * pulse)
                    size = max(3, step // 3)
                    pygame.draw.rect(
                        screen,
                        (3, min(235, level + 35), min(160, level)),
                        pygame.Rect(int(col["x"] - size / 2), int(y), size, size),
                        border_radius=1,
                    )

    def _render_screensaver(self, dt, now):
        if self._saver_started is None:
            self._saver_started = now
            self._saver_mode_started = now
            self._saver_mode_index = rand.randrange(max(1, len(tuple(config.CRT_SCREENSAVER_MODES))))
            self._reset_saver_particles()
        mode = self._saver_mode(now)
        if mode == "bmo_bounce":
            self._render_bmo_bounce(dt, now)
        elif mode == "pixel_rain":
            self._render_pixel_rain(dt, now)
        else:
            self._render_star_drift(dt, now)

    def _render_idle_backend(self, dt, now):
        """Prefer user-supplied legacy Windows .scr files; fall back safely."""
        manager = getattr(self._app.chat, "windows_screensaver", None)
        if config.ENABLE_WINDOWS_SCR_SCREENSAVERS and manager is not None:
            try:
                result = manager.tick(now)
                if result.get("user_exit"):
                    # A classic .scr normally exits on mouse/keyboard input.
                    # Treat that as activity so another saver is not launched
                    # on the very next frame.
                    self._app.chat.note_user_activity()
                    self._saver_started = None
                    self._render_face()
                    return
                if result.get("running"):
                    # Wine owns the visible fullscreen saver. Keep Bemo's own
                    # surface dark underneath it so alt-tab/focus changes never
                    # expose a bright static face.
                    if self._saver_started is None:
                        # Started: the movie/game guard is not asked again, or it sees this saver's own
                        # full-screen window and closes it within 5 s (2026-10-23).
                        self._saver_started = now
                    self._app._screen.fill((0, 0, 0))
                    return
            except Exception:
                # The CRT must still be protected if Wine or a particular SCR
                # crashes. The built-in saver is always the fallback.
                pass
        self._render_screensaver(dt, now)

    def _stop_external_saver(self):
        manager = getattr(self._app.chat, "windows_screensaver", None)
        if manager is not None:
            try:
                manager.stop()
            except Exception:
                pass

    # ----------------------------------------------------------------------
    # Model-switch loading screen + the runner game (systems/runner_game.py).
    # ----------------------------------------------------------------------
    def _reset_switch_game(self, generation, now):
        self._seen_switch_generation = generation
        self._game_last_update = now
        if self._runner is None:
            self._runner = runner.RunnerGame(getattr(config, "RUNNER_SCORES_PATH", None))
        else:
            self._runner.reset()

    def _switch_visible(self, status, now):
        if not config.ENABLE_MODEL_SWITCH_LOADING_SCREEN:
            return False
        if status.get("model_switch_active"):
            return True
        finished = float(status.get("model_switch_finished") or 0.0)
        started = float(status.get("model_switch_started") or 0.0)
        if not finished or not started:
            return False
        min_until = started + max(0.0, float(config.MODEL_SWITCH_MIN_DISPLAY_SECONDS))
        return now < min_until

    def _retro_track(self, rect):
        """A Windows-95-ish sunken track; returns the inside, where the blocks go."""
        screen = self._app._screen
        rect = pygame.Rect(int(rect[0]), int(rect[1]), int(rect[2]), int(rect[3]))
        face, shadow, dark, highlight, track = (192, 192, 192), (128, 128, 128), (64, 64, 64), (255, 255, 255), \
            (236, 233, 216)
        pygame.draw.rect(screen, face, rect)
        pygame.draw.line(screen, dark, rect.topleft, (rect.right - 1, rect.top), 1)
        pygame.draw.line(screen, dark, rect.topleft, (rect.left, rect.bottom - 1), 1)
        pygame.draw.line(screen, highlight, (rect.left, rect.bottom - 1), (rect.right - 1, rect.bottom - 1), 1)
        pygame.draw.line(screen, highlight, (rect.right - 1, rect.top), (rect.right - 1, rect.bottom - 1), 1)
        inner = rect.inflate(-4, -4)
        pygame.draw.line(screen, shadow, inner.topleft, (inner.right - 1, inner.top), 1)
        pygame.draw.line(screen, shadow, inner.topleft, (inner.left, inner.bottom - 1), 1)
        pygame.draw.rect(screen, track, inner.inflate(-2, -2))
        return inner.inflate(-4, -4)

    @staticmethod
    def _retro_blocks(content, blocks):
        """Where each block of a retro bar goes."""
        gap = max(1, int(content.height * 0.18))
        block_w = max(2, (content.width - gap * (blocks - 1)) // blocks)
        used = block_w * blocks + gap * (blocks - 1)
        start_x = content.left + max(0, (content.width - used) // 2)
        return [pygame.Rect(start_x + i * (block_w + gap), content.top, block_w, content.height) for i in range(blocks)]

    def _render_retro_progress_bar(self, rect, now):
        """Render a Windows-95-ish indeterminate segmented progress bar.

        This intentionally does not pretend that model loading has a known
        percentage. A short train of blue blocks cycles through a classic
        sunken 3-D track until Supervisor reports the model switch complete.
        """
        content = self._retro_track(rect)
        blocks = max(6, int(config.MODEL_SWITCH_RETRO_PROGRESS_BLOCKS))
        lit = max(1, min(blocks - 1, int(config.MODEL_SWITCH_RETRO_PROGRESS_LIT_BLOCKS)))
        step_seconds = max(0.04, float(config.MODEL_SWITCH_RETRO_PROGRESS_STEP_SECONDS))
        head = int(now / step_seconds) % (blocks + lit)
        for i, block in enumerate(self._retro_blocks(content, blocks)):
            # The train wraps cleanly, with older blocks slightly darker.
            age = (head - i) % (blocks + lit)
            if age < lit:
                pygame.draw.rect(self._app._screen, (0, 0, 255) if age == 0 else (0, 0, 128), block)

    def _render_retro_fill_bar(self, rect, fraction):
        """The same bar, filled to a real fraction (the install screen)."""
        content = self._retro_track(rect)
        blocks = max(10, content.width // max(8, int(content.height * 0.7)))
        lit = int(round(max(0.0, min(1.0, float(fraction))) * blocks))
        for block in self._retro_blocks(content, blocks)[:lit]:
            pygame.draw.rect(self._app._screen, (0, 0, 128), block)

    @staticmethod
    def _pretty_role(label):
        return str(label or "Bemo").replace("_", " ").strip().title()

    def _render_model_switch(self, status, now):
        screen = self._app._screen
        w, h = self._app.SCREEN_WIDTH, self._app.SCREEN_HEIGHT
        generation = int(status.get("model_switch_generation") or 0)
        if generation != self._seen_switch_generation:
            self._reset_switch_game(generation, now)

        # Slowly moving grid / scan motif so the loading page itself is not static.
        self._green_grid(now, speed=1.0)

        drift = int(math.sin(now * 1.15) * 7)
        title = self.MEDIUM_FONT.render("BMO MODEL SWAP", True, (108, 245, 206))
        screen.blit(title, title.get_rect(center=(w // 2 + drift, int(h * 0.10))))

        from_name = self._pretty_role(status.get("model_switch_from"))
        to_name = self._pretty_role(status.get("model_switch_to"))
        route = self.SMALL_FONT.render(f"{from_name}  >  {to_name}", True, (175, 229, 218))
        screen.blit(route, route.get_rect(center=(w // 2 - drift, int(h * 0.16))))

        started = float(status.get("model_switch_started") or now)
        elapsed = max(0.0, now - started)
        state_text = "LOADING" if status.get("model_switch_active") else (
            "READY" if status.get("model_switch_success") else "RETURNING"
        )
        status_surface = self.SMALL_FONT.render(
            f"{state_text}  {elapsed:0.1f}s", True, (70, 190, 168)
        )
        screen.blit(status_surface, status_surface.get_rect(center=(w // 2 + drift, int(h * 0.21))))

        # Honest indeterminate progress. By default this is a classic Windows
        # 95-style segmented bar; the older smooth scanner remains available as
        # a fallback if the retro style is disabled in config.
        bar_rect = pygame.Rect(int(w * 0.20), int(h * 0.245), int(w * 0.60), max(22, int(h * 0.042)))
        if config.MODEL_SWITCH_RETRO_PROGRESS:
            self._render_retro_progress_bar(bar_rect, now)
        else:
            pygame.draw.rect(screen, (12, 48, 48), bar_rect, border_radius=bar_rect.height // 2)
            travel = max(1, bar_rect.width - int(bar_rect.width * 0.23))
            phase = (math.sin(now * 2.25) + 1.0) / 2.0
            block_w = max(24, int(bar_rect.width * 0.23))
            block = pygame.Rect(bar_rect.left + int(travel * phase), bar_rect.top, block_w, bar_rect.height)
            pygame.draw.rect(screen, (55, 220, 174), block, border_radius=bar_rect.height // 2)

        if not config.ENABLE_MODEL_SWITCH_MINIGAME:
            # Passive animated packets if the interactive game is disabled.
            for idx in range(14):
                angle = now * (0.55 + idx * 0.025) + idx * 0.72
                radius = min(w, h) * (0.18 + 0.006 * idx)
                x = w / 2 + math.cos(angle) * radius
                y = h * 0.60 + math.sin(angle * 1.13) * radius * 0.55
                pygame.draw.circle(screen, (21, 140 + (idx * 5) % 80, 122), (int(x), int(y)), 4)
            return
        self._render_runner(now, drift)

    GRID_BACKGROUND = (3, 14, 6)  # the green grid behind the brain-swap and setup screens (2026-10-27)

    def _green_grid(self, now, speed=1.0):
        screen = self._app._screen
        w, h = self._app.SCREEN_WIDTH, self._app.SCREEN_HEIGHT
        screen.fill(self.GRID_BACKGROUND)
        grid = max(28, int(min(w, h) * 0.055))
        offset_x = int((now * 23 * speed) % grid)
        offset_y = int((now * 11 * speed) % grid)
        for x in range(-grid + offset_x, w + grid, grid):
            pygame.draw.line(screen, (10, 56, 22), (x, 0), (x, h), 1)
        for y in range(-grid + offset_y, h + grid, grid):
            pygame.draw.line(screen, (9, 48, 19), (0, y), (w, y), 1)

    def _render_runner(self, now, drift):
        """The runner game in the lower part of the brain-swap screen (systems/runner_game.py)."""
        screen = self._app._screen
        w, h = self._app.SCREEN_WIDTH, self._app.SCREEN_HEIGHT
        game = self._runner
        play = pygame.Rect(int(w * 0.10), int(h * 0.32), int(w * 0.80), int(h * 0.56))
        unit = play.height * 1.15                        # one "play height" in pixels
        ground_y = play.bottom - int(play.height * 0.14)
        game.width = play.width / unit
        game.update(now - self._game_last_update)
        self._game_last_update = now

        pygame.draw.rect(screen, (24, 92, 44), play, width=2, border_radius=10)
        clip = screen.get_clip()
        screen.set_clip(play.inflate(-4, -4))
        # Far-off specks drift slower than the ground (a bit of depth), then the ground itself.
        for i in range(18):
            x = play.left + (i * 0.53 * unit - game.distance * unit * 0.3) % play.width
            y = play.top + play.height * (0.18 + 0.4 * ((i * 37) % 11) / 11)
            pygame.draw.rect(screen, (34, 104, 56), pygame.Rect(int(x), int(y), 3, 3))
        pygame.draw.line(screen, (70, 190, 165), (play.left, ground_y), (play.right, ground_y), 2)
        dash = max(6, int(unit * 0.05))
        offset = int(game.distance * unit) % (dash * 3)
        for x in range(play.left - offset, play.right, dash * 3):
            pygame.draw.line(screen, (38, 116, 103), (x, ground_y + 6), (x + dash, ground_y + 6), 1)

        def box(gx, gy, gw, gh):
            return pygame.Rect(int(play.left + gx * unit), int(ground_y - (gy + gh) * unit),
                               max(2, int(gw * unit)), max(2, int(gh * unit)))

        flap = int(now * 8) % 2
        for o in game.obstacles:
            if o.kind.endswith("bug"):
                body = box(o.x, o.y, o.w, o.h)
                pygame.draw.ellipse(screen, (226, 86, 140), body)
                wing = body.height * (0.9 if flap else -0.5)
                for side in (0.3, 0.7):
                    x = body.left + body.width * side
                    pygame.draw.line(screen, (250, 170, 205), (x, body.top + 2),
                                     (x - body.width * 0.15, body.top - wing), 2)
                pygame.draw.rect(screen, (30, 10, 20), pygame.Rect(body.left + 3, body.centery - 2, 4, 4))
                continue
            parts = [(o.x, o.w * 0.42), (o.x + o.w * 0.58, o.w * 0.42)] if o.kind == "pair" else [(o.x, o.w)]
            for gx, gw in parts:
                block = box(gx, o.y, gw, o.h)
                pygame.draw.rect(screen, (232, 158, 64), block)
                pygame.draw.rect(screen, (120, 70, 20), block, width=2)
                for row in range(block.top + 4, block.bottom - 2, 6):  # glitchy scan lines
                    pygame.draw.line(screen, (250, 200, 120), (block.left + 3, row), (block.right - 4, row), 1)

        ducking = game.ducking and game.on_ground
        bw, bh = (runner.DUCK_W, runner.DUCK_H) if ducking else (runner.RUNNER_W, runner.RUNNER_H)
        self._draw_little_bmo(box(runner.RUNNER_X, game.y, bw, bh), game, ducking)
        screen.set_clip(clip)

        font, big = self.SMALL_FONT, self.MEDIUM_FONT
        hud = font.render(f"LEVEL {game.level}    SCORE {int(game.score):05d}    BEST {game.best:05d}", True,
                          (150, 235, 214))
        screen.blit(hud, hud.get_rect(midtop=(w // 2, play.top + 8)))
        if game.state == runner.READY:
            lines = [(big, "PRESS SPACE TO RUN", (230, 255, 245)),
                     (font, "SPACE or UP: jump (hold for higher)    DOWN: duck", (120, 200, 185))]
        elif game.state == runner.OVER:
            lines = [(big, "NEW BEST!" if game.new_best else "OUCH!", (255, 214, 120)),
                     (font, f"Level {game.level}, {int(game.score)} points. Press SPACE to go again.",
                      (190, 235, 222))]
        elif game.level_shown_at is not None and game.distance - game.level_shown_at < 1.6 * game.speed:
            lines = [(big, f"LEVEL {game.level}!", (255, 214, 120))]
        else:
            lines = []
        y = play.top + play.height * 0.30
        for face, text, color in lines:
            surface = face.render(text, True, color)
            screen.blit(surface, surface.get_rect(midtop=(w // 2, int(y))))
            y += surface.get_height() + 6
        controls = font.render("jump: SPACE / UP / pad A      duck: DOWN / pad B", True, (55, 135, 124))
        screen.blit(controls, controls.get_rect(center=(w // 2 + drift, int(h * 0.94))))

    # The runner's BMO as pixel art (2026-10-27), so he stays crisp on the CRT next to the pixel lettering:
    # T body, S screen, K face, Y the yellow D-pad, R the red button. Squashed into a smaller box when he ducks.
    _BMO_PIXELS = (
        " TTTTTTTTTTTT ",
        "TTTTTTTTTTTTTT",
        "TTSSSSSSSSSSTT",
        "TTSSKSSSSKSSTT",
        "TTSSKSSSSKSSTT",
        "TTSSSSSSSSSSTT",
        "TTSSKSSSSKSSTT",
        "TTSSSKKKKSSSTT",
        "TTSSSSSSSSSSTT",
        "TTTTTTTTTTTTTT",
        "TTTYTTTTTTTTTT",
        "TTYYYTTTTRRTTT",
        "TTTYTTTTTRRTTT",
        "TTTTTTTTTTTTTT",
        "TTTTTTTTTTTTTT",
        " TTTTTTTTTTTT ",
    )
    _BMO_CRASHED_ROWS = {6: "TTSSSSSSSSSSTT", 7: "TTSSSKKKKSSSTT"}  # a flat mouth
    _BMO_COLORS = {"T": (99, 196, 178), "S": (190, 240, 212), "K": (20, 40, 36), "Y": (246, 206, 70),
                   "R": (226, 70, 70)}
    _BMO_LIMBS = (60, 150, 134)
    _BMO_SHOULDER_ROW = 10

    def _little_bmo_body(self, size, crashed):
        """The body drawn once per size and face, then reused every frame."""
        cache = self.__dict__.setdefault("_little_bmo_cache", {})
        key = (size, crashed)
        if key not in cache:
            rows = list(self._BMO_PIXELS)
            if crashed:
                for index, row in self._BMO_CRASHED_ROWS.items():
                    rows[index] = row
            width, height = size
            surface = pygame.Surface((width, height), pygame.SRCALPHA)
            cols, count = len(rows[0]), len(rows)
            for r, row in enumerate(rows):
                y0, y1 = round(r * height / count), round((r + 1) * height / count)
                for c, cell in enumerate(row):
                    if cell != " ":
                        x0, x1 = round(c * width / cols), round((c + 1) * width / cols)
                        surface.fill(self._BMO_COLORS[cell], pygame.Rect(x0, y0, max(1, x1 - x0), max(1, y1 - y0)))
            cache[key] = surface
        return cache[key]

    def _draw_little_bmo(self, rect, game, ducking):
        """BMO: pixel body, arms that swing as he runs (up for a jump, tucked when he ducks) and legs that run."""
        screen = self._app._screen
        legs = max(2, int(rect.height * 0.18))
        body = pygame.Rect(rect.left, rect.top, rect.width, rect.height - legs)
        cell = max(1.0, body.width / len(self._BMO_PIXELS[0]))
        limb_w = max(2, int(round(cell * 1.2)))
        length = body.height * (0.30 if ducking else 0.42)
        shoulder_y = body.top + body.height * self._BMO_SHOULDER_ROW / len(self._BMO_PIXELS)
        if not game.on_ground:
            reach = (-0.75, -0.75)                      # both arms up mid-jump
        elif game.state == runner.RUNNING and not ducking:
            swing = 0.35 * math.sin(game.distance * 14 * math.pi)
            reach = (0.55 + swing, 0.55 - swing)        # one up while the other is down
        else:
            reach = (0.9, 0.9)
        for side, down in ((-1, reach[0]), (1, reach[1])):
            shoulder = (body.left if side < 0 else body.right - 1, shoulder_y)
            hand = (shoulder[0] + side * length * 0.55, shoulder_y + length * down)
            pygame.draw.line(screen, self._BMO_LIMBS, shoulder, hand, limb_w)
            pygame.draw.rect(screen, self._BMO_LIMBS, pygame.Rect(int(hand[0] - limb_w), int(hand[1] - limb_w // 2),
                                                                 limb_w * 2, limb_w * 2))
        screen.blit(self._little_bmo_body(body.size, game.state == runner.OVER), body.topleft)
        step = int(game.distance * 14) % 2 if game.on_ground and game.state == runner.RUNNING else 0
        leg_w = max(2, int(round(cell * 2)))
        for i, col in enumerate((3, 9)):
            reach_down = legs if (i == step or not game.on_ground) else legs * 0.55
            pygame.draw.rect(screen, self._BMO_LIMBS, pygame.Rect(int(body.left + col * cell), body.bottom,
                                                                 leg_w, max(1, int(reach_down))))

    # ----------------------------------------------------------------------
    # The install screen (systems/first_boot.py, SetupView).
    # ----------------------------------------------------------------------
    _SETUP_COLORS = {"done": (150, 230, 205), "running": (235, 255, 248), "pending": (78, 128, 120),
                     "failed": (255, 190, 90), "skipped": (110, 132, 126)}

    def _setup_fonts(self, h):
        cache = getattr(self, "_setup_font_cache", None)
        if not cache or cache[0] != h:
            cache = (h, ui_font(max(16, h // 20), bold=True),
                     ui_font(max(11, h // 34)),
                     ui_font(max(10, h // 42)))
            self._setup_font_cache = cache
        return cache[1:]

    def _setup_icon(self, rect, status, now):
        screen = self._app._screen
        color = self._SETUP_COLORS.get(status, (78, 128, 120))
        thick = max(2, rect.width // 7)
        if status == "done":
            pygame.draw.lines(screen, (90, 230, 150), False,
                              [(rect.left + rect.width * 0.12, rect.top + rect.height * 0.55),
                               (rect.left + rect.width * 0.42, rect.top + rect.height * 0.85),
                               (rect.left + rect.width * 0.9, rect.top + rect.height * 0.15)], thick)
        elif status == "running":
            pulse = 0.55 + 0.45 * math.sin(now * 5.0)
            shade = tuple(int(c * pulse) for c in (120, 255, 220))
            pygame.draw.polygon(screen, shade, [(rect.left + rect.width * 0.2, rect.top + rect.height * 0.1),
                                                (rect.right - rect.width * 0.1, rect.centery),
                                                (rect.left + rect.width * 0.2, rect.bottom - rect.height * 0.1)])
        elif status == "failed":
            pygame.draw.rect(screen, color, rect, width=max(1, thick // 2))
            stem = pygame.Rect(rect.centerx - thick // 2, rect.top + rect.height * 0.18, thick, rect.height * 0.42)
            pygame.draw.rect(screen, color, stem)
            pygame.draw.rect(screen, color, pygame.Rect(rect.centerx - thick // 2, rect.bottom - rect.height * 0.3,
                                                        thick, thick))
        elif status == "skipped":
            pygame.draw.line(screen, color, (rect.left + 2, rect.centery), (rect.right - 2, rect.centery), thick)
        else:
            pygame.draw.rect(screen, color, rect, width=max(1, thick // 2))

    def _render_setup(self, setup, now):
        screen = self._app._screen
        w, h = self._app.SCREEN_WIDTH, self._app.SCREEN_HEIGHT
        title_font, row_font, small_font = self._setup_fonts(h)
        self._green_grid(now, speed=0.25)  # slower here: it's up for an hour or more
        # The white frame the screen step asks about; otherwise a quiet one.
        framed = bool(setup.get("frame"))
        thick = max(2, h // 160) * (3 if framed else 1)
        pygame.draw.rect(screen, (255, 255, 255) if framed else (28, 96, 44), pygame.Rect(0, 0, w, h), width=thick)
        if framed:  # heavier corners, so a cut-off one is easy to spot
            arm, heavy = int(min(w, h) * 0.08), thick * 2
            for x, y in ((0, 0), (w - arm, 0), (0, h - heavy), (w - arm, h - heavy)):
                pygame.draw.rect(screen, (255, 255, 255), pygame.Rect(x, y, arm, heavy))
            for x, y in ((0, 0), (w - heavy, 0), (0, h - arm), (w - heavy, h - arm)):
                pygame.draw.rect(screen, (255, 255, 255), pygame.Rect(x, y, heavy, arm))
        # Everything drifts a few pixels over minutes, so nothing sits still for hours on a CRT.
        dx, dy = int(math.sin(now / 37.0) * 3), int(math.cos(now / 53.0) * 2)
        left, right, top = int(w * 0.07) + dx, int(w * 0.93) + dx, int(h * 0.05) + dy

        title = title_font.render("BMO SETUP", True, (108, 245, 206))
        screen.blit(title, (left, top))
        overall = float(setup.get("overall") or 0.0)
        percent = title_font.render(f"{int(overall * 100)}%", True, (108, 245, 206))
        screen.blit(percent, percent.get_rect(topright=(right, top)))
        count = small_font.render(f"{setup.get('done', 0)} of {setup.get('total', 0)} done", True, (90, 160, 148))
        screen.blit(count, count.get_rect(bottomright=(right - percent.get_width() - 16, top + percent.get_height())))
        bar = pygame.Rect(left, top + title.get_height() + int(h * 0.015), right - left, max(18, int(h * 0.045)))
        self._render_retro_fill_bar(bar, overall)

        rows = setup.get("rows") or []
        rows_top, rows_bottom = bar.bottom + int(h * 0.03), int(h * 0.79)
        row_h = min(int(h * 0.05), max(12, (rows_bottom - rows_top) // max(1, len(rows))))
        mini_left, mini_right = left + int((right - left) * 0.47), left + int((right - left) * 0.70)
        for i, row in enumerate(rows):
            y = rows_top + i * row_h
            status = row.get("status") or "pending"
            icon = pygame.Rect(left, y + int(row_h * 0.15), int(row_h * 0.7), int(row_h * 0.7))
            self._setup_icon(icon, status, now)
            color = self._SETUP_COLORS.get(status, (78, 128, 120))
            label = row_font.render(str(row.get("label", "")), True, color)
            screen.blit(label, (icon.right + int(row_h * 0.5), y + (row_h - label.get_height()) // 2))
            progress = row.get("progress")
            if status == "running":
                track = pygame.Rect(mini_left, y + int(row_h * 0.3), mini_right - mini_left, max(4, int(row_h * 0.4)))
                pygame.draw.rect(screen, (10, 44, 20), track)
                if progress is None:  # no real number to show: a block slides along
                    block_w = max(6, track.width // 5)
                    x = track.left + int((track.width - block_w) * (0.5 + 0.5 * math.sin(now * 2.2)))
                    pygame.draw.rect(screen, (55, 220, 174), pygame.Rect(x, track.top, block_w, track.height))
                else:
                    pygame.draw.rect(screen, (55, 220, 174),
                                     pygame.Rect(track.left, track.top, int(track.width * progress), track.height))
            detail = small_font.render(str(row.get("detail") or ""), True, color)
            screen.blit(detail, detail.get_rect(midright=(right, y + row_h // 2)))

        text, hint = str(setup.get("text") or ""), str(setup.get("hint") or "")
        y = int(h * 0.81)
        for line in (self._wrap_text(row_font, text, right - left)[:2] if text else []):
            surface = row_font.render(line, True, (205, 236, 228))
            screen.blit(surface, (left, y))
            y += surface.get_height() + 2
        if hint:
            surface = row_font.render(hint, True, (255, 226, 140))
            screen.blit(surface, (left, max(y + 4, int(h * 0.90))))

        panel_lines, heading, selected = [], "", -1
        if setup.get("problems"):
            heading, panel_lines = "THESE NEED YOU", list(setup["problems"])
        elif setup.get("choices"):
            heading = "WI-FI NETWORKS"
            panel_lines = [f"{n}  {name}" for n, name in enumerate(setup["choices"], 1)]
            selected = int(setup.get("selected") or 0)
        if panel_lines:
            wrapped = [(line, i) for i, item in enumerate(panel_lines)
                       for line in self._wrap_text(row_font, item, int(w * 0.7))]
            line_h = row_font.get_linesize()
            panel = pygame.Rect(0, 0, int(w * 0.78), line_h * (len(wrapped) + 2) + 24)
            panel.center = (w // 2 + dx, (rows_top + rows_bottom) // 2 + dy)
            pygame.draw.rect(screen, (4, 24, 10), panel)
            pygame.draw.rect(screen, (255, 190, 90) if setup.get("problems") else (108, 245, 206), panel, width=2)
            surface = row_font.render(heading, True, (255, 226, 140))
            screen.blit(surface, (panel.left + 16, panel.top + 10))
            y = panel.top + 10 + int(line_h * 1.5)
            for line, item in wrapped:
                if item == selected:
                    pygame.draw.rect(screen, (20, 84, 40), pygame.Rect(panel.left + 8, y - 1, panel.width - 16, line_h))
                surface = row_font.render(line, True, (235, 255, 248) if item == selected else (205, 236, 228))
                screen.blit(surface, (panel.left + 16, y))
                y += line_h

    # ----------------------------------------------------------------------
    # Text-only conversation mode.
    # ----------------------------------------------------------------------
    @staticmethod
    def _wrap_text(font, text, max_width):
        words = str(text or "").replace("\n", " \n ").split()
        lines = []
        current = ""
        for word in words:
            if word == "\n":
                if current:
                    lines.append(current)
                    current = ""
                continue
            candidate = word if not current else current + " " + word
            if current and font.size(candidate)[0] > max_width:
                lines.append(current)
                current = word
            else:
                current = candidate
        if current:
            lines.append(current)
        return lines or [""]

    def _render_parts_menu(self, status, now):
        """3D shapes menu (2026-10-22): every
        basic shape turning, in place of the face while BMO makes a part or after "show me the parts"; the face
        comes back afterwards. Pictures from systems/part_gallery.py; names only until they are drawn."""
        from systems import part_gallery
        from systems.cad_shapes import SHAPES
        screen = self._app._screen
        w, h = self._app.SCREEN_WIDTH, self._app.SCREEN_HEIGHT
        screen.fill((2, 10, 11))
        kinds = list(SHAPES)
        cols = math.ceil(len(kinds) / 2) if w >= h else 3
        rows = math.ceil(len(kinds) / cols)
        cache = getattr(self, "_parts_fonts", None)
        if not cache or cache[0] != w:
            cache = (w, ui_font(max(18, w // 30), bold=True),
                     ui_font(max(11, w // 70)))
            self._parts_fonts = cache
        title_font, label_font = cache[1], cache[2]
        top, bottom = int(h * 0.11), int(h * 0.07)
        tile_w, tile_h = w // cols, (h - top - bottom) // rows
        label_h = label_font.get_linesize()
        picture = (max(8, min(tile_w - 10, part_gallery.SIZE[0])), max(8, min(tile_h - label_h - 6, part_gallery.SIZE[1])))
        ready = part_gallery.ready()
        loaded = getattr(self, "_parts_frames", None)
        if ready and (not loaded or loaded[0] != picture):
            frames = {}
            for kind in kinds:
                surfaces = []
                for path in part_gallery.frames(kind):
                    image = pygame.image.load(str(path)).convert()
                    scale = min(picture[0] / image.get_width(), picture[1] / image.get_height())
                    surfaces.append(pygame.transform.smoothscale(
                        image, (max(1, int(image.get_width() * scale)), max(1, int(image.get_height() * scale)))))
                frames[kind] = surfaces
            loaded = self._parts_frames = (picture, frames)
        drift = int(math.sin(now * 1.15) * 5)
        title = title_font.render("BMO 3D SHAPES", True, (108, 245, 206))
        screen.blit(title, title.get_rect(center=(w // 2 + drift, top // 2)))
        turn = int(now * part_gallery.FRAMES / 6.0)  # one full turn every 6 seconds
        for i, kind in enumerate(kinds):
            x0, y0 = (i % cols) * tile_w, top + (i // cols) * tile_h
            surfaces = (loaded[1].get(kind) if ready and loaded else None) or []
            label = label_font.render(kind.replace("_", " "), True, (175, 229, 218))
            if surfaces:  # the picture with its name right under it, centered in the tile
                image = surfaces[turn % len(surfaces)]
                block = image.get_height() + 4 + label_h
                top_y = y0 + max(0, (tile_h - block) // 2)
                screen.blit(image, image.get_rect(midtop=(x0 + tile_w // 2, top_y)))
                screen.blit(label, label.get_rect(midtop=(x0 + tile_w // 2, top_y + image.get_height() + 4)))
            else:
                screen.blit(label, label.get_rect(center=(x0 + tile_w // 2, y0 + tile_h // 2)))
        if status.get("parts_menu_busy"):
            note = "Making your part" + "." * (1 + int(now * 2) % 3)
        elif not ready:
            note = "Drawing the pictures..."
        else:
            note = "Say: Hey BMO, make a part ... with its sizes in millimeters"
        footer = label_font.render(note, True, (108, 245, 206))
        screen.blit(footer, footer.get_rect(center=(w // 2, h - bottom // 2)))

    def _render_calibration(self, status):
        """Settings plan S2 (approved): a full-screen calibration pattern in place of
        the face, only while a calibration session runs. The white frame sits exactly on
        the edge of the picture, so on a CRT you can see which sides are cut off."""
        screen = self._app._screen
        w, h = screen.get_width(), screen.get_height()
        white, yellow, grey = (255, 255, 255), (255, 214, 64), (70, 70, 70)
        screen.fill((0, 0, 0))
        for i in range(1, 10):  # 10% grid
            pygame.draw.line(screen, grey, (w * i // 10, 0), (w * i // 10, h - 1), 1)
            pygame.draw.line(screen, grey, (0, h * i // 10), (w - 1, h * i // 10), 1)
        pygame.draw.rect(screen, white, pygame.Rect(0, 0, w, h), max(3, w // 320))
        inset = max(6, int(min(w, h) * 0.03))
        pygame.draw.rect(screen, yellow, pygame.Rect(inset, inset, w - 2 * inset, h - 2 * inset), 2)
        size = max(18, int(min(w, h) * 0.06))
        for cx, cy, dx, dy in ((0, 0, 1, 1), (w - 1, 0, -1, 1), (0, h - 1, 1, -1), (w - 1, h - 1, -1, -1)):
            pygame.draw.polygon(screen, white, [(cx, cy), (cx + dx * size, cy), (cx, cy + dy * size)])
        cache = getattr(self, "_calibration_fonts", None)
        if not cache or cache[0] != w:  # text sized to the screen: 640x480 CRTs too
            cache = (w, ui_font(max(18, w // 32), bold=True),
                     ui_font(max(14, w // 44)))
            try:
                self._calibration_fonts = cache
            except AttributeError:
                pass
        font, small = cache[1], cache[2]
        for text, pos in (("TOP", (w // 2, inset + 22)), ("BOTTOM", (w // 2, h - inset - 22)),
                          ("LEFT", (inset + 45, h // 2)), ("RIGHT", (w - inset - 50, h // 2))):
            label = font.render(text, True, white)
            screen.blit(label, label.get_rect(center=pos))
        r = int(min(w, h) * 0.12)  # a circle should look round, not oval
        pygame.draw.circle(screen, white, (w // 2, h // 2), r, 2)
        pygame.draw.line(screen, white, (w // 2 - r // 2, h // 2), (w // 2 + r // 2, h // 2), 2)
        pygame.draw.line(screen, white, (w // 2, h // 2 - r // 2), (w // 2, h // 2 + r // 2), 2)
        y = inset + 22 + font.get_height() + 12  # instructions under TOP, numbers above BOTTOM
        for text, f, color in (("SCREEN CALIBRATION", font, yellow),
                               ("Make the white frame just visible.", small, white),
                               ("smaller - bigger - move left/right/up/down", small, white),
                               ("lower resolution - keep it - cancel", small, white)):
            surface = f.render(text, True, color)
            screen.blit(surface, surface.get_rect(center=(w // 2, y)))
            y += surface.get_height() + 4
        numbers = str(status.get("calibration_text") or "")
        if numbers:
            surface = small.render(numbers, True, yellow)
            screen.blit(surface, surface.get_rect(center=(w // 2, h - inset - 22 - font.get_height())))

    def _render_text_mode(self, status):
        screen = self._app._screen
        w, h = self._app.SCREEN_WIDTH, self._app.SCREEN_HEIGHT
        screen.fill((3, 14, 12))

        pad = max(12, int(min(w, h) * 0.025))
        header = self.MEDIUM_FONT.render("BEMO // TEXT MODE", True, (112, 246, 205))
        screen.blit(header, (pad, pad))

        try:
            snap = self._app.chat.text_display_snapshot()
        except Exception:
            snap = {"messages": [], "state": "ready", "profile": "normal", "tts_enabled": True}

        state = str(snap.get("state") or "ready").upper()
        profile = str(snap.get("profile") or "normal").upper()
        tts = "ON" if bool(snap.get("tts_enabled", True)) else "OFF"
        status_text = f"STATE {state}   MODEL {profile}   TTS {tts}"
        status_surface = self.SMALL_FONT.render(status_text, True, (78, 170, 148))
        screen.blit(status_surface, (pad, pad + header.get_height() + 4))

        sep_y = pad + header.get_height() + status_surface.get_height() + 12
        pygame.draw.line(screen, (42, 106, 91), (pad, sep_y), (w - pad, sep_y), 1)

        line_h = max(20, self.SMALL_FONT.get_linesize() + 2)
        footer_h = line_h + pad
        available_top = sep_y + 12
        available_bottom = h - footer_h
        max_width = max(80, w - pad * 2)

        rendered = []
        for item in snap.get("messages", []):
            role = str(item.get("role") or "").lower()
            if role not in {"user", "assistant"}:
                continue
            label = "YOU>" if role == "user" else "BEMO>"
            color = (230, 239, 192) if role == "user" else (135, 255, 213)
            content = str(item.get("content") or "").strip()
            first_prefix = label + " "
            prefix_w = self.SMALL_FONT.size(first_prefix)[0]
            wrapped = self._wrap_text(self.SMALL_FONT, content, max(60, max_width - prefix_w))
            for idx, line in enumerate(wrapped):
                text = (first_prefix if idx == 0 else " " * len(label) + " ") + line
                rendered.append((text, color))
            rendered.append(("", color))

        max_lines = max(1, int((available_bottom - available_top) / line_h))
        visible = rendered[-max_lines:]
        y = available_top
        for text, color in visible:
            if text:
                surface = self.SMALL_FONT.render(text, True, color)
                screen.blit(surface, (pad, y))
            y += line_h

        footer = self.SMALL_FONT.render("VOICE CONTROLS ONLY", True, (58, 126, 110))
        screen.blit(footer, (pad, h - pad - footer.get_height()))

    # ----------------------------------------------------------------------
    # Normal Bemo face.
    # ----------------------------------------------------------------------
    def _render_face(self):
        self._app._screen.fill(self.BACKGROUND_COLOR)

        if config.SHOW_DEBUG_OVERLAY:
            text = self.COMIC_SANS.render(
                f"{self.i} {self.current_mouth_frame}", True, (0, 0, 250)
            )
            text_rect = text.get_rect(
                center=(self._app.SCREEN_WIDTH / 2, self._app.SCREEN_HEIGHT - 20)
            )
            self._app._screen.blit(text, text_rect)
        self.i += 1

        if self.blink_timer - time.time() < 0:
            self.blinking = not self.blinking
            self.blink_timer = time.time() + (0.2 if self.blinking else 4)

        if self.blinking:
            self._app._screen.blit(
                self.LEFT_EYE_CLOSED,
                self.LEFT_EYE_OPEN.get_rect(center=self.LEFT_EYE_POSE),
            )
            self._app._screen.blit(
                self.RIGHT_EYE_CLOSED,
                self.RIGHT_EYE_OPEN.get_rect(center=self.RIGHT_EYE_POSE),
            )
        else:
            self._app._screen.blit(
                self.LEFT_EYE_OPEN,
                self.LEFT_EYE_OPEN.get_rect(center=self.LEFT_EYE_POSE),
            )
            self._app._screen.blit(
                self.RIGHT_EYE_OPEN,
                self.RIGHT_EYE_OPEN.get_rect(center=self.RIGHT_EYE_POSE),
            )

        if self._app.chat.state is State.TALKING:
            self._app.chat.count += config.VALUES_PER_SECOND / config.FPS
            output = self.visualizer(
                self._app.chat.count,
                self._app.chat.xf_list,
                self._app.chat.yf_list,
            )

            if output is not None and self.mouth_frame_change_timer - time.time() < 0:
                self.mouth_frame_change_timer = time.time() + config.TALK_DELAY
                index = min(
                    int(self._app.chat.count),
                    max(0, len(self._app.chat.rms_list) - 1),
                )
                rms = self._app.chat.rms_list[index] if self._app.chat.rms_list else 0.0
                xf, yf = output
                self.current_mouth_frame = self._choose_mouth_frame(xf, yf, rms)
        else:
            self.current_mouth_frame = 1

        self._app._screen.blit(
            self.MOUTH_FRAMES[self.current_mouth_frame],
            self.MOUTH_FRAMES[0].get_rect(center=self.MOUTH_POSE),
        )


    def render(self):
        if hasattr(self._app, "prepare_frame"):
            self._app.prepare_frame()
        now = time.monotonic()
        dt = min(0.1, max(0.0, now - self.frame_timer))

        # DONE -> READY must happen even when a saver/loading view owns the
        # screen; otherwise the chat worker would wait forever for the renderer.
        if self._app.chat.state is State.DONE:
            self._app.chat.state = State.READY

        try:
            status = self._app.chat.visual_status()
        except Exception:
            status = {
                "idle_seconds": 0.0,
                "model_switch_active": False,
                "model_switch_generation": 0,
            }

        idle_allowed = self._app.chat.state in (State.READY, State.LISTENING)
        try:
            managed_display_active = bool(self._app.chat.managed_display_active())
        except Exception:
            managed_display_active = False
        idle_due = (
            config.ENABLE_CRT_SCREENSAVER
            and idle_allowed
            and not managed_display_active
            and float(status.get("idle_seconds") or 0.0)
            >= float(config.CRT_SCREENSAVER_IDLE_SECONDS)
            and not status.get("parts_menu")
        )
        if idle_due and self._saver_started is None:
            # Not over a movie or a game (2026-10-20; systems/saver_guard.py). Asked only before the
            # saver starts: the saver's own full-screen window must not stop it.
            if getattr(self, "_saver_guard", None) is None:
                from systems.saver_guard import SaverGuard
                self._saver_guard = SaverGuard()
            if self._saver_guard.hold_off():
                idle_due = False

        if status.get("calibrating"):
            # Calibration owns the screen until keep / cancel / timeout.
            self._reset_crt_static_refresh(now)
            self._stop_external_saver()
            self._saver_started = None
            self._render_calibration(status)
            if hasattr(self._app, "present"):
                self._app.present()
            else:
                pygame.display.flip()
            self.frame_timer = now
            self.fpsclock.tick(self.FPS)
            return

        setup = status.get("setup")
        if setup:
            # The install screen owns the window until the setup is done (systems/first_boot.py).
            self._reset_crt_static_refresh(now)
            self._stop_external_saver()
            self._saver_started = None
            self._render_setup(setup, now)
            if hasattr(self._app, "present"):
                self._app.present()
            else:
                pygame.display.flip()
            self.frame_timer = now
            self.fpsclock.tick(self.FPS)
            return

        display_mode = str(status.get("display_mode") or "face")
        switch_visible = self._switch_visible(status, now)
        if self._switch_was_visible and not switch_visible and self._runner is not None:
            self._runner.finish()  # the brain is ready mid-run: a best score still counts
        self._switch_was_visible = switch_visible
        static_allowed = (
            display_mode == "face"
            and not switch_visible
            and not status.get("parts_menu")
            and not idle_due
            and not managed_display_active
            and self._app.chat.state in (State.READY, State.LISTENING)
        )
        static_active = self._crt_static_refresh_active(now, allowed=static_allowed)

        if display_mode == "text":
            self._reset_crt_static_refresh(now)
            if idle_due:
                self._render_idle_backend(dt, now)
            else:
                self._stop_external_saver()
                self._saver_started = None
                self._render_text_mode(status)
        elif switch_visible:
            self._reset_crt_static_refresh(now)
            self._stop_external_saver()
            self._saver_started = None
            self._render_model_switch(status, now)
        elif status.get("parts_menu"):
            self._reset_crt_static_refresh(now)
            self._stop_external_saver()
            self._saver_started = None
            self._render_parts_menu(status, now)
        elif idle_due:
            self._reset_crt_static_refresh(now)
            self._render_idle_backend(dt, now)
        elif static_active:
            self._stop_external_saver()
            self._saver_started = None
            self._render_crt_static_refresh(now)
        else:
            self._stop_external_saver()
            self._saver_started = None
            self._render_face()

        if hasattr(self._app, "present"):
            self._app.present()
        else:
            pygame.display.flip()
        self.frame_timer = now
        self.fpsclock.tick(self.FPS)


def np_max(values):
    # Tiny helper avoids importing all of numpy into the renderer just to scale
    # the optional debug graph; values is already a numpy array from Chat.
    return values.max() if hasattr(values, "max") else max(values)
