"""BMO's music (2026-11-15): the songs in ~/Music on his screen, and a player that keeps going.

The Supervisor plays them (MusicPlayer), one ffplay at a time with no window, so music never stops him being used the
way a movie does. Going to sleep stops it. "Hey BMO" pauses it until he's done (hold), so he can hear you,
and it picks up again by itself even if he never comes back to say so. Pausing is the operating system's own stop and
continue signal, so nothing is lost or restarted.
"""
from __future__ import annotations

import os
from pathlib import Path
import random
import re
import signal
import subprocess
import threading
import time

AUDIO = {".mp3", ".flac", ".ogg", ".oga", ".opus", ".m4a", ".aac", ".wav", ".wma"}
COVERS = ("cover", "folder", "front", "album")
HOLD_SECONDS = 15.0  # the longest "Hey BMO" keeps it paused without hearing he's done


def songs(folders) -> list[Path]:
    """Every song under the music folders, A to Z by artist, or by the song's name when it has no artist, then by
    name (2026-11-17)."""
    found = []
    for folder in folders:
        folder = Path(folder).expanduser()
        if folder.is_dir():
            found += [p for p in folder.rglob("*") if p.suffix.lower() in AUDIO and p.is_file()]
    return sorted(set(found), key=order)


def order(path: Path) -> tuple:
    song, artist = names(path)
    return (_alphabetical(artist or song), _alphabetical(song), str(path).lower())


def _alphabetical(text: str) -> str:
    return re.sub(r"^(?:the|a|an)\s+", "", re.sub(r"[^\w\s]", "", str(text).lower()).strip())


def names(path: Path) -> tuple[str, str]:
    """(song, artist), from the tags inside the file, else from a '01 - Artist - Song.mp3' name. The artist is ""
    when neither says."""
    found = tags(path)
    song, artist = found.get("title", ""), found.get("artist", "")
    if song:
        return song, artist
    name = re.sub(r"[_]+", " ", Path(path).stem)
    name = re.sub(r"^\s*\d{1,3}\s*[-.)]?\s+", "", name)  # a track number in front
    name = re.sub(r"\s+", " ", name).strip() or Path(path).stem
    named_artist, dash, song = name.partition(" - ")
    return (song.strip(), artist or named_artist.strip()) if dash and song.strip() else (name, artist)


def title(path: Path) -> str:
    """'Artist - Song', or just 'Song' when there's no artist, tidied for saying out loud."""
    song, artist = names(path)
    return f"{artist} - {song}" if artist else song


# --- The tags inside a song (ID3, as MP3s carry them) ------------------------------------------------------

_TAGS: dict = {}
_TEXT_FRAMES = {"TIT2": "title", "TPE1": "artist", "TALB": "album", "TPE2": "band",
                "TT2": "title", "TP1": "artist", "TAL": "album", "TP2": "band"}


def tags(path: Path) -> dict:
    """{'title', 'artist', 'album'}, whichever the file has; {} for none. Read once per file and kept while it doesn't
    change. Cover pictures aren't kept: a big library's would fill the memory."""
    try:
        stat = Path(path).stat()
    except OSError:
        return {}
    key = (str(path), stat.st_mtime_ns, stat.st_size)
    if key not in _TAGS:
        if len(_TAGS) > 4000:
            _TAGS.clear()
        try:
            _TAGS[key] = _read_tags(Path(path))
        except Exception:
            _TAGS[key] = {}
    return _TAGS[key]


