"""Gamepads for GameCube games (2026-10-21): pads plugged in get players 1-4 with Dolphin's "SDL Gamepad"
profile; none means the keyboard on player 1; a layout the owner made in Dolphin is kept."""
import glob
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(os.environ.get("BEMO_TEST_CODE_ROOT", Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(ROOT))
from systems import gamepads as gp
from systems import games as g

# Dolphin 2609's own first GCPadNew.ini on this PC (2026-10-20): keyboard on player 1.
DOLPHIN_DEFAULT = ("[GCPad1]\nDevice = XInput2/0/Virtual core pointer\n" + gp.KEYBOARD_PROFILE
                   + "Triforce/Test = `1`\nTriforce/Service = `2`\nTriforce/Coin = `3`\n"
                   + "".join(f"[GCPad{i}]\nDevice = XInput2/0/Virtual core pointer\n" for i in (2, 3, 4)))


class PadTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.config, self.state = self.dir / "Config", self.dir / "gamepads.json"

    def tearDown(self):
        shutil.rmtree(self.dir)

    def ini(self):
        return (self.config / "GCPadNew.ini").read_text()

    def test_no_pad_means_keyboard_on_player_1(self):
        ports = gp.prepare(self.config, self.state, [])
        self.assertTrue(self.ini().startswith("[GCPad1]\nDevice = XInput2/0/Virtual core pointer\nButtons/A = `X`"))
        self.assertEqual(ports[1::2], ["Dolphin.Core.SIDevice0=6", "Dolphin.Core.SIDevice1=0",
                                       "Dolphin.Core.SIDevice2=0", "Dolphin.Core.SIDevice3=0"])

    def test_each_pad_gets_a_player(self):
        ports = gp.prepare(self.config, self.state, ["Xbox Series X Controller", "PS5 Controller"])
        text = self.ini()
        self.assertIn("[GCPad1]\nDevice = SDL/0/Xbox Series X Controller\nButtons/A = `Button A`", text)
        self.assertIn("[GCPad2]\nDevice = SDL/0/PS5 Controller\n", text)
        self.assertNotIn("[GCPad3]", text)
        self.assertEqual(ports[1::2][:3], ["Dolphin.Core.SIDevice0=6", "Dolphin.Core.SIDevice1=6",
                                           "Dolphin.Core.SIDevice2=0"])

    def test_two_of_the_same_pad_are_told_apart(self):
        self.assertEqual(gp.dolphin_devices(["8BitDo Pro 2", "8BitDo Pro 2", "Xbox 360 Controller"]),
                         ["SDL/0/8BitDo Pro 2", "SDL/1/8BitDo Pro 2", "SDL/0/Xbox 360 Controller"])

    def test_dolphins_first_file_is_replaced_and_bmos_own_file_follows_the_pads(self):
        self.config.mkdir()
        (self.config / "GCPadNew.ini").write_text(DOLPHIN_DEFAULT)
        gp.prepare(self.config, self.state, ["PS4 Controller"])
        self.assertIn("SDL/0/PS4 Controller", self.ini())
        gp.prepare(self.config, self.state, [])  # pad unplugged: back to the keyboard
        self.assertIn("Device = XInput2/0/Virtual core pointer", self.ini())
        self.assertEqual(json.loads(self.state.read_text())["pads"], [])

    def test_the_owners_own_layout_is_kept(self):
        self.config.mkdir()
        own = DOLPHIN_DEFAULT.replace("Buttons/A = `X`", "Buttons/A = `Space`")
        (self.config / "GCPadNew.ini").write_text(own)
        self.assertEqual(gp.prepare(self.config, self.state, ["PS4 Controller"]), [])
        self.assertEqual(self.ini(), own)

    def test_listing_pads(self):
        reply = subprocess.CompletedProcess([], 0, 'pygame noise\n["Xbox 360 Controller"]\n', "")
        self.assertEqual(gp.connected(run=lambda *a, **k: reply), ["Xbox 360 Controller"])
        broken = subprocess.CompletedProcess([], 1, "", "Traceback")
        self.assertEqual(gp.connected(run=lambda *a, **k: broken), [])

    def test_the_listing_script_runs_here(self):
        # The real SDL check, with no pad needed: it must run cleanly and answer with a list.
        done = subprocess.run([sys.executable, "-c", gp._LIST_PADS], capture_output=True, text=True, timeout=30)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIsInstance(json.loads(done.stdout.strip().splitlines()[-1]), list)

    def test_bmos_profile_is_dolphins_sdl_gamepad_profile(self):
        shipped = glob.glob(os.path.expanduser(
            "~/.local/share/flatpak/app/org.DolphinEmu.dolphin-emu/*/*/*/files/share/dolphin-emu/"
            "Sys/Profiles/GCPad/SDL Gamepad.ini"))
        if not shipped:
            self.skipTest("Dolphin's Flatpak isn't installed here")
        lines = Path(shipped[0]).read_text().splitlines()
        self.assertEqual([x for x in lines if x and not x.startswith("[")], gp.PAD_PROFILE.splitlines())


class LaunchTests(unittest.TestCase):
    def test_every_launch_sets_up_the_pads_and_logs_dolphins_devices(self):
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch("systems.gamepads.connected", return_value=["Xbox 360 Controller"]):
            lib = g.GameLibrary(tmp, ROOT)
            lib.ensure_layout()
            (lib.gamecube_dir / "Custom Robo (USA).rvz").write_bytes(b"RVZ\x01")
            lib.dolphin_prefix = lambda: ["dolphin-emu"]
            command = lib.command_for(lib.find("custom robo")[0])
            self.assertIn("Dolphin.Core.SIDevice0=6", command)
            self.assertIn("Logger.Logs.CONTROLLERINTERFACE=True", command)
            self.assertLess(command.index("Logger.Options.WriteToFile=True"), command.index("-e"))
            self.assertIn("SDL/0/Xbox 360 Controller",
                          (lib.dolphin_user_dir / "Config" / "GCPadNew.ini").read_text())
            self.assertIn("Graphics.Settings.AspectRatio=0", command)  # no screen settings here: the game's own shape

    def test_games_stretch_on_the_squeezed_crt(self):
        from systems.display import DisplayState
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch("systems.gamepads.connected", return_value=[]), \
                mock.patch("systems.display.saved_for_connected",
                           return_value=DisplayState(1280, 720, 26, 52, 14, 28, fit="desktop")):
            lib = g.GameLibrary(tmp, ROOT)
            lib.ensure_layout()
            (lib.gamecube_dir / "Custom Robo (USA).rvz").write_bytes(b"RVZ\x01")
            lib.dolphin_prefix = lambda: ["dolphin-emu"]
            self.assertIn("Graphics.Settings.AspectRatio=3", lib.command_for(lib.find("custom robo")[0]))


if __name__ == "__main__":
    unittest.main()
