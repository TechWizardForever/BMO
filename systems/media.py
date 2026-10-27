"""Movies and shows by voice (2026-10-20): "play Spirited Away", "play Jimmy Neutron season 2 episode 3",
"pause", "resume", "next episode", "skip ahead 30 seconds", "stop the movie".

The library is the Videos folder (MEDIA_DIRS). Videos play in Celluloid, Mint's player, full screen with English
audio first (the Ghibli files carry English and Japanese). Control goes through MPRIS, the standard D-Bus media
interface, with gdbus: nothing extra to install. Linux only (systems/adapters boundary).

Shows: a file named "... - S02E03 - Title ..." is season 2, episode 3; "S01E02-E03" covers both. Playing an
episode queues the rest of its season, so "next episode" moves on; the last episode played of each show is
remembered (~/.bemo/media.json), so "play Jimmy Neutron" carries on where it left off.

Where you stopped (2026-10-28): the player saves its place when it closes (mpv's watch-later files, in BMO's own
folder), and the next "play" picks up there; "restart" / "play <title> from the beginning" starts over. While the
player is open BMO notes which file is on (watch()), so a show carries on from the episode last watched even
after the player moved on to the next one by itself: that episode again if it was stopped part way, else the next.
"""
from __future__ import annotations

import difflib
import hashlib
import json
import re
import subprocess
import time
from urllib.parse import unquote
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


def find(query: str, videos: list[Video], last_played: dict | None = None, resumable=None) -> Video | None:
    """The video meant by what was said after "play", or None. resumable(path): True when that file was stopped
    part way (a show then carries on with that episode rather than the next one)."""
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
    episodes = episodes_of(best.show, videos)
    if season or episode:
        want_season = _number(season.group(1)) if season else None
        want_episode = _number(episode.group(1)) if episode else 1
        for video in episodes:
            if (want_season is None or video.season == want_season) and want_episode in video.episodes:
                return video
        return None
    return show_episode(episodes, last_played, resumable)


def show_episode(episodes: list[Video], last_played: dict | None = None, resumable=None) -> Video:
    """Where a show carries on: the episode last watched if it was stopped part way, else the one after it;
    the first regular episode for a show not watched yet. `episodes` in order."""
    last = (last_played or {}).get(episodes[0].show)
    paths = [str(v.path) for v in episodes]
    if last in paths:
        at = paths.index(last)
        if resumable is not None and resumable(Path(last)):
            return episodes[at]
        if at + 1 < len(paths):
            return episodes[at + 1]
    regular = [v for v in episodes if v.season and v.season > 0]
    return (regular or episodes)[0]


def episodes_of(show: str, videos: list[Video]) -> list[Video]:
    return sorted((v for v in videos if v.show == show), key=lambda v: (v.season, v.episodes))


def queue_for(video: Video, videos: list[Video]) -> list[Path]:
    """The video, then the rest of its season (so "next episode" works)."""
    if not video.show:
        return [video.path]
    season = sorted((v for v in videos if v.show == video.show and v.season == video.season),
                    key=lambda v: v.episodes)
    paths = [v.path for v in season]
    return paths[paths.index(video.path):] if video.path in paths else [video.path]


class Player:
    """Celluloid through MPRIS (gdbus). watch_later: the folder where the player saves its place on closing."""

    def __init__(self, run=subprocess.run, popen=subprocess.Popen, watch_later: Path | None = None):
        self._run, self._popen = run, popen
        self.watch_later = Path(watch_later) if watch_later else None

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

    def play_files(self, paths: list[Path], from_start: bool = False) -> None:
        command = list(PLAYER)
        if self.watch_later:
            self.watch_later.mkdir(parents=True, exist_ok=True)
            command += ["--mpv-save-position-on-quit=yes", f"--mpv-watch-later-directory={self.watch_later}"]
            if from_start:
                command.append("--mpv-resume-playback=no")
        self._popen([*command, *[str(p) for p in paths]], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    start_new_session=True)

    def _property(self, name: str, prop: str) -> str:
        return self._gdbus("--dest", name, "--object-path", "/org/mpris/MediaPlayer2", "--method",
                           "org.freedesktop.DBus.Properties.Get", "org.mpris.MediaPlayer2.Player", prop).stdout or ""

    def now_playing(self) -> tuple[Path, float, float | None] | None:
        """(file, seconds in, length in seconds) of what BMO's player has on, or None."""
        name = self.bus_name()
        if not name or "celluloid" not in name.lower():
            return None
        meta = self._property(name, "Metadata")
        url = re.search(r"'xesam:url': <'file://([^']+)'>", meta)
        if not url:
            return None
        length = re.search(r"'mpris:length': <(?:int64|uint64)? ?(\d+)>", meta)
        position = re.search(r"<(?:int64 )?(-?\d+)>", self._property(name, "Position"))
        return (Path(unquote(url.group(1))), max(0.0, int(position.group(1)) / 1e6) if position else 0.0,
                int(length.group(1)) / 1e6 if length else None)

    def restart(self) -> bool:
        """Back to the start of what's on (a big backwards Seek is ignored by Celluloid: tried 2026-10-28)."""
        name = self.bus_name()
        if not name:
            return False
        track = re.search(r"'mpris:trackid': <objectpath '([^']+)'>", self._property(name, "Metadata"))
        if not track:
            return False
        return self._gdbus("--dest", name, "--object-path", "/org/mpris/MediaPlayer2", "--method",
                           "org.mpris.MediaPlayer2.Player.SetPosition", f"objectpath '{track.group(1)}'",
                           "int64 0").returncode == 0

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
    ("Restart", re.compile(r"^(?:restart|start over|start (?:it|this|that) over|go back to the (?:start|beginning)|"
                           r"(?:start|play|watch) (?:it |this |that |the " + _THING + " )?(?:again )?from the "
                           r"(?:start|beginning)|from the (?:start|beginning))(?: (?:it|this|the " + _THING + "))?$")),
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


