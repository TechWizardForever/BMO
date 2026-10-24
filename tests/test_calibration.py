"""Screen calibration by voice (settings plan S2): phrases, nudges, modes, whole-desktop fit, the session."""
import ast
import json
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
from systems import calibration
from systems.calibration import next_mode, nudge, parse_calibration, pick_rate
from systems.display import DisplayManager, DisplayState, IDENTITY_TRANSFORM, fit_transform

# The test PC's monitor as xrandr lists it (EDID left out), plus CRT-converter style modes.
XRANDR = """Screen 0: minimum 320 x 200, current 1920 x 1080, maximum 16384 x 16384
HDMI-A-0 connected 1920x1080+0+0 (normal left inverted right x axis y axis) 600mm x 340mm
   1920x1080    143.98*+ 120.00   100.00   119.88   60.00    50.00    59.94
   1280x720      60.00    50.00    59.94
   720x480       60.00    59.94
   640x480       75.00    60.00    59.94
"""
MODES = [(1920, 1080), (1280, 720), (720, 480), (640, 480)]


class FakeXrandr:
    def __init__(self, transforms_work=True):
        self.calls, self.transforms_work = [], transforms_work

    def __call__(self, argv, **kw):
        self.calls.append(argv[1:])
        if argv[1:] in (["--props"], ["--current", "--props"]):
            return types.SimpleNamespace(returncode=0, stdout=XRANDR, stderr="")
        ok = self.transforms_work or "--transform" not in argv
        return types.SimpleNamespace(returncode=0 if ok else 1, stdout="", stderr="" if ok else "BadMatch")

    def transforms(self):
        return [c[c.index("--transform") + 1] for c in self.calls if "--transform" in c]


class Screen(FakeXrandr):
    """A screen with its own EDID, so the display manager can tell screens apart."""

    def __init__(self, edid, mode):
        super().__init__()
        self.edid, self.mode = edid, mode

    def __call__(self, argv, **kw):
        if argv[1:] in (["--props"], ["--current", "--props"]):
            self.calls.append(argv[1:])
            text = XRANDR.replace("1920x1080+0+0", f"{self.mode}+0+0") + "\tEDID:\n\t\t" + self.edid + "\n"
            text = text.replace("143.98*+", "143.98 +") if self.mode != "1920x1080" else text
            text = text.replace("1280x720      60.00", "1280x720      60.00*") if self.mode == "1280x720" else text
            return types.SimpleNamespace(returncode=0, stdout=text, stderr="")
        if "--mode" in argv:
            self.mode = argv[argv.index("--mode") + 1]
        return super().__call__(argv, **kw)


def manager(fake=None, path=None):
    fake = fake or FakeXrandr()
    return DisplayManager(path or Path(tempfile.mkdtemp()) / "display.json", runner=fake), fake


class PhraseTests(unittest.TestCase):
    def test_nudges(self):
        p = parse_calibration
        self.assertEqual(p("smaller"), {"do": "all", "sign": 1, "size": 1.0})
        self.assertEqual(p("a little smaller"), {"do": "all", "sign": 1, "size": 0.5})
        self.assertEqual(p("make it much bigger"), {"do": "all", "sign": -1, "size": 3.0})
        self.assertEqual(p("move it left"), {"do": "move", "edge": "left", "size": 1.0})
        self.assertEqual(p("move it up a bit"), {"do": "move", "edge": "top", "size": 0.5})
        self.assertEqual(p("the left side is cut off"), {"do": "edge", "edge": "left", "sign": 1, "size": 1.0})
        self.assertEqual(p("I can't see the top"), {"do": "edge", "edge": "top", "sign": 1, "size": 1.0})
        self.assertEqual(p("too much space at the bottom"), {"do": "edge", "edge": "bottom", "sign": -1, "size": 1.0})
        self.assertEqual(p("a little more"), {"do": "again", "size": 0.5})
        for word, do in (("too far", "undo"), ("keep it", "keep"), ("looks good", "keep"), ("cancel", "cancel"),
                         ("reset", "reset"), ("say the numbers", "numbers"), ("help", "help")):
            self.assertEqual(p(word)["do"], do, word)

    def test_modes_and_rates(self):
        self.assertEqual(parse_calibration("lower resolution"), {"do": "resolution", "direction": -1})
        self.assertEqual(parse_calibration("try 640 by 480"), {"do": "mode", "width": 640, "height": 480})
        self.assertEqual(parse_calibration("use 60 hertz"), {"do": "rate", "rate": 60})
        self.assertIsNone(parse_calibration("tell me a joke"))

    def test_start_phrases(self):
        for said in ("calibrate the screen", "the screen is cut off", "fit the picture to the tv",
                     "adjust the display", "the left side is cut off"):
            self.assertTrue(calibration.START.search(said), said)
        for other in ("open the screenshot tool", "what time is it", "tell me about tv shows"):
            self.assertFalse(calibration.START.search(other), other)
        for question in ("how do I fix a cracked phone screen", "what does calibrate the screen mean",
                         "can you fix my laptop screen"):
            self.assertFalse(calibration.should_start(question), question)
        for request in ("calibrate the screen", "can you fit the picture to the tv", "the screen is cut off"):
            self.assertTrue(calibration.should_start(request), request)


