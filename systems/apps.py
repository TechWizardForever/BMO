"""Find installed applications, launch them, and verify a window appeared.

"Open Firefox" should never need a language model: it is a lookup, a launch,
and an observation. This module holds the portable logic plus the Linux/X11
implementation (XDG desktop entries, gtk-launch, wmctrl). Other platforms
plug in their own catalog/probe; host_app_catalog() and host_window_probe()
return None where nothing is implemented yet, and callers must say so
instead of guessing.
"""
from __future__ import annotations

import configparser
from dataclasses import dataclass, field
import difflib
import os
from pathlib import Path
import platform
import re
import shlex
import shutil
import subprocess
import time

_FIELD_CODE = re.compile(r"%[fFuUdDnNickvm%]")
_FILLER = {
    "the", "a", "an", "my", "app", "application", "program", "please", "up",
    "for", "me", "can", "you", "could", "would", "will", "new",
}
_ADMIN_LAUNCHERS = {"pkexec", "sudo", "gksu", "gksudo", "kdesu", "su"}


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", str(text or "").lower())


def normalize_app_query(text: str) -> str:
    return " ".join(w for w in _words(text) if w not in _FILLER)


@dataclass(frozen=True)
class AppEntry:
    desktop_id: str
    name: str
    generic_name: str = ""
    keywords: tuple[str, ...] = ()
    exec_argv: tuple[str, ...] = ()
    startup_wm_class: str = ""
    terminal: bool = False
    path: str = ""

    @property
    def needs_admin(self) -> bool:
        return bool(self.exec_argv) and os.path.basename(self.exec_argv[0]) in _ADMIN_LAUNCHERS

    @property
    def program(self) -> str:
        """The real program name, looking past env/flatpak wrappers."""
        argv = list(self.exec_argv)
        while argv and (argv[0] == "env" or "=" in argv[0]):
            argv.pop(0)
        if argv and os.path.basename(argv[0]) == "flatpak":
            ids = [a for a in argv[2:] if not a.startswith("-")]
            return ids[0].rsplit(".", 1)[-1].lower() if ids else "flatpak"
        return os.path.basename(argv[0]).lower() if argv else ""

    def window_hints(self) -> tuple[str, ...]:
        hints = {self.startup_wm_class.lower(), self.program, self.desktop_id.lower(),
                 self.desktop_id.rsplit(".", 1)[-1].lower()}
        return tuple(sorted(h for h in hints if len(h) >= 3))


@dataclass
class AppMatch:
    entry: AppEntry | None
    score: float = 0.0
    alternatives: list[AppEntry] = field(default_factory=list)

    @property
    def ambiguous(self) -> bool:
        return bool(self.alternatives)


class XdgAppCatalog:
    """Applications from freedesktop .desktop files (Linux/BSD desktops)."""

    def __init__(self, data_dirs: list[str | Path] | None = None, desktop: str | None = None):
        if data_dirs is None:
            home = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
            system = os.environ.get("XDG_DATA_DIRS", "/usr/local/share:/usr/share").split(":")
            data_dirs = [home, *system]
        self.dirs = [Path(d) / "applications" for d in data_dirs if d]
        current = os.environ.get("XDG_CURRENT_DESKTOP", "") if desktop is None else desktop
        self.desktops = {d.strip().lower() for d in current.split(":") if d.strip()}
        self._entries: list[AppEntry] | None = None
        self._loaded_at = 0.0

    def entries(self, *, max_age_seconds: float = 300.0) -> list[AppEntry]:
        if self._entries is None or time.monotonic() - self._loaded_at > max_age_seconds:
            self._entries = self._load()
            self._loaded_at = time.monotonic()
        return self._entries

    def _visible_here(self, section) -> bool:
        only = {x.strip().lower() for x in section.get("OnlyShowIn", "").split(";") if x.strip()}
        never = {x.strip().lower() for x in section.get("NotShowIn", "").split(";") if x.strip()}
        if only and not (only & self.desktops):
            return False
        return not (never & self.desktops)

    def _load(self) -> list[AppEntry]:
        found: dict[str, AppEntry] = {}
        for folder in self.dirs:  # earlier folders (user) override later (system)
            if not folder.is_dir():
                continue
            for path in sorted(folder.rglob("*.desktop")):
                desktop_id = str(path.relative_to(folder))[: -len(".desktop")].replace("/", "-")
                if desktop_id in found:
                    continue
                entry = self._parse(path, desktop_id)
                if entry is not None:
                    found[desktop_id] = entry
        return sorted(found.values(), key=lambda e: e.name.lower())

    def _parse(self, path: Path, desktop_id: str) -> AppEntry | None:
        parser = configparser.RawConfigParser(strict=False, interpolation=None)
        parser.optionxform = str
        try:
            parser.read(path, encoding="utf-8")
        except (configparser.Error, UnicodeDecodeError, OSError):
            return None
        if "Desktop Entry" not in parser:
            return None
        s = parser["Desktop Entry"]
        if s.get("Type", "") != "Application" or not s.get("Name"):
            return None
        if s.get("NoDisplay", "").lower() == "true" or s.get("Hidden", "").lower() == "true":
            return None
        if not self._visible_here(s):
            return None
        try_exec = s.get("TryExec", "").strip()
        if try_exec and not (shutil.which(try_exec) or Path(try_exec).exists()):
            return None
        try:
            argv = tuple(a for a in shlex.split(_FIELD_CODE.sub("", s.get("Exec", ""))) if a)
        except ValueError:
            return None
        if not argv:
            return None
        return AppEntry(
            desktop_id=desktop_id,
            name=s.get("Name", "").strip(),
            generic_name=s.get("GenericName", "").strip(),
            keywords=tuple(k.strip().lower() for k in s.get("Keywords", "").split(";") if k.strip()),
            exec_argv=argv,
            startup_wm_class=s.get("StartupWMClass", "").strip(),
            terminal=s.get("Terminal", "").lower() == "true",
            path=str(path),
        )

    def find(self, query: str) -> AppMatch:
        return match_app(query, self.entries())


