"""Six basic shapes for simple 3D parts, filled in by number (2026-10-22: Engineering, simple single-shape
parts only, about six shapes).

Why fill-in-the-numbers: with four ready-made parts like this the Engineering brain got 6/6 right first try at
~1.75 min a part; asked to place pieces itself it took 4-7 minutes and often got it wrong. So each shape has the
everyday features built in (a hole through it, holes in the corners, hollow with walls, rounded edges) and the brain
only gives numbers. BMO writes the OpenSCAD and states the outside size and the holes that go all the way through,
which make_cad_model checks on the real STL. All sizes in millimeters.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


class ShapeError(ValueError):
    pass


@dataclass(frozen=True)
class Shape:
    kind: str
    settings: str   # shown to the brain, e.g. "diameter, height, hole_diameter=0"
    build: Callable[["Settings"], tuple[str, list[float], int]]
    example: dict   # typical numbers: the shapes menu's picture and the tests


SHAPES: dict[str, Shape] = {}
ALIASES = {"box": "block", "cube": "block", "plate": "block", "rectangle": "block", "tube": "cylinder",
           "spacer": "cylinder", "washer": "cylinder", "rod": "cylinder", "cup": "cylinder", "pipe": "cylinder",
           "ball": "sphere", "hexagon": "prism", "polygon": "prism", "sign": "text", "tag": "text", "label": "text"}


def shape(kind: str, settings: str, example: dict):
    def register(build):
        SHAPES[kind] = Shape(kind, settings, build, example)
        return build
    return register


def g(value: float) -> str:
    """A number for OpenSCAD: short, never in exponent form."""
    text = f"{value:.4f}".rstrip("0").rstrip(".")
    return "0" if text in ("-0", "") else text


class Settings:
    def __init__(self, kind: str, values: dict):
        self.kind, self.values = kind, {str(k).lower(): v for k, v in (values or {}).items()}

    def num(self, key: str, default: float | None = None, *, minimum: float = 0.0, also=()) -> float:
        value = self.values.get(key)
        for other in also:  # e.g. a cube's one "size" for width, depth and height
            value = self.values.get(other) if value in (None, "") else value
        value = default if value in (None, "") else value
        if value in (None, ""):
            raise ShapeError(f"{self.kind} needs {key}")
        try:
            value = float(value)
        except (TypeError, ValueError):
            raise ShapeError(f"{key} must be a number of millimeters") from None
        if value < minimum:
            raise ShapeError(f"{key} must be at least {g(minimum)}")
        return value

    def fits(self, ok: bool, why: str) -> None:
        if not ok:
            raise ShapeError(f"that {self.kind} can't be made: {why}")


def through(x: float, y: float, top: float, d: float) -> str:
    """A hole straight down through everything up to `top`, a little longer than the part."""
    return f"\n  translate([{g(x)}, {g(y)}, -1]) cylinder(h={g(top + 2)}, d={g(d)});"


def cut(body: str, cuts: list[str]) -> str:
    return f"difference() {{\n  {body}{''.join(cuts)}\n}}\n" if cuts else body + "\n"


@shape("block", "width, depth, height, radius=0 (rounds the upright edges), wall=0 (hollow with an open top), "
                "holes=none|center|corners, hole_diameter, hole_inset=5",
       {"width": 60, "depth": 40, "height": 20, "wall": 2, "holes": "corners", "hole_diameter": 4, "hole_inset": 6})
def _block(s: Settings):
    w = s.num("width", minimum=0.5, also=("size",))
    d = s.num("depth", minimum=0.5, also=("size",))
    h = s.num("height", minimum=0.5, also=("thickness", "size"))
    r, wall = s.num("radius", 0.0), s.num("wall", 0.0)
    s.fits(r < min(w, d) / 2, "the radius must be less than half the width and depth")
    if r > 0:
        corners = " ".join(f"translate([{g(x)}, {g(y)}, 0]) cylinder(h={g(h)}, r={g(r)});"
                           for x in (r, w - r) for y in (r, d - r))
        body = f"hull() {{ {corners} }}"
    else:
        body = f"cube([{g(w)}, {g(d)}, {g(h)}]);"
    cuts = []
    if wall > 0:
        s.fits(2 * wall < min(w, d) and wall < h, "the walls must leave room inside")
        inner_r = max(0.0, r - wall)
        if inner_r > 0:
            inner = " ".join(f"translate([{g(x)}, {g(y)}, 0]) cylinder(h={g(h)}, r={g(inner_r)});"
                             for x in (r, w - r) for y in (r, d - r))
            cuts.append(f"\n  translate([0, 0, {g(wall)}]) hull() {{ {inner} }}")
        else:
            cuts.append(f"\n  translate([{g(wall)}, {g(wall)}, {g(wall)}]) cube([{g(w - 2 * wall)}, {g(d - 2 * wall)}, {g(h)}]);")
    holes = str(s.values.get("holes") or "none").lower()
    hd = s.num("hole_diameter", 0.0)
    if holes == "center":
        s.fits(0 < hd < min(w, d) - 2 * wall, "the hole must be smaller than the block (inside its walls)")
        spots = [(w / 2, d / 2)]
    elif holes == "corners":
        inset = s.num("hole_inset", 5.0)
        s.fits(hd > 0 and hd / 2 < inset < min(w, d) / 2,
               "each corner hole must sit inside the block (inset more than half the hole, less than half the block)")
        spots = [(x, y) for x in (inset, w - inset) for y in (inset, d - inset)]
    elif holes == "none":
        spots = []
    else:
        raise ShapeError("holes must be none, center or corners")
    cuts += [through(x, y, h, hd) for x, y in spots]
    return cut(body, cuts), [w, d, h], len(spots)


@shape("cylinder", "diameter, height, hole_diameter=0 (a hole through: tube, spacer, washer), "
                   "wall=0 (hollow with an open top: a cup)",
       {"diameter": 30, "height": 25, "hole_diameter": 12})
def _cylinder(s: Settings):
    d, h = s.num("diameter", minimum=0.5), s.num("height", minimum=0.2, also=("length",))
    hd, wall = s.num("hole_diameter", 0.0, also=("inner_diameter",)), s.num("wall", 0.0)
    s.fits(not (hd > 0 and wall > 0), "give a hole or a wall, not both")
    s.fits(hd < d - 0.4, "the hole must leave a wall at least 0.2 mm thick")
    s.fits(wall == 0 or (2 * wall < d and wall < h), "the wall must leave room inside")
    cuts = [through(0, 0, h, hd)] if hd > 0 else []
    if wall > 0:
        cuts.append(f"\n  translate([0, 0, {g(wall)}]) cylinder(h={g(h)}, d={g(d - 2 * wall)});")
    return cut(f"cylinder(h={g(h)}, d={g(d)});", cuts), [d, d, h], 1 if hd > 0 else 0


@shape("cone", "bottom_diameter, top_diameter (0 for a point), height, hole_diameter=0",
       {"bottom_diameter": 30, "top_diameter": 10, "height": 25, "hole_diameter": 6})
def _cone(s: Settings):
    b, t, h = s.num("bottom_diameter"), s.num("top_diameter", 0.0), s.num("height", minimum=0.2)
    hd = s.num("hole_diameter", 0.0)
    s.fits(max(b, t) > 0, "one end must be wider than 0")
    s.fits(hd == 0 or hd < min(b, t) - 0.4, "the hole must fit inside the narrow end")
    cuts = [through(0, 0, h, hd)] if hd > 0 else []
    return cut(f"cylinder(h={g(h)}, d1={g(b)}, d2={g(t)});", cuts), [max(b, t), max(b, t), h], 1 if hd > 0 else 0


@shape("sphere", "diameter, hole_diameter=0 (straight down through it: a bead)", {"diameter": 30, "hole_diameter": 5})
def _sphere(s: Settings):
    d, hd = s.num("diameter", minimum=0.5), s.num("hole_diameter", 0.0)
    s.fits(hd < d - 1, "the hole must be smaller than the sphere")
    cuts = [through(0, 0, d, hd)] if hd > 0 else []
    return cut(f"translate([0, 0, {g(d / 2)}]) sphere(d={g(d)});", cuts), [d, d, d], 1 if hd > 0 else 0


@shape("prism", "sides (3-12), diameter (across the corners), height, hole_diameter=0",
       {"sides": 6, "diameter": 30, "height": 12, "hole_diameter": 10})
def _prism(s: Settings):
    import math
    n, d, h = int(s.num("sides", minimum=3)), s.num("diameter", minimum=0.5), s.num("height", minimum=0.2)
    hd = s.num("hole_diameter", 0.0)
    s.fits(n <= 12, "sides must be from 3 to 12")
    flats = d * math.cos(math.pi / n)  # across the flats
    s.fits(hd < flats - 0.4, "the hole must fit inside the flats")
    xs = [d / 2 * math.cos(2 * math.pi * i / n) for i in range(n)]
    ys = [d / 2 * math.sin(2 * math.pi * i / n) for i in range(n)]
    cuts = [through(0, 0, h, hd)] if hd > 0 else []
    return (cut(f"cylinder(h={g(h)}, d={g(d)}, $fn={n});", cuts),
            [max(xs) - min(xs), max(ys) - min(ys), h], 1 if hd > 0 else 0)


@shape("text", 'text, size (letter height), height (how far the letters stand up), plate_width, plate_depth, '
               'plate_thickness=2 (the letters stand on this plate), hole_diameter=0 (a keyring hole at the left)',
       {"text": "BMO", "size": 12, "height": 2, "plate_width": 60, "plate_depth": 20, "hole_diameter": 4})
def _text(s: Settings):
    words = str(s.values.get("text") or "").strip()
    s.fits(bool(words) and len(words) <= 40 and '"' not in words and "\\" not in words,
           "the text must be 1 to 40 letters, without quotes or backslashes")
    size, th = s.num("size", minimum=1), s.num("height", 1.0, minimum=0.2)
    pw, pd = s.num("plate_width", minimum=5), s.num("plate_depth", minimum=5)
    pt, hd = s.num("plate_thickness", 2.0, minimum=0.6), s.num("hole_diameter", 0.0)
    left = pw / 2
    cuts = []
    if hd > 0:
        s.fits(hd + 4 < pd and hd + 4 < pw / 3, "the keyring hole must fit at the left end")
        cuts.append(through(hd / 2 + 2, pd / 2, pt, hd))
        left = (hd + 4 + pw) / 2
    letters = (f"translate([{g(left)}, {g(pd / 2)}, {g(pt - 0.01)}]) linear_extrude(height={g(th + 0.01)}) "
               f"text(\"{words}\", size={g(size)}, font=\"Liberation Sans:style=Bold\", halign=\"center\", "
               f"valign=\"center\");")
    body = f"union() {{ cube([{g(pw)}, {g(pd)}, {g(pt)}]); {letters} }}"
    return cut(body, cuts), [pw, pd, pt + th], 1 if hd > 0 else 0


def kind_for(kind: str) -> str | None:
    key = str(kind or "").strip().lower().replace(" ", "_")
    return ALIASES.get(key, key) if (key in SHAPES or key in ALIASES) else None


CUTOUT = ("cutout=circle|square|rectangle|hexagon (negative space cut into any shape), cutout_size (diameter, or "
          "side), cutout_width and cutout_length for a rectangle, cutout_depth=0 (0: all the way through; else from the "
          "top down), cutout_x=0, cutout_y=0 (offset from the middle)")
_SIDES = {"circle": 0, "round": 0, "hole": 0, "square": 4, "hexagon": 6, "hex": 6, "triangle": 3, "octagon": 8}


def _cutout(kind: str, s: Settings, size: list[float]) -> tuple[str, int]:
    """Negative space cut into the shape from the top (2026-10-22),
    centered on the shape unless moved. Returns its OpenSCAD and 1 if it is a hole that goes all the way through
    (inside the outline, so the part stays one ring around it), else 0."""
    import math
    what = str(s.values.get("cutout") or "").strip().lower()
    if not what or what == "none":
        return "", 0
    width, depth, height = size
    cx, cy = (width / 2, depth / 2) if kind in ("block", "text") else (0.0, 0.0)
    cx, cy = cx + s.num("cutout_x", 0.0, minimum=-1e6), cy + s.num("cutout_y", 0.0, minimum=-1e6)
    deep = s.num("cutout_depth", 0.0)
    s.fits(deep < height or deep == 0, "a cutout deeper than the part is just all the way through: give cutout_depth=0")
    through_ = deep == 0
    z0, h = (-1.0, height + 2) if through_ else (height - deep, deep + 1)
    if what == "rectangle":
        cw, cl = s.num("cutout_width", minimum=0.1), s.num("cutout_length", minimum=0.1)
        shape_ = f"translate([{g(cx - cw / 2)}, {g(cy - cl / 2)}, {g(z0)}]) cube([{g(cw)}, {g(cl)}, {g(h)}]);"
        half = (cw / 2, cl / 2)
    elif what in _SIDES:
        d = s.num("cutout_size", minimum=0.1)
        if what == "square":
            shape_ = f"translate([{g(cx - d / 2)}, {g(cy - d / 2)}, {g(z0)}]) cube([{g(d)}, {g(d)}, {g(h)}]);"
            half = (d / 2, d / 2)
        else:
            sides = _SIDES[what]
            fn = f", $fn={sides}" if sides else ""
            shape_ = f"translate([{g(cx)}, {g(cy)}, {g(z0)}]) cylinder(h={g(h)}, d={g(d)}{fn});"
            half = (d / 2, d / 2)
    else:
        raise ShapeError("cutout must be circle, square, rectangle or hexagon")
    # A through cutout is a hole only when the material goes all round it; one reaching the edge is a notch.
    if kind in ("block", "text"):
        inside = cx - half[0] > 0.2 and cx + half[0] < width - 0.2 and cy - half[1] > 0.2 and cy + half[1] < depth - 0.2
    else:
        inside = math.hypot(abs(cx) + half[0], abs(cy) + half[1]) < min(width, depth) / 2 - 0.2
    return "\n  " + shape_, 1 if through_ and inside else 0


def build(kind: str, values: dict) -> tuple[str, list[float], int]:
    found = SHAPES.get(kind_for(kind) or "")
    if found is None:
        raise ShapeError(f"there is no {kind!r} shape; the shapes are {', '.join(SHAPES)}. "
                         "Use make_cad_model for anything else")
    try:
        settings = Settings(found.kind, values)
        source, size, holes = found.build(settings)
        extra, more = _cutout(found.kind, settings, size)
        if extra:
            source = f"difference() {{\n  {source.strip()}{extra}\n}}\n"
        return source, size, holes + more
    except ShapeError as exc:
        raise ShapeError(f"{exc}. {found.kind} takes: {found.settings}; any shape: {CUTOUT}") from None


def catalog() -> str:
    return " ".join(f"{s.kind}({s.settings})." for s in SHAPES.values()) + f" Any shape also takes {CUTOUT}."