class NudgeTests(unittest.TestCase):
    zero = {"left": 0, "right": 0, "top": 0, "bottom": 0}

    def test_smaller_bigger_and_edges(self):
        b, said = nudge(self.zero, {"do": "all", "sign": 1, "size": 1.0}, 1920, 1080)
        self.assertEqual((b, said), ({"left": 19, "right": 19, "top": 11, "bottom": 11}, "Smaller."))
        b, _ = nudge(b, {"do": "all", "sign": -1, "size": 3.0}, 1920, 1080)
        self.assertEqual(b, self.zero)  # never below zero
        b, said = nudge(self.zero, {"do": "edge", "edge": "left", "sign": 1, "size": 0.5}, 640, 480)
        self.assertEqual((b["left"], said), (3, "Left edge in."))

    def test_move_keeps_the_size_and_stops_at_an_edge(self):
        start = {"left": 20, "right": 20, "top": 10, "bottom": 10}
        b, said = nudge(start, {"do": "move", "edge": "left", "size": 1.0}, 1920, 1080)
        self.assertEqual((b["left"], b["right"], said), (1, 39, "Left."))
        b, said = nudge(dict(start, left=0), {"do": "move", "edge": "left", "size": 1.0}, 1920, 1080)
        self.assertEqual(said, "It's already against the left edge.")

    def test_next_mode_and_rate(self):
        self.assertEqual(next_mode(MODES, (1920, 1080), -1), (1280, 720))
        self.assertEqual(next_mode(MODES, (720, 480), -1), (640, 480))
        self.assertIsNone(next_mode(MODES, (640, 480), -1))
        self.assertEqual(next_mode(MODES, (640, 480), 1), (720, 480))
        self.assertEqual(pick_rate((143.98, 120.0, 60.0, 59.94), 143.98), 143.98)
        self.assertEqual(pick_rate((75.0, 60.0, 59.94), 143.98), 60.0)  # CRT converters want 60
        self.assertEqual(pick_rate((60.0, 59.94), 60), 60.0)