def _read_tags(path: Path, want_cover: bool = False) -> dict:
    found: dict = {}
    with open(path, "rb") as file:
        head = file.read(10)
        if len(head) == 10 and head[:3] == b"ID3" and head[3] in (2, 3, 4):
            version = head[3]
            size = _syncsafe(head[6:10])
            data = file.read(min(size, 16 * 1024 * 1024))
            if head[5] & 0x40 and version >= 3:  # an extended header first
                skip = _syncsafe(data[:4]) if version == 4 else int.from_bytes(data[:4], "big") + 4
                data = data[skip:]
            _frames(data, version, found, want_cover)
        if not found.get("title"):
            try:
                file.seek(-128, os.SEEK_END)
                tail = file.read(128)
            except OSError:
                tail = b""
            if tail[:3] == b"TAG":  # ID3 version 1, at the very end
                for name, start in (("title", 3), ("artist", 33), ("album", 63)):
                    value = tail[start:start + 30].split(b"\0")[0].decode("latin-1").strip()
                    if value and not found.get(name):
                        found[name] = value
    if not found.get("artist") and found.get("band"):
        found["artist"] = found["band"]
    found.pop("band", None)
    return found


def _syncsafe(four: bytes) -> int:
    return (four[0] << 21) | (four[1] << 14) | (four[2] << 7) | four[3] if len(four) == 4 else 0


def _frames(data: bytes, version: int, found: dict, want_cover: bool = False) -> None:
    at, id_len, head_len = 0, (3 if version == 2 else 4), (6 if version == 2 else 10)
    while at + head_len <= len(data):
        frame = data[at:at + id_len]
        if not frame.strip(b"\0") or not frame.isalnum():
            break  # the padding after the last frame
        raw = data[at + id_len:at + id_len + (3 if version == 2 else 4)]
        size = (int.from_bytes(raw, "big") if version < 4 else _syncsafe(raw))
        body = data[at + head_len:at + head_len + size]
        at += head_len + size
        name = frame.decode("latin-1")
        if name in _TEXT_FRAMES and body and not found.get(_TEXT_FRAMES[name]):
            text = _text(body[0], body[1:]).split("\0")[0].strip()
            if text:
                found[_TEXT_FRAMES[name]] = text
        elif want_cover and name in ("APIC", "PIC") and body and "cover" not in found:
            picture = _picture(body, version)
            if picture:
                found["cover"] = picture


def _text(encoding: int, raw: bytes) -> str:
    try:
        if encoding == 1:
            return raw.decode("utf-16")
        if encoding == 2:
            return raw.decode("utf-16-be")
        if encoding == 3:
            return raw.decode("utf-8")
    except UnicodeDecodeError:
        return ""
    return raw.decode("latin-1")


def _picture(body: bytes, version: int) -> tuple[bytes, str] | None:
    """The cover picture's bytes and its file ending, from an APIC (or v2.2 PIC) frame."""
    encoding = body[0]
    if version == 2:
        kind, at = body[1:4].decode("latin-1", "ignore").lower(), 4
    else:
        end = body.find(b"\0", 1)
        if end < 0:
            return None
        kind, at = body[1:end].decode("latin-1", "ignore").lower(), end + 1
    at += 1  # what the picture is (front cover, back, ...)
    if encoding in (1, 2):  # the description ends with two zero bytes, on an even step
        while at + 1 < len(body) and body[at:at + 2] != b"\0\0":
            at += 2
        at += 2
    else:
        end = body.find(b"\0", at)
        at = (end + 1) if end >= 0 else len(body)
    picture = body[at:]
    if picture[:3] == b"\xff\xd8\xff" or "jp" in kind:
        return (picture, ".jpg") if picture else None
    if picture[:8] == b"\x89PNG\r\n\x1a\n" or "png" in kind:
        return (picture, ".png") if picture else None
    return None


def cover_file(path: Path, folder: Path | None) -> Path | None:
    """The cover inside the song, saved once in `folder` so the screen can show it; else the album folder's."""
    if folder is not None:
        import hashlib
        try:
            name = hashlib.sha1(f"{path}|{Path(path).stat().st_size}".encode()).hexdigest()[:20]
            for ending in (".jpg", ".png"):
                saved = Path(folder) / (name + ending)
                if saved.is_file():
                    return saved
            picture = _read_tags(Path(path), want_cover=True).get("cover")
            if picture:
                out = Path(folder) / (name + picture[1])
                Path(folder).mkdir(parents=True, exist_ok=True)
                part = out.with_suffix(out.suffix + ".part")
                part.write_bytes(picture[0])
                part.replace(out)
                return out
        except Exception:
            pass  # an odd file just has no cover of its own
    return cover(path)


