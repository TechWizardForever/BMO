"""Screensavers after 4 minutes with no input anywhere, in standby too, but not over a movie or a game
(2026-10-20). Fake desktop tools; nothing is started."""
import os
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(os.environ.get("BEMO_TEST_CODE_ROOT", Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(ROOT))
import config
from systems import saver_guard as sg
from systems.standby_saver import StandbySaver


class FakeDesktop:
    def __init__(self, inhibitors=(), fullscreen_pid=None, wm_class="Firefox", games=()):
        self.inhibitors, self.fullscreen_pid, self.wm_class, self.games = inhibitors, fullscreen_pid, wm_class, games
        self.calls = 0

    def __call__(self, argv, **kwargs):
        self.calls += 1
        out = ""
        if argv[0] == "dbus-send":
            out = "method return\n   array [\n" + "".join(f'      string "{n}"\n' for n in self.inhibitors) + "   ]\n"
        elif argv[:2] == ["xprop", "-root"]:
            out = "_NET_ACTIVE_WINDOW(WINDOW): window id # 0x4600003, 0x0\n"
        elif argv[0] == "xprop":
            state = "_NET_WM_STATE_FULLSCREEN" if self.fullscreen_pid else ""
            out = (f"_NET_WM_STATE(ATOM) = {state}\n_NET_WM_PID(CARDINAL) = {self.fullscreen_pid or 1}\n"
                   f'WM_CLASS(STRING) = "x", "{self.wm_class}"\n')
        elif argv[0] == "pgrep":
            return subprocess.CompletedProcess(argv, 0 if argv[-1] in self.games else 1, "123\n" if argv[-1] in self.games else "", "")
        return subprocess.CompletedProcess(argv, 0, out, "")


class GuardTests(unittest.TestCase):
    def test_a_video_in_the_browser_or_a_player_holds_it_off_other_apps_dont(self):
        # Seen 2026-10-20: a desktop chat app asks to keep the screen on all the time.
        self.assertIsNone(sg.SaverGuard(FakeDesktop(["chat-desktop"])).hold_off())
        self.assertEqual(sg.SaverGuard(FakeDesktop(["chat-desktop", "/usr/bin/google-chrome-stable"])).hold_off(),
                         "playing: google-chrome-stable")
        self.assertEqual(sg.SaverGuard(FakeDesktop(["vlc"])).hold_off(), "playing: vlc")

    def test_full_screen_holds_it_off_unless_it_is_bmo_himself(self):
        self.assertEqual(sg.SaverGuard(FakeDesktop(fullscreen_pid=999, wm_class="dolphin-emu"), own_pid=1).hold_off(),
                         "full screen: dolphin-emu")
        self.assertIsNone(sg.SaverGuard(FakeDesktop(fullscreen_pid=4242), own_pid=4242).hold_off())

    def test_a_running_game_holds_it_off(self):
        self.assertEqual(sg.SaverGuard(FakeDesktop(games=("es-de",))).hold_off(), "game: es-de")

    def test_answers_are_kept_a_few_seconds(self):
        desktop, now = FakeDesktop(), [0.0]
        guard = sg.SaverGuard(desktop, clock=lambda: now[0])
        guard.hold_off()
        calls = desktop.calls
        guard.hold_off()
        self.assertEqual(desktop.calls, calls)
        now[0] += sg.CACHE_SECONDS + 1
        guard.hold_off()
        self.assertGreater(desktop.calls, calls)


class FakeManager:
    def __init__(self, user_exit_after=None):
        self.started, self.stopped, self.ticks, self.user_exit_after = 0, 0, 0, user_exit_after

    def tick(self):
        self.ticks += 1
        if self.user_exit_after is not None and self.ticks > self.user_exit_after:
            return {"running": False, "user_exit": True}
        self.started += 1 if self.ticks == 1 else 0
        return {"running": True, "user_exit": False, "current": "x.scr"}

    def stop(self):
        self.stopped += 1


class FakeGuard:
    def __init__(self, reason=None):
        self.reason = reason

    def hold_off(self):
        return self.reason


class StandbySaverTests(unittest.TestCase):
    def saver(self, idle, *, bemo=False, reason=None, manager=None):
        self.idle, self.bemo = [idle], [bemo]
        self.manager = manager or FakeManager()
        return StandbySaver(bemo_running=lambda: self.bemo[0], manager=self.manager, idle=lambda: self.idle[0],
                            guard=FakeGuard(reason), idle_seconds=240)

    def test_starts_after_four_minutes_and_stops_on_input(self):
        s = self.saver(100)
        self.assertEqual(s.step(), "waiting")
        self.idle[0] = 241
        self.assertEqual(s.step(), "started")
        self.assertEqual(s.step(), "running")
        self.idle[0] = 0.5  # a key or the mouse
        self.assertEqual(s.step(), "woken")
        self.assertEqual(self.manager.stopped, 1)

    def test_not_over_a_movie_or_a_game(self):
        s = self.saver(600, reason="playing: vlc")
        self.assertEqual(s.step(), "held off (playing: vlc)")
        self.assertEqual(self.manager.ticks, 0)

    def test_bmo_waking_up_ends_it(self):
        s = self.saver(600)
        s.step()
        self.bemo[0] = True
        self.assertEqual(s.step(), "bemo")
        self.assertFalse(s.running)

    def test_the_saver_closing_itself_counts_as_woken(self):
        s = self.saver(600, manager=FakeManager(user_exit_after=1))
        s.step()
        self.assertEqual(s.step(), "woken")

    def test_four_minutes_is_the_setting(self):
        self.assertEqual(config.CRT_SCREENSAVER_IDLE_SECONDS, 240.0)


class WiringTests(unittest.TestCase):
    def test_bmo_awake_and_in_standby_both_use_the_rules(self):
        render = (ROOT / "systems" / "render.py").read_text()
        self.assertIn("if idle_due and self._saver_started is None:", render)
        self.assertIn("self._saver_guard.hold_off()", render)
        self.assertIn("from systems.standby_saver import from_config", (ROOT / "supervisor.py").read_text())


class SilentTests(unittest.TestCase):
    """2026-10-20: some savers played sound. Wine is started with its sound drivers disabled; checked by
    hand that winepulse.drv loads without this and no sound driver loads with it."""

    def launch_env(self, **outside):
        import tempfile
        from unittest import mock
        from systems.windows_screensaver import WindowsScreensaverManager
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, outside), \
                mock.patch("subprocess.Popen") as popen:
            m = WindowsScreensaverManager(Path(tmp), wine_binary=sys.executable)
            self.assertTrue(m._start_locked(Path(tmp) / "Noisy.scr", 0.0))
            return popen.call_args.kwargs["env"]

    def test_a_saver_starts_with_wines_sound_drivers_off(self):
        env = self.launch_env()
        for driver in ("winepulse.drv", "winealsa.drv", "wineoss.drv"):
            self.assertIn(driver, env["WINEDLLOVERRIDES"])
        self.assertTrue(env["WINEDLLOVERRIDES"].endswith("=d"))

    def test_other_wine_overrides_are_kept(self):
        env = self.launch_env(WINEDLLOVERRIDES="mscoree=")
        self.assertTrue(env["WINEDLLOVERRIDES"].startswith("mscoree=;winepulse.drv"))