class DesktopFitTests(unittest.TestCase):
    def test_transform_math(self):
        self.assertEqual(fit_transform(DisplayState(1920, 1080, 48, 48, 27, 27)),
                         "1.052632,0,-50.526316,0,1.052632,-28.421053,0,0,1")

    def test_preview_revert_and_rates(self):
        dm, fake = manager()
        self.assertEqual(dm.current_rate, 143.98)
        self.assertEqual(dm.rates_for(640, 480), (75.0, 60.0, 59.94))
        result = dm.test_fit(48, 48, 27, 27)
        self.assertTrue(result["desktop_fit"])
        self.assertIn(["--output", "HDMI-A-0", "--fb", "1920x1080", "--transform",
                       "1.052632,0,-50.526316,0,1.052632,-28.421053,0,0,1"], fake.calls)
        dm.reject()
        self.assertEqual(fake.transforms()[-1], IDENTITY_TRANSFORM)
        self.assertEqual(dm.current_state().fit, "app")

    def test_borders_scale_with_the_resolution(self):
        dm, fake = manager()
        dm.test_fit(48, 48, 27, 27)
        dm.test_resolution(640, 480, 60)
        state = dm.current_state()
        self.assertEqual((state.width, state.crop_left, state.crop_top, state.rate), (640, 16, 12, 60.0))
        self.assertIn(["--output", "HDMI-A-0", "--mode", "640x480", "--rate", "60"], fake.calls)

    def test_saved_fit_is_applied_at_startup(self):
        path = Path(tempfile.mkdtemp()) / "display.json"
        dm, _ = manager(path=path)
        dm.test_fit(20, 20, 10, 10)
        dm.approve()
        data = json.loads(path.read_text())
        self.assertEqual(data["screens"][data["last"]]["fit"], "desktop")
        dm2, fake2 = manager(path=path)
        dm2.apply_startup()
        self.assertEqual(fake2.transforms()[-1], fit_transform(DisplayState(1920, 1080, 20, 20, 10, 10, fit="desktop")))

    def test_each_screen_keeps_its_own_settings(self):
        # 2026-10-22: a small CRT (through an HDMI converter) fits at 1280x720 shrunk 30 px a side; the TV is
        # 1920x1080 as it is. Every approval used to keep the first screen's id, so the CRT's shrink came back on
        # the TV.
        path = Path(tempfile.mkdtemp()) / "display.json"
        crt, tv = Screen("00ffffffffffff0021573618bde90200", "1280x720"), Screen("00ffffffffffff004c2d4f0c00000000", "1920x1080")
        dm, _ = manager(crt, path)
        dm.test_resolution(1280, 720)
        dm.test_fit(30, 30, 30, 30)
        dm.approve()
        dm, _ = manager(tv, path)  # the TV plugged in: its own live mode, no shrink
        self.assertEqual(dm.apply_startup()["used"], "different-monitor")
        self.assertEqual(tv.transforms()[-1], IDENTITY_TRANSFORM)
        dm.test_resolution(1920, 1080)
        dm.approve()
        dm, _ = manager(crt, path)  # back to the CRT: its settings come back
        self.assertEqual(dm.apply_startup()["used"], "saved")
        self.assertIn(["--output", "HDMI-A-0", "--mode", "1280x720"], [c[:4] for c in crt.calls])
        self.assertEqual(crt.transforms()[-1], fit_transform(DisplayState(1280, 720, 30, 30, 30, 30, fit="desktop")))
        data = json.loads(path.read_text())
        self.assertEqual(len(data["screens"]), 2)
        self.assertEqual({v["width"] for v in data["screens"].values()}, {1280, 1920})

    def test_a_settings_file_from_before_still_loads(self):
        path = Path(tempfile.mkdtemp()) / "display.json"
        path.write_text(json.dumps({"width": 640, "height": 480, "crop_left": 8, "crop_right": 8, "crop_top": 6,
                                    "crop_bottom": 6, "monitor_id": "output:HDMI-A-0", "fit": "desktop", "rate": 0.0}))
        dm, fake = manager(path=path)
        self.assertEqual(dm.apply_startup()["used"], "saved")
        self.assertEqual(fake.transforms()[-1], fit_transform(DisplayState(640, 480, 8, 8, 6, 6, fit="desktop")))

    def test_games_stretch_only_on_a_squeezed_crt(self):
        # 2026-10-22: in Dolphin the game "was in the bounds but could have gone to the sides a lot more".
        from systems.display import games_should_stretch
        self.assertTrue(games_should_stretch(DisplayState(1280, 720, 26, 52, 14, 28, fit="desktop")))  # the CRT
        self.assertFalse(games_should_stretch(DisplayState(1920, 1080)))  # the TV
        self.assertFalse(games_should_stretch(DisplayState(800, 600, 10, 10, 10, 10, fit="desktop")))  # 4:3 signal
        self.assertFalse(games_should_stretch(None))

    def test_the_connected_screens_settings(self):
        from systems.display import saved_for_connected
        path = Path(tempfile.mkdtemp()) / "display.json"
        crt = Screen("00ffffffffffff0021573618bde90200", "1280x720")
        dm, _ = manager(crt, path)
        dm.test_resolution(1280, 720)
        dm.test_fit(26, 52, 14, 28)
        dm.approve()
        from systems.adapters.xrandr import XrandrBackend
        state = saved_for_connected(path, XrandrBackend(crt))
        self.assertEqual((state.width, state.crop_right), (1280, 52))
        tv = Screen("00ffffffffffff004c2d4f0c00000000", "1920x1080")
        self.assertIsNone(saved_for_connected(path, XrandrBackend(tv)))

    def test_swapping_screens_applies_their_own_settings_in_standby(self):
        # 2026-10-22: after swapping the CRT for the TV while BMO slept, the TV kept the CRT's shrink.
        from systems.display import ScreenWatcher
        ids = iter(["edid:crt", "edid:crt", "edid:tv", "edid:tv", None, "edid:crt"])
        looks = []
        backend = type("B", (), {"query": lambda self, **kw: looks.append(kw) or (
            (lambda i: {"monitor_id": i} if i else None)(next(ids)))})()
        applied = []
        manager_ = type("M", (), {"apply_startup": lambda self: applied.append(1) or {"used": "saved"}})
        watcher = ScreenWatcher(Path(tempfile.mkdtemp()) / "d.json", backend=backend, make_manager=manager_)
        results = [watcher.check() for _ in range(6)]
        self.assertEqual(results, [None, None, "saved", None, None, "saved"])
        self.assertEqual(len(applied), 2)
        # Audit 2026-10-24: a full look every 5 s re-read the TV and grew the Xorg log 3 MB an hour.
        self.assertEqual(looks, [{"current": True}] * 6)
        from systems.adapters.xrandr import XrandrBackend
        screen = Screen("00ffffffffffff004c2d4f0c00000000", "1920x1080")
        self.assertTrue(XrandrBackend(screen).query(current=True)["monitor_id"])
        self.assertEqual(screen.calls[-1], ["--current", "--props"])
        self.assertIn("ScreenWatcher(config.DISPLAY_SETTINGS_PATH).start()", (ROOT / "supervisor.py").read_text())

    def test_driver_without_transforms_falls_back_to_bmos_window(self):
        dm, _ = manager(FakeXrandr(transforms_work=False))
        result = dm.test_fit(20, 20, 10, 10)
        self.assertFalse(result["desktop_fit"])
        self.assertEqual(dm.current_state().fit, "app")


