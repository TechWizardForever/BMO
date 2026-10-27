"""Movie and game libraries on BMO's screen (2026-10-28): "Hey BMO, show my movies" / "show my games".

Tiles with a picture and the name under it, numbered, eight to a page; a plain numbered list when nothing has a
picture. "scroll down" / "scroll up" turn the page and "play number 3" plays that one; while a library is open
these and "close" work without "Hey BMO" (like pause and play during a movie). It closes on its own after a few
minutes without a word, and when something starts playing.

Pictures, first found: a poster next to the movie ("Spirited Away.jpg", "Spirited Away-poster.jpg", or
poster/folder/cover in a folder holding just that movie; for a show, in its folder), a game's cover from ES-DE's
media folder or next to the ROM; else, for movies and shows, a still from the video itself, taken once with
ffmpegthumbnailer (or ffmpeg) and kept in ~/.bemo/library-stills. Every tile shows its name too (owner 2026-10-28).
"""
from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, replace
from pathlib import Path

from systems import media

PICTURE_TYPES = (".jpg", ".jpeg", ".png", ".webp")
FOLDER_PICTURES = ("poster", "folder", "cover", "show")
GAME_MEDIA = ("covers", "3dboxes", "miximages", "titlescreens", "screenshots")
GRID_PAGE, LIST_PAGE = 8, 10
# A 480-pixel JPEG this small is a solid colour or nearly (measured 2026-10-28: any solid colour ~1 KB, a title
# card or a fade ~3.4 KB, real scenes 13 KB and up), so BMO tries a later spot (owner: never a solid colour).
MIN_STILL_BYTES = 8000
STILL_SPOTS = ("15%", "30%", "45%", "60%")
IDLE_CLOSE_SECONDS = 180.0


@dataclass(frozen=True)
class Item:
    title: str
    kind: str                       # "movie", "show" or "game"
    key: str                        # what playing it takes: the movie's path, the show's name, the ROM's path
    picture: Path | None = None
    still_from: Path | None = None  # a video to take a still from when there's no picture


def _sort_key(title: str) -> str:
    return re.sub(r"^(?:the|a|an)\s+", "", title.lower())


def _picture_named(folder: Path, stems) -> Path | None:
    for stem in stems:
        for kind in PICTURE_TYPES:
            for name in (stem + kind, stem + kind.upper()):
                if (folder / name).is_file():
                    return folder / name
    return None


def movie_picture(path: Path, videos_in_folder: int) -> Path | None:
    found = _picture_named(path.parent, (path.stem, f"{path.stem}-poster", f"{path.stem}.poster"))
    if found is None and videos_in_folder == 1:  # a folder for this one movie
        found = _picture_named(path.parent, FOLDER_PICTURES)
    return found


def show_picture(episodes: list[media.Video]) -> Path | None:
    folder = episodes[0].path.parent
    for place in (folder, folder.parent):  # the episode's folder, or the show's when that one is "Season 1"
        found = _picture_named(place, FOLDER_PICTURES)
        if found:
            return found
    return None


def movie_items(folders) -> list[Item]:
    """Every movie, and one tile per show; by name, ignoring a leading "The"."""
    videos = media.library(folders)
    per_folder: dict[Path, int] = {}
    for video in videos:
        per_folder[video.path.parent] = per_folder.get(video.path.parent, 0) + 1
    items = [Item(v.title, "movie", str(v.path), movie_picture(v.path, per_folder[v.path.parent]), v.path)
             for v in videos if not v.show]
    for show in sorted({v.show for v in videos if v.show}):
        episodes = media.episodes_of(show, videos)
        regular = [v for v in episodes if v.season] or episodes
        items.append(Item(media.short_show(show), "show", show, show_picture(episodes), regular[0].path))
    return sorted(items, key=lambda item: _sort_key(item.title))


def game_items(game_library) -> list[Item]:
    """Every game in BMO's GameCube folder, with its cover when ES-DE (or the folder) has one."""
    items = []
    for game in game_library.scan():
        rom = Path(game.path)
        try:
            relative = rom.relative_to(game_library.gamecube_dir).with_suffix("")
        except ValueError:
            relative = Path(rom.stem)
        picture = _picture_named(rom.parent, (rom.stem,))
        for kind in GAME_MEDIA:
            if picture:
                break
            folder = game_library.artwork_dir / "gc" / kind / relative.parent
            picture = _picture_named(folder, (relative.name,))
        items.append(Item(game.title, "game", str(rom), picture))
    return sorted(items, key=lambda item: _sort_key(item.title))


