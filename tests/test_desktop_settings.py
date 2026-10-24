"""Readability by voice (settings plan S3): text size, pointer size, panel auto-hide."""
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
from systems import desktop_settings
from systems.desktop_settings import GnomeSettings, RevertJournal, XfceSettings, parse_setting, step


class FakeXfconf:
    """xfconf-query as on the test PC (Mint 22.3 Xfce): DPI unset, cursor 24, one panel, no autohide set."""

    def __init__(self):
        self.props = {("xsettings", "/Gtk/CursorThemeSize"): "24", ("xfce4-panel", "/panels"): "Value is an array with 1 items:\n\n1"}

    def __call__(self, argv, **kw):
        channel, prop = argv[argv.index("-c") + 1], argv[argv.index("-p") + 1]
        if "-s" in argv:
            self.props[(channel, prop)] = argv[argv.index("-s") + 1]
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")
        if "-r" in argv:
            self.props.pop((channel, prop), None)
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")
        value = self.props.get((channel, prop))
        return types.SimpleNamespace(returncode=0 if value is not None else 1, stdout=value or "", stderr="")


class FakeGsettings:
    def __init__(self):
        self.values = {"text-scaling-factor": "1.0", "cursor-size": "24"}

    def __call__(self, argv, **kw):
        if argv[1] == "set":
            self.values[argv[3]] = argv[4]
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")
        return types.SimpleNamespace(returncode=0, stdout=self.values[argv[3]], stderr="")


class ParseTests(unittest.TestCase):
    def test_phrases(self):
        p = parse_setting
        self.assertEqual(p("make the text bigger"), {"kind": "text", "dir": 1, "big": False})
        self.assertEqual(p("make the text a lot bigger"), {"kind": "text", "dir": 1, "big": True})
        self.assertEqual(p("make the font smaller"), {"kind": "text", "dir": -1, "big": False})
        self.assertEqual(p("text size 150 percent"), {"kind": "text", "value": 150})
        self.assertEqual(p("normal text size"), {"kind": "text", "value": 100})
        self.assertEqual(p("make the mouse pointer bigger"), {"kind": "pointer", "dir": 1, "big": False})
        self.assertEqual(p("normal pointer size"), {"kind": "pointer", "value": 24})
        self.assertEqual(p("hide the panel"), {"kind": "panel", "value": True})
        self.assertEqual(p("bring back the taskbar"), {"kind": "panel", "value": False})
        self.assertEqual(p("stop hiding the panel"), {"kind": "panel", "value": False})
        for other in ("type this text", "read the text", "what is a font", "make it bigger", "tell me a joke"):
            self.assertIsNone(p(other), other)

    def test_levels(self):
        self.assertEqual(step(desktop_settings.TEXT_LEVELS, 100, 1), 112)
        self.assertEqual(step(desktop_settings.TEXT_LEVELS, 130, -1), 125)  # between levels
        self.assertIsNone(step(desktop_settings.TEXT_LEVELS, 200, 1))
        self.assertEqual(step(desktop_settings.POINTER_LEVELS, 24, 1), 32)


class BackendTests(unittest.TestCase):
    def test_xfce(self):
        fake = FakeXfconf()
        xfce = XfceSettings(runner=fake)
        self.assertEqual((xfce.text_percent(), xfce.pointer(), xfce.panel_hidden()), (100, 24, False))
        xfce.set_text_percent(125)
        self.assertEqual(fake.props[("xsettings", "/Xft/DPI")], "120")
        self.assertEqual(xfce.text_percent(), 125)
        xfce.set_panel_hidden(True)
        self.assertEqual(fake.props[("xfce4-panel", "/panels/panel-1/autohide-behavior")], "2")
        self.assertTrue(xfce.panel_hidden())

    def test_gnome_style_desktops(self):
        fake = FakeGsettings()
        gnome = GnomeSettings(runner=fake)
        gnome.set_text_percent(150)
        self.assertEqual((fake.values["text-scaling-factor"], gnome.text_percent()), ("1.50", 150))
        self.assertFalse(gnome.supports("panel"))

    def test_desktop_detection(self):
        self.assertIsNone(desktop_settings.host_desktop_settings({"XDG_CURRENT_DESKTOP": "KDE"}))

    def test_unconfirmed_change_is_undone_at_start(self):
        journal = RevertJournal(Path(tempfile.mkdtemp()) / "pending.json")
        journal.record("text", 100)
        self.assertEqual(journal.pending(), {"kind": "text", "previous": 100})
        journal.clear()
        self.assertIsNone(journal.pending())


