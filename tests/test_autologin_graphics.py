"""Autologin survives a late graphics driver: LightDM waits for it (found on a Ryzen 5700U, 2026-11-24).
Runs the real wait script against a fake /sys; nothing on this PC is changed."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest

ROOT = Path(os.environ.get("BEMO_TEST_CODE_ROOT", Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(ROOT))
import mint_appliance as ma


class GraphicsWaitTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.script, self.dropin = self.dir / "libexec" / "wait", self.dir / "lightdm.service.d" / "wait.conf"
        ma.install_graphics_wait(self.script, self.dropin)
        self.sys = self.dir / "sys"
        self.cmdline = self.dir / "cmdline"
        self.cmdline.write_text("ro quiet splash\n")
        self.modprobe = self.dir / "modprobe"
        self.set_driver_installed(True)

    def tearDown(self):
        shutil.rmtree(self.dir)

    def set_driver_installed(self, installed):
        self.modprobe.write_text("#!/bin/sh\n" + ("echo amdgpu\n" if installed else ""))
        self.modprobe.chmod(0o755)

    def device(self, name, cls, bound):
        dev = self.sys / "bus" / "pci" / "devices" / name
        dev.mkdir(parents=True)
        (dev / "class").write_text(cls + "\n")
        (dev / "modalias").write_text("pci:v00001002d00001636\n")
        if bound:
            self.bind(dev)
        return dev

    @staticmethod
    def bind(dev, screen=True):
        (dev / "driver").mkdir()
        if screen:
            (dev / "drm" / "card1").mkdir(parents=True)

    def run_wait(self, tries=10):
        env = dict(os.environ, BEMO_SYSFS=str(self.sys), BEMO_CMDLINE=str(self.cmdline),
                   BEMO_MODPROBE=str(self.modprobe), BEMO_GRAPHICS_WAIT_TRIES=str(tries), BEMO_UDEVADM="true")
        start = time.monotonic()
        done = subprocess.run([str(self.script)], env=env, timeout=30)
        return done.returncode, time.monotonic() - start

    def test_driver_already_there_means_no_wait(self):
        self.device("0000:05:00.0", "0x030000", bound=True)
        self.device("0000:00:14.0", "0x0c0330", bound=False)  # not a display device
        code, took = self.run_wait()
        self.assertEqual(code, 0)
        self.assertLess(took, 0.5)

    def test_waits_until_the_driver_arrives(self):
        dev = self.device("0000:05:00.0", "0x030000", bound=False)
        threading.Timer(0.6, lambda: self.bind(dev)).start()
        code, took = self.run_wait(tries=50)
        self.assertEqual(code, 0)
        self.assertGreater(took, 0.5)
        self.assertLess(took, 3)

    def test_an_attached_driver_is_not_enough_until_the_screen_is_up(self):
        # 2026-11-24: LightDM started when amdgpu attached, 0.07 s before its screen, and Xorg aborted.
        dev = self.device("0000:05:00.0", "0x030000", bound=False)
        self.bind(dev, screen=False)
        threading.Timer(0.6, lambda: (dev / "drm" / "card1").mkdir(parents=True)).start()
        code, took = self.run_wait(tries=50)
        self.assertEqual(code, 0)
        self.assertGreater(took, 0.5)

    def test_gives_up_and_lets_the_pc_boot(self):
        self.device("0000:05:00.0", "0x030000", bound=False)
        code, took = self.run_wait(tries=3)
        self.assertEqual(code, 0)
        self.assertLess(took, 2)

    def test_no_wait_when_nothing_can_arrive(self):
        self.device("0000:05:00.0", "0x030000", bound=False)
        self.set_driver_installed(False)
        self.assertLess(self.run_wait(tries=50)[1], 0.5)
        self.set_driver_installed(True)
        self.cmdline.write_text("ro nomodeset quiet\n")
        self.assertLess(self.run_wait(tries=50)[1], 0.5)

    def test_files_and_wiring(self):
        self.assertEqual(self.script.stat().st_mode & 0o777, 0o755)
        self.assertEqual(self.dropin.stat().st_mode & 0o777, 0o644)
        self.assertIn(f"ExecStartPre=-{ma.GRAPHICS_WAIT_SCRIPT}", ma.GRAPHICS_WAIT_DROPIN_TEXT)
        self.assertEqual(str(ma.GRAPHICS_WAIT_SCRIPT), "/usr/local/libexec/bemo-wait-for-graphics")  # root-owned
        self.assertEqual(ma.GRAPHICS_WAIT_DROPIN.name, "wait-for-graphics.conf")  # replaces the hand-made one
        source = (ROOT / "mint_appliance.py").read_text()
        # A normal user (the tests) must never trigger systemd's password window.
        self.assertIn('if os.geteuid() == 0 and shutil.which("systemctl"):', source)
        self.assertIn("udevadm} settle --timeout=10", ma.GRAPHICS_WAIT_SCRIPT_TEXT)
        self.assertLess(source.index("os.replace(tmp, LIGHTDM_BEMO_CONFIG)\n    install_graphics_wait()"),
                        source.index("def status()"))


if __name__ == "__main__":
    unittest.main()