# --- Stills from the videos ----------------------------------------------------------------------------

def still_path(video: Path, folder: Path) -> Path:
    try:
        size = video.stat().st_size
    except OSError:
        size = 0
    return Path(folder) / (hashlib.sha1(f"{video}|{size}".encode()).hexdigest()[:20] + ".jpg")


def make_still(video: Path, out: Path, run=subprocess.run, which=shutil.which) -> bool:
    """One frame, 480 pixels wide, from 15 % into the video; a later spot when that one is a solid colour, a title
    card or a fade (the most detailed one tried is kept). True when a picture was made."""
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp, best = out.with_suffix(".part.jpg"), out.with_suffix(".best.jpg")
    best.unlink(missing_ok=True)
    for n, spot in enumerate(STILL_SPOTS):
        if which("ffmpegthumbnailer"):
            command = ["ffmpegthumbnailer", "-i", str(video), "-o", str(tmp), "-s", "480", "-t", spot, "-q", "8"]
        elif which("ffmpeg"):
            command = ["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-ss", str(300 + 600 * n), "-i", str(video),
                       "-frames:v", "1", "-vf", "scale=480:-2", str(tmp)]
        else:
            return False
        try:
            done = run(command, capture_output=True, timeout=120, check=False)
        except (OSError, subprocess.TimeoutExpired):
            break
        if done.returncode != 0 or not tmp.is_file() or tmp.stat().st_size == 0:
            break
        if not best.is_file() or tmp.stat().st_size > best.stat().st_size:
            tmp.replace(best)
        if best.stat().st_size >= MIN_STILL_BYTES:
            break
    tmp.unlink(missing_ok=True)
    if not best.is_file():
        return False
    best.replace(out)
    return True


def with_stills(items: list[Item], folder: Path) -> list[Item]:
    """The stills already made, as pictures."""
    out = []
    for item in items:
        if item.picture is None and item.still_from is not None:
            still = still_path(item.still_from, folder)
            if still.is_file():
                item = replace(item, picture=still)
        out.append(item)
    return out


def make_stills(view: "LibraryView", items: list[Item], folder: Path, *, make=make_still, stop=lambda: False) -> int:
    """The missing stills, the page on screen first; each one shows up on its tile as it is made."""
    wanted = [item for item in items if item.picture is None and item.still_from is not None]
    first = view.page_keys()
    wanted.sort(key=lambda item: item.key not in first)
    made = 0
    for item in wanted:
        if stop():
            break
        still = still_path(item.still_from, folder)
        if still.is_file() or make(item.still_from, still):
            view.set_picture(item.key, still)
            made += 1
    return made


# --- What's on the screen ------------------------------------------------------------------------------

class LibraryView:
    """Thread-safe: the chat side opens, scrolls and closes it; the renderer draws snapshot()."""

    def __init__(self, clock=time.monotonic):
        self._lock = threading.Lock()
        self._clock = clock
        self.kind: str | None = None
        self.items: list[Item] = []
        self.top = 0
        self.per_page = GRID_PAGE
        self.grid = True
        self._touched = 0.0
        self.generation = 0

    @property
    def active(self) -> bool:
        with self._lock:
            if self.kind and self._clock() - self._touched > IDLE_CLOSE_SECONDS:
                self.kind = None  # nobody has said anything for a while: BMO's face comes back
            return self.kind is not None

    def open(self, kind: str, items: list[Item], grid: bool | None = None) -> None:
        with self._lock:
            self.kind, self.items, self.top = kind, list(items), 0
            self.grid = any(i.picture or i.still_from for i in items) if grid is None else grid
            self.per_page = GRID_PAGE if self.grid else LIST_PAGE
            self._touched = self._clock()
            self.generation += 1

    def close(self) -> bool:
        with self._lock:
            was, self.kind = self.kind is not None, None
            self.generation += 1
            return was

    def scroll(self, pages: int) -> bool:
        """Turn the page; False when there is nothing more that way."""
        with self._lock:
            self._touched = self._clock()
            last = max(0, (len(self.items) - 1) // self.per_page * self.per_page)
            new = min(last, max(0, self.top + pages * self.per_page))
            moved, self.top = new != self.top, new
            return moved

    def to_top(self) -> None:
        with self._lock:
            self.top, self._touched = 0, self._clock()

    def item(self, number: int) -> Item | None:
        with self._lock:
            self._touched = self._clock()
            return self.items[number - 1] if 1 <= number <= len(self.items) else None

    def page_keys(self) -> set[str]:
        with self._lock:
            return {item.key for item in self.items[self.top:self.top + self.per_page]}

    def set_picture(self, key: str, picture: Path) -> None:
        with self._lock:
            self.items = [replace(i, picture=picture) if i.key == key and i.picture is None else i
                          for i in self.items]

    def snapshot(self) -> dict | None:
        if not self.active:
            return None
        with self._lock:
            page = self.items[self.top:self.top + self.per_page]
            return {"kind": self.kind, "grid": self.grid, "total": len(self.items), "first": self.top + 1,
                    "tiles": [{"number": self.top + i + 1, "title": item.title, "kind": item.kind,
                               "picture": str(item.picture) if item.picture else None}
                              for i, item in enumerate(page)]}


# --- What was said -------------------------------------------------------------------------------------

# "open games" / "open the game library" stay the ES-DE game frontend (its app names), so "open" is only for movies.
_OPEN = re.compile(
    r"^(?:can you |could you |please )?(?:(?:show|bring up|pull up|see)(?: me)?(?: (?:my|the|our|all the|all my))?\s*"
    r"(?P<what>movies?|films?|shows?|tv shows?|videos?|movie library|movies and shows|cartoons|"
    r"games?|game library|gamecube games|video games)(?: library| list)?|"
    r"open(?: up)?(?: (?:my|the|our))?\s*(?P<opened>movies|films|shows|tv shows|movie library|movie list|video library)|"
    r"what (?P<asked>movies|games|shows) do (?:i|we) have)$")
_NUMBER_WORDS = ("zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen "
                 "sixteen seventeen eighteen nineteen twenty").split()
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80,
         "ninety": 90}


