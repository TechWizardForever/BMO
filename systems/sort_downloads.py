"""Games and movies from the Downloads folder to where BMO keeps them (2026-10-28): "Hey BMO, sort my downloads".

BMO looks first and says what he found; nothing moves until "Hey BMO, yes" (owner's choice: move, not copy).
  - GameCube games (.iso .rvz ...) go to BMO's games folder, the same one "import games from USB" fills; a .zip or
    .7z holding them is unpacked there and the archive goes to the Trash.
  - Episodes ("Show - S02E03 ...") go to Videos/TV Shows/<show>/Season 2, into the show's folder if there is one.
  - Other videos go to Videos/Movies, with their subtitles and a poster if the download had one.
Left alone: anything still downloading (a .part / .crdownload / .!qB file next to it, or changed in the last two
minutes), "sample" clips, something already there, hidden folders, and any folder with code in it (.git) such as
BMO's own.
"""
from __future__ import annotations

import difflib
import os
import re
import shutil
import subprocess
import tempfile
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from systems import media
from systems.games import GAMECUBE_EXTENSIONS

SUBTITLE_TYPES = {".srt", ".ass", ".ssa", ".sub", ".idx", ".vtt"}
PICTURE_TYPES = {".jpg", ".jpeg", ".png", ".webp"}
PARTIAL_TYPES = {".part", ".crdownload", ".download", ".partial", ".!qb", ".aria2", ".opdownload", ".tmp"}
ARCHIVE_TYPES = {".zip", ".7z"}
SETTLE_SECONDS = 120.0       # a file changed this recently may still be downloading
SAMPLE_BYTES = 300 * 1024 ** 2
MAX_DEPTH = 4


@dataclass
class Move:
    kind: str                    # "game", "movie" or "episode"
    source: Path
    target: Path
    extras: list = field(default_factory=list)    # (from, to): subtitles, a poster
    members: list = field(default_factory=list)   # for an archive: the game files inside it
    show: str = ""


@dataclass
class Plan:
    moves: list = field(default_factory=list)
    skipped: list = field(default_factory=list)   # (path, "downloading" | "sample" | "already there")

    def count(self, kind: str) -> int:
        return sum(len(m.members) if m.members else 1 for m in self.moves if m.kind == kind)

    def summary(self) -> str:
        """'2 games, a movie and 3 episodes of Jimmy Neutron'."""
        parts = []
        games, movies = self.count("game"), self.count("movie")
        if games:
            parts.append("a game" if games == 1 else f"{games} games")
        if movies:
            parts.append("a movie" if movies == 1 else f"{movies} movies")
        shows: dict[str, int] = {}
        for move in self.moves:
            if move.kind == "episode":
                shows[move.show] = shows.get(move.show, 0) + 1
        for show, n in shows.items():
            parts.append(f"{'an episode' if n == 1 else f'{n} episodes'} of {media.short_show(show)}")
        return _join(parts)


def _join(parts: list[str]) -> str:
    return parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1] if parts else ""


def downloads_folder() -> Path:
    """The desktop's Downloads folder (named in the user's language), else ~/Downloads."""
    try:
        done = subprocess.run(["xdg-user-dir", "DOWNLOAD"], capture_output=True, text=True, timeout=5, check=False)
        found = Path(done.stdout.strip())
        if done.returncode == 0 and found.is_dir() and found != Path.home():
            return found
    except (OSError, subprocess.TimeoutExpired):
        pass
    return Path.home() / "Downloads"


