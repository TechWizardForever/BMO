"""A stand-in BMO for routing tests (2026-11-24 command check): any sentence goes through his real command handling
with every outside effect faked (no speech, players, programs or network). route(sentence, state) says which
_handle_* method took it ("BRAIN" when none did: the main brain would answer) and what he'd say first.

States: "idle", "movie" (a video playing), "music", "radio", "library:<kind>" (a menu on his screen).
Not a test file itself: tests/test_command_routing.py uses it.
"""
import contextlib
import io
import os
import sys
import tempfile
import threading
import time
from unittest import mock

ROOT = os.environ.get("BEMO_TEST_CODE_ROOT", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import config  # noqa: E402
from loguru import logger  # noqa: E402
logger.remove()
from systems import chat as chat_module  # noqa: E402
from systems.chat import Chat  # noqa: E402


class Stub:
    """Anything: every attribute and call is another Stub, falsy, empty, None-ish for .get()."""
    def __init__(self, name="stub"):
        self._name = name

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return Stub(f"{self._name}.{name}")

    def __call__(self, *a, **k):
        return Stub(self._name + "()")

    def __bool__(self):
        return False

    def __iter__(self):
        return iter(())

    def __len__(self):
        return 0

    def __str__(self):
        return ""

    def get(self, key, default=None):
        return default

    def __eq__(self, other):
        return False

    def __hash__(self):
        return id(self)

    def __lt__(self, other):
        return False

    def __gt__(self, other):
        return False

    def __float__(self):
        return 0.0

    def __int__(self):
        return 0


class SupervisorStub:
    """BMO's Supervisor, answering yes to everything (truthy, unlike a Stub)."""
    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return lambda *a, **k: {"ok": True, "running": [], "apps": [], "games": [], "candidates": []}


class FakePlayer:
    def __init__(self, on):
        self.on = on

    def running(self):
        return self.on

    def status(self):
        return "Playing" if self.on else None

    def now_playing(self):
        from pathlib import Path
        return (Path("/videos/Spirited.Away.2001.1080p.mkv"), 600.0, 7500.0) if self.on else None

    def __getattr__(self, name):
        return lambda *a, **k: True


class ProbeChat(Chat):
    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        if name.startswith("_"):
            return None  # private state that was never set: nothing pending, nothing remembered
        return Stub(name)


def _wrap(name):
    real = getattr(Chat, name)

    def wrapper(self, *a, **k):
        self._entered.append(name)
        result = real(self, *a, **k)
        if result:
            self._taken.append(name)
        return result
    return wrapper


for _name in dir(Chat):
    if _name.startswith("_handle_") and callable(getattr(Chat, _name)) and _name not in (
            "_handle_local_command", "_handle_local_command_once"):
        setattr(ProbeChat, _name, _wrap(_name))


def _defaults():
    """BMO's own starting values for his simple settings, read from Chat.__init__ (numbers, text, flags, lists)."""
    import ast
    source = open(os.path.join(ROOT, "systems", "chat.py")).read()
    cls = next(n for n in ast.parse(source).body if isinstance(n, ast.ClassDef) and n.name == "Chat")
    init = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "__init__")
    found = {}
    for node in ast.walk(init):
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name) and target.value.id == "self":
                    try:
                        found[target.attr] = ast.literal_eval(node.value)
                    except Exception:
                        if ast.unparse(node.value) == "time.monotonic()":
                            found[target.attr] = time.monotonic()
    return found


DEFAULTS = _defaults()


def make(state="idle"):
    c = ProbeChat.__new__(ProbeChat)
    for key, value in DEFAULTS.items():
        setattr(c, key, value.copy() if isinstance(value, (list, dict, set)) else value)
    c._taken, c._said, c._entered = [], [], []
    c._visual_lock = threading.Lock()
    c.stop_event = threading.Event()
    c.stop_event.set()  # nothing waits (sleep's animation, voice questions)
    c.state = None
    c.supervisor = SupervisorStub()
    c.active_profile_name = config.MAIN_MODEL_PROFILE
    c.speak = lambda text, **k: c._said.append(str(text))
    c._say_local = lambda command, text: (c._said.append(str(text)), True)[1]
    c._archive_local_exchange = lambda *a, **k: None
    c._confirm_by_voice = lambda question, **k: (c._said.append("ASKS: " + str(question)), False)[1]
    c.respond = lambda text, *a, **k: c._said.append("RESPOND:" + str(text))
    c._open_voice_session = c._close_voice_session = lambda *a, **k: None
    on = state in ("music", "radio")
    station = {"name": "Jazz One", "url": "http://x", "about": "jazz"}
    status = {"ok": True, "playing": on, "paused": False, "song": "Jazz One" if state == "radio" else ("A Song" if on else None),
              "radio": state == "radio", "station": station if state == "radio" else None}
    c._music = lambda op, **p: dict(status)
    c._music_playing = lambda: on
    c._player = lambda: FakePlayer(state == "movie")
    c.interface_settings = chat_module.InterfaceSettings(os.path.join(tempfile.mkdtemp(), "interface.json"))
    if state.startswith("library:"):
        from systems import library
        view = library.LibraryView()
        kind = state.split(":", 1)[1]
        items = [library.Item(f"Item {n}", "movie", f"/x/{n}") for n in range(12)]
        view.open(kind, items)
        c.library_view = view
    else:
        from systems import library
        c.library_view = library.LibraryView()
    return c


def route(sentence, state="idle"):
    c = make(state)
    with contextlib.redirect_stdout(io.StringIO()), mock.patch("systems.radio.search", return_value=[
            {"name": "Jazz One", "url": "http://x", "about": "jazz"}]), \
            mock.patch("urllib.request.urlopen", side_effect=OSError("no network in the probe")), \
            mock.patch("subprocess.run", side_effect=OSError("no programs in the probe")), \
            mock.patch("subprocess.Popen", side_effect=OSError("no programs in the probe")):
        try:
            handled = c._handle_local_command(sentence)
        except Exception as exc:  # after BMO answered, a crash is the probe's fakes running out; before, a finding
            where = c._entered[-1] if c._entered else "inline"
            if c._said or "in the probe" in str(exc):  # it got there; the probe's fakes stopped it going further
                return (where, (c._said[0] if c._said else "(acted)")[:120])
            return ("CRASH in " + where + ":" + type(exc).__name__ + ":" + str(exc)[:60], "")
    if not handled:
        return ("BRAIN", "")
    return (c._taken[0] if c._taken else "inline", (c._said[0] if c._said else "")[:120])
