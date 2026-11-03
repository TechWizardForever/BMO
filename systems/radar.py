"""The rain radar on BMO's screen (owner 2026-11-02): "Hey BMO, show me the radar".

Open-Meteo has no radar pictures. These are the National Weather Service's radars (NEXRAD, lower 48 states) as tiles
from the Iowa Environmental Mesonet, public domain, no account, over an OpenStreetMap map that's kept on disk once
fetched (their tile rules ask for that; CARTO's free dark map now stamps "API KEY REQUIRED" on every tile, 2026-11-02).
The last hour plays as a loop, so you can see what's moving in. It only looks back: nothing here predicts where the rain
goes next. Nothing is contacted until he's asked; the tiles only give away a region, a few hundred miles across.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import io
import math
from pathlib import Path
import re
import urllib.request

import pygame

RADAR_URL = "https://mesonet.agron.iastate.edu/cache/tile.py/1.0.0/nexrad-n0q-900913{age}/{z}/{x}/{y}.png"
MAP_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
CREDIT = "Radar: NWS / Iowa Environmental Mesonet. Map: OpenStreetMap contributors"
MAP_DIMMED = (88, 96, 104)  # OpenStreetMap is bright; the rain has to stand out on it
TILE = 256
ZOOM, COLUMNS, ROWS = 7, 4, 3  # about 700 miles across at the latitude of the middle of the US
MINUTES_AGO = (50, 40, 30, 20, 10, 0)
_ASKED = re.compile(r"\b(?:radar|weather map)\b", re.I)


class RadarError(Exception):
    """A spoken-safe reason there's no radar."""


def asked_for(text: str) -> bool:
    return bool(_ASKED.search(str(text)))


def covers(latitude: float, longitude: float) -> bool:
    """The lower 48 states, near enough: the mosaic stops there."""
    return 24.0 <= latitude <= 50.0 and -125.5 <= longitude <= -66.0


def tile_position(latitude: float, longitude: float, zoom: int = ZOOM) -> tuple[float, float]:
    scale = 2 ** zoom
    x = (longitude + 180.0) / 360.0 * scale
    y = (1.0 - math.asinh(math.tan(math.radians(latitude))) / math.pi) / 2.0 * scale
    return x, y


def layout(latitude: float, longitude: float) -> dict:
    """Which tiles, with the place as near the middle as whole tiles allow, and where it lands in the picture."""
    x, y = tile_position(latitude, longitude)
    first_x, first_y = math.floor(x - COLUMNS / 2 + 0.5), math.floor(y - ROWS / 2 + 0.5)
    return {"first": (first_x, first_y), "size": (COLUMNS * TILE, ROWS * TILE),
            "marker": (int((x - first_x) * TILE), int((y - first_y) * TILE))}


def _get(url: str, timeout: float = 8.0) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "Bemo-local-assistant"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def _tile_surface(data: bytes):
    return pygame.image.load(io.BytesIO(data), "tile.png")


def _map_tile(x: int, y: int, folder: Path, fetch) -> bytes | None:
    path = Path(folder) / f"{ZOOM}_{x}_{y}.png"
    try:
        return path.read_bytes()
    except OSError:
        pass
    try:
        data = fetch(MAP_URL.format(z=ZOOM, x=x, y=y))
    except OSError:
        return None  # the rain over a plain background still says something
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    except OSError:
        pass
    return data


def _radar_tile(age: int, x: int, y: int, fetch) -> bytes | None:
    suffix = f"-m{age:02d}m" if age else ""
    try:
        return fetch(RADAR_URL.format(age=suffix, z=ZOOM, x=x, y=y))
    except OSError:
        return None


def build(latitude: float, longitude: float, map_folder: Path, *, fetch=_get, workers: int = 6, stop=lambda: False) -> dict:
    """The loop: {"frames": [(minutes ago, picture), ...] oldest first, "marker": (x, y)}. Raises RadarError when no
    radar could be fetched, so he never shows an empty map as if it weren't raining."""
    plan = layout(latitude, longitude)
    first_x, first_y = plan["first"]
    cells = [(column, row) for row in range(ROWS) for column in range(COLUMNS)]
    base = pygame.Surface(plan["size"])
    base.fill((12, 18, 22))
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="bemo-radar") as pool:
        maps = pool.map(lambda c: _map_tile(first_x + c[0], first_y + c[1], map_folder, fetch), cells)
        for (column, row), data in zip(cells, list(maps)):
            if data:
                try:
                    base.blit(_tile_surface(data), (column * TILE, row * TILE))
                except pygame.error:
                    pass
        base.fill(MAP_DIMMED, special_flags=pygame.BLEND_RGB_MULT)
        frames = []
        for age in MINUTES_AGO:
            if stop():
                break
            tiles = list(pool.map(lambda c: _radar_tile(age, first_x + c[0], first_y + c[1], fetch), cells))
            if not any(tiles):
                continue
            frame = base.copy()
            for (column, row), data in zip(cells, tiles):
                if data:
                    try:
                        frame.blit(_tile_surface(data), (column * TILE, row * TILE))
                    except pygame.error:
                        pass
            frames.append((age, frame))
    if not frames and not stop():
        raise RadarError("I couldn't reach the radar")
    return {"frames": frames, "marker": plan["marker"], "size": plan["size"]}
