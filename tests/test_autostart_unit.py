"""The systemd unit that starts BMO at login (untouched-systems check, 2026-10-19).

Live finding: the unit had WorkingDirectory="/home/bemo/Downloads/bemo-master" (quoted), which systemd
rejects as "not absolute", so the whole unit was refused and BMO never started at boot.
"""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(os.environ.get("BEMO_TEST_CODE_ROOT", Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(ROOT))
from systems.autostart import AutostartManager


class UnitTests(unittest.TestCase):
    def test_working_directory_is_a_bare_absolute_path(self):
        unit = AutostartManager(Path("/home/bemo/Downloads/bemo-master")).render_unit()
        self.assertIn("\nWorkingDirectory=/home/bemo/Downloads/bemo-master\n", unit)
        self.assertIn('ExecStart=/bin/bash "/home/bemo/Downloads/bemo-master/start.sh"', unit)

    @unittest.skipUnless(shutil.which("systemd-analyze"), "systemd-analyze not installed")
    def test_systemd_accepts_the_unit(self):
        folder = Path(tempfile.mkdtemp()) / "my bemo"  # a space, as some users' folders have
        folder.mkdir()
        (folder / "start.sh").write_text("#!/bin/bash\nexit 0\n")
        unit_path = folder.parent / "bemo-test.service"
        unit_path.write_text(AutostartManager(folder).render_unit())
        done = subprocess.run(["systemd-analyze", "--user", "verify", str(unit_path)],
                              capture_output=True, text=True, timeout=60)
        problems = [line for line in (done.stderr + done.stdout).splitlines() if "bemo-test.service" in line]
        self.assertEqual(problems, [], done.stderr)


if __name__ == "__main__":
    unittest.main()