def cover(path: Path) -> Path | None:
    for stem in COVERS:
        for ext in (".jpg", ".jpeg", ".png"):
            for name in (stem + ext, stem.title() + ext):
                candidate = Path(path).parent / name
                if candidate.is_file():
                    return candidate
    return None


# --- What was said ----------------------------------------------------------------------------------------

_MUSIC = r"(?:music|songs?|tunes?|tracks?|playlist)"
_SONG = r"(?:song|son|track|tune)"
_PLAY_ALL = re.compile(rf"^(?:play|put on|start|turn on|throw on)(?: (?:some|my|the|all my|all the|all of my))? {_MUSIC}$|"
                       rf"^(?:play|put on)(?: (?:some|my|the))? {_MUSIC} (?:please|for me)$|^(?:music|some music)(?: please)?$")
_SHUFFLE = re.compile(rf"^(?:shuffle|play shuffled|shuffle play|mix up)(?: (?:some|my|the|all my|all the))? {_MUSIC}$|"
                      rf"^(?:play|put on)(?: (?:some|my|the))? {_MUSIC} (?:on )?shuffle(?:d)?$|^shuffle$|"
                      # More ways (2026-11-17)
                      rf"^(?:shuffle|shuffle play|mix up) (?:it|them|everything|all|all of it|all of them|the list|"
                      rf"my library|the library|my playlist|the playlist)$|^(?:mix it up|mix them up|shuffle it up)$|"
                      rf"^(?:put|play|turn)(?: (?:it|them|my music|the music|my songs|everything))? on shuffle$|"
                      rf"^(?:turn on|switch on|start) shuffle(?: mode)?$|^shuffle (?:mode|on|all)$|"
                      rf"^(?:play|put on)(?: (?:me|us))? (?:something|anything) random$|"
                      rf"^(?:play|put on)(?: (?:me|us))? (?:a|some) random {_MUSIC}$|^random (?:{_MUSIC}|order)$|"
                      rf"^(?:play|put on)(?: (?:some|my|the|all my))? {_MUSIC} (?:in (?:a )?random order|randomly|at random|mixed up)$|"
                      rf"^(?:surprise me|play me a surprise)$")
_PAUSE = re.compile(rf"^(?:pause|hold)(?: (?:the|my))? (?:music|{_SONG})$")
_RESUME = re.compile(rf"^(?:resume|unpause|continue|keep playing|play)(?: (?:the|my))? (?:music|{_SONG})(?: again)?$|"
                     rf"^(?:start|turn) the music (?:again|back on)$")
_STOP = re.compile(rf"^(?:stop|turn off|kill|end|quit|shut off)(?: (?:the|my|all the))? (?:music|{_SONG}s?)$|^no more music$|"
                   # More ways (2026-11-17)
                   rf"^(?:stop|turn off|kill|end|quit|shut off|switch off|cut|close|silence)(?: (?:the|my|this|that|all the|all my))? "
                   rf"(?:music(?: player)?|{_SONG}s?|tunes|music app)$|"
                   rf"^(?:turn|shut|switch|cut)(?: (?:the|my|this|that))? (?:music|{_SONG}|tunes)(?: player)? off$|"
                   rf"^stop playing(?: (?:the|my|that|this))? (?:music|{_SONG}s?|tunes)$|^(?:music|{_SONG}) (?:off|stop)$|"
                   rf"^(?:thats |that is )?enough (?:music|{_SONG}s?)$|^no more (?:{_SONG}s?|tunes)$|"
                   rf"^(?:get rid of|make|i dont want|i dont need)(?: the| that| this)? (?:music|{_SONG})(?: stop| anymore)?$|"
                   rf"^(?:im|i am|were|we are) done with(?: the| this| my)? (?:music|{_SONG}s?)$")
_NEXT = re.compile(rf"^(?:next|skip|skip this|skip the|skip to the next|play the next|go to the next|change the)(?: one)? {_SONG}$|"
                   rf"^skip(?: this| the)? {_SONG}$|^next {_SONG} please$")
