"""Pictures for the 3D shapes menu (2026-10-22): the basic shapes turning on screen while a part is made.

Each basic shape, at its example size, is drawn by OpenSCAD from FRAMES angles around it into
~/.bemo/cache/part_gallery/<kind>/NN.png, once: again only when the catalog changes (the stamp). The renderer
turns them by showing the frames in turn. Drawn in the background at low priority; the menu shows names until
the pictures are ready.
"""
from __future__ import annotations

import hashlib
import logging
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading

LOG = logging.getLogger("bemo-part-gallery")
FRAMES = 24
SIZE = (360, 270)
COLORS = "Tomorrow Night"
TILT = 30          # degrees: a round shape turning on its own axis looks still (2026-10-23)
FACETS = 24        # flat sides a round shape is drawn with, so a turning sphere shows it is turning
EXTRAS = ("hole_diameter", "holes", "hole_inset", "wall")  # the menu shows the plain shapes (2026-10-23)
_lock = threading.Lock()


def folder() -> Path:
    import config
    return Path(config.DATA_ROOT) / "cache" / "part_gallery"


def stamp() -> str:
    from systems import cad_shapes
    source = Path(cad_shapes.__file__).read_bytes()
    return hashlib.sha256(source + f"{FRAMES}{SIZE}{COLORS}{TILT}{FACETS}{EXTRAS}".encode()).hexdigest()[:16]


def plain(example: dict) -> dict:
    """A shape's example without its holes and walls: the menu shows the shape itself."""
    return {k: v for k, v in example.items() if k not in EXTRAS}


def show_source(stl: Path, center, frame: int) -> str:
    """One menu frame: the part, centred, tilted toward the viewer and turned a step further about upright."""
    x, y, z = (-float(c) for c in center)
    return (f'color("#4fd1b5") rotate([0, 0, {frame * 360 // FRAMES}]) rotate([{TILT}, 0, 0]) '
            f'translate([{x:g}, {y:g}, {z:g}]) import("{stl}");\n')


def ready(base: Path | None = None) -> bool:
    base = base or folder()
    try:
        return (base / "stamp").read_text().strip() == stamp()
    except OSError:
        return False


def frames(kind: str, base: Path | None = None) -> list[Path]:
    paths = sorted(((base or folder()) / kind).glob("*.png"))
    return paths if len(paths) == FRAMES else []


def draw(base: Path | None = None, openscad: str = "openscad", run=subprocess.run, kinds=None) -> int:
    """Draw every part's frames (or only `kinds`); returns how many parts were drawn."""
    from systems import cad_shapes
    base = base or folder()
    binary = shutil.which(openscad)
    if not binary:
        return 0
    drawn = 0
    with _lock, tempfile.TemporaryDirectory() as tmp:
        for old in base.iterdir() if base.is_dir() else []:
            if old.is_dir() and old.name not in cad_shapes.SHAPES and old.parent == base:
                shutil.rmtree(old)  # pictures of shapes no longer in the catalog (BMO's own cache)
        for kind, part in cad_shapes.SHAPES.items():
            if kinds and kind not in kinds:
                continue
            source, size, _holes = cad_shapes.build(kind, plain(part.example))
            scad, stl = Path(tmp) / f"{kind}.scad", Path(tmp) / f"{kind}.stl"
            scad.write_text(f"$fn = {FACETS};\n" + source)
            if run(["nice", "-n", "19", binary, "-o", str(stl), str(scad)], capture_output=True, timeout=180).returncode:
                LOG.warning("Part gallery: could not draw %s", kind)
                continue
            center = _center(stl)
            # One camera distance for every frame of a shape, so it turns without growing and shrinking.
            distance = 2.4 * sum(float(v) ** 2 for v in size) ** 0.5
            show = Path(tmp) / f"{kind}_show.scad"
            out = base / kind
            out.mkdir(parents=True, exist_ok=True)
            for i in range(FRAMES):
                show.write_text(show_source(stl, center, i))
                run(["nice", "-n", "19", binary, "--preview", f"--imgsize={SIZE[0]},{SIZE[1]}",
                     f"--colorscheme={COLORS}", f"--camera=0,0,0,62,0,0,{distance:.1f}", "-o",
                     str(out / f"{i:02d}.png"), str(show)], capture_output=True, timeout=60)
            drawn += 1
        (base / "stamp").write_text(stamp() + "\n")
    LOG.info("Part gallery: drew %d parts", drawn)
    return drawn


def _center(stl: Path):
    try:
        import trimesh
        lo, hi = trimesh.load(stl, force="mesh").bounds
        return [(a + b) / 2 for a, b in zip(lo, hi)]
    except Exception:
        return [0.0, 0.0, 0.0]


def draw_in_background(delay: float = 90.0) -> threading.Thread | None:
    """At BMO's start: draw the pictures if they are missing or the catalog changed, after `delay` seconds so the
    start itself isn't slowed (about 45 s of OpenSCAD at low priority on the test PC)."""
    if ready() or _lock.locked():
        return None
    thread = threading.Thread(target=lambda: _safe_draw(delay), name="bemo-part-gallery", daemon=True)
    thread.start()
    return thread


def _safe_draw(delay: float = 0.0):
    import time
    time.sleep(delay)
    try:
        if not ready():
            draw()
    except Exception:
        LOG.exception("Part gallery: drawing failed")