class SessionTests(unittest.TestCase):
    def setUp(self):
        from voice_commands import VoiceSessionGate
        src = (ROOT / "systems" / "chat.py").read_text()
        chat = next(n for n in ast.parse(src).body if isinstance(n, ast.ClassDef) and n.name == "Chat")
        keep = {"_handle_desktop_setting", "_desktop_settings", "_describe_setting", "_setting_target", "_say_local",
                "_normalize_command_text", "_recover_desktop_setting", "_SETTING_KEEP", "_SETTING_CANCEL"}
        body = [n for n in chat.body if (isinstance(n, ast.FunctionDef) and n.name in keep)
                or (isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") in keep)]
        self.now = [0.0]
        self.journal_path = Path(tempfile.mkdtemp()) / "pending.json"
        cfg = types.SimpleNamespace(**{k: getattr(config, k) for k in dir(config) if k.isupper()})
        cfg.DESKTOP_SETTINGS_JOURNAL = self.journal_path
        from systems.spoken_match import closest
        ns = {"config": cfg, "re": re, "desktop_settings": desktop_settings, "closest_spoken": closest,
              "logger": types.SimpleNamespace(info=lambda *a, **k: None, warning=lambda *a, **k: None,
                                              exception=lambda *a, **k: None),
              "time": types.SimpleNamespace(monotonic=lambda: self.now[0]),
              "State": types.SimpleNamespace(RESPONDING="r")}
        exec(compile(ast.fix_missing_locations(ast.Module(body=[ast.ClassDef(
            name="Mini", bases=[], keywords=[], body=body, decorator_list=[])], type_ignores=[])), "mini", "exec"), ns)
        self.Mini, self.Gate = ns["Mini"], VoiceSessionGate

    def session(self, said, heard, fake=None):
        c = self.Mini()
        c.fake = fake or FakeXfconf()
        c._desktop_settings_backend = XfceSettings(runner=c.fake)
        c.voice_gate = self.Gate(wake_aliases=config.VOICE_WAKE_ALIASES)
        c._active_microphone, c.stop_event = object(), types.SimpleNamespace(is_set=lambda: False)
        c.spoken, heard = [], list(heard)
        c.speak, c._archive_local_exchange = c.spoken.append, lambda *a: None

        def listen(source):
            self.now[0] += 8.0
            return heard.pop(0) if heard else None
        c.listen = listen
        c._handle_desktop_setting(said)
        return c

    def dpi(self, c):
        return c.fake.props.get(("xsettings", "/Xft/DPI"))

    def test_keep(self):
        c = self.session("make the text bigger", ["keep it"])
        self.assertEqual(self.dpi(c), "108")  # 112%
        self.assertEqual(c.spoken[0], "Text is 112 percent. Say keep it, or cancel, bigger, or smaller. "
                                      "If I don't hear you, I'll put it back in about half a minute.")
        self.assertEqual(c.spoken[-1], "Kept.")
        self.assertFalse(self.journal_path.exists())

    def test_follow_up_then_keep(self):
        c = self.session("make the text bigger", ["bigger", "Hey BMO, bigger", "keep it"])
        self.assertEqual(self.dpi(c), "144")  # 112 -> 125 -> 150 %
        self.assertEqual(c.spoken[1:3], ["Text is 125 percent.", "Text is 150 percent."])

    def test_misheard_follow_ups(self):
        # Live 2026-10-18: "bigger" came back as "Baker" twice and BMO said nothing.
        c = self.session("make the text bigger", ["Baker", "Kansas", "keep it"])
        self.assertEqual(self.dpi(c), "120")  # 112 -> 125 %: "Baker" counted as bigger
        self.assertEqual(c.spoken[1:3], ["Text is 125 percent.", "keep it, cancel, bigger, smaller, or normal?"])
        self.assertEqual(c.spoken[-1], "Kept.")

    def test_sound_alike_matching(self):
        from systems.spoken_match import closest
        choices = ["keep it", "cancel", "bigger", "smaller", "normal"]
        for heard, meant in (("Baker", "bigger"), ("vigor", "bigger"), ("counsel", "cancel"), ("smoller", "smaller")):
            self.assertEqual(closest(heard, choices), meant, heard)
        for noise in ("Kansas", "tell me a joke", "a", ""):
            self.assertIsNone(closest(noise, choices), noise)

    def test_cancel_and_silence_put_it_back(self):
        c = self.session("make the mouse pointer bigger", ["cancel"])
        self.assertEqual(c.fake.props[("xsettings", "/Gtk/CursorThemeSize")], "24")
        self.assertEqual(c.spoken[-1], "Okay, I put it back. The mouse pointer is 24 pixels.")
        c = self.session("hide the panel", [])
        self.assertEqual(c.fake.props[("xfce4-panel", "/panels/panel-1/autohide-behavior")], "0")
        self.assertIn("put it back", c.spoken[-1])

    def test_limits(self):
        fake = FakeXfconf()
        fake.props[("xsettings", "/Xft/DPI")] = "192"
        c = self.session("make the text bigger", [], fake=fake)
        self.assertEqual(c.spoken, ["Text is already as big as I can make it."])

    def test_crash_recovery(self):
        fake = FakeXfconf()
        fake.props[("xsettings", "/Xft/DPI")] = "144"   # left at 150% by a crash...
        RevertJournal(self.journal_path).record("text", 100)
        c = self.Mini()
        c._desktop_settings_backend = XfceSettings(runner=fake)
        c._recover_desktop_setting()
        self.assertNotIn(("xsettings", "/Xft/DPI"), fake.props)  # ...is undone at the next start (default again)
        self.assertFalse(self.journal_path.exists())


if __name__ == "__main__":
    unittest.main()