def _plain(text: str) -> str:
    said = re.sub(r"^\W*h[aeiy]+\s*b[e]?e?mo\b[\s,.!]*", "", str(text), flags=re.I)
    said = " ".join(re.findall(r"[a-z0-9]+", said.lower().replace("'", "")))
    return re.sub(r"^(?:please|ok|okay|now|just|um|uh)\s+", "", said)


def open_request(text: str) -> str | None:
    """'movies' or 'games' for "show my movies" / "open the game library" / "what games do we have"."""
    found = _OPEN.match(_plain(text))
    if not found:
        return None
    what = found.group("what") or found.group("opened") or found.group("asked")
    return "games" if what.startswith(("game", "video game")) else "movies"


def spoken_number(text: str) -> int | None:
    words = text.split()
    if len(words) == 1 and words[0].isdigit():
        return int(words[0])
    total = 0
    for word in words:
        if word in _TENS:
            total += _TENS[word]
        elif word in _NUMBER_WORDS:
            total += _NUMBER_WORDS.index(word)
        elif word in ("to", "too"):
            total += 2
        elif word in ("for",):
            total += 4
        else:
            return None
    return total or None


_SCROLL_DOWN = re.compile(r"^(?:scroll|page|go|move)? ?(?:down|forward|ahead)(?: (?:a|one) page)?$|"
                          r"^(?:next|the next) page$|^(?:show )?more$|^keep going$|^scroll$")
_SCROLL_UP = re.compile(r"^(?:scroll|page|go|move)? ?(?:up|back|backward|backwards)(?: (?:a|one) page)?$|"
                        r"^(?:previous|last|the previous|the last) page$|^go back up$")
_TOP = re.compile(r"^(?:go |scroll |back )?(?:to )?the (?:top|start|beginning)(?: of the list)?$|^top$|^back to the top$")
_CLOSE = re.compile(r"^(?:close|hide|exit|quit|leave)(?: (?:it|this|that|the|my))?(?: (?:list|library|movies|games|"
                    r"movie library|game library|screen|menu))?$|^(?:im |i am |were )?done$|^never ?mind$|^thats all$|"
                    r"^no thanks?(?: you)?$")
_PLAY_NUMBER = re.compile(r"^(?:(?:play|watch|start|open|pick|choose|put on)(?: the one)?(?: number)?|number)\s+(.+)$")


def command(text: str) -> tuple[str, int] | None:
    """For an open library: ('down', 0), ('up', 0), ('top', 0), ('close', 0) or ('play', n); else None."""
    said = _plain(text)
    if _SCROLL_DOWN.match(said):
        return "down", 0
    if _SCROLL_UP.match(said):
        return "up", 0
    if _TOP.match(said):
        return "top", 0
    if _CLOSE.match(said):
        return "close", 0
    found = _PLAY_NUMBER.match(said)
    if found:
        number = spoken_number(found.group(1))
        if number:
            return "play", number
    return None
