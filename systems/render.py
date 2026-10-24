import math
import random as rand
import time

import pygame

import config
from systems.chat import State


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
        self.SMALL_FONT = pygame.font.SysFont("DejaVu Sans Mono", 18)
        self.MEDIUM_FONT = pygame.font.SysFont("DejaVu Sans Mono", 26, bold=True)
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
        # Model-switch loading / mini-game state.
        # ------------------------------------------------------------------
        self._seen_switch_generation = -1
        self._game_last_update = time.monotonic()
        self._game_last_spawn = time.monotonic()
        self._game_packets = []
        self._game_score = 0
        self._game_missed = 0
        self._game_paddle_x = self._app.SCREEN_WIDTH / 2.0
        self._game_paddle_direction = 0

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
        self._game_paddle_x = self._app.SCREEN_WIDTH / 2.0
        self._game_packets = []

    # ----------------------------------------------------------------------
    # Input used only by the model-switch mini-game. App owns wake/quit/F11.
    # ----------------------------------------------------------------------
    def handle_event(self, event):
        if not config.ENABLE_MODEL_SWITCH_MINIGAME:
            return
        if event.type == pygame.KEYDOWN:
            if event.key in (pygame.K_LEFT, pygame.K_a):
                self._game_paddle_direction = -1
            elif event.key in (pygame.K_RIGHT, pygame.K_d):
                self._game_paddle_direction = 1
        elif event.type == pygame.KEYUP:
            if event.key in (pygame.K_LEFT, pygame.K_a) and self._game_paddle_direction < 0:
                self._game_paddle_direction = 0
            elif event.key in (pygame.K_RIGHT, pygame.K_d) and self._game_paddle_direction > 0:
                self._game_paddle_direction = 0
        elif event.type == pygame.MOUSEMOTION:
            # Mouse movement is optional; it makes the loading game usable even
            # on a tiny keyboard-less setup without changing normal Bemo input.
            viewport = getattr(self._app, "viewport_rect", None)
            x = float(event.pos[0] - (viewport.left if viewport is not None else 0))
            self._game_paddle_x = max(0.0, min(float(self._app.SCREEN_WIDTH), x))

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
    # Model-switch loading screen + optional Data Catch mini-game.
    # ----------------------------------------------------------------------
    def _reset_switch_game(self, generation, now):
        self._seen_switch_generation = generation
        self._game_last_update = now
        self._game_last_spawn = now
        self._game_packets = []
        self._game_score = 0
        self._game_missed = 0
        self._game_paddle_x = self._app.SCREEN_WIDTH / 2.0
        self._game_paddle_direction = 0

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

    def _update_switch_game(self, now):
        dt = min(0.1, max(0.0, now - self._game_last_update))
        self._game_last_update = now
        w, h = self._app.SCREEN_WIDTH, self._app.SCREEN_HEIGHT
        play_left = int(w * 0.16)
        play_right = int(w * 0.84)
        paddle_half = max(38.0, w * 0.045)
        self._game_paddle_x += (
            self._game_paddle_direction
            * float(config.MODEL_SWITCH_GAME_PADDLE_SPEED)
            * dt
        )
        self._game_paddle_x = max(play_left + paddle_half, min(play_right - paddle_half, self._game_paddle_x))

        if now - self._game_last_spawn >= max(0.12, float(config.MODEL_SWITCH_GAME_PACKET_INTERVAL_SECONDS)):
            self._game_last_spawn = now
            self._game_packets.append(
                {
                    "x": rand.uniform(play_left + 12, play_right - 12),
                    "y": h * 0.34,
                    "speed": float(config.MODEL_SWITCH_GAME_PACKET_SPEED) * rand.uniform(0.78, 1.28),
                    "phase": rand.uniform(0, math.tau),
                }
            )

        paddle_y = h * 0.83
        next_packets = []
        for packet in self._game_packets:
            packet["y"] += packet["speed"] * dt
            packet["x"] += math.sin(now * 2.2 + packet["phase"]) * 18.0 * dt
            if packet["y"] >= paddle_y - 10:
                if abs(packet["x"] - self._game_paddle_x) <= paddle_half + 8:
                    self._game_score += 1
                else:
                    self._game_missed += 1
                continue
            if packet["y"] < h + 20:
                next_packets.append(packet)
        self._game_packets = next_packets[-30:]
        return dt, play_left, play_right, paddle_half, paddle_y


    def _render_retro_progress_bar(self, rect, now):
        """Render a Windows-95-ish indeterminate segmented progress bar.

        This intentionally does not pretend that model loading has a known
        percentage. A short train of blue blocks cycles through a classic
        sunken 3-D track until Supervisor reports the model switch complete.
        """
        screen = self._app._screen
        x, y, width, height = rect
        rect = pygame.Rect(int(x), int(y), int(width), int(height))

        # Win95-style sunken bevel: dark top/left, bright bottom/right.
        face = (192, 192, 192)
        shadow = (128, 128, 128)
        dark = (64, 64, 64)
        highlight = (255, 255, 255)
        track = (236, 233, 216)
        blue = (0, 0, 128)
        blue_hi = (0, 0, 255)

        pygame.draw.rect(screen, face, rect)
        pygame.draw.line(screen, dark, rect.topleft, (rect.right - 1, rect.top), 1)
        pygame.draw.line(screen, dark, rect.topleft, (rect.left, rect.bottom - 1), 1)
        pygame.draw.line(screen, highlight, (rect.left, rect.bottom - 1), (rect.right - 1, rect.bottom - 1), 1)
        pygame.draw.line(screen, highlight, (rect.right - 1, rect.top), (rect.right - 1, rect.bottom - 1), 1)
        inner = rect.inflate(-4, -4)
        pygame.draw.line(screen, shadow, inner.topleft, (inner.right - 1, inner.top), 1)
        pygame.draw.line(screen, shadow, inner.topleft, (inner.left, inner.bottom - 1), 1)
        pygame.draw.rect(screen, track, inner.inflate(-2, -2))

        content = inner.inflate(-4, -4)
        blocks = max(6, int(config.MODEL_SWITCH_RETRO_PROGRESS_BLOCKS))
        lit = max(1, min(blocks - 1, int(config.MODEL_SWITCH_RETRO_PROGRESS_LIT_BLOCKS)))
        gap = max(1, int(content.height * 0.18))
        block_w = max(2, (content.width - gap * (blocks - 1)) // blocks)
        used = block_w * blocks + gap * (blocks - 1)
        start_x = content.left + max(0, (content.width - used) // 2)
        step_seconds = max(0.04, float(config.MODEL_SWITCH_RETRO_PROGRESS_STEP_SECONDS))
        head = int(now / step_seconds) % (blocks + lit)

        for i in range(blocks):
            # The train wraps cleanly, with older blocks slightly darker.
            age = (head - i) % (blocks + lit)
            if age < lit:
                color = blue_hi if age == 0 else blue
                bx = start_x + i * (block_w + gap)
                pygame.draw.rect(
                    screen,
                    color,
                    pygame.Rect(bx, content.top, block_w, content.height),
                )

    @staticmethod
    def _pretty_role(label):
        return str(label or "Bemo").replace("_", " ").strip().title()

    def _render_model_switch(self, status, now):
        screen = self._app._screen
        w, h = self._app.SCREEN_WIDTH, self._app.SCREEN_HEIGHT
        generation = int(status.get("model_switch_generation") or 0)
        if generation != self._seen_switch_generation:
            self._reset_switch_game(generation, now)

        screen.fill((2, 10, 11))
        # Slowly moving grid / scan motif so the loading page itself is not static.
        grid = max(28, int(min(w, h) * 0.055))
        offset_x = int((now * 23) % grid)
        offset_y = int((now * 11) % grid)
        for x in range(-grid + offset_x, w + grid, grid):
            pygame.draw.line(screen, (5, 34, 34), (x, 0), (x, h), 1)
        for y in range(-grid + offset_y, h + grid, grid):
            pygame.draw.line(screen, (5, 28, 29), (0, y), (w, y), 1)

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

        _, play_left, play_right, paddle_half, paddle_y = self._update_switch_game(now)
        play_top = int(h * 0.32)
        play_bottom = int(h * 0.88)
        pygame.draw.rect(
            screen,
            (13, 50, 48),
            pygame.Rect(play_left, play_top, play_right - play_left, play_bottom - play_top),
            width=2,
            border_radius=10,
        )
        for packet in self._game_packets:
            x, y = int(packet["x"]), int(packet["y"])
            pygame.draw.rect(screen, (89, 233, 185), pygame.Rect(x - 6, y - 6, 12, 12), border_radius=2)
            pygame.draw.rect(screen, (183, 255, 226), pygame.Rect(x - 3, y - 3, 6, 6), border_radius=1)
        paddle = pygame.Rect(
            int(self._game_paddle_x - paddle_half),
            int(paddle_y),
            int(paddle_half * 2),
            max(8, int(h * 0.015)),
        )
        pygame.draw.rect(screen, (75, 228, 180), paddle, border_radius=5)

        game_label = self.SMALL_FONT.render(
            f"DATA CATCH   SCORE {self._game_score}   MISSED {self._game_missed}",
            True,
            (116, 215, 192),
        )
        screen.blit(game_label, game_label.get_rect(center=(w // 2 - drift, int(h * 0.92))))
        controls = self.SMALL_FONT.render("< >  or  A D", True, (55, 135, 124))
        screen.blit(controls, controls.get_rect(center=(w // 2 + drift, int(h * 0.965))))

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
            cache = (w, pygame.font.SysFont("DejaVu Sans Mono", max(18, w // 30), bold=True),
                     pygame.font.SysFont("DejaVu Sans Mono", max(11, w // 70)))
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
            cache = (w, pygame.font.SysFont("DejaVu Sans Mono", max(18, w // 32), bold=True),
                     pygame.font.SysFont("DejaVu Sans Mono", max(14, w // 44)))
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

        display_mode = str(status.get("display_mode") or "face")
        switch_visible = self._switch_visible(status, now)
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