def _norm(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", text.lower().replace("'", "")))


def _same_show(a: str, b: str) -> bool:
    a, b = _norm(a), _norm(b)
    if not a or not b:
        return False
    return set(a.split()) <= set(b.split()) or set(b.split()) <= set(a.split()) or \
        difflib.SequenceMatcher(None, a, b).ratio() >= 0.8


def show_folder(show: str, shows_root: Path) -> Path:
    """Where the show already is: the folder of an episode BMO has of it ("Jimmy Neutron Boy Genius" joins
    "The Adventures of Jimmy Neutron Boy Genius - S01E01" in "The Adventures of Jimmy Neutron"), or a folder named
    like it; else a new folder."""
    if shows_root.is_dir():
        for video in media.library([shows_root]):
            if video.show and _same_show(show, video.show):
                folder = video.path.parent
                if re.fullmatch(r"(?:season \d+|specials|extras)", folder.name, re.I):
                    folder = folder.parent
                if folder != shows_root:
                    return folder
        for folder in sorted(shows_root.iterdir()):
            if folder.is_dir() and _same_show(show, folder.name):
                return folder
    return shows_root / re.sub(r'[\\/:*?"<>|]+', " ", show).strip()


def _archive_games(path: Path) -> list[tuple[str, int]]:
    """(name inside, size) of the GameCube games in a .zip or .7z; [] when it holds none or can't be read."""
    try:
        if path.suffix.lower() == ".zip":
            with zipfile.ZipFile(path) as archive:
                return [(info.filename, info.file_size) for info in archive.infolist()
                        if not info.is_dir() and Path(info.filename).suffix.lower() in GAMECUBE_EXTENSIONS]
        if shutil.which("7z"):
            done = subprocess.run(["7z", "l", "-ba", "-slt", str(path)], capture_output=True, text=True, timeout=60,
                                  check=False)
            found, name = [], None
            for line in done.stdout.splitlines():
                if line.startswith("Path = "):
                    name = line[7:]
                elif line.startswith("Size = ") and name and Path(name).suffix.lower() in GAMECUBE_EXTENSIONS:
                    found.append((name, int(line[7:] or 0)))
            return found
    except (OSError, ValueError, zipfile.BadZipFile, subprocess.TimeoutExpired):
        pass
    return []


def _taken(target: Path, size: int) -> bool:
    """Already there: the same name with the same size."""
    try:
        return target.is_file() and target.stat().st_size == size
    except OSError:
        return False


def plan(downloads: Path, videos_root: Path, games_dir: Path, *, skip=(), now=time.time) -> Plan:
    out = Plan()
    downloads, skip = Path(downloads), [Path(s).resolve() for s in skip]
    if not downloads.is_dir():
        return out
    root_depth = len(downloads.parts)
    for folder, dirs, files in os.walk(downloads):
        here = Path(folder)
        if ".git" in dirs or here.resolve() in skip:
            dirs[:] = []
            continue
        dirs[:] = sorted(d for d in dirs if not d.startswith(".") and len(here.parts) - root_depth < MAX_DEPTH)
        busy = any(Path(name).suffix.lower() in PARTIAL_TYPES for name in files)  # a download in progress here
        videos = [here / n for n in files if Path(n).suffix.lower() in media.VIDEO_TYPES]
        for name in sorted(files):
            path = here / name
            suffix = path.suffix.lower()
            if name.startswith(".") or suffix not in GAMECUBE_EXTENSIONS | media.VIDEO_TYPES | ARCHIVE_TYPES:
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            if busy or now() - stat.st_mtime < SETTLE_SECONDS:
                out.skipped.append((path, "downloading"))
                continue
            if suffix in GAMECUBE_EXTENSIONS:
                target = games_dir / name
                if _taken(target, stat.st_size):
                    out.skipped.append((path, "already there"))
                else:
                    out.moves.append(Move("game", path, target))
            elif suffix in ARCHIVE_TYPES:
                members = _archive_games(path)
                if members:
                    new = [(m, s) for m, s in members if not _taken(games_dir / Path(m).name, s)]
                    if new:
                        out.moves.append(Move("game", path, games_dir, members=[m for m, _ in new]))
                    else:
                        out.skipped.append((path, "already there"))
            else:
                if "sample" in path.stem.lower() and stat.st_size < SAMPLE_BYTES:
                    out.skipped.append((path, "sample"))
                    continue
                video = media.describe(path)
                if video.show:
                    season = "Specials" if not video.season else f"Season {video.season}"
                    target = show_folder(video.show, videos_root / "TV Shows") / season / name
                    kind = "episode"
                else:
                    target = videos_root / "Movies" / name
                    kind = "movie"
                if _taken(target, stat.st_size):
                    out.skipped.append((path, "already there"))
                    continue
                move = Move(kind, path, target, show=video.show)
                alone = len(videos) == 1  # the download's own folder: its subtitles and poster belong to it
                for extra in sorted(here.iterdir()) + (sorted((here / "Subs").iterdir())
                                                       if alone and (here / "Subs").is_dir() else []):
                    ext = extra.suffix.lower()
                    if not extra.is_file() or extra == path:
                        continue
                    if ext in SUBTITLE_TYPES and (extra.name.startswith(path.stem) or alone):
                        rest = extra.name[len(path.stem):] if extra.name.startswith(path.stem) else "." + extra.name
                        move.extras.append((extra, target.parent / (target.stem + rest)))
                    elif ext in PICTURE_TYPES and alone and kind == "movie" and \
                            re.search(r"poster|cover|folder", extra.stem, re.I):
                        move.extras.append((extra, target.with_suffix(ext)))
                out.moves.append(move)
    return out


def _unique(target: Path) -> Path:
    if not target.exists():
        return target
    for n in range(2, 100):
        candidate = target.with_name(f"{target.stem} ({n}){target.suffix}")
        if not candidate.exists():
            return candidate
    raise FileExistsError(target)


def _trash(path: Path, run=subprocess.run) -> bool:
    try:
        return run(["gio", "trash", str(path)], capture_output=True, timeout=60, check=False).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _unpack(move: Move, run=subprocess.run) -> list[Path]:
    """The game files from an archive into the games folder (by way of a temporary folder next to it)."""
    move.target.mkdir(parents=True, exist_ok=True)
    done = []
    with tempfile.TemporaryDirectory(prefix=".bmo-unpack-", dir=move.target) as temp:
        temp = Path(temp)
        if move.source.suffix.lower() == ".zip":
            with zipfile.ZipFile(move.source) as archive:
                for member in move.members:
                    archive.extract(member, temp)
        else:
            run(["7z", "x", "-y", f"-o{temp}", str(move.source), *move.members], capture_output=True,
                timeout=3600, check=True)
        for member in move.members:
            unpacked = temp / member
            if unpacked.is_file():
                target = _unique(move.target / Path(member).name)
                unpacked.replace(target)
                done.append(target)
    return done


def run(plan: Plan, *, trash=_trash, unpack=_unpack) -> dict:
    """Do the moves. {"moved": {"game": n, "movie": n, "episode": n}, "failed": [(path, reason)]}."""
    moved = {"game": 0, "movie": 0, "episode": 0}
    failed = []
    for move in plan.moves:
        try:
            if move.members:
                placed = unpack(move)
                if len(placed) != len(move.members):
                    raise OSError("not every game came out of the archive")
                moved["game"] += len(placed)
                trash(move.source)
                continue
            size = move.source.stat().st_size
            target = _unique(move.target)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(move.source), str(target))
            if target.stat().st_size != size:
                raise OSError("the moved file isn't the same size")
            for extra, where in move.extras:
                if extra.exists() and not where.exists():
                    shutil.move(str(extra), str(where))
            moved[move.kind] += 1
        except (OSError, shutil.Error, subprocess.CalledProcessError, zipfile.BadZipFile) as exc:
            failed.append((move.source, str(exc) or exc.__class__.__name__))
    return {"moved": moved, "failed": failed}


_ASK = re.compile(r"^(?:can you |could you |please )?(?:sort|organi[sz]e|tidy(?: up)?|put away|file away|sort out)"
                  r"(?: (?:my|the|all my|all the))?(?: (?:games and movies|movies and games|games|movies|roms))?"
                  r"(?: (?:in|from) (?:my|the))? ?(?:downloads?|downloads folder|download folder)$")


def is_request(text: str) -> bool:
    """'sort my downloads' / 'organize the downloads folder' / 'sort the games and movies in my downloads'."""
    said = re.sub(r"^\W*h[aeiy]+\s*b[e]?e?mo\b[\s,.!]*", "", str(text), flags=re.I)
    return bool(_ASK.match(" ".join(re.findall(r"[a-z0-9]+", said.lower()))))
