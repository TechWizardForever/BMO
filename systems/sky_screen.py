"""BMO's sky screens (2026-11-03): tonight's sky, the moon, the space station and what's coming up, with pixel art that
stays crisp on a CRT: a moon that shows its phase, a little International Space Station flying over the curve of the Earth,
meteors, eclipses, and two lights drawing together. Everything sits 6% in from the edges, like the weather screens.
"""
from __future__ import annotations

from datetime import datetime, timezone
import math
import random

import pygame

from systems import sky as sky_data
from systems.weather_screen import MARGIN, _fitted, _text

NIGHT, TEXT, DIM, HEAD, WARM, EDGE = (3, 6, 18), (214, 226, 240), (110, 130, 160), (150, 205, 255), (255, 214, 130), (40, 70, 110)
BOX = (8, 14, 32)
MOON_LIT, MOON_DARK, MOON_SPOT, MOON_RED = (232, 228, 205), (26, 30, 46), (196, 192, 170), (170, 62, 40)
SUN = (255, 214, 90)
PANEL, TRUSS, GOLD, EARTH, EARTH_EDGE = (60, 110, 200), (216, 220, 228), (222, 176, 80), (22, 64, 130), (110, 180, 255)
PLANET_COLORS = {"Mercury": (190, 180, 170), "Venus": (250, 240, 200), "Mars": (235, 110, 80), "Jupiter": (235, 205, 165),
                 "Saturn": (225, 200, 130)}
# The station: solar arrays either side of the long truss, the modules in the middle. A pixel is one letter.
ISS_ART = ("AaAaA.AaAaA.......w.......AaAaA.AaAaA",
           "aAaAa.aAaAa......RRR......aAaAa.aAaAa",
           "AaAaA.AaAaA.......w.......AaAaA.AaAaA",
           "aAaAa.aAaAa.......w.......aAaAa.aAaAa",
           "AaAaA.AaAaA.....GWWWG.....AaAaA.AaAaA",
           "WWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWW",
           "sssssssssssssssssssssssssssssssssssss",
           "AaAaA.AaAaA.....GWWWs.....AaAaA.AaAaA",
           "aAaAa.aAaAa.....WWWWs.....aAaAa.aAaAa",
           "AaAaA.AaAaA......WWs......AaAaA.AaAaA",
           "aAaAa.aAaAa.......w.......aAaAa.aAaAa",
           "AaAaA.AaAaA......RRR......AaAaA.AaAaA",
           "..................w..................")
_ISS_COLORS = {"A": PANEL, "a": (38, 76, 150), "W": TRUSS, "w": (176, 182, 194), "s": (110, 116, 130), "G": GOLD, "R": (236, 240, 246)}


# --- The pixel art ----------------------------------------------------------------------------------------

