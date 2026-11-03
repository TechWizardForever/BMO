"""BMO's weather screens (owner 2026-11-02): a mini weather station for one day, little animated boxes for several, and
the radar loop. Plain shapes that stay crisp on a CRT; everything sits at least 6% in from the edges, because a tube cuts
off more than that near its corners. A number the service didn't send shows as a dash, never a guess.
"""
from __future__ import annotations

import math

import pygame

from systems import radar as radar_data

TEXT, DIM, HEAD, WARM, COOL = (205, 236, 228), (96, 150, 140), (108, 245, 206), (255, 196, 110), (130, 190, 255)
BOX, EDGE, RAIN_BAR = (6, 30, 16), (28, 96, 44), (70, 150, 240)
SUN, CLOUD, DARK_CLOUD, DROP, SNOW, BOLT, FOG, MOON = ((255, 214, 90), (205, 216, 222), (120, 132, 142),
                                                       (90, 170, 250), (235, 245, 255), (255, 240, 120),
                                                       (150, 165, 165), (222, 226, 200))
MARGIN = 0.06
RADAR_FRAME_SECONDS = 0.7
RADAR_LAST_FRAME_HOLD = 3  # the latest picture stays up longest


def dash(value, unit="") -> str:
    return "--" if value is None or value == "" else f"{value}{unit}"


# --- The little animations ------------------------------------------------------------------------------

def _sun(screen, x, y, r, now):
    for n in range(8):
        angle = now * 0.5 + n * math.pi / 4
        inner, outer = r * 1.3, r * (1.65 + 0.12 * math.sin(now * 2 + n))
        pygame.draw.line(screen, SUN, (x + math.cos(angle) * inner, y + math.sin(angle) * inner),
                         (x + math.cos(angle) * outer, y + math.sin(angle) * outer), max(1, int(r / 6)))
    pygame.draw.circle(screen, SUN, (int(x), int(y)), int(r))


def _moon(screen, x, y, r, back):
    pygame.draw.circle(screen, MOON, (int(x), int(y)), int(r))
    pygame.draw.circle(screen, back, (int(x + r * 0.45), int(y - r * 0.2)), int(r * 0.85))


def _cloud(screen, x, y, size, color):
    r = max(2, int(size * 0.26))
    for dx, dy, radius in ((-0.28, 0.04, r), (0.0, -0.1, int(r * 1.3)), (0.3, 0.05, r)):
        pygame.draw.circle(screen, color, (int(x + dx * size), int(y + dy * size)), radius)
    pygame.draw.rect(screen, color, (int(x - size * 0.42), int(y), int(size * 0.84), r))


def _falling(screen, rect, count, speed, now, seed, draw):
    for n in range(count):
        spot = (n * 0.618 + seed * 0.37) % 1.0
        fall = (now * speed + n * 0.381 + seed * 0.13) % 1.0
        draw(screen, rect.left + spot * rect.width, rect.top + fall * rect.height, n)