_PREVIOUS = re.compile(rf"^(?:previous|last|play the previous|play the last|go back(?: to)? the(?: last| previous)?|back) {_SONG}$|"
                       rf"^go back a {_SONG}$|^(?:play )?(?:that|the) last {_SONG} again$")
_WHAT = re.compile(rf"^(?:what|which) {_SONG} is (?:this|playing|on)$|^whats (?:this {_SONG}|playing|this)$|"
                   rf"^what(?:s| is) (?:the name of )?(?:this|the) {_SONG}$|^what am i listening to$|^who sings this$|"
                   rf"^what(?:s| is) (?:the )?(?:music|{_SONG}) (?:playing|on)$")
# Said while music plays and no video is on: the music's, like a movie's.
_PLAIN = {"pause": "pause", "stop": "stop", "resume": "resume", "unpause": "resume", "next": "next", "skip": "next",
          "skip it": "next", "previous": "previous", "keep going": "resume", "go on": "resume", "play": "resume",
          # 2026-11-17
          "stop it": "stop", "stop playing": "stop", "turn it off": "stop", "turn that off": "stop", "shut it off": "stop",
          "switch it off": "stop", "kill it": "stop", "enough": "stop", "thats enough": "stop", "that is enough": "stop",
          "stop that": "stop", "turn this off": "stop",
          # 2026-11-24 command check: as on the movie and library screens
          "close": "stop", "close it": "stop", "close that": "stop", "go back": "previous", "back": "previous",
          "go back one": "previous", "skip this": "next", "skip that": "next", "next one": "next"}


def request(text: str, *, playing: bool = False) -> str | None:
    """'play all', 'shuffle', 'pause', 'resume', 'stop', 'next', 'previous' or 'what', else None. With `playing`, the
    plain "pause", "stop", "next" a movie would take."""
    from systems.media_phrases import first
    from systems.sound_alikes import put_right

    def read(said):
        said = re.sub(r"[^\w\s]", "", said)
        said = re.sub(r"\bmusics\b", "music", re.sub(r"\bthem (music|songs?)\b", r"the \1", said))  # Whisper's ways
        said = re.sub(r"^(?:shovel|shuffled|shuffles|shuffling|scuffle|shuffel)\b", "shuffle", said)  # Whisper's ways
        for name, form in (("shuffle", _SHUFFLE), ("play all", _PLAY_ALL), ("pause", _PAUSE), ("resume", _RESUME),
                           ("stop", _STOP), ("next", _NEXT), ("previous", _PREVIOUS), ("what", _WHAT)):
            if form.match(said):
                return name
        return _PLAIN.get(said) if playing else None
    return first(put_right(str(text or "")), read)


# --- The player, in the Supervisor ------------------------------------------------------------------------

