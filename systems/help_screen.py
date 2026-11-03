"""The help center's screens (owner 2026-11-03): every subject as a numbered list, and a subject's commands with what each
does. The same plain boxes and margins as the weather screens, so it stays inside a CRT's picture."""
from __future__ import annotations

import pygame

from systems.weather_screen import HEAD, TEXT, WARM, _box, _frame, _text, _wrapped


def _render_home(renderer, lib, now, fonts):
    screen = renderer._app._screen
    title_font, row_font, small_font = fonts
    tiles = lib.get("tiles") or []
    area = _frame(renderer, "HELP", f"{len(tiles)} SUBJECTS", "Say a subject or a number, or close", now, fonts)
    rows = (len(tiles) + 1) // 2 or 1
    gap = max(3, area.height // 90)
    width = (area.width - gap) // 2
    height = (area.height - gap * (rows - 1)) // rows
    pad = max(4, width // 40)
    number_room = row_font.size("00")[0] + pad
    for n, tile in enumerate(tiles):
        rect = pygame.Rect(area.left + (n % 2) * (width + gap), area.top + (n // 2) * (height + gap), width, height)
        _box(screen, rect)
        _text(screen, row_font, str(tile["number"]), WARM, midright=(rect.left + number_room, rect.centery))
        # The only number on a row: he's listening for one.
        room = rect.right - rect.left - number_room - 2 * pad
        lines = _wrapped(row_font, tile["data"]["subject"], room, 2)
        line = row_font.get_linesize()
        y = rect.centery - len(lines) * line // 2
        for row in lines:
            _text(screen, row_font, row, TEXT, topleft=(rect.left + number_room + pad, y))
            y += line


def _render_subject(renderer, lib, now, fonts):
    screen = renderer._app._screen
    _, row_font, small_font = fonts
    tiles, total, first = lib.get("tiles") or [], int(lib.get("total") or 0), int(lib.get("first") or 1)
    extra = lib.get("extra") or {}
    note = f"{first}-{first + len(tiles) - 1} of {total}" if total > len(tiles) else f"{total} COMMANDS"
    hint = "Scroll down, scroll up, back, or close" if total > len(tiles) else "Say: back, or close"
    area = _frame(renderer, str(extra.get("subject", "")).upper(), note, hint, now, fonts)
    per_page = int(extra.get("per_page") or 3)
    gap = max(3, area.height // 60)
    height = (area.height - gap * (per_page - 1)) // per_page
    pad = max(4, height // 14)
    for n, tile in enumerate(tiles):
        rect = pygame.Rect(area.left, area.top + n * (height + gap), area.width, height)
        _box(screen, rect)
        room = rect.width - 2 * pad
        y = rect.top + pad
        for row in _wrapped(row_font, tile["data"]["say"], room, 2):
            y = _text(screen, row_font, row, HEAD, topleft=(rect.left + pad, y)).bottom
        y += 2
        # The larger text when it all fits, for a CRT; the small one for the long entries.
        font = small_font
        if len(_wrapped(row_font, tile["data"]["does"], room, 99)) * row_font.get_linesize() <= rect.bottom - pad - y:
            font = row_font
        lines = max(1, (rect.bottom - pad - y) // font.get_linesize())
        for row in _wrapped(font, tile["data"]["does"], room, lines):
            y = _text(screen, font, row, TEXT, topleft=(rect.left + pad, y)).bottom


def render(renderer, lib, now):
    """The screen for an open help view."""
    fonts = renderer._setup_fonts(renderer._app.SCREEN_HEIGHT)
    if (lib.get("extra") or {}).get("page") == "home":
        _render_home(renderer, lib, now, fonts)
    else:
        _render_subject(renderer, lib, now, fonts)