def draw_icon(screen, kind, rect, now, seed=0, *, night=False, back=BOX):
    """A sky about the size of rect: the sun turns, clouds drift, rain and snow fall, lightning flashes."""
    now += seed * 1.7
    size = min(rect.width, rect.height)
    x, y = rect.centerx, rect.centery
    drift = math.sin(now * 0.6) * size * 0.05
    clip = screen.get_clip()
    screen.set_clip(rect.inflate(4, 4).clip(clip) if clip else rect.inflate(4, 4))
    weight = max(1, size // 34)
    below = pygame.Rect(rect.left + size // 10, y + size // 8, rect.width - size // 5, max(4, rect.bottom - y - size // 8))

    def drop(s, px, py, n):
        pygame.draw.line(s, DROP, (px, py), (px - weight, py + max(3, size // 11)), weight)

    def flake(s, px, py, n):
        px += math.sin(now * 1.5 + n) * size * 0.04
        pygame.draw.rect(s, SNOW, (int(px), int(py), max(2, weight + 1), max(2, weight + 1)))

    if kind == "clear":
        (_moon(screen, x, y, size * 0.26, back) if night else _sun(screen, x, y, size * 0.24, now))
    elif kind == "partly":
        (_moon(screen, x - size * 0.16, y - size * 0.14, size * 0.2, back) if night
         else _sun(screen, x - size * 0.16, y - size * 0.14, size * 0.18, now))
        _cloud(screen, x + size * 0.08 + drift, y + size * 0.1, size * 0.72, CLOUD)
    elif kind == "cloudy":
        _cloud(screen, x - size * 0.14 - drift, y - size * 0.12, size * 0.6, DARK_CLOUD)
        _cloud(screen, x + size * 0.08 + drift, y + size * 0.08, size * 0.76, CLOUD)
    elif kind == "fog":
        for n in range(4):
            slide = math.sin(now * 0.7 + n * 1.4) * size * 0.1
            line_y = y - size * 0.22 + n * size * 0.15
            pygame.draw.line(screen, FOG, (x - size * 0.34 + slide, line_y), (x + size * 0.34 + slide, line_y),
                             max(2, size // 16))
    else:
        if kind in ("rain", "light rain", "storm", "ice"):
            count = {"light rain": 5, "rain": 10, "storm": 12, "ice": 6}[kind]
            _falling(screen, below, count, 1.1 if kind != "light rain" else 0.8, now, seed, drop)
        if kind in ("snow", "ice"):
            _falling(screen, below, 9 if kind == "snow" else 5, 0.3, now, seed + 3, flake)
        if kind == "storm" and (now % 4.0) < 0.25:
            top_x, top_y = x, y + size * 0.05
            pygame.draw.lines(screen, BOLT, False, [(top_x, top_y), (top_x - size * 0.1, top_y + size * 0.2),
                                                    (top_x + size * 0.03, top_y + size * 0.2),
                                                    (top_x - size * 0.08, top_y + size * 0.42)], max(2, size // 22))
        _cloud(screen, x + drift, y - size * 0.1, size * 0.8, DARK_CLOUD if kind in ("rain", "storm") else CLOUD)
    screen.set_clip(clip)


# --- Pieces ---------------------------------------------------------------------------------------------

def _box(screen, rect):
    pygame.draw.rect(screen, BOX, rect)
    pygame.draw.rect(screen, EDGE, rect, 1)


def _text(screen, font, text, color, **where):
    surface = font.render(str(text), True, color)
    place = surface.get_rect(**where)
    screen.blit(surface, place)
    return place


def _fitted(font, text, room):
    text = str(text)
    while text and font.size(text)[0] > room:
        text = text[:-1]
    return text.rstrip()


def _bar(screen, rect, share, color):
    pygame.draw.rect(screen, (3, 16, 8), rect)
    if share:
        filled = rect.copy()
        filled.width = max(1, int(rect.width * min(1.0, share)))
        pygame.draw.rect(screen, color, filled)
    pygame.draw.rect(screen, EDGE, rect, 1)


def _reading(screen, rect, label, value, label_font, value_font):
    _box(screen, rect)
    _text(screen, label_font, label, DIM, midtop=(rect.centerx, rect.top + 3))
    _text(screen, value_font, _fitted(value_font, value, rect.width - 6), TEXT,
          midbottom=(rect.centerx, rect.bottom - 3))


def _frame(renderer, heading, note, hint, now, fonts):
    """The green grid, the heading and the line of what to say. Returns the room left, drifting against burn-in."""
    screen = renderer._app._screen
    w, h = renderer._app.SCREEN_WIDTH, renderer._app.SCREEN_HEIGHT
    title_font, row_font, small_font = fonts
    renderer._green_grid(now, speed=0.25)
    dx, dy = int(math.sin(now / 37.0) * 3), int(math.cos(now / 53.0) * 2)
    area = pygame.Rect(int(w * MARGIN) + dx, int(h * MARGIN) + dy, int(w * (1 - 2 * MARGIN)), int(h * (1 - 2 * MARGIN)))
    title = _text(screen, title_font, _fitted(title_font, heading, area.width * 0.62), HEAD, topleft=area.topleft)
    if note:
        _text(screen, small_font, _fitted(small_font, note, area.width * 0.36), DIM,
              bottomright=(area.right, title.bottom))
    line = _text(screen, small_font, hint, WARM, midbottom=(area.centerx, area.bottom))
    return pygame.Rect(area.left, title.bottom + int(h * 0.015), area.width, line.top - title.bottom - int(h * 0.03))


# --- The several-days boxes -----------------------------------------------------------------------------

def _day_box(screen, rect, day, now, seed, span, fonts):
    _title, row_font, small_font = fonts
    _box(screen, rect)
    line = small_font.get_linesize()
    pad = max(3, rect.width // 28)
    _text(screen, row_font, _fitted(row_font, day["name"], rect.width - 2 * pad), HEAD, midtop=(rect.centerx, rect.top + pad))
    top = rect.top + pad + row_font.get_linesize()
    _text(screen, small_font, day["date"], DIM, midtop=(rect.centerx, top))
    top += line
    numbers = 3 * line + row_font.get_linesize() + 2 * pad
    room = pygame.Rect(rect.left + pad, top, rect.width - 2 * pad, rect.bottom - top - numbers)
    draw_icon(screen, day["kind"], room, now, seed)
    y = room.bottom + pad // 2
    high = _text(screen, row_font, f"HI {dash(day['high'])}", WARM, topright=(rect.centerx - 2, y))
    _text(screen, row_font, f"LO {dash(day['low'])}", COOL, topleft=(rect.centerx + 2, y))
    y = high.bottom + 1
    low_all, high_all = span
    if day["high"] is not None and day["low"] is not None and high_all > low_all:  # this day against the others
        track = pygame.Rect(rect.left + pad, y, rect.width - 2 * pad, max(2, line // 4))
        pygame.draw.rect(screen, (3, 16, 8), track)
        left = track.left + (day["low"] - low_all) / (high_all - low_all) * track.width
        right = track.left + (day["high"] - low_all) / (high_all - low_all) * track.width
        pygame.draw.rect(screen, WARM, (int(left), track.top, max(2, int(right - left)), track.height))
    y += max(2, line // 4) + 2
    _text(screen, small_font, _fitted(small_font, f"RAIN {dash(day['rain'], '%')}", rect.width - 2 * pad), TEXT,
          midtop=(rect.centerx, y))
    y += line
    bar = pygame.Rect(rect.left + pad, y, rect.width - 2 * pad, max(3, line // 3))
    _bar(screen, bar, (day["rain"] or 0) / 100.0, RAIN_BAR)
    wind = f"{dash(day['wind'])} {day['wind_from']}".strip()
    _text(screen, small_font, _fitted(small_font, f"WIND {wind}", rect.width - 2 * pad), DIM,
          midtop=(rect.centerx, bar.bottom + 2))


def _render_days(renderer, lib, now, fonts):
    screen = renderer._app._screen
    tiles, total, first = lib.get("tiles") or [], int(lib.get("total") or 0), int(lib.get("first") or 1)
    extra = lib.get("extra") or {}
    note = f"{first}-{first + len(tiles) - 1} of {total}" if total > len(tiles) else extra.get("source", "")
    hint = "Say: scroll down, scroll up, or close" if total > len(tiles) else "Say: close"
    area = _frame(renderer, f"WEATHER {extra.get('place', '')}".strip(), note, hint, now, fonts)
    count = len(tiles)
    columns = count if count <= 4 else 4
    rows = 1 if count <= 4 else 2
    gap = max(4, area.width // 90)
    width = (area.width - gap * (columns - 1)) // max(1, columns)
    height = (area.height - gap * (rows - 1)) // rows
    days = [tile["data"] for tile in tiles]
    known = [d[key] for d in (extra.get("all_days") or days) for key in ("high", "low") if d[key] is not None]
    span = (min(known), max(known)) if known else (0, 0)
    for n, day in enumerate(days):
        rect = pygame.Rect(area.left + (n % columns) * (width + gap), area.top + (n // columns) * (height + gap), width, height)
        _day_box(screen, rect, day, now, tiles[n]["number"], span, fonts)


# --- One day: the mini weather station ------------------------------------------------------------------

def _hour_strip(screen, rect, rows, fonts):
    _title, _row, small_font = fonts
    _box(screen, rect)
    if not rows:
        return
    line = small_font.get_linesize()
    width = rect.width / len(rows)
    rain_word = _text(screen, small_font, "RAIN %", (80, 130, 200), topright=(rect.right - 4, rect.top + 2))
    _text(screen, small_font, "TEMP", WARM, topright=(rain_word.left - 8, rect.top + 2))
    temps = [row["temp"] for row in rows if row["temp"] is not None]
    low, high = (min(temps), max(temps)) if temps else (0, 1)
    top = rect.top + line + 3
    bottom = rect.bottom - line - 3
    points = []
    for n, row in enumerate(rows):
        x = rect.left + width * (n + 0.5)
        if n % 2 == 0:
            _text(screen, small_font, row["hour"], DIM, midbottom=(x, rect.bottom - 2))
        if row["rain"]:
            tall = int((bottom - top) * row["rain"] / 100)
            pygame.draw.rect(screen, (28, 70, 120), (int(x - width * 0.32), bottom - tall, max(2, int(width * 0.64)), tall))
        if row["temp"] is not None:
            y = bottom - 4 - (row["temp"] - low) / max(1, high - low) * (bottom - top - 8)
            points.append((x, y))
            if n % 2 == 0:
                _text(screen, small_font, row["temp"], WARM, midbottom=(x, max(rect.top + line + 1, y - 2)))
    if len(points) > 1:
        pygame.draw.lines(screen, WARM, False, points, max(1, rect.height // 50))


def _render_station(renderer, lib, now, fonts):
    screen = renderer._app._screen
    h = renderer._app.SCREEN_HEIGHT
    extra = lib.get("extra") or {}
    tile = (lib.get("tiles") or [{}])[0]
    day = tile.get("data") or {}
    current = extra.get("current")
    area = _frame(renderer, f"{day.get('name', '')} {extra.get('place', '')}".strip().upper(),
                  extra.get("source", ""), "Say: close", now, fonts)
    _title, row_font, small_font = fonts
    gap = max(4, area.width // 100)
    strip_height = int(area.height * 0.26)
    strip = pygame.Rect(area.left, area.bottom - strip_height, area.width, strip_height)
    top_height = area.height - strip_height - gap
    left = pygame.Rect(area.left, area.top, int(area.width * 0.36), top_height)
    _box(screen, left)
    big = _big_font(renderer, h)
    icon_height = int(top_height * 0.46)
    icon = pygame.Rect(left.left + 4, left.top + 4, left.width - 8, icon_height)
    kind = current["kind"] if current else day.get("kind")
    draw_icon(screen, kind, icon, now, 0, night=bool(current and current.get("night")))
    y = icon.bottom + 2
    main = dash(current["temp"] if current else day.get("high"))
    number = _text(screen, big, main, TEXT if current else WARM, midtop=(left.centerx, y))
    pygame.draw.circle(screen, TEXT if current else WARM, (number.right + big.get_height() // 7, number.top + big.get_height() // 5),
                       max(2, big.get_height() // 10), max(1, big.get_height() // 22))  # the pixel font has no degree sign
    y = number.bottom
    sky = current["sky"] if current else day.get("sky", "")
    _text(screen, small_font, _fitted(small_font, sky.upper(), left.width - 8), TEXT, midtop=(left.centerx, y))
    y += small_font.get_linesize() + 2
    high = _text(screen, row_font, f"HI {dash(day.get('high'))}", WARM, topright=(left.centerx - 4, y))
    _text(screen, row_font, f"LO {dash(day.get('low'))}", COOL, topleft=(left.centerx + 4, y))
    y = high.bottom + 2
    rain = day.get("rain")
    _text(screen, small_font, f"RAIN {dash(rain, '%')}", TEXT, midtop=(left.centerx, y))
    y += small_font.get_linesize()
    if y + 4 < left.bottom:
        _bar(screen, pygame.Rect(left.left + 8, y, left.width - 16, max(3, min(left.bottom - y - 4, small_font.get_linesize() // 2))),
             (rain or 0) / 100.0, RAIN_BAR)
    wind_day = f"{dash(day.get('wind'))} {day.get('wind_from', '')}".strip()
    if current:
        wind_now = f"{dash(current['wind'])} {current['wind_from']}".strip()
        readings = (("FEELS", dash(current["feels"])), ("WIND", wind_now), ("GUSTS", dash(current["gust"])),
                    ("HUMIDITY", dash(current["humidity"], "%")), ("DEW PT", dash(current["dew"])),
                    ("PRESSURE", dash(current["pressure"])), ("CLOUDS", dash(current["clouds"], "%")),
                    ("VISIBLE", dash(current["visibility"])), ("UV NOW", dash(current["uv"])),
                    ("UV MAX", dash(day.get("uv"))), ("SUNRISE", dash(day.get("sunrise"))), ("SUNSET", dash(day.get("sunset"))))
    else:
        readings = (("WIND", wind_day), ("GUSTS", dash(day.get("gust"))), ("RAIN AMT", dash(day.get("precip"))),
                    ("SNOW AMT", dash(day.get("snow"))), ("UV MAX", dash(day.get("uv"))), ("DATE", dash(day.get("date"))),
                    ("SUNRISE", dash(day.get("sunrise"))), ("SUNSET", dash(day.get("sunset"))))
    columns = 3 if len(readings) > 9 or len(readings) % 3 == 0 else 2
    rows = math.ceil(len(readings) / columns)
    grid = pygame.Rect(left.right + gap, area.top, area.right - left.right - gap, top_height)
    width = (grid.width - gap * (columns - 1)) // columns
    height = (grid.height - gap * (rows - 1)) // rows
    for n, (label, value) in enumerate(readings):
        rect = pygame.Rect(grid.left + (n % columns) * (width + gap), grid.top + (n // columns) * (height + gap), width, height)
        _reading(screen, rect, label, value, small_font, row_font)
    _hour_strip(screen, strip, extra.get("hours") or [], fonts)


def _big_font(renderer, h):
    cache = getattr(renderer, "_weather_big_font", None)
    if not cache or cache[0] != h:
        from systems.render import ui_font
        cache = (h, ui_font(max(24, h // 9), bold=True))
        renderer._weather_big_font = cache
    return cache[1]


# --- The radar ------------------------------------------------------------------------------------------

def _scaled_frames(renderer, radar, size):
    """The pictures scaled once for this screen."""
    cache = getattr(renderer, "_radar_scaled", None)
    frames = radar.get("frames") or []
    if not cache or cache[0] is not frames or cache[1] != size or len(cache[2]) != len(frames):
        scaled = [(age, pygame.transform.smoothscale(picture, size)) for age, picture in frames]
        cache = (frames, size, scaled)
        renderer._radar_scaled = cache
    return cache[2]


def render_radar(renderer, lib, now, fonts):
    screen = renderer._app._screen
    extra = lib.get("extra") or {}
    radar = extra.get("radar") or {}
    area = _frame(renderer, f"RADAR {extra.get('place', '')}".strip(), "", "Say: close", now, fonts)
    _title, row_font, small_font = fonts
    line = small_font.get_linesize()
    room = pygame.Rect(area.left, area.top, area.width, area.height - line - 2)
    credit_y = room.bottom + 1
    _text(screen, small_font, _fitted(small_font, radar_data.CREDIT, area.width), DIM, midtop=(area.centerx, credit_y))
    frames = radar.get("frames") or []
    if not frames:
        words = "LOADING THE RADAR..." if extra.get("loading") else "NO RADAR RIGHT NOW"
        _text(screen, row_font, words, TEXT, center=room.center)
        return
    width, height = radar["size"]
    scale = min(room.width / width, room.height / height)
    size = (int(width * scale), int(height * scale))
    picture_rect = pygame.Rect(0, 0, *size)
    picture_rect.center = room.center
    pictures = _scaled_frames(renderer, radar, size)
    steps = len(pictures) + RADAR_LAST_FRAME_HOLD
    step = int(now / RADAR_FRAME_SECONDS) % steps
    age, picture = pictures[min(step, len(pictures) - 1)]
    screen.blit(picture, picture_rect)
    pygame.draw.rect(screen, EDGE, picture_rect.inflate(2, 2), 1)
    x = picture_rect.left + int(radar["marker"][0] * scale)
    y = picture_rect.top + int(radar["marker"][1] * scale)
    radius = max(5, int(height * scale * 0.012))
    pygame.draw.circle(screen, (255, 255, 255), (x, y), radius + int(2 + 2 * math.sin(now * 3)), 1)
    pygame.draw.circle(screen, (255, 80, 80), (x, y), max(2, radius // 2))
    label = "NOW" if not age else f"{age} MIN AGO"
    tag = _text(screen, row_font, label, WARM if not age else TEXT, topleft=(picture_rect.left + 6, picture_rect.top + 6))
    # how far through the hour the loop is
    bar = pygame.Rect(tag.left, tag.bottom + 2, picture_rect.width // 4, max(3, line // 3))
    _bar(screen, bar, (min(step, len(pictures) - 1) + 1) / len(pictures), WARM)
    ramp = ((70, 150, 240), (40, 200, 90), (250, 225, 60), (250, 140, 40), (230, 40, 40))
    slot = (small_font.size("LIGHT")[0] + small_font.size("HEAVY")[0] + 10) // len(ramp)
    legend_right = picture_rect.right - 6
    legend_left = legend_right - slot * len(ramp)
    legend_top = picture_rect.top + 6 + line
    for n, color in enumerate(ramp):
        pygame.draw.rect(screen, color, (legend_left + n * slot, legend_top, slot, max(4, line // 2)))
    _text(screen, small_font, "LIGHT", TEXT, bottomleft=(legend_left, legend_top - 1))
    _text(screen, small_font, "HEAVY", TEXT, bottomright=(legend_right, legend_top - 1))


def render(renderer, lib, now):
    """The screen for an open weather view."""
    fonts = renderer._setup_fonts(renderer._app.SCREEN_HEIGHT)
    if lib.get("kind") == "radar":
        render_radar(renderer, lib, now, fonts)
    elif (lib.get("extra") or {}).get("station"):
        _render_station(renderer, lib, now, fonts)
    else:
        _render_days(renderer, lib, now, fonts)
