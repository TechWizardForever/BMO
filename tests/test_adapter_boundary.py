"""Portability plan P2: only the OS adapter modules call Linux desktop tools directly."""
import os
from pathlib import Path
import re
import sys
import unittest

ROOT = Path(os.environ.get("BEMO_TEST_CODE_ROOT", Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(ROOT))
from systems.adapters import LINUX_IMPLEMENTATION_MODULES

TOOLS = ("xdotool", "wmctrl", "xrandr", "xprintidle", "pactl", "xfconf-query", "gsettings", "gtk-launch",
         "xdg-open", "gio", "pkexec", "scrot", "gnome-screenshot", "grim", "nmcli", "flatpak", "lightdm")
# A call: the tool as the first item of an argv list, or looked up with which().
CALL = re.compile(r'(?:\[\s*["\'](?:%s)["\']|which\(\s*["\'](?:%s)["\']\s*\))' % ("|".join(TOOLS), "|".join(TOOLS)))


class BoundaryTests(unittest.TestCase):
    def test_no_os_tool_calls_outside_the_adapters(self):
        offenders = []
        for path in [ROOT / "app.py", ROOT / "supervisor.py", *sorted((ROOT / "systems").rglob("*.py"))]:
            rel = path.relative_to(ROOT).as_posix()
            if any(rel.startswith(allowed) for allowed in LINUX_IMPLEMENTATION_MODULES):
                continue
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if CALL.search(line) and not line.lstrip().startswith("#"):
                    offenders.append(f"{rel}:{number}: {line.strip()[:90]}")
        self.assertEqual(offenders, [], "call these through systems.adapters instead")

    def test_the_check_catches_a_raw_call(self):
        self.assertTrue(CALL.search('subprocess.run(["wmctrl", "-ia", target])'))
        self.assertTrue(CALL.search('exe = shutil.which("xdotool")'))
        self.assertFalse(CALL.search('"service", "crontab", "at", "nmcli", "hostnamectl"'))  # a policy list


if __name__ == "__main__":
    unittest.main()
