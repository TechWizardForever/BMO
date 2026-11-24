"""What the CRT shows while the radio plays (2026-11-24): a pixel-art radio with the station on its dial, and music notes
that pop out of its speaker and bounce around the screen, off the edges and off the radio. Everything keeps moving
a little (the notes, the needle's wobble, the knobs, the ON AIR lamp, a long name scrolling), so nothing burns in.

The radio is real pixel art: drawn once on a 128 x 88 grid, shaded, and scaled up by a whole number without smoothing,
so it stays crisp on a CRT. The parts that move are drawn on the same grid every frame.
"""
from __future__ import annotations

import math

from systems.music_screen import COLORS, NOTES, Notes

GRID_W, GRID_H = 128, 88
BACK = (0, 0, 0)

# BMO's teal cabinet, with light from the top left
TEAL = (40, 132, 116)
TEAL_LIGHT = (86, 186, 164)
TEAL_SHINE = (150, 226, 206)
TEAL_DARK = (24, 86, 76)
TEAL_DEEP = (14, 52, 46)
CREAM = (232, 222, 188)
CREAM_DARK = (196, 184, 146)
CREAM_LIGHT = (248, 242, 220)
CHROME = (196, 204, 204)
CHROME_LIGHT = (240, 246, 246)
CHROME_DARK = (112, 122, 122)
CLOTH = (52, 46, 40)
CLOTH_LIGHT = (74, 66, 58)
CLOTH_DARK = (34, 30, 26)
GLASS = (8, 34, 18)
GLASS_GLARE = (30, 70, 48)
DIAL_TEXT = (108, 245, 206)
DIAL_DIM = (60, 150, 120)
NEEDLE = (240, 72, 60)
KNOB = (44, 48, 46)
KNOB_LIGHT = (120, 128, 124)
KNOB_SHINE = (190, 198, 194)
LAMP_ON, LAMP_GLOW, LAMP_OFF = (255, 84, 64), (255, 170, 150), (96, 36, 30)
FOOT = (30, 34, 32)

# Where the parts sit on the grid
HANDLE = (40, 5, 48, 13)
CABINET = (4, 14, 120, 64)
PANEL = (10, 20, 108, 52)
GRILLE = (16, 26, 40, 38)
DIAL = (62, 26, 50, 27)
KNOBS = ((72, 62), (102, 62))
KNOB_R = 6
LAMP = (20, 67)
ANTENNA = ((104, 15), (120, 1))
FREQUENCIES = ("88", "98", "108")  # more ran together at 4 pixels a letter

# A 3 x 5 pixel alphabet for the printing on the radio
TINY = {
    "0": ("###", "#.#", "#.#", "#.#", "###"), "1": (".#.", "##.", ".#.", ".#.", "###"),
    "2": ("###", "..#", "###", "#..", "###"), "4": ("#.#", "#.#", "###", "..#", "..#"),
    "6": ("###", "#..", "###", "#.#", "###"), "8": ("###", "#.#", "###", "#.#", "###"),
    "9": ("###", "#.#", "###", "..#", "###"), "O": ("###", "#.#", "#.#", "#.#", "###"),
    "N": ("#.#", "###", "###", "###", "#.#"), "A": (".#.", "#.#", "###", "#.#", "#.#"),
    "I": ("###", ".#.", ".#.", ".#.", "###"), "R": ("##.", "#.#", "##.", "#.#", "#.#"),
    "B": ("##.", "#.#", "##.", "#.#", "##."), "M": ("#.#", "###", "###", "#.#", "#.#"),
    " ": ("...", "...", "...", "...", "..."),
}


def _tiny(surface, text: str, x: int, y: int, color) -> None:
    for ch in text:
        for row, line in enumerate(TINY[ch]):
            for col, cell in enumerate(line):
                if cell == "#":
                    surface.set_at((x + col, y + row), color)
        x += 4


def _tiny_width(text: str) -> int:
    return len(text) * 4 - 1


def _bevel(surface, rect, base, light, dark, radius) -> None:
    """A rounded box lit from the top left: dark edge, then the face, then light along the top and left."""
    import pygame
    x, y, w, h = rect
    pygame.draw.rect(surface, dark, (x, y, w, h), border_radius=radius)
    pygame.draw.rect(surface, light, (x, y, w - 1, h - 1), border_radius=radius)
    pygame.draw.rect(surface, base, (x + 1, y + 1, w - 2, h - 2), border_radius=max(0, radius - 1))


