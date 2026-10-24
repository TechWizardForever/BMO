"""App launcher: catalog matching, window verification, and the voice handler."""
import ast
import os
from pathlib import Path
import re
import sys
import tempfile
import types
import unittest

ROOT = Path(os.environ.get("BEMO_TEST_CODE_ROOT", Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(ROOT))
import config
from systems import apps
from systems.apps import (
    AppEntry, AppMatch, Window, XdgAppCatalog, match_app, sounds_like_app, spoken_app_name, wait_for_window,
    window_matches,
)

FIREFOX = AppEntry("firefox", "Firefox Web Browser", "Web Browser", ("internet", "www"), ("firefox",))
CALC = AppEntry("org.gnome.Calculator", "Calculator", "", ("math",), ("gnome-calculator",))
WRITER = AppEntry("libreoffice-writer", "LibreOffice Writer", "Word Processor", (), ("libreoffice", "--writer"), "libreoffice-writer")
THUNAR = AppEntry("thunar", "Thunar File Manager", "File Manager", ("file", "explorer"), ("thunar",))
CATFISH = AppEntry("org.xfce.Catfish", "Catfish File Search", "File Search", (), ("catfish",), "catfish")
LOGIN = AppEntry("lightdm-settings", "Login Window", "", (), ("pkexec", "lightdm-settings"))
ALL = [FIREFOX, CALC, WRITER, THUNAR, CATFISH, LOGIN]


class MatchTests(unittest.TestCase):
    def test_names_generic_names_and_partial_names(self):
        for query, expected in (("firefox", FIREFOX), ("fire fox", FIREFOX), ("the web browser", FIREFOX),
                                ("calculator", CALC), ("writer", WRITER), ("file manager", THUNAR)):
            self.assertIs(match_app(query, ALL).entry, expected, query)

    def test_unknown_and_ambiguous(self):
        self.assertIsNone(match_app("pod bay doors", ALL).entry)
        self.assertTrue(match_app("files", ALL).ambiguous)  # Thunar vs Catfish: ask

    def test_misheard_names_do_not_open_the_wrong_app(self):
        # Live 2026-10-18: Whisper turned "Thunar" into "the center" (opened
        # Settings Manager via its keyword) and "thunder" (would open Thunderbird).
        bird = AppEntry("thunderbird", "Thunderbird Mail", "", (), ("thunderbird",))
        settings = AppEntry("xfce-settings-manager", "Settings Manager", "", ("control", "panel", "center"),
                            ("xfce4-settings-manager",))
        apps = ALL + [bird, settings]
        self.assertIsNone(match_app("thunder", apps).entry)
        self.assertIsNone(match_app("the center", apps).entry)
        self.assertIs(match_app("control panel", apps).entry, settings)
        for heard in ("sooner", "to nar", "sonar", "thunder", "the center"):
            self.assertIs(sounds_like_app(heard, apps), THUNAR, heard)
        writer = WRITER
        self.assertIs(sounds_like_app("libreoffice rider", apps), writer)  # live 2026-10-18
        for other in ("pod bay doors", "banana", "my documents"):
            self.assertIsNone(sounds_like_app(other, apps), other)

    def test_admin_apps_are_flagged(self):
        self.assertTrue(match_app("login window", ALL).entry.needs_admin)
        self.assertFalse(FIREFOX.needs_admin)

    def test_spoken_names(self):
        self.assertEqual(spoken_app_name(FIREFOX), "Firefox")
        self.assertEqual(spoken_app_name(THUNAR), "Thunar")
        self.assertEqual(spoken_app_name(CALC), "Calculator")


class CatalogTests(unittest.TestCase):
    def test_desktop_files_parsed_and_filtered(self):
        with tempfile.TemporaryDirectory() as tmp:
            user, system = Path(tmp, "user", "applications"), Path(tmp, "sys", "applications")
            user.mkdir(parents=True), system.mkdir(parents=True)
            entry = "[Desktop Entry]\nType=Application\nName={name}\nExec={exec}\n{extra}\n"
            (system / "edit.desktop").write_text(entry.format(name="Editor", exec="xed %U", extra=""))
            (user / "edit.desktop").write_text(entry.format(name="My Editor", exec="xed %U", extra=""))
            (system / "kde.desktop").write_text(entry.format(name="KDE Only", exec="k", extra="OnlyShowIn=KDE;"))
            (system / "hidden.desktop").write_text(entry.format(name="Hidden", exec="h", extra="NoDisplay=true"))
            (system / "gone.desktop").write_text(entry.format(name="Gone", exec="g", extra="TryExec=/nonexistent/bin"))
            catalog = XdgAppCatalog([Path(tmp, "user"), Path(tmp, "sys")], desktop="XFCE")
            names = [e.name for e in catalog.entries()]
            self.assertEqual(names, ["My Editor"])  # user entry overrides, others filtered
            self.assertEqual(catalog.entries()[0].exec_argv, ("xed",))  # %U removed


class FakeProbe:
    def __init__(self, frames):
        self.frames = list(frames)  # successive window lists
        self.active = ""

    def windows(self):
        return self.frames.pop(0) if len(self.frames) > 1 else self.frames[0]

    def active_window_id(self):
        return self.active


class VerifyTests(unittest.TestCase):
    def test_window_matching(self):
        self.assertTrue(window_matches(FIREFOX, Window("0x1", 1, "Navigator.firefox", "YouTube — Mozilla Firefox")))
        self.assertTrue(window_matches(CALC, Window("0x2", 2, "gnome-calculator.Gnome-calculator", "Calculator")))
        self.assertTrue(window_matches(WRITER, Window("0x3", 3, "libreoffice.libreoffice-writer", "Untitled 1")))
        self.assertFalse(window_matches(CALC, Window("0x4", 4, "xfce4-terminal.Xfce4-terminal", "Terminal")))

    def test_new_window_confirms_launch(self):
        before = {"0x1": Window("0x1", 1, "xfce4-terminal.Xfce4-terminal", "Terminal")}
        appeared = dict(before, **{"0x9": Window("0x9", 9, "gnome-calculator.Gnome-calculator", "Calculator")})
        clock = iter(range(100))
        window = wait_for_window(FakeProbe([before, before, appeared]), CALC, before, timeout=10,
                                 clock=lambda: next(clock), sleep=lambda s: None)
        self.assertEqual(window.window_id, "0x9")

    def test_no_window_means_no_success(self):
        before = {}
        clock = iter(range(100))
        self.assertIsNone(wait_for_window(FakeProbe([before]), CALC, before, timeout=5,
                                          clock=lambda: next(clock), sleep=lambda s: None))


class VoiceHandlerTests(unittest.TestCase):
    def setUp(self):
        src = (ROOT / "systems" / "chat.py").read_text()
        chat = next(n for n in ast.parse(src).body if isinstance(n, ast.ClassDef) and n.name == "Chat")
        keep = {"_handle_app_launch", "_app_action", "_say_local", "_APP_OPEN", "_NOT_AN_APP", "_stt_hotwords",
                "_app_tools", "_handle_app_window_command", "_APP_SWITCH", "_APP_CLOSE",
                "_match_profile_name", "_normalize_command_text", "_remember_gui_app"}
        body = [n for n in chat.body if (isinstance(n, ast.FunctionDef) and n.name in keep)
                or (isinstance(n, ast.Assign) and n.targets[0].id in keep)]
        ns = {"config": config, "re": re, "time": types.SimpleNamespace(sleep=lambda s: None, monotonic=lambda: 0.0),
              "State": types.SimpleNamespace(RESPONDING="responding"), "SupervisorError": RuntimeError,
              "logger": types.SimpleNamespace(info=lambda *a, **k: None),
              "launch_argv": apps.launch_argv, "spoken_app_name": spoken_app_name,
              "sounds_like_app": sounds_like_app, "AppMatch": AppMatch,
              "window_matches": window_matches, "open_windows": apps.open_windows,
              "process_ancestors": lambda: {4242},  # pretend Bemo runs in pid 4242
              "wait_for_windows_gone": lambda probe, ids, timeout: set(ids) & set(probe.windows()),
              "wait_for_window": lambda probe, entry, before, timeout: probe.new_window}
        exec(compile(ast.fix_missing_locations(ast.Module(body=[ast.ClassDef(
            name="Mini", bases=[], keywords=[], body=body, decorator_list=[])], type_ignores=[])), "mini", "exec"), ns)
        self.Mini = ns["Mini"]

    def chat(self, windows=None, new_window=None, launch_result=None, frames=None):
        c = self.Mini()
        c._app_catalog = types.SimpleNamespace(find=lambda q: match_app(q, ALL), entries=lambda: ALL)
        c.answers, c.questions = [], []
        c._confirm_by_voice = lambda q, **kw: c.questions.append(q) or (c.answers.pop(0) if c.answers else False)
        probe = FakeProbe(frames or [windows or {}])
        probe.new_window = new_window
        c._window_probe = probe
        c.spoken, c.actions = [], []
        c.speak = c.spoken.append
        c._archive_local_exchange = lambda cmd, resp: None
        c.supervisor = types.SimpleNamespace(computer_action=lambda action, payload, **kw: (
            c.actions.append((action, payload)) or (launch_result or {"ok": True})))
        return c

    def test_open_verified(self):
        c = self.chat(new_window=Window("0x9", 9, "gnome-calculator.Gnome-calculator", "Calculator"))
        self.assertTrue(c._handle_app_launch("open calculator", "open calculator"))
        self.assertEqual(c.spoken, ["Opening Calculator.", "Calculator is open."])
        self.assertEqual(c.actions[0][0], "launch")

    def test_no_window_is_reported_honestly(self):
        c = self.chat(new_window=None)
        c._handle_app_launch("open calculator", "can you open the calculator please")
        self.assertIn("didn't see its window", c.spoken[-1])

    def test_already_open_is_focused_not_relaunched(self):
        win = Window("0x1", 1, "Navigator.firefox", "Mozilla Firefox")
        c = self.chat(windows={"0x1": win})
        c._window_probe.active = "0x1"
        c._handle_app_launch("open firefox", "open firefox")
        self.assertEqual(c.actions, [("window_focus", {"window_id": "0x1"})])
        self.assertEqual(c.spoken, ["Firefox is already open, so I brought it to the front."])

    def test_declines_admin_unknown_and_ambiguous(self):
        c = self.chat()
        c._handle_app_launch("x", "open login window")
        c._handle_app_launch("x", "open the pod bay doors.")
        c._handle_app_launch("x", "open files")
        self.assertIn("administrator rights", c.spoken[0])
        self.assertEqual(c.spoken[1], "I couldn't find an app called the pod bay doors on this computer.")
        self.assertIn("Which one?", c.spoken[2])
        self.assertEqual(c.actions, [])

    def test_misheard_name_asks_before_opening(self):
        c = self.chat(new_window=Window("0x5", 5, "thunar.Thunar", "Home - Thunar"))
        c.answers = [True]
        c._handle_app_launch("open sooner", "open sooner")
        self.assertEqual(c.questions, ["Did you mean Thunar? Say Hey BMO, yes, or just say no."])
        self.assertEqual(c.spoken, ["Opening Thunar.", "Thunar is open."])
        c = self.chat()
        c._handle_app_launch("open sooner", "open sooner")  # no answer = no
        self.assertEqual(c.actions, [])
        self.assertIn("Try the app's name again", c.spoken[-1])

    def test_whisper_hint_adds_only_unspellable_file_managers(self):
        c = self.chat()
        c.stt = types.SimpleNamespace(hf_tokenizer=types.SimpleNamespace(
            encode=lambda text, add_special_tokens=False: types.SimpleNamespace(
                ids=[0, 0, 0] if "Thunar" in text else [0])))
        self.assertEqual(c._stt_hotwords(), "Bemo Thunar")  # Firefox, Calculator: no hint needed
        self.assertEqual(c._stt_app_hotwords, {"thunar"})

    def test_files_websites_and_timers_go_to_the_model(self):
        c = self.chat()
        for text in ("open my documents folder", "open youtube.com", "open the file report.pdf", "start a timer"):
            self.assertFalse(c._handle_app_launch(text, text), text)
        self.assertEqual(c.spoken, [])


CALC_WIN = Window("0x9", 9, "gnome-calculator.Gnome-calculator", "Calculator")
FOX_WIN = Window("0x1", 1, "Navigator.firefox", "Mozilla Firefox")
THUNAR_WIN = Window("0x5", 5, "thunar.Thunar", "Home - Thunar")


class SwitchCloseTests(VoiceHandlerTests):
    def test_switch_brings_window_forward_and_checks_it(self):
        c = self.chat(windows={"0x1": FOX_WIN})
        c._window_probe.active = "0x1"
        self.assertTrue(c._handle_app_window_command("x", "switch to firefox"))
        self.assertEqual(c.actions, [("window_focus", {"window_id": "0x1"})])
        self.assertEqual(c.spoken, ["Here's Firefox."])

    def test_model_names_still_switch_brains(self):
        c = self.chat()
        for text in ("switch to coding", "switch to main brain", "go to sleep", "close project"):
            self.assertFalse(c._handle_app_window_command(text, text), text)

    def test_switch_to_app_that_is_not_open(self):
        c = self.chat()
        c._handle_app_window_command("x", "switch to calculator")
        self.assertEqual(c.spoken, ["Calculator isn't open. Say Hey BMO, open Calculator, if you want it."])

    def test_close_is_confirmed_by_the_window_disappearing(self):
        c = self.chat(frames=[{"0x9": CALC_WIN}, {}])
        c._handle_app_window_command("x", "close the calculator")
        self.assertEqual(c.actions, [("window_close", {"window_id": "0x9"})])
        self.assertEqual(c.spoken, ["Closed Calculator."])

    def test_unsaved_work_prompt_is_respected(self):
        c = self.chat(windows={"0x9": CALC_WIN})  # window never goes away
        c._handle_app_window_command("x", "quit calculator")
        self.assertIn("may be asking you to save something", c.spoken[0])

    def test_never_closes_the_terminal_bemo_runs_in(self):
        term = AppEntry("xfce4-terminal", "Xfce Terminal", "Terminal Emulator", (), ("xfce4-terminal",))
        c = self.chat(windows={"0x7": Window("0x7", 4242, "xfce4-terminal.Xfce4-terminal", "Terminal")})
        c._app_catalog = types.SimpleNamespace(find=lambda q: match_app(q, ALL + [term]))
        c._handle_app_window_command("x", "close terminal")
        self.assertEqual(c.actions, [])
        self.assertEqual(c.spoken, ["I won't close Xfce Terminal, because I'm running inside it."])

    def test_ambiguous_name_resolved_by_what_is_open(self):
        c = self.chat(frames=[{"0x5": THUNAR_WIN}, {}])
        c._handle_app_window_command("x", "close files")
        self.assertEqual(c.spoken, ["Closed Thunar."])


if __name__ == "__main__":
    unittest.main()