class FakeScreen:
    def __init__(self):
        self.mode, self.set = (1920, 1080), []

    def query(self):
        return {"output": "HDMI-A-0", "width": self.mode[0], "height": self.mode[1], "current_rate": 60.0}

    def set_mode(self, output, width, height, rate=0.0):
        self.set.append((output, width, height, rate))
        self.mode = (width, height)
        return True, None


class ScreenModeTests(unittest.TestCase):
    """Live 2026-10-22: the "Jungle" saver switched the desktop to 640x480 and left it there when woken."""

    def manager(self, script):
        import tempfile
        from systems.windows_screensaver import WindowsScreensaverManager
        folder = Path(tempfile.mkdtemp())
        (folder / "Jungle.scr").write_text(script)  # run by the test's Python standing in for Wine
        self.screen = FakeScreen()
        return WindowsScreensaverManager(folder, wine_binary=sys.executable, startup_grace_seconds=0.25,
                                         search_removable=False, display=self.screen)

    def test_a_saver_that_closes_itself_puts_the_screen_back(self):
        import time
        m = self.manager("import time; time.sleep(0.5)")
        self.assertTrue(m.tick()["running"])
        self.screen.mode = (640, 480)  # what the saver did
        time.sleep(1.0)
        self.assertTrue(m.tick()["user_exit"])
        self.assertEqual(self.screen.set, [("HDMI-A-0", 1920, 1080, 60.0)])

    def test_stopping_a_saver_puts_the_screen_back(self):
        m = self.manager("import time; time.sleep(30)")
        m.tick()
        self.screen.mode = (640, 480)
        m.stop()
        self.assertEqual(self.screen.mode, (1920, 1080))

    def test_an_unchanged_screen_is_left_alone(self):
        m = self.manager("import time; time.sleep(30)")
        m.tick()
        m.stop()
        self.assertEqual(self.screen.set, [])

    def test_awake_and_standby_both_give_the_savers_the_screen(self):
        self.assertIn("display=XrandrBackend()", (ROOT / "systems" / "standby_saver.py").read_text())
        self.assertIn("display=XrandrBackend(),", (ROOT / "systems" / "chat.py").read_text())


class AwakeIdleTests(unittest.TestCase):
    def test_bmos_window_does_not_reset_the_idle_timer(self):
        # SDL's default resets X's idle time every 30 s, so the awake saver never started (2026-10-23).
        env = {k: v for k, v in os.environ.items() if k != "SDL_VIDEO_ALLOW_SCREENSAVER"}
        done = subprocess.run([sys.executable, "-c", "import app, os; print(os.environ['SDL_VIDEO_ALLOW_SCREENSAVER'])"],
                              cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
        self.assertEqual(done.stdout.strip().splitlines()[-1:], ["1"], done.stderr[-2000:])

    def test_talking_to_bmo_counts_as_activity(self):
        # Live 2026-10-23: he answered, then the saver came straight back (only keyboard/mouse counted).
        from systems.desktop_idle import DesktopIdleMonitor
        monitor = DesktopIdleMonitor.__new__(DesktopIdleMonitor)
        monitor.query_idle_seconds = lambda: 600.0  # no keyboard or mouse for 10 minutes
        self.assertEqual(monitor.idle_seconds(5.0), 5.0)  # but he was talked to 5 s ago
        self.assertEqual(monitor.idle_seconds(900.0), 600.0)
        monitor.query_idle_seconds = lambda: None
        self.assertEqual(monitor.idle_seconds(30.0), 30.0)


if __name__ == "__main__":
    unittest.main()