def _art():
    """The still parts of the radio on its 128 x 88 grid."""
    import pygame
    art = pygame.Surface((GRID_W, GRID_H), pygame.SRCALPHA)
    # The carry handle, with its shine
    hx, hy, hw, hh = HANDLE
    pygame.draw.rect(art, TEAL_DEEP, (hx, hy + 1, hw, hh), width=3, border_radius=5)
    pygame.draw.rect(art, TEAL_DARK, (hx, hy, hw, hh), width=3, border_radius=5)
    pygame.draw.line(art, TEAL_LIGHT, (hx + 5, hy), (hx + hw - 6, hy))
    # The feet, then the cabinet: deep shadow under it, the light top and left edges, a shine along the top
    for fx in (14, 100):
        pygame.draw.rect(art, FOOT, (fx, 77, 14, 5), border_radius=1)
    cx, cy, cw, ch = CABINET
    pygame.draw.rect(art, TEAL_DEEP, (cx, cy + 2, cw, ch), border_radius=7)
    _bevel(art, CABINET, TEAL, TEAL_LIGHT, TEAL_DARK, 7)
    pygame.draw.line(art, TEAL_SHINE, (cx + 8, cy + 1), (cx + cw - 9, cy + 1))
    pygame.draw.line(art, TEAL_SHINE, (cx + 1, cy + 8), (cx + 1, cy + 20))
    for k in range(cx + 10, cx + cw - 10, 6):  # a fine pinstripe along the top band
        art.set_at((k, cy + 3), TEAL_LIGHT)
    # The cream front panel, set in: dark above and left, light below and right
    px, py, pw, ph = PANEL
    pygame.draw.rect(art, CREAM_DARK, (px, py, pw, ph), border_radius=4)
    pygame.draw.rect(art, CREAM_LIGHT, (px + 1, py + 1, pw - 1, ph - 1), border_radius=4)
    pygame.draw.rect(art, CREAM, (px + 1, py + 1, pw - 2, ph - 2), border_radius=3)
    # The speaker: a chrome frame around woven cloth, darker toward the bottom
    gx, gy, gw, gh = GRILLE
    _bevel(art, (gx - 2, gy - 2, gw + 4, gh + 4), CHROME, CHROME_LIGHT, CHROME_DARK, 3)
    pygame.draw.rect(art, CLOTH_DARK, (gx, gy, gw, gh), border_radius=2)
    for yy in range(gy + 1, gy + gh - 1):
        for xx in range(gx + 1, gx + gw - 1):
            weave = (xx + yy) % 4 == 0 or (xx - yy) % 4 == 0
            if weave:
                art.set_at((xx, yy), CLOTH_LIGHT if yy < gy + gh * 0.6 else CLOTH)
            elif (xx + yy) % 2 == 0:
                art.set_at((xx, yy), CLOTH)
    for k in range(3):  # three chrome bars across the cloth
        bar_y = gy + (k + 1) * gh // 4
        pygame.draw.line(art, CHROME_DARK, (gx + 1, bar_y + 1), (gx + gw - 2, bar_y + 1))
        pygame.draw.line(art, CHROME_LIGHT, (gx + 1, bar_y), (gx + gw - 2, bar_y))
    # The dial window: a chrome bezel, dark glass with a glare streak, ticks and the frequencies
    dx, dy, dw, dh = DIAL
    _bevel(art, (dx - 2, dy - 2, dw + 4, dh + 4), CHROME, CHROME_LIGHT, CHROME_DARK, 3)
    pygame.draw.rect(art, GLASS, (dx, dy, dw, dh), border_radius=2)
    for k in range(4):  # a "//" shine in the glass's top right corner
        art.set_at((dx + dw - 8 + k, dy + 5 - k), GLASS_GLARE)
        art.set_at((dx + dw - 5 + k, dy + 5 - k), GLASS_GLARE)
    scale_y = dy + dh - 9
    pygame.draw.line(art, DIAL_DIM, (dx + 3, scale_y), (dx + dw - 4, scale_y))
    for k in range(21):
        x = dx + 3 + k * (dw - 7) // 20
        pygame.draw.line(art, DIAL_DIM, (x, scale_y - (2 if k % 4 == 0 else 1)), (x, scale_y))
    for k, label in enumerate(FREQUENCIES):
        x = dx + 3 + k * (dw - 7) // (len(FREQUENCIES) - 1) - _tiny_width(label) // 2
        _tiny(art, label, max(dx + 1, min(x, dx + dw - 1 - _tiny_width(label))), scale_y + 2, DIAL_DIM)
    # The knobs' chrome collars
    for kx, ky in KNOBS:
        pygame.draw.circle(art, CHROME_DARK, (kx, ky + 1), KNOB_R + 2)
        pygame.draw.circle(art, CHROME, (kx, ky), KNOB_R + 2)
        pygame.draw.circle(art, CHROME_LIGHT, (kx - 1, ky - 1), KNOB_R + 1, width=1)
    # The printing: ON AIR under the speaker, BMO's badge on the cabinet
    _tiny(art, "ON AIR", LAMP[0] + 6, LAMP[1], TEAL_DARK)
    badge = (64 - 8, 73, 17, 7)
    _bevel(art, badge, CHROME, CHROME_LIGHT, CHROME_DARK, 1)
    _tiny(art, "BMO", badge[0] + 3, badge[1] + 1, TEAL_DEEP)
    # The antenna, in segments that catch the light
    (ax, ay), (bx, by) = ANTENNA
    steps = max(abs(bx - ax), abs(by - ay))
    for k in range(steps + 1):
        x, y = round(ax + (bx - ax) * k / steps), round(ay + (by - ay) * k / steps)
        art.set_at((x, y), CHROME_LIGHT if (k // 4) % 2 == 0 else CHROME_DARK)
    pygame.draw.circle(art, CHROME_DARK, (bx, by + 1), 2)
    pygame.draw.circle(art, CHROME_LIGHT, (bx, by), 1)
    return art


def layout(w: int, h: int) -> dict:
    """The radio's size and place on a w x h screen: a whole-number scale of the grid, centred a little low."""
    import pygame
    scale = max(1, min(int(w * 0.66 // GRID_W), int(h * 0.58 // GRID_H)))
    left = (w - GRID_W * scale) // 2
    top = int(h * 0.56) - GRID_H * scale // 2
    cabinet = pygame.Rect(left + CABINET[0] * scale, top + CABINET[1] * scale, CABINET[2] * scale, (CABINET[3] + 4) * scale)
    speaker = (left + (GRILLE[0] + GRILLE[2] // 2) * scale, top + GRILLE[1] * scale)
    return {"scale": scale, "left": left, "top": top, "body": cabinet, "speaker": speaker}


class RadioNotes(Notes):
    """The music notes, born above the speaker one at a time, then bouncing off the screen's edges and the radio."""

    BIRTH_EVERY = 0.45

    def __init__(self, count: int = 10, seed: int | None = None):
        super().__init__(count=count, seed=seed)
        self._born_at = 0.0
        self._radio = None

    def _place(self, w: int, h: int) -> None:
        self._notes = []
        self._size = (w, h)
        self._radio = layout(w, h)
        self._born_at = 0.0

    def _birth(self, w: int, h: int) -> None:
        kinds = list(NOTES)
        k = len(self._notes)
        x, _ = self._radio["speaker"]
        speed = 0.22 * h * self._random.uniform(0.8, 1.25)
        angle = self._random.uniform(math.radians(200), math.radians(340))  # up and out, never straight down
        pixel = self.pixel(h, k % 3 == 0)
        art = NOTES[kinds[k % len(kinds)]]
        self._notes.append({
            "kind": kinds[k % len(kinds)], "big": k % 3 == 0, "color": k % len(COLORS),
            "x": x - len(art[0]) * pixel / 2, "y": self._radio["body"].top - len(art) * pixel,
            "dx": speed * math.cos(angle), "dy": -abs(speed * math.sin(angle)), "age": 0.0,
        })

    def update(self, dt: float, w: int, h: int) -> None:
        if self._size != (w, h):
            self._place(w, h)
        dt = max(0.0, min(0.1, dt))
        self._born_at += dt
        if len(self._notes) < self._count and self._born_at >= self.BIRTH_EVERY:
            self._born_at = 0.0
            self._birth(w, h)
        body = self._radio["body"]
        for note in self._notes:
            pixel = self.pixel(h, note["big"])
            art = NOTES[note["kind"]]
            nw, nh = len(art[0]) * pixel, len(art) * pixel
            note["age"] = note.get("age", 0.0) + dt
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
            if note["age"] > 0.6 and body.colliderect((note["x"], note["y"], nw, nh)):
                # off the radio, from whichever side it came in by least
                overlaps = {"left": note["x"] + nw - body.left, "right": body.right - note["x"],
                            "top": note["y"] + nh - body.top, "bottom": body.bottom - note["y"]}
                side = min(overlaps, key=overlaps.get)
                if side == "left":
                    note["x"], note["dx"] = body.left - nw, -abs(note["dx"])
                elif side == "right":
                    note["x"], note["dx"] = body.right, abs(note["dx"])
                elif side == "top":
                    note["y"], note["dy"] = body.top - nh, -abs(note["dy"])
                else:
                    note["y"], note["dy"] = body.bottom, abs(note["dy"])
                bounced = True
            if bounced:  # a new colour at every bounce, as on the music screen
                note["color"] = (note["color"] + self._random.randint(1, len(COLORS) - 1)) % len(COLORS)

    def draw_notes(self, surface) -> None:
        h = surface.get_height()
        for note in self._notes:
            surface.blit(self.sprite(note["kind"], note["color"], self.pixel(h, note["big"])),
                         (int(note["x"]), int(note["y"])))


def draw_radio(surface, station: dict, now: float, fonts, cache: dict) -> None:
    """The radio. `station`: name, and what it plays or where it's from (about); `fonts`: (name, about)."""
    import pygame
    w, h = surface.get_size()
    place = layout(w, h)
    s, left, top = place["scale"], place["left"], place["top"]
    if cache.get("scale") != s:
        cache["scale"], cache["art"] = s, pygame.transform.scale(_art(), (GRID_W * s, GRID_H * s))
    surface.blit(cache["art"], (left, top))

    def px(x, y, color, n=1):
        surface.fill(color, (left + x * s, top + y * s, n * s, s))

    # The knobs, slowly turning: ridges round the rim, a shine, and the pointer
    for n, (kx, ky) in enumerate(KNOBS):
        pygame.draw.circle(surface, KNOB, (left + kx * s + s // 2, top + ky * s + s // 2), KNOB_R * s)
        turn = now * (0.35 if n == 0 else -0.25)
        for k in range(12):
            a = turn + k * math.pi / 6
            px(round(kx + math.cos(a) * (KNOB_R - 1)), round(ky + math.sin(a) * (KNOB_R - 1)), KNOB_LIGHT)
        px(kx - 2, ky - 3, KNOB_SHINE, 2)
        px(kx - 3, ky - 2, KNOB_SHINE)
        for r in range(1, KNOB_R - 1):
            px(round(kx + math.cos(turn) * r), round(ky + math.sin(turn) * r), KNOB_SHINE)
    # The ON AIR lamp, blinking
    on = int(now * 1.2) % 2 == 0
    lx, ly = LAMP
    px(lx, ly, LAMP_ON if on else LAMP_OFF, 3)
    px(lx, ly + 1, LAMP_ON if on else LAMP_OFF, 3)
    px(lx, ly + 2, LAMP_ON if on else LAMP_OFF, 3)
    if on:
        px(lx, ly, LAMP_GLOW)
    # The needle, at the station's own place on the dial, trembling a little
    dx, dy, dw, dh = DIAL
    spot = (sum(map(ord, str(station.get("name", "")))) % 80 + 10) / 100.0
    nx = dx + 3 + int((dw - 7) * spot) + (1 if math.sin(now * 2.3) > 0.7 else 0)
    for y in range(dy + dh - 14, dy + dh - 8):
        px(nx, y, NEEDLE)
    # The station's name across the top of the glass (scrolling when long), and what it plays under it
    name_font, about_font = fonts
    room = pygame.Rect(left + (dx + 3) * s, top + (dy + 2) * s, (dw - 6) * s, (dh - 13) * s)
    name = name_font.render(str(station.get("name", "")).upper(), False, DIAL_TEXT)
    clip = surface.get_clip()
    surface.set_clip(room)
    if name.get_width() <= room.width:
        surface.blit(name, name.get_rect(midtop=(room.centerx, room.top)))
    else:  # a long name scrolls across the glass, like a digital radio's display
        loop = name.get_width() + name_font.size("    ")[0]
        shift = int(now * max(40, h // 12)) % loop
        surface.blit(name, (room.left - shift, room.top))
        surface.blit(name, (room.left - shift + loop, room.top))
    about = str(station.get("about", "")).upper()
    if about:
        line = about_font.render(about, False, DIAL_DIM)
        surface.blit(line, line.get_rect(midtop=(room.centerx, room.top + name.get_height())))
    surface.set_clip(clip)


def render(renderer, station: dict, now: float, dt: float) -> None:
    """The radio screen in BMO's window: black, the notes, then the radio on top."""
    screen = renderer._app._screen
    w, h = screen.get_size()
    notes = getattr(renderer, "_radio_notes", None)
    if notes is None:
        notes = renderer._radio_notes = RadioNotes()
    s = layout(w, h)["scale"]
    fonts = getattr(renderer, "_radio_fonts", None)
    if fonts is None or fonts[0] != (h, s):
        from systems.render import ui_font
        fonts = renderer._radio_fonts = ((h, s), (ui_font(max(12, 5 * s)), ui_font(max(9, 3 * s + 1))), {})
    notes.update(dt, w, h)
    screen.fill(BACK)
    notes.draw_notes(screen)
    draw_radio(screen, station, now, fonts[1], fonts[2])