class SessionTests(unittest.TestCase):
    def setUp(self):
        from voice_commands import VoiceSessionGate
        src = (ROOT / "systems" / "chat.py").read_text()
        chat = next(n for n in ast.parse(src).body if isinstance(n, ast.ClassDef) and n.name == "Chat")
        body = [n for n in chat.body if isinstance(n, ast.FunctionDef)
                and n.name in {"_calibration_session", "_calibration_text", "_say_local"}]
        from systems.spoken_match import closest
        ns = {"config": config, "calibration": calibration, "closest_spoken": closest,
              "logger": types.SimpleNamespace(info=lambda *a, **k: None),
              "State": types.SimpleNamespace(RESPONDING="r", DONE="d")}
        exec(compile(ast.fix_missing_locations(ast.Module(body=[ast.ClassDef(
            name="Mini", bases=[], keywords=[], body=body, decorator_list=[])], type_ignores=[])), "mini", "exec"), ns)
        self.Mini, self.Gate = ns["Mini"], VoiceSessionGate

    def run_session(self, heard, dm=None):
        c = self.Mini()
        c.display_manager, c.fake = dm or manager()
        c.voice_gate = self.Gate(wake_aliases=config.VOICE_WAKE_ALIASES)
        c._active_microphone, c.stop_event = object(), types.SimpleNamespace(is_set=lambda: False)
        c.spoken, heard, c.seen_pattern = [], list(heard), []
        c.speak, c._archive_local_exchange = c.spoken.append, lambda *a: None

        def listen(source):
            c.seen_pattern.append(c._calibrating)
            if not heard:
                c.display_manager.reject(timeout=True)  # silence: the preview times out
                return None
            return heard.pop(0)
        c.listen = listen
        c._calibration_session("calibrate the screen")
        return c

    def test_nudge_undo_keep(self):
        c = self.run_session(["smaller", "Hey BMO, move it left", "too far", "a little smaller", "keep it"])
        state = c.display_manager.current_state()
        self.assertEqual((state.fit, state.crop_left, state.crop_top), ("desktop", 29, 16))  # 19 + 10, 11 + 5
        self.assertTrue(c.display_manager.saved_state())
        self.assertEqual(c.spoken[1:], ["Smaller.", "Left.", "Undone.", "Smaller.",
                                        "Saved. The screen will look like this every time I start."])
        self.assertTrue(all(c.seen_pattern))  # pattern showed for the whole session
        self.assertFalse(c._calibrating)      # and the face is back

    def test_misheard_words_count(self):
        c = self.run_session(["smoller", "counsel"])
        self.assertEqual(c.spoken[1], "Smaller.")
        self.assertEqual(c.spoken[-1], "Okay, I put the screen back the way it was.")

    def test_cancel_restores(self):
        c = self.run_session(["bigger", "smaller", "cancel"])
        self.assertEqual(c.fake.transforms()[-1], IDENTITY_TRANSFORM)
        self.assertEqual(c.spoken[-1], "Okay, I put the screen back the way it was.")
        self.assertIsNone(c.display_manager.saved_state())

    def test_silence_puts_it_back(self):
        c = self.run_session(["smaller"])
        self.assertEqual(c.spoken[-1], "I didn't hear anything for a while, so I put the screen back the way it was.")
        self.assertEqual(c.fake.transforms()[-1], IDENTITY_TRANSFORM)

    def test_lower_resolution_keeps_60hz_for_crts(self):
        c = self.run_session(["lower resolution", "lower resolution", "keep it"])
        state = c.display_manager.current_state()
        self.assertEqual((state.width, state.height, state.rate), (720, 480, 60.0))
        self.assertIn("Now 720 by 480 at 60 hertz.", c.spoken[2])


if __name__ == "__main__":
    unittest.main()