def stars(screen, w, h, now):
    screen.fill(NIGHT)
    field = random.Random(11)
    for n in range(max(60, w * h // 9000)):
        x, y, base, rate = field.randrange(w), field.randrange(h), field.randint(70, 200), field.uniform(0.4, 1.6)
        level = int(base * (0.65 + 0.35 * math.sin(now * rate + n)))
        screen.fill((level, level, min(255, level + 20)), (x, y, 2 if n % 7 == 0 else 1, 2 if n % 7 == 0 else 1))


# The near side's seas and best-known craters, in the moon's own coordinates (x right, y down, the disc radius 1), so
# it looks like our moon and not a random one (2026-11-15).
_MARIA = ((-0.38, -0.40, 0.30, 0.22), (-0.62, 0.05, 0.26, 0.42), (0.02, -0.30, 0.18, 0.15), (0.20, 0.02, 0.22, 0.18),
          (0.60, -0.18, 0.13, 0.12), (0.30, 0.38, 0.16, 0.12), (-0.18, 0.18, 0.12, 0.10), (-0.42, 0.44, 0.16, 0.10))
_CRATERS = ((-0.12, 0.72, 0.08), (-0.30, -0.06, 0.06), (0.40, 0.62, 0.05), (-0.70, -0.42, 0.05), (0.62, 0.30, 0.05),
            (0.10, -0.70, 0.05))


def _shade(color, k):
    return tuple(max(0, min(255, int(c * k))) for c in color)


def draw_moon(screen, center, radius, cycle, *, lit=MOON_LIT, dark=MOON_DARK):
    """A pixel moon lit by the sun from the right as it grows and the left as it shrinks (cycle: 0 new, 0.5 full, 1 new):
    a soft edge where day meets night, the real seas and craters, a little earthshine on the dark part, and a dimmer rim."""
    cx, cy, radius = int(center[0]), int(center[1]), max(3, int(radius))
    key = (radius, round(cycle * 96) / 96, tuple(lit), tuple(dark))
    cache = _MOON_CACHE
    if key not in cache:
        if len(cache) > 48:
            cache.clear()
        cache[key] = _moon_surface(radius, key[1], lit, dark)
    picture = cache[key]
    screen.blit(picture, picture.get_rect(center=(cx, cy)))


_MOON_CACHE: dict = {}


def _moon_surface(radius, cycle, lit, dark):
    cells = max(12, min(36, radius // 2))  # chunky enough to read as pixel art at any size
    grid = pygame.Surface((cells, cells), pygame.SRCALPHA)
    angle = 2 * math.pi * cycle
    sun = (math.sin(angle), 0.0, -math.cos(angle))
    earthshine = tuple(min(255, d + 10) for d in dark)
    for j in range(cells):
        for i in range(cells):
            u, v = (i + 0.5) / cells * 2 - 1, (j + 0.5) / cells * 2 - 1
            r2 = u * u + v * v
            if r2 > 1:
                continue
            z = math.sqrt(1 - r2)
            light = u * sun[0] + z * sun[2]
            sea = any(((u - x) / a) ** 2 + ((v - y) / b) ** 2 <= 1 for x, y, a, b in _MARIA)
            tone = 0.70 if sea else 1.0
            for x, y, size in _CRATERS:
                d = math.hypot(u - x, v - y)
                if d <= size:
                    tone = 0.82 if (u - x) * sun[0] > 0 else 1.08  # the rim facing the sun bright, the far side dark
                elif d <= size * 1.5 and not sea:
                    tone = min(tone, 0.94)
            rim = 0.80 + 0.20 * z  # dimmer towards the edge
            if light > 0.10:
                color = _shade(lit, tone * rim)
            elif light > -0.02:  # the soft line between day and night, dithered
                color = _shade(lit, tone * rim * 0.62) if (i + j) % 2 else _shade(earthshine, 1.15)
            else:
                color = _shade(earthshine if sea is False else dark, 0.9 + 0.2 * z)
            grid.set_at((i, j), (*color, 255))
    return pygame.transform.scale(grid, (radius * 2, radius * 2))


def draw_planet(screen, name, center, radius):
    """Each planet as itself: Saturn's rings, Jupiter's bands, Mars's ice cap, Venus's glow."""
    color = PLANET_COLORS.get(name, TEXT)
    x, y, r = int(center[0]), int(center[1]), max(3, int(radius))
    if name == "Venus":
        glow = pygame.Surface((r * 6, r * 6), pygame.SRCALPHA)
        pygame.draw.circle(glow, (*color, 22), (r * 3, r * 3), int(r * 2.4))
        pygame.draw.circle(glow, (*color, 45), (r * 3, r * 3), int(r * 1.6))
        screen.blit(glow, (x - r * 3, y - r * 3))
    if name == "Saturn":
        pygame.draw.ellipse(screen, _shade(color, 0.75), (x - r * 2, y - r // 2, r * 4, r), max(1, r // 3))
    pygame.draw.circle(screen, color, (x, y), r)
    pygame.draw.circle(screen, _shade(color, 0.72), (x + r // 3, y + r // 3), max(1, r * 2 // 3), max(1, r // 3))
    if name == "Jupiter":
        for k in (-1, 1):
            pygame.draw.line(screen, _shade(color, 0.7), (x - r + 1, y + k * r // 3), (x + r - 1, y + k * r // 3), max(1, r // 4))
    elif name == "Mars":
        pygame.draw.circle(screen, (245, 240, 235), (x, y - r + max(1, r // 4)), max(1, r // 3))
    elif name == "Saturn":
        pygame.draw.line(screen, _shade(color, 0.75), (x - r * 2, y), (x + r * 2, y), max(1, r // 3))
    pygame.draw.circle(screen, _shade(color, 1.2), (x - r // 3, y - r // 3), max(1, r // 4))  # where the sun catches it


def draw_iss(screen, center, pixel):
    x0 = int(center[0]) - len(ISS_ART[0]) * pixel // 2
    y0 = int(center[1]) - len(ISS_ART) * pixel // 2
    for j, row in enumerate(ISS_ART):
        for i, cell in enumerate(row):
            color = _ISS_COLORS.get(cell)
            if color:
                screen.fill(color, (x0 + i * pixel, y0 + j * pixel, pixel, pixel))


def _flight(screen, rect, now):
    """The station crossing over the Earth, again and again, while the Earth slowly turns (systems/earth.py)."""
    from systems import earth
    clip = screen.get_clip()
    screen.set_clip(rect)
    radius = int(min(rect.height * 0.80, rect.width * 0.40))
    middle = (rect.centerx, rect.bottom + int(radius * 0.12))
    for k, alpha in ((16, 36), (9, 70), (4, 130)):  # the atmosphere's glow
        halo = pygame.Surface(rect.size, pygame.SRCALPHA)
        pygame.draw.circle(halo, (*EARTH_EDGE, alpha), (middle[0] - rect.left, middle[1] - rect.top),
                           radius + max(2, rect.height // 90) * k // 4)
        screen.blit(halo, rect.topleft)
    pixel = max(2, radius // 46)
    cells = max(24, 2 * radius // pixel)
    picture = pygame.transform.scale(earth.globe(cells, int(now * earth.DEGREES_PER_SECOND) % 360),
                                     (cells * pixel, cells * pixel))
    screen.blit(picture, picture.get_rect(center=middle))
    pygame.draw.circle(screen, EARTH_EDGE, middle, cells * pixel // 2, max(1, pixel // 2))
    share = (now / 14.0) % 1.0
    top = middle[1] - radius
    x = rect.left - rect.width * 0.2 + share * rect.width * 1.4
    y = top - (top - rect.top) * (0.45 + 0.25 * math.sin(share * math.pi))  # a gentle arc above the curve
    draw_iss(screen, (x, y), max(1, rect.width // 170))
    screen.set_clip(clip)


def draw_event(screen, kind, rect, now, seed=0):
    """A little animation for each kind of event."""
    clip = screen.get_clip()
    screen.set_clip(rect)
    size = min(rect.width, rect.height)
    x, y, now = rect.centerx, rect.centery, now + seed * 1.3
    radius = int(size * 0.32)
    if kind == "meteors":
        dot = max(1, size // 34)
        for n in range(4):
            share = (now * 0.55 + n * 0.31) % 1.0
            hx, hy = rect.left + (0.15 + 0.2 * n + share * 0.5) * rect.width, rect.top + share * rect.height * 0.9
            for k in range(14):  # the trail, unbroken and fading pixel by pixel
                fade = 1 - k / 14
                screen.fill((int(70 + 170 * fade), int(100 + 140 * fade), int(180 + 75 * fade)),
                            (int(hx - k * dot * 1.4), int(hy - k * dot), dot + 1, dot + 1))
            screen.fill((255, 255, 255), (int(hx) - dot, int(hy) - dot, dot * 2, dot * 2))
    elif kind == "lunar eclipse":
        share = (now / 7.0) % 1.0
        depth = 1.0 - abs(share * 2 - 1)
        draw_moon(screen, (x, y), radius, 0.5, lit=tuple(int(a + (b - a) * depth) for a, b in zip(MOON_LIT, MOON_RED)))
    elif kind == "solar eclipse":
        share = (now / 8.0) % 1.0
        offset = int((share * 2 - 1) * radius * 2.2)
        cover = max(0.0, 1 - abs(offset) / (radius * 2.0))
        for n in range(16):  # the corona shows as the moon covers the sun
            angle = n * math.pi / 8 + now * 0.1
            reach = radius * (1.25 + 0.25 * (n % 2)) * (0.6 + 0.6 * cover)
            pygame.draw.line(screen, (255, 250, 220), (x + math.cos(angle) * radius, y + math.sin(angle) * radius),
                             (x + math.cos(angle) * reach, y + math.sin(angle) * reach), max(1, radius // 10))
        pygame.draw.circle(screen, SUN, (x, y), radius)
        pygame.draw.circle(screen, (255, 238, 150), (x - radius // 4, y - radius // 4), radius // 2)
        pygame.draw.circle(screen, (4, 6, 12), (x + offset, y), radius)
    elif kind == "conjunction":
        gap = (0.2 + 0.25 * (1 + math.sin(now * 0.8))) * size * 0.5
        draw_planet(screen, "Venus", (int(x - gap), y), max(2, size // 11))
        draw_planet(screen, "Jupiter", (int(x + gap), y), max(2, size // 13))
    elif kind == "aurora":
        band = max(2, size // 20)
        for n in range(3):
            for i in range(0, rect.width, band):
                top = y - size * 0.3 + n * size * 0.12 + math.sin(now * 1.1 + i * 0.05 + n) * size * 0.08
                height = size * (0.22 + 0.08 * math.sin(i * 0.3 + now * 2 + n))
                for k in range(4):  # brightest at the bottom of each curtain, fading up
                    shade = (30 + 20 * n, int((210 - n * 35) * (1 - k * 0.22)), int((120 + n * 30) * (1 - k * 0.18)))
                    screen.fill(shade, (rect.left + i, int(top + height * (1 - (k + 1) / 4)), band, int(height / 4) + 1))
    elif kind == "iss":
        draw_iss(screen, (x + math.sin(now * 0.5) * size * 0.1, y), max(1, size // 40))
    else:
        draw_moon(screen, (x, y), radius, 0.5)
    screen.set_clip(clip)


# --- The pieces -------------------------------------------------------------------------------------------

def _frame(renderer, heading, note, hint, now, fonts):
    screen = renderer._app._screen
    w, h = renderer._app.SCREEN_WIDTH, renderer._app.SCREEN_HEIGHT
    title_font, _row, small_font = fonts
    stars(screen, w, h, now)
    dx, dy = int(math.sin(now / 37.0) * 3), int(math.cos(now / 53.0) * 2)
    area = pygame.Rect(int(w * MARGIN) + dx, int(h * MARGIN) + dy, int(w * (1 - 2 * MARGIN)), int(h * (1 - 2 * MARGIN)))
    title = _text(screen, title_font, _fitted(title_font, heading, area.width * 0.7), HEAD, topleft=area.topleft)
    if note:
        _text(screen, small_font, _fitted(small_font, note, area.width * 0.28), DIM, bottomright=(area.right, title.bottom))
    line = _text(screen, small_font, hint, WARM, midbottom=(area.centerx, area.bottom))
    return pygame.Rect(area.left, title.bottom + int(h * 0.015), area.width, line.top - title.bottom - int(h * 0.03))


def _box(screen, rect):
    pygame.draw.rect(screen, BOX, rect)
    pygame.draw.rect(screen, EDGE, rect, 1)


def _rows(screen, rect, rows, fonts):
    """Label and value, one under the other, inside a box."""
    _title, row_font, small_font = fonts
    _box(screen, rect)
    height = row_font.get_linesize() + small_font.get_linesize() + 3
    pad = max(4, rect.width // 40)
    y = rect.top + pad
    for label, value in rows:
        if y + height > rect.bottom:
            break
        _text(screen, small_font, label, DIM, topleft=(rect.left + pad, y))
        y += small_font.get_linesize()
        _text(screen, row_font, _fitted(row_font, value, rect.width - 2 * pad), TEXT, topleft=(rect.left + pad, y))
        y += row_font.get_linesize() + 3


def _lines(screen, rect, lines, font, color=TEXT):
    """Wrapped sentences, as many as fit."""
    y = rect.top
    for sentence in lines:
        words, line = str(sentence).split(), ""
        for word in words + [None]:
            trial = f"{line} {word}".strip() if word else line
            if word and font.size(trial)[0] <= rect.width:
                line = trial
                continue
            if y + font.get_linesize() > rect.bottom:
                return
            _text(screen, font, line, color, topleft=(rect.left, y))
            y += font.get_linesize()
            line = word or ""
        y += 3


# --- The screens ------------------------------------------------------------------------------------------

def _tonight(renderer, extra, now, fonts):
    screen = renderer._app._screen
    area = _frame(renderer, f"SKY TONIGHT {extra.get('place', '')}".strip(), extra.get("clouds", ""), "Say: close", now, fonts)
    _title, row_font, small_font = fonts
    gap = max(4, area.width // 100)
    left = pygame.Rect(area.left, area.top, int(area.width * 0.34), area.height)
    _box(screen, left)
    moon = extra["moon"]
    radius = int(min(left.width, left.height) * 0.32)
    draw_moon(screen, (left.centerx, left.top + radius + 8), radius, moon["cycle"])
    y = left.top + 2 * radius + 16
    _text(screen, row_font, _fitted(row_font, moon["name"].upper(), left.width - 8), TEXT, midtop=(left.centerx, y))
    y += row_font.get_linesize()
    _text(screen, small_font, f"{moon['lit']}% LIT", DIM, midtop=(left.centerx, y))
    y += small_font.get_linesize() + 2
    _text(screen, small_font, f"RISES {moon['rise_text']}", DIM, midtop=(left.centerx, y))
    y += small_font.get_linesize()
    _text(screen, small_font, f"SETS {moon['set_text']}", DIM, midtop=(left.centerx, y))
    y += small_font.get_linesize()
    _text(screen, small_font, f"FULL {moon['full_text'].upper()}", DIM, midtop=(left.centerx, y))
    y += small_font.get_linesize()
    _text(screen, small_font, f"NEW {moon['new_text'].upper()}", DIM, midtop=(left.centerx, y))
    right = pygame.Rect(left.right + gap, area.top, area.right - left.right - gap, area.height)
    planets = extra.get("planets") or []
    planet_height = int(right.height * 0.48)
    box = pygame.Rect(right.left, right.top, right.width, planet_height)
    _box(screen, box)
    _text(screen, small_font, "PLANETS", DIM, topleft=(box.left + 6, box.top + 4))
    y = box.top + 6 + small_font.get_linesize()
    line = row_font.get_linesize() + 2
    if not planets:
        _text(screen, row_font, "None tonight", TEXT, topleft=(box.left + 8, y))
    for planet in planets:
        if y + line > box.bottom:
            break
        draw_planet(screen, planet["name"], (box.left + 12, y + line // 2), max(3, line // 4))
        words = f"{planet['name'].upper()}  {planet['direction']}  BEST {planet['time_text']}, {planet['height']} UP"
        _text(screen, row_font, _fitted(row_font, words, box.width - 30), TEXT, topleft=(box.left + 24, y))
        y += line
    lower = pygame.Rect(right.left, box.bottom + gap, right.width, right.bottom - box.bottom - gap)
    icon = pygame.Rect(lower.left, lower.top, int(lower.width * 0.34), lower.height)
    _box(screen, lower)
    found = extra.get("next_event") or {}
    kind = "iss" if extra.get("pass_text") else found.get("kind", "meteors")
    draw_event(screen, kind, icon.inflate(-8, -8), now)
    words = pygame.Rect(icon.right + 4, lower.top + 6, lower.right - icon.right - 10, lower.height - 10)
    _lines(screen, words, [text for text in (extra.get("pass_text"), found.get("text")) if text] or ["Nothing big coming up soon."],
           small_font)


def _moon(renderer, extra, now, fonts):
    screen = renderer._app._screen
    moon = extra["moon"]
    area = _frame(renderer, "THE MOON", extra.get("place", ""), "Say: close", now, fonts)
    _title, row_font, small_font = fonts
    gap = max(4, area.width // 100)
    strip_height = int(area.height * 0.24)
    top = pygame.Rect(area.left, area.top, area.width, area.height - strip_height - gap)
    radius = int(min(top.width * 0.26, top.height * 0.46))
    draw_moon(screen, (top.left + int(top.width * 0.3), top.centery), radius, moon["cycle"])
    panel = pygame.Rect(top.left + int(top.width * 0.58), top.top, top.width - int(top.width * 0.58), top.height)
    _rows(screen, panel, (("NOW", moon["name"].upper()), ("LIT", f"{moon['lit']}%"), ("AGE", f"{moon['age_days']} DAYS"),
                          ("RISES", moon["rise_text"]), ("SETS", moon["set_text"]), ("NEXT FULL", moon["full_text"]),
                          ("NEXT NEW", moon["new_text"]), ("AWAY", f"{moon['miles']:,} MI")), fonts)
    strip = pygame.Rect(area.left, top.bottom + gap, area.width, strip_height)
    _box(screen, strip)
    steps = 8
    spot = int((now / 1.0) % steps)  # the month playing through, one phase a second
    tiny = max(4, min(strip.width // (steps * 3), strip.height // 3))
    width = strip.width / steps
    here = int(moon["cycle"] * steps + 0.5) % steps
    for n in range(steps):
        x = strip.left + width * (n + 0.5)
        draw_moon(screen, (x, strip.centery), tiny, n / steps)
        if n == spot:
            pygame.draw.circle(screen, EDGE, (int(x), strip.centery), tiny + 4, 1)
        if n == here:
            _text(screen, small_font, "TONIGHT", WARM, midbottom=(x, strip.bottom - 1))


def _station(renderer, extra, now, fonts):
    screen = renderer._app._screen
    area = _frame(renderer, "SPACE STATION", extra.get("place", ""), "Say: close", now, fonts)
    _title, row_font, small_font = fonts
    gap = max(4, area.width // 100)
    art = pygame.Rect(area.left, area.top, area.width, int(area.height * 0.5))
    _flight(screen, art, now)
    pygame.draw.rect(screen, EDGE, art, 1)
    orbit = extra.get("orbit")
    if orbit and sky_data.available():
        try:
            live = sky_data.Sky.iss_now(orbit, datetime.now(timezone.utc))
            words = f"OVER {live['over']}  {live['miles_up']} MI UP  {live['mph']:,} MPH"
            _text(screen, small_font, _fitted(small_font, words, art.width - 12), TEXT, topleft=(art.left + 6, art.top + 4))
        except Exception:
            pass
    box = pygame.Rect(area.left, art.bottom + gap, area.width, area.bottom - art.bottom - gap)
    _box(screen, box)
    y = box.top + 4
    rows = extra.get("passes") or []
    _text(screen, small_font, "PASSES YOU CAN SEE" if rows else "", DIM, topleft=(box.left + 8, y))
    y += small_font.get_linesize() + 2
    if not rows:
        _lines(screen, box.inflate(-16, -12), [extra.get("no_passes", "No passes you can see in the next few days.")], row_font)
    for row in rows:
        if y + row_font.get_linesize() > box.bottom:
            break
        _text(screen, row_font, _fitted(row_font, row, box.width - 16), TEXT, topleft=(box.left + 8, y))
        y += row_font.get_linesize() + 2


def _events(renderer, lib, now, fonts):
    screen = renderer._app._screen
    extra = lib.get("extra") or {}
    tiles, total, first = lib.get("tiles") or [], int(lib.get("total") or 0), int(lib.get("first") or 1)
    note = f"{first}-{first + len(tiles) - 1} of {total}" if total > len(tiles) else extra.get("place", "")
    hint = "Say: scroll down, scroll up, or close" if total > len(tiles) else "Say: close"
    area = _frame(renderer, "COMING UP IN THE SKY", note, hint, now, fonts)
    _title, row_font, small_font = fonts
    if not tiles:
        _text(screen, row_font, "Nothing big on the way.", TEXT, center=area.center)
        return
    per_page = max(1, int(extra.get("per_page") or 4))
    gap = max(3, area.height // 90)
    height = (area.height - gap * (per_page - 1)) // per_page
    for n, tile in enumerate(tiles):
        data = tile["data"]
        rect = pygame.Rect(area.left, area.top + n * (height + gap), area.width, height)
        _box(screen, rect)
        icon = pygame.Rect(rect.left + 2, rect.top + 2, height + height // 3, height - 4)
        draw_event(screen, data["kind"], icon, now, tile["number"])
        x = icon.right + 8
        _text(screen, row_font, _fitted(row_font, data["title"].upper(), rect.right - x - 6), HEAD if data.get("important") else TEXT,
              topleft=(x, rect.top + 3))
        room = pygame.Rect(x, rect.top + 4 + row_font.get_linesize(), rect.right - x - 6, height - row_font.get_linesize() - 5)
        _lines(screen, room, [data["text"]], small_font)


def _alert(renderer, extra, now, fonts):
    screen = renderer._app._screen
    area = _frame(renderer, "LOOK UP!", "", "Say: close", now, fonts)
    _title, row_font, _small = fonts
    art = pygame.Rect(area.left, area.top, area.width, int(area.height * 0.62))
    if extra.get("event_kind") == "iss":
        _flight(screen, art, now)
    else:
        draw_event(screen, extra.get("event_kind"), art, now)
    pygame.draw.rect(screen, EDGE, art, 1)
    _lines(screen, pygame.Rect(area.left + 4, art.bottom + 8, area.width - 8, area.bottom - art.bottom - 10), [extra.get("text", "")], row_font)


def render(renderer, lib, now):
    fonts = renderer._setup_fonts(renderer._app.SCREEN_HEIGHT)
    extra = lib.get("extra") or {}
    mode = extra.get("mode")
    if mode == "moon":
        _moon(renderer, extra, now, fonts)
    elif mode == "iss":
        _station(renderer, extra, now, fonts)
    elif mode == "events":
        _events(renderer, lib, now, fonts)
    elif mode == "alert":
        _alert(renderer, extra, now, fonts)
    else:
        _tonight(renderer, extra, now, fonts)