_PLAY = re.compile(r"^(?:please |can you |could you )?(?:play|watch|put on|start|restart)\s+(?P<what>.+)$")
_FROM_START = re.compile(r"\s+(?:over(?: from the (?:start|beginning))?|(?:again )?from the (?:start|beginning))$")


def play_request(text: str) -> str | None:
    found = _PLAY.match(words(text))
    return _FROM_START.sub("", found.group("what")) if found else None


def from_start(text: str) -> bool:
    """'restart Spirited Away' / 'play Spirited Away from the beginning' / 'start Ponyo over'."""
    said = words(text)
    return bool(re.match(r"^(?:please |can you |could you )?restart\s", said) or
                (_PLAY.match(said) and _FROM_START.search(said)))


# --- Where you stopped ---------------------------------------------------------------------------------

def saved_position(path: Path, folder: Path | None) -> float | None:
    """Seconds into `path` where the player was closed, from mpv's watch-later file (named by the MD5 of the
    path as given to the player); None when it was finished or never stopped part way."""
    if not folder:
        return None
    try:
        text = (Path(folder) / hashlib.md5(str(path).encode()).hexdigest().upper()).read_text()
    except OSError:
        return None
    found = re.search(r"^start=([\d.]+)", text, re.M)
    return float(found.group(1)) if found else None


def forget_position(path: Path, folder: Path | None) -> None:
    if folder:
        (Path(folder) / hashlib.md5(str(path).encode()).hexdigest().upper()).unlink(missing_ok=True)


def near_end(position: float, length: float | None) -> bool:
    """Into the credits: the last 3 minutes, or the last 5 % of a long movie."""
    return bool(length) and length - position <= max(180.0, length * 0.05)


def spoken_time(seconds: float) -> str:
    hours, minutes = int(seconds // 3600), int(seconds % 3600 // 60)
    if not hours and not minutes:
        return f"{int(seconds)} seconds"
    parts = ([f"{hours} hour{'s' if hours != 1 else ''}"] if hours else []) + \
        ([f"{minutes} minute{'s' if minutes != 1 else ''}"] if minutes else [])
    return " ".join(parts)


def watch(player: "Player", memory: "LastPlayed", *, every: float = 5.0, sleep=time.sleep, wait_for: float = 60.0,
          stop=lambda: False):
    """While BMO's player is open: note what's on and its length every few seconds (memory.watched). Ends when the
    player closes, if it never showed up within `wait_for` seconds, or when stop() (a newer play took over)."""
    seen, waited, last = False, 0.0, None
    while not stop():
        try:
            now = player.now_playing()
        except Exception:
            now = None
        if now is None:
            if seen and not player.running():
                return
            if not seen and waited >= wait_for:
                return
        else:
            seen = True
            if now[0] != last or now[2]:
                memory.watched(*now)
                last = now[0]
        sleep(every)
        waited += every


class LastPlayed:
    """~/.bemo/media.json: the last episode watched of each show, and how long each video is (for "near the end")."""
    def __init__(self, path: Path):
        self.path = Path(path)

    def _all(self) -> dict:
        try:
            data = json.loads(self.path.read_text())
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2) + "\n")
        tmp.replace(self.path)

    def load(self) -> dict:
        return dict(self._all().get("last_played") or {})

    def length(self, path: Path) -> float | None:
        return (self._all().get("lengths") or {}).get(str(path))

    def remember(self, video: Video) -> None:
        if not video.show:
            return
        data = self._all()
        data.setdefault("last_played", {})[video.show] = str(video.path)
        self._save(data)

    def watched(self, path: Path, position: float = 0.0, length: float | None = None) -> None:
        data = self._all()
        video = describe(Path(path))
        changed = False
        if video.show and (data.get("last_played") or {}).get(video.show) != str(path):
            data.setdefault("last_played", {})[video.show] = str(path)
            changed = True
        if length and (data.get("lengths") or {}).get(str(path)) != round(length, 1):
            data.setdefault("lengths", {})[str(path)] = round(length, 1)
            changed = True
        if changed:
            self._save(data)


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