class MusicPlayer:
    """One song at a time through ffplay, then the next. Thread-safe; tick() moves it on."""

    RADIO_RETRIES = 3  # a station that drops is reconnected this many times before "next" is tried

    def __init__(self, *, start=None, signal_to=os.kill, clock=time.monotonic):
        self._start = start or (lambda path: subprocess.Popen(
            ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet",
             *(["-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "5"]
               if str(path).startswith(("http://", "https://")) else []), str(path)],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        self._signal, self._clock = signal_to, clock
        self._lock = threading.Lock()
        self.queue: list[str] = []
        self.index = 0
        self._proc = None
        self.paused = False         # the user's own pause
        self._held_until = 0.0      # paused for "Hey BMO"
        self._stop = threading.Event()
        self.stations: list[dict] = []  # the radio (2026-11-24): each queued stream's name and what it plays
        self._retries = 0

    def _kill(self):
        if self._proc is not None and self._proc.poll() is None:
            try:
                self._signal(self._proc.pid, signal.SIGCONT)
                self._proc.terminate()
                self._proc.wait(timeout=2)  # gone, not left behind as a stopped process
            except subprocess.TimeoutExpired:
                self._proc.kill()
            except OSError:
                pass
        self._proc = None

    def _play_index(self):
        self._kill()
        if 0 <= self.index < len(self.queue):
            self._proc = self._start(self.queue[self.index])
            self._started_at = self._clock()
            if self.paused or self._held_until > self._clock():
                self._pause_proc()

    def _pause_proc(self):
        if self._proc is not None and self._proc.poll() is None:
            try:
                self._signal(self._proc.pid, signal.SIGSTOP)
            except OSError:
                pass

    def _resume_proc(self):
        if self._proc is not None and self._proc.poll() is None:
            try:
                self._signal(self._proc.pid, signal.SIGCONT)
            except OSError:
                pass

    def status(self) -> dict:
        with self._lock:
            on = self._proc is not None and self._proc.poll() is None
            song = self.queue[self.index] if on and 0 <= self.index < len(self.queue) else None
            station = self.stations[self.index] if song and self.index < len(self.stations) else None
            return {"ok": True, "playing": bool(on and not self.paused), "paused": bool(on and self.paused),
                    "song": (station["name"] if station else title(Path(song))) if song else None,
                    "number": self.index + 1 if song else None, "of": len(self.queue) if song else 0,
                    "radio": station is not None, "station": dict(station) if station else None}

    def handle(self, request_: dict) -> dict:
        op = request_.get("op")
        with self._lock:
            if op == "play":
                paths = [str(p) for p in request_.get("paths") or []]
                if not paths:
                    return {"ok": False}
                stations = [dict(s) for s in request_.get("stations") or []]
                if request_.get("shuffle"):
                    random.shuffle(paths)
                    stations = []
                self.queue, self.index, self.paused, self._held_until = paths, int(request_.get("start") or 0), False, 0.0
                self.stations = stations if len(stations) == len(paths) else []
                self._retries = 0
                self._play_index()
            elif op == "pause":
                self.paused = True
                self._pause_proc()
            elif op == "resume":
                self.paused = False
                if self._held_until <= self._clock():
                    self._resume_proc()
            elif op == "stop":
                self.queue, self.stations, self.paused, self._held_until = [], [], False, 0.0
                self._kill()
            elif op in ("next", "previous"):
                if not self.queue:
                    return {"ok": False}
                self.index = (self.index + (1 if op == "next" else -1)) % len(self.queue)
                self._retries = 0
                self._play_index()
            elif op == "hold":  # "Hey BMO": quiet while he listens and answers
                self._held_until = self._clock() + float(request_.get("seconds") or HOLD_SECONDS)
                self._pause_proc()
            elif op == "release":
                self._held_until = min(self._held_until, self._clock() + float(request_.get("seconds") or 1.0))
            elif op != "status":
                return {"ok": False}
        return self.status()

    def tick(self) -> None:
        with self._lock:
            if self._held_until and self._held_until <= self._clock():
                self._held_until = 0.0
                if not self.paused:
                    self._resume_proc()
            if self._retries and self._proc is not None and self._proc.poll() is None \
                    and self._clock() - getattr(self, "_started_at", 0.0) > 60:
                self._retries = 0  # it's been playing a minute: a later drop gets its retries again
            if self._proc is not None and self._proc.poll() is not None and self.queue:
                if self.stations and self._retries < self.RADIO_RETRIES:
                    self._retries += 1  # a stream that dropped: the same station again
                    self._play_index()
                elif self.stations and len(self.queue) > 1:
                    self.index, self._retries = (self.index + 1) % len(self.queue), 0  # it won't come back: the next
                    self._play_index()
                elif self.index + 1 < len(self.queue) and not self.stations:
                    self.index += 1
                    self._play_index()
                else:
                    self.queue, self._proc = [], None  # the end of the list

    def start(self) -> threading.Thread:
        def loop():
            while not self._stop.wait(0.5):
                try:
                    self.tick()
                except Exception:
                    pass
        thread = threading.Thread(target=loop, name="bemo-music", daemon=True)
        thread.start()
        return thread

    def stop(self) -> None:
        self._stop.set()
        with self._lock:
            self._kill()
