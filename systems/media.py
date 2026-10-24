"""Movies and shows by voice (2026-10-20): "play Spirited Away", "play Jimmy Neutron season 2 episode 3",
"pause", "resume", "next episode", "skip ahead 30 seconds", "stop the movie".

The library is the Videos folder (MEDIA_DIRS). Videos play in Celluloid, Mint's player, full screen with English
audio first (the Ghibli files carry English and Japanese). Control goes through MPRIS, the standard D-Bus media
interface, with gdbus: nothing extra to install. Linux only (systems/adapters boundary).

Shows: a file named "... - S02E03 - Title ..." is season 2, episode 3; "S01E02-E03" covers both. Playing an
episode queues the rest of its season, so "next episode" moves on; the last episode played of each show is
remembered (~/.bemo/media.json), so "play Jimmy Neutron" carries on where it left off.
"""
from __future__ import annotations

import difflib
import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

try:
    import numpy as np
except ImportError:  # the media module is also read without BMO's environment
    np = None

VIDEO_TYPES = {".mkv", ".mp4", ".avi", ".mov", ".m4v", ".webm", ".wmv", ".mpg", ".mpeg"}
PLAYER = ["celluloid", "--mpv-fs=yes", "--mpv-alang=eng,en,english", "--mpv-sid=no"]
_EPISODE = re.compile(r"\bS(\d{1,2})E(\d{1,3})(?:-?E(\d{1,3}))?\b", re.I)
_NUMBERS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
            "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
            "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
            "sixty": 60, "ninety": 90, "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6,
            "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10, "a": 1, "an": 1, "to": 2, "too": 2, "for": 4}


