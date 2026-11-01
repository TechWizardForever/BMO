"""'Hey BMO, eject the USB' really does it (owner 2026-11-01). Live, the brain said "USB ejected." with nothing behind
it, and the stick was still mounted."""
import os
from pathlib import Path
import sys
import types
import unittest
from unittest import mock

ROOT = Path(os.environ.get("BEMO_TEST_CODE_ROOT", Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(ROOT))
import config
from systems import usb_eject
from systems.chat import Chat
from systems.grounding import unbacked_claim


def done(code=0, out="", err=""):
    return types.SimpleNamespace(returncode=code, stdout=out, stderr=err)


class PhraseTests(unittest.TestCase):
    def test_asks(self):
        for said in ("Hey BMO, eject the USB.", "eject my USB stick", "eject", "unmount the USB drive",
                     "safely remove the USB", "could you safely remove the USB drive?", "remove the flash drive",
                     "eject the thumb drive", "take out the USB", "make the USB safe to remove"):
            self.assertTrue(usb_eject.is_request(said), said)

    def test_not_asks(self):
        for said in ("what is a USB stick", "sort my USB", "remove the file", "eject the movie", "how do I eject a disc"):
            self.assertFalse(usb_eject.is_request(said), said)


class EjectTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.state = {"/media/me/STICK": "/dev/sdb1"}

    def run_ok(self, command, **kw):
        self.calls.append(command)
        if command[:2] == ["udisksctl", "unmount"]:
            self.state.clear()
        return done(out="sdb\n" if command[0] == "lsblk" else "")

    def test_unmounts_checks_then_powers_off(self):
        result = usb_eject.eject("/media/me/STICK", run=self.run_ok, mounted=lambda: self.state)
        self.assertEqual(result, {"ok": True})
        self.assertEqual(self.calls[0][:2], ["udisksctl", "unmount"])
        self.assertIn("/dev/sdb1", self.calls[0])
        self.assertIn("--no-user-interaction", self.calls[0])
        self.assertEqual(self.calls[-1][:2], ["udisksctl", "power-off"])
        self.assertIn("/dev/sdb", self.calls[-1])

    def test_busy_says_so_and_never_powers_off(self):
        run = lambda command, **kw: self.calls.append(command) or done(1, err="target is busy")
        self.assertEqual(usb_eject.eject("/media/me/STICK", run=run, mounted=lambda: self.state),
                         {"ok": False, "why": "busy"})
        self.assertEqual(len(self.calls), 1)

    def test_still_mounted_is_a_failure_whatever_the_tool_said(self):
        run = lambda command, **kw: done()
        self.assertEqual(usb_eject.eject("/media/me/STICK", run=run, mounted=lambda: self.state),
                         {"ok": False, "why": "failed"})

    def test_a_failed_power_off_is_still_safe(self):
        def run(command, **kw):
            if command[:2] == ["udisksctl", "unmount"]:
                self.state.clear()
                return done()
            raise OSError("no")
        with self.assertRaises(OSError):
            run(["x"])
        result = usb_eject.eject("/media/me/STICK", run=lambda c, **k: run(c) if c[0] != "lsblk" else done(out="sdb\n"),
                                 mounted=lambda: self.state)
        self.assertEqual(result, {"ok": True})


class HandlerTests(unittest.TestCase):
    def say(self, roots, results):
        c = Chat.__new__(Chat)
        said = []
        c._say_local = lambda command, text: said.append(text) or True
        with mock.patch("systems.games.discover_removable_drives", return_value=roots), \
                mock.patch.object(usb_eject, "eject", side_effect=results):
            self.assertTrue(c._handle_eject_usb("eject the USB"))
        return said[0]

    def test_what_he_says(self):
        self.assertIn("don't see", self.say([], []))
        self.assertIn("safe to pull out", self.say(["/m/a"], [{"ok": True}]))
        self.assertIn("all 2", self.say(["/m/a", "/m/b"], [{"ok": True}, {"ok": True}]))
        busy = self.say(["/m/a"], [{"ok": False, "why": "busy"}])
        self.assertIn("still using it", busy)
        self.assertIn("Don't pull it out", busy)
        self.assertNotIn("safe to pull", self.say(["/m/a", "/m/b"], [{"ok": True}, {"ok": False, "why": "failed"}]))

    def test_it_comes_before_the_brain(self):
        c = Chat.__new__(Chat)
        self.assertFalse(c._handle_eject_usb("play a movie"))
        self.assertIn("if self._handle_eject_usb(command):", (ROOT / "systems" / "chat.py").read_text())


class BackToFaceTests(unittest.TestCase):
    def desk(self):
        win = lambda title: types.SimpleNamespace(title=title)
        return types.SimpleNamespace(
            windows=lambda: {"1": win("86EA-6471 - Thunar"), "2": win("bemo - Thunar"), "3": win("BMO")},
            window_ids=lambda pid: ["3"] if pid == os.getpid() else [],
            closed=[], active=[],
            close=lambda w: self.closed.append(w), activate=lambda w: self.active.append(w))

    def setUp(self):
        self.closed, self.active = [], []

    def test_the_sticks_window_closes_and_his_face_comes_up(self):
        with mock.patch("systems.adapters.desktop", self.desk):
            Chat.__new__(Chat)._back_to_face(closing=["/media/me/86EA-6471"])
        self.assertEqual(self.closed, ["1"])   # not the owner's home folder window
        self.assertEqual(self.active, ["3"])

    def test_nothing_closes_when_nothing_was_ejected(self):
        with mock.patch("systems.adapters.desktop", self.desk):
            Chat.__new__(Chat)._back_to_face()
        self.assertEqual(self.closed, [])
        self.assertEqual(self.active, ["3"])

    def test_trouble_never_stops_the_job(self):
        with mock.patch("systems.adapters.desktop", side_effect=RuntimeError("no X")):
            Chat.__new__(Chat)._back_to_face(closing=["/media/me/x"])

    def test_the_eject_and_the_sort_use_it(self):
        src = (ROOT / "systems" / "chat.py").read_text()
        self.assertIn("self._back_to_face(closing=", src)
        start = src.index("def _handle_sort_usb")
        self.assertIn("self._back_to_face()", src[start:start + 500])


class NoPopupTests(unittest.TestCase):
    def test_it_turns_off_only_the_window(self):
        from systems.desktop_settings import no_usb_popup
        calls = []

        def run(command, **kw):
            calls.append(command)
            return done()
        self.assertTrue(no_usb_popup(run=run, which=lambda name: "/usr/bin/" + name))
        self.assertEqual(calls[0][:5], ["xfconf-query", "-c", "thunar-volman", "-p", "/autobrowse/enabled"])
        self.assertEqual(calls[0][-2:], ["-s", "false"])
        self.assertEqual(len(calls), 1)                  # mounting stays as it is
        self.assertNotIn("automount", " ".join(calls[0]))

    def test_other_desktops_are_left_alone(self):
        from systems.desktop_settings import no_usb_popup
        self.assertFalse(no_usb_popup(run=lambda *a, **k: self.fail("ran"), which=lambda name: None))

    def test_a_failure_says_so(self):
        from systems.desktop_settings import no_usb_popup
        self.assertFalse(no_usb_popup(run=lambda *a, **k: done(1), which=lambda name: "/x"))

    def test_bmo_does_it_at_start(self):
        self.assertIn("no_usb_popup()", (ROOT / "systems" / "chat.py").read_text())


class ClaimTests(unittest.TestCase):
    def test_the_brain_cant_claim_it(self):
        for text in ("USB ejected.", "Your USB stick is ejected now.", "The drive is unmounted.", "I ejected the USB.",
                     "The USB is safe to remove."):
            self.assertEqual(unbacked_claim(text, set()), "undone", text)

    def test_talk_about_it_is_fine(self):
        self.assertIsNone(unbacked_claim("A USB stick stores files.", set()))


class CommandListTests(unittest.TestCase):
    def test_listed(self):
        self.assertIn("eject the USB", (ROOT / "VOICE_COMMANDS.md").read_text())
        self.assertIn("eject the USB", (ROOT / "voice_commands.py").read_text())


if __name__ == "__main__":
    unittest.main()