def _singular(words) -> set[str]:
    return {w[:-1] if len(w) > 3 and w.endswith("s") and not w.endswith("ss") else w for w in words}


def _score(query: str, entry: AppEntry) -> float:
    q = query.replace(" ", "")
    name = normalize_app_query(entry.name)
    names = {name, name.replace(" ", ""), entry.program, entry.desktop_id.rsplit(".", 1)[-1].lower(),
             entry.desktop_id.lower(), entry.startup_wm_class.lower()}
    if query in names or q in names:
        # "thunar" is both Thunar File Manager and "thunar --bulk-rename":
        # the plain program wins over a mode of it.
        if query not in names - {entry.program} and q not in names - {entry.program} and len(entry.exec_argv) > 1:
            return 99.0
        return 100.0
    nw = name.split()
    if len(q) >= 5 and any("".join(nw[:k]) == q for k in range(1, len(nw) + 1)):
        # "pulse audio" -> "PulseAudio Volume Control", whole words only:
        # "thunder" (Whisper's "Thunar") must not open Thunderbird Mail.
        return 85.0
    if query and query == normalize_app_query(entry.generic_name):
        return 90.0
    qwords, nwords = _singular(query.split()), _singular(name.split())
    if qwords and qwords <= nwords:
        # "writer" -> "libreoffice writer"; fewer extra words ranks higher.
        return 80.0 - len(nwords - qwords)
    generic = _singular(_words(entry.generic_name))
    if qwords and qwords <= generic:
        return 75.0 - len(generic - qwords)
    if len(query.split()) >= 2 and (query in entry.keywords or qwords <= _singular(entry.keywords)):
        # Search keywords only with two or more words ("control panel"): live
        # 2026-10-18 a misheard "the center" opened Settings Manager.
        return 70.0
    best = max((difflib.SequenceMatcher(None, q, n.replace(" ", "")).ratio() for n in names if n), default=0.0)
    return 60.0 * best if best >= 0.8 else 0.0


def _sound_key(text: str) -> str:
    from systems.spoken_match import sound_key  # shared with the preview sessions
    return sound_key(text)


def sounds_like_app(query: str, entries: list[AppEntry], minimum: float = 0.75) -> AppEntry | None:
    """The one app whose name sounds like a misheard query, for a 'Did you mean…?' question.

    Only single-word names (brand names are what Whisper mishears); None when
    nothing is close or two apps are equally close.
    """
    # Whisper also splits a name into words: "Thunar" -> "the center", so the
    # sound is compared with and without the little words.
    keys = {k for k in (_sound_key(normalize_app_query(query)), _sound_key(" ".join(_words(query)))) if len(k) >= 3}
    if not keys:
        return None
    scored = []
    several = len(_words(query)) >= 2
    for entry in entries:
        if entry.needs_admin:
            continue
        name = spoken_app_name(entry)
        # One-word brand names, and full names when the query has several words
        # ("LibreOffice Rider" -> LibreOffice Writer, live 2026-10-18).
        names = [n for n in {name, entry.name} if len(n.split()) == 1 or (several and len(n.split()) >= 2)]
        if not names:
            continue
        # Multi-word names must sound much closer ("my documents" is not Document Scanner).
        ratio = max(difflib.SequenceMatcher(None, key, _sound_key(n)).ratio() - (0.10 if len(n.split()) > 1 else 0.0)
                    for key in keys for n in names)
        scored.append((ratio, entry))
    scored.sort(key=lambda x: -x[0])
    if not scored or scored[0][0] < minimum or (len(scored) > 1 and scored[0][0] - scored[1][0] < 0.03):
        return None  # nothing close, or no clear winner
    return scored[0][1]