def words(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", str(text).lower().replace("'", "")))


@dataclass
class Video:
    path: Path
    title: str                      # "Spirited Away" / episode title
    show: str = ""                  # "The Adventures of Jimmy Neutron Boy Genius" for episodes
    season: int | None = None
    episodes: tuple = field(default_factory=tuple)

    @property
    def spoken(self) -> str:
        if self.show:
            eps = (f"episodes {self.episodes[0]} and {self.episodes[1]}" if len(self.episodes) > 1
                   else f"episode {self.episodes[0]}")
            return f"{short_show(self.show)}, season {self.season}, {eps}"
        return self.title


def short_show(show: str) -> str:
    return re.sub(r"^the adventures of ", "", show, flags=re.I).split(" Boy Genius")[0]


def describe(path: Path) -> Video:
    name = path.stem
    found = _EPISODE.search(name)
    if found:
        show = name[:found.start()].strip(" -._")
        rest = name[found.end():].strip(" -._")
        title = re.sub(r"\s*\((?:\d{3,4}p|[^)]*x26[45])[^)]*\)\s*$", "", rest).strip()
        eps = tuple(int(x) for x in (found.group(2), found.group(3)) if x)
        return Video(path, title or name, show=show.replace(".", " ").strip(), season=int(found.group(1)), episodes=eps)
    parts = re.split(r"[._ ]+", name)
    title = []
    for part in parts:
        if re.fullmatch(r"(?:19|20)\d\d", part) and title:
            break
        if re.fullmatch(r"\d{3,4}p|x26[45]|bdrip|webrip|bluray|dvdrip|hdtv", part, re.I):
            break
        title.append(part)
    return Video(path, " ".join(title) or name)


def library(folders) -> list[Video]:
    found = []
    for folder in folders:
        folder = Path(folder).expanduser()
        if not folder.is_dir():
            continue
        for path in sorted(folder.rglob("*")):
            if path.suffix.lower() in VIDEO_TYPES and path.is_file():
                found.append(describe(path))
    return found


def _number(word: str | None) -> int | None:
    if word is None:
        return None
    digits = re.fullmatch(r"(\d+)(?:st|nd|rd|th)?", word)
    return int(digits.group(1)) if digits else _NUMBERS.get(word)


def _episode_phrase(said: str) -> str:
    """'the first episode of jimmy neutron' / 'episode 3 of season 2 of jimmy neutron' / 'season 2 of jimmy
    neutron' -> 'jimmy neutron season N episode N' (live 2026-10-20: "play the first episode of Jimmy Neutron")."""
    found = re.match(r"^(?:the )?(\w+) episode (?:of season (\w+) )?(?:of|from) (.+)$", said)
    if found and _number(found.group(1)):
        return f"{found.group(3)} season {found.group(2) or 1} episode {found.group(1)}"
    found = re.match(r"^episode (\w+) (?:of season (\w+) )?(?:of|from) (.+)$", said)
    if found:
        return f"{found.group(3)} season {found.group(2) or 1} episode {found.group(1)}"
    found = re.match(r"^(?:the )?(?:(\w+) season|season (\w+)) (?:of|from) (.+)$", said)
    if found:
        return f"{found.group(3)} season {found.group(1) or found.group(2)} episode 1"
    return said


def find(query: str, videos: list[Video], last_played: dict | None = None) -> Video | None:
    """The video meant by what was said after "play", or None."""
    said = words(query)
    said = re.sub(r"^(?:the movie|the film|the show|an episode of|some|a bit of)\s+", "", said)
    said = _episode_phrase(said)
    season = re.search(r"\bseason (\w+)", said)
    episode = re.search(r"\bepisode (\w+)", said)
    name = re.sub(r"\b(?:season|episode) \w+", " ", said)
    name = re.sub(r"\b(?:the next|next) episode\b|\bfrom the (?:start|beginning)\b", " ", name).strip()
    if not name:
        return None
    best, best_score = None, 0.0
    for video in videos:
        for candidate in filter(None, (words(video.show), words(short_show(video.show)) if video.show else "",
                                       words(video.title))):
            if set(name.split()) <= set(candidate.split()):
                score = 1.0 + len(name) / max(1, len(candidate))
            else:
                score = difflib.SequenceMatcher(None, name, candidate).ratio()
            if score > best_score:
                best, best_score = video, score
    if best is None or best_score < 0.75:
        return None
    if not best.show:
        return best
    episodes = sorted((v for v in videos if v.show == best.show), key=lambda v: (v.season, v.episodes))
    if season or episode:
        want_season = _number(season.group(1)) if season else None
        want_episode = _number(episode.group(1)) if episode else 1
        for video in episodes:
            if (want_season is None or video.season == want_season) and want_episode in video.episodes:
                return video
        return None
    last = (last_played or {}).get(best.show)
    if last:
        paths = [str(v.path) for v in episodes]
        if last in paths and paths.index(last) + 1 < len(paths):
            return episodes[paths.index(last) + 1]
    regular = [v for v in episodes if v.season and v.season > 0]
    return (regular or episodes)[0]


def queue_for(video: Video, videos: list[Video]) -> list[Path]:
    """The video, then the rest of its season (so "next episode" works)."""
    if not video.show:
        return [video.path]
    season = sorted((v for v in videos if v.show == video.show and v.season == video.season),
                    key=lambda v: v.episodes)
    paths = [v.path for v in season]
    return paths[paths.index(video.path):] if video.path in paths else [video.path]


class Player:
    """Celluloid through MPRIS (gdbus)."""

    def __init__(self, run=subprocess.run, popen=subprocess.Popen):
        self._run, self._popen = run, popen

    def _gdbus(self, *args, timeout=5):
        return self._run(["gdbus", "call", "--session", *args], capture_output=True, text=True, timeout=timeout,
                         check=False)

    def bus_name(self) -> str | None:
        done = self._gdbus("--dest", "org.freedesktop.DBus", "--object-path", "/org/freedesktop/DBus",
                           "--method", "org.freedesktop.DBus.ListNames")
        names = re.findall(r"'(org\.mpris\.MediaPlayer2\.[^']+)'", done.stdout or "")
        return next((n for n in names if "celluloid" in n.lower()), names[0] if names else None)

    def running(self) -> bool:
        return self.bus_name() is not None

    def bmo_player_open(self) -> bool:
        """BMO's own player (Celluloid) is open. A browser tab is a media player too (live 2026-10-21: a Chrome
        window made BMO think a video was on), but it isn't BMO's movie."""
        return "celluloid" in (self.bus_name() or "").lower()

    def play_files(self, paths: list[Path]) -> None:
        self._popen([*PLAYER, *[str(p) for p in paths]], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    start_new_session=True)

    def control(self, method: str, *args: str) -> bool:
        name = self.bus_name()
        if not name:
            return False
        done = self._gdbus("--dest", name, "--object-path", "/org/mpris/MediaPlayer2",
                           "--method", f"org.mpris.MediaPlayer2.Player.{method}", *args)
        return done.returncode == 0

    def quit(self) -> bool:
        """Close the player (MPRIS Quit), so BMO's face is back."""
        name = self.bus_name()
        if not name:
            return False
        return self._gdbus("--dest", name, "--object-path", "/org/mpris/MediaPlayer2",
                           "--method", "org.mpris.MediaPlayer2.Quit").returncode == 0

    def status(self) -> str | None:
        """'Playing', 'Paused', 'Stopped', or None without a player."""
        name = self.bus_name()
        if not name:
            return None
        done = self._gdbus("--dest", name, "--object-path", "/org/mpris/MediaPlayer2", "--method",
                           "org.freedesktop.DBus.Properties.Get", "org.mpris.MediaPlayer2.Player", "PlaybackStatus")
        found = re.search(r"'(Playing|Paused|Stopped)'", done.stdout or "")
        return found.group(1) if found else None

    def volume(self) -> float | None:
        name = self.bus_name()
        if not name:
            return None
        done = self._gdbus("--dest", name, "--object-path", "/org/mpris/MediaPlayer2", "--method",
                           "org.freedesktop.DBus.Properties.Get", "org.mpris.MediaPlayer2.Player", "Volume")
        found = re.search(r"<([0-9.]+)>", done.stdout or "")
        return float(found.group(1)) if found else None

    def set_volume(self, value: float) -> bool:
        name = self.bus_name()
        if not name:
            return False
        return self._gdbus("--dest", name, "--object-path", "/org/mpris/MediaPlayer2", "--method",
                           "org.freedesktop.DBus.Properties.Set", "org.mpris.MediaPlayer2.Player", "Volume",
                           f"<{max(0.0, min(1.5, value)):.3f}>").returncode == 0

    def seek(self, seconds: float) -> bool:
        return self.control("Seek", str(int(seconds * 1_000_000)))


_THING = r"(?:movie|video|show|episode|film|player|media player|celluloid|playback|playing|tv|cartoon)"
_CONTROL = (
    ("Pause", re.compile(r"^(?:pause|hold on|hold up|hold it)(?: (?:it|this|that|the " + _THING + "))?$")),
    ("Play", re.compile(r"^(?:resume|unpause|continue|keep (?:playing|going|watching)|go on|play|start it again)"
                        r"(?: (?:it|the " + _THING + "))?$")),
    ("Stop", re.compile(r"^(?:stop|close|quit|exit|end|turn off|shut off)(?: (?:the|this|that|my))?(?: " + _THING
                        + ")?(?: (?:now|please))?$|^(?:stop|close|turn) it off$|^(?:stop|close) (?:it|that|this)$")),
    ("Next", re.compile(r"^(?:next|next episode|play the next (?:episode|one)|skip (?:this|the) episode|next one|"
                        r"go to the next episode)$")),
    ("Previous", re.compile(r"^(?:previous episode|last episode|go back an episode|play the previous episode|"
                            r"the episode before)$")),
    ("Seek+", re.compile(r"^(?:skip|skip (?:ahead|forward)|fast forward|forward|jump ahead)$")),
    ("Seek-", re.compile(r"^(?:rewind|go back|back up|skip back|jump back)$")),
)
_SEEK = re.compile(r"^(?:skip|jump|go|fast forward|rewind|go back|back up)(?: (ahead|forward|back|backward|backwards))?"
                   r"(?: by)? (\w+) (seconds?|minutes?)$")
_VERB_FORMS = {"stops": "stop", "stopped": "stop", "stopping": "stop", "clothes": "close", "closed": "close",
               "closes": "close", "pauses": "pause", "paused": "pause", "skips": "skip", "skipped": "skip"}
SKIP_SECONDS, BACK_SECONDS = 30.0, 10.0


def _plain(text: str) -> str:
    """The words without "Hey BMO" (also as one word: "Hibemo"), fillers or "we must" (live 2026-10-20)."""
    from systems.voice_intents import canonical
    said = re.sub(r"^\W*h[aeiy]+\s*b[e]?e?mo\b[\s,.!]*", "", str(text), flags=re.I)
    said = re.sub(r"^\W*(?:he|hey|hi|hit|a)\s+be\s+my\b[\s,.!]*", "", said, flags=re.I)  # "He be my pause."
    said = canonical(said, mishearings=False)
    said = words(said)
    said = re.sub(r"^(?:(?:we|you|i)\s+(?:must|need to|should|have to|want to)|please|ok|okay|alright|now|just)\s+",
                  "", said)
    return " ".join(_VERB_FORMS.get(w, w) for w in said.split())


def control_command(text: str, titles=()) -> tuple[str, float] | None:
    """('Pause', 0) / ('Seek', -30.0) for a player command, else None. `titles`: names of what's in the library,
    so "stop Jimmy Neutron" / "close Spirited Away" close the player too."""
    said = _plain(text)
    for method, pattern in _CONTROL:
        if pattern.match(said):
            if method == "Seek+":
                return "Seek", SKIP_SECONDS
            if method == "Seek-":
                return "Seek", -BACK_SECONDS
            return method, 0.0
    found = _SEEK.match(said)
    if found:
        amount = _number(found.group(2))
        if amount:
            seconds = amount * (60 if found.group(3).startswith("minute") else 1)
            backwards = (found.group(1) or "").startswith("back") or said.startswith(("rewind", "go back", "back up"))
            return "Seek", -float(seconds) if backwards else float(seconds)
    closing = re.match(r"^(?:stop|close|quit|exit|end|turn off)\s+(?:watching\s+)?(.+)$", said)
    if closing and titles:
        name = closing.group(1)
        for title in titles:
            candidate = words(title)
            if candidate and (set(name.split()) <= set(candidate.split())
                              or difflib.SequenceMatcher(None, name, candidate).ratio() >= 0.75):
                return "Stop", 0.0
    return None


_BARE_PAUSE = re.compile(r"^(?:pause|pause it|pause (?:the )?(?:movie|video|show|film))$")
_BARE_PLAY = re.compile(r"^(?:play|resume|unpause|play it|resume (?:the )?(?:movie|video|show|film)|keep playing)$")


def bare_command(text: str, status: str | None) -> str | None:
    """Pause or play said WITHOUT "Hey BMO" while a video is open (2026-10-20: it took a few tries).
    Only these two, and only when they make sense: "pause" while playing (a wrong pause is harmless), "play" while
    paused (the movie is silent then, so its own dialogue can't say it). Stop, skip, next still need Hey BMO."""
    said = _plain(text)
    if status == "Playing" and _BARE_PAUSE.match(said):
        return "Pause"
    if status == "Paused" and _BARE_PLAY.match(said):
        return "Play"
    return None


_PLAY = re.compile(r"^(?:please |can you |could you )?(?:play|watch|put on|start)\s+(?P<what>.+)$")


def play_request(text: str) -> str | None:
    found = _PLAY.match(words(text))
    return found.group("what") if found else None


class LastPlayed:
    def __init__(self, path: Path):
        self.path = Path(path)

    def load(self) -> dict:
        try:
            data = json.loads(self.path.read_text())
            return data.get("last_played", {}) if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def remember(self, video: Video) -> None:
        if not video.show:
            return
        data = self.load()
        data[video.show] = str(video.path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"last_played": data}, indent=2) + "\n")