def match_app(query: str, entries: list[AppEntry]) -> AppMatch:
    q = normalize_app_query(query)
    if not q:
        return AppMatch(None)
    scored = sorted(((s, e) for e in entries if (s := _score(q, e)) > 0), key=lambda x: -x[0])
    if not scored:
        return AppMatch(None)
    top, entry = scored[0]
    rivals = [e for s, e in scored[1:] if s >= top - 0.5 and e.name.lower() != entry.name.lower()]
    return AppMatch(entry, top, rivals[:3])


from systems.adapters import Window  # noqa: E402  (moved to the OS adapter, P2)


def window_matches(entry: AppEntry, window: Window) -> bool:
    wm = window.wm_class.lower()
    parts = set(wm.split("."))
    for hint in entry.window_hints():
        if hint in parts or (len(hint) >= 5 and hint in wm):
            return True
    return bool(entry.name) and entry.name.lower() in window.title.lower()


class X11WindowProbe:
    """Window list for the app launcher; the X11 adapter does the work (portability plan P2)."""

    def __init__(self, adapter=None):
        from systems.adapters import desktop
        self._desktop = adapter or desktop()

    def available(self) -> bool:
        return self._desktop.available()[0]

    def windows(self) -> dict[str, Window]:
        return self._desktop.windows()

    def active_window_id(self) -> str:
        active = self._desktop.active_window()
        return f"0x{active:08x}" if active is not None else ""


def host_app_catalog():
    if platform.system() == "Linux":
        return XdgAppCatalog()
    return None  # Windows Start Menu / macOS /Applications adapters not implemented yet


def host_window_probe():
    if platform.system() == "Linux" and not os.environ.get("WAYLAND_DISPLAY"):
        probe = X11WindowProbe()
        return probe if probe.available() else None
    return None  # native Wayland, Windows, macOS: not implemented yet


def spoken_app_name(entry: AppEntry) -> str:
    """'Firefox Web Browser' -> 'Firefox'; 'Thunar File Manager' -> 'Thunar'."""
    name, generic = entry.name.strip(), entry.generic_name.strip()
    if generic and name.lower().endswith(" " + generic.lower()):
        return name[: -len(generic)].strip()
    return name


def launch_argv(entry: AppEntry) -> list[str]:
    """gtk-launch runs the entry exactly as the menu would (Terminal=, D-Bus)."""
    if shutil.which("gtk-launch"):
        return ["gtk-launch", entry.desktop_id]
    if entry.terminal and shutil.which("x-terminal-emulator"):
        return ["x-terminal-emulator", "-e", *entry.exec_argv]
    return list(entry.exec_argv)


def wait_for_window(probe, entry: AppEntry, before: dict[str, Window], *, timeout: float = 20.0,
                    interval: float = 0.25, clock=time.monotonic, sleep=time.sleep) -> Window | None:
    """Return the first NEW window that belongs to the app, or None on timeout."""
    deadline = clock() + float(timeout)
    while True:
        for wid, window in probe.windows().items():
            if wid not in before and window_matches(entry, window):
                return window
        if clock() >= deadline:
            return None
        sleep(interval)


def open_windows(entry: AppEntry, windows: dict[str, Window]) -> list[Window]:
    """The app's windows, oldest first (wmctrl lists in stacking/creation order)."""
    return [w for w in windows.values() if window_matches(entry, w)]


def process_ancestors(pid: int | None = None) -> set[int]:
    """This process and every parent: Bemo must never close the window it runs in."""
    try:
        import psutil
    except ImportError:
        return {os.getpid()}
    pids = set()
    try:
        proc = psutil.Process(pid or os.getpid())
        while proc is not None:
            pids.add(proc.pid)
            proc = proc.parent()
    except psutil.Error:
        pass
    return pids or {os.getpid()}


def wait_for_windows_gone(probe, window_ids, *, timeout: float = 6.0, interval: float = 0.25,
                          clock=time.monotonic, sleep=time.sleep) -> set[str]:
    """Wait until the windows close; return the ids still open at the deadline."""
    remaining = set(window_ids)
    deadline = clock() + float(timeout)
    while remaining:
        remaining &= set(probe.windows())
        if not remaining or clock() >= deadline:
            break
        sleep(interval)
    return remaining