class Ducker:
    """Turns the movie down the moment "Hey BMO" is heard (2026-10-20: commands got lost under the movie).

    Movie dialogue is speech too, so ducking on any voice would pump all through the film; this listens for the
    wake phrase itself, on the live audio while BMO captures a phrase, and turns the player to DUCK_TO of its
    volume. restore() puts it back (BMO calls it when he listens again, after his answer)."""
    DUCK_TO = 0.25
    THRESHOLD = 0.5   # a wrong duck only makes the movie quieter for a moment

    def __init__(self, player, scorer):
        self.player, self.scorer = player, scorer   # scorer: callable(int16 frame of 1280 samples) -> score
        self.original = None
        self._buffer = np.zeros(0, dtype=np.int16) if np is not None else None

    @property
    def ducked(self) -> bool:
        return self.original is not None

    def feed(self, raw: bytes, rate: int, width: int) -> None:
        if self.ducked or width != 2:
            return
        samples = np.frombuffer(raw, dtype="<i2")
        if rate != 16000:
            from scipy.signal import resample_poly
            samples = resample_poly(samples.astype(np.float32), 16000, rate).astype(np.int16)
        self._buffer = np.concatenate([self._buffer, samples])
        while len(self._buffer) >= 1280 and not self.ducked:
            frame, self._buffer = self._buffer[:1280], self._buffer[1280:]
            if self.scorer(frame) >= self.THRESHOLD:
                self.duck()

    log = staticmethod(lambda *args: None)

    def duck(self) -> None:
        volume = self.player.volume()
        if volume is None:
            self.log("MEDIA duck: the player didn't report its volume")
            return
        self.original = volume
        ok = self.player.set_volume(volume * self.DUCK_TO)
        self.log(f"MEDIA duck on Hey BMO: volume {volume:.2f} -> {volume * self.DUCK_TO:.2f} ({'ok' if ok else 'refused'})")

    def restore(self) -> None:
        if self.ducked:
            ok = self.player.set_volume(self.original)
            self.log(f"MEDIA volume restored to {self.original:.2f} ({'ok' if ok else 'refused'})")
            self.original = None
