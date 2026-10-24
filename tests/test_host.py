"""Host detection and the capability report (portability plan P1), on fake hosts."""
import ast
import os
from pathlib import Path
import re
import sys
import unittest

ROOT = Path(os.environ.get("BEMO_TEST_CODE_ROOT", Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(ROOT))
from systems.host import capabilities, detect_host, printed_report, spoken_report

MINT = 'NAME="Linux Mint"\nPRETTY_NAME="Linux Mint 22.3"\nID=linuxmint\nID_LIKE="ubuntu debian"\n'
UBUNTU = 'PRETTY_NAME="Ubuntu 24.04.1 LTS"\nID=ubuntu\nID_LIKE=debian\n'
FEDORA = 'PRETTY_NAME="Fedora Linux 41 (Workstation Edition)"\nID=fedora\n'
ALL_TOOLS = {"wmctrl", "xdotool", "xrandr", "pactl", "xfconf-query", "gsettings", "nmcli", "gtk-launch", "pkexec",
             "systemctl", "ollama", "lightdm", "apt-get", "openscad"}


def host(release=MINT, env=None, tools=ALL_TOOLS, pyatspi=True, ram=28.2, systemd=True):
    env = {"XDG_SESSION_TYPE": "x11", "XDG_CURRENT_DESKTOP": "XFCE", "DISPLAY": ":0"} if env is None else env
    return detect_host(env=env, os_release_text=release, which=lambda t: f"/usr/bin/{t}" if t in tools else None,
                       pyatspi=pyatspi, systemd_dir="/" if systemd else "/nonexistent-systemd", ram_gib=ram,
                       cpu="Test CPU")


def by_key(h, vision_files=True):
    return {c.key: c for c in capabilities(h, vision_files=vision_files)}


class DetectionTests(unittest.TestCase):
    def test_this_pc(self):
        h = host(tools=ALL_TOOLS)  # xprintidle missing, as on the test PC 2026-10-19
        self.assertEqual(h.describe(), "Linux Mint 22.3 with Xfce on X11")
        self.assertEqual((h.package_manager, h.init, h.distro_family), ("apt", "systemd", "linuxmint ubuntu debian"))
        caps = by_key(h)
        self.assertTrue(all(c.ok for c in caps.values()))
        self.assertIn("install xprintidle", caps["idle"].partial)
        said = spoken_report(h, list(caps.values()))
        self.assertTrue(said.startswith("This is Linux Mint 22.3 with Xfce on X11. Here I can run my brain models, "
                                        "open and use apps, click by looking, change the screen, sound and text size, "
                                        "and start by myself."), said)

    def test_3d_models_need_openscad(self):
        # 2026-10-20: the Engineering brain makes STLs with OpenSCAD.
        self.assertTrue(by_key(host())["cad"].ok)
        self.assertEqual(by_key(host(tools=ALL_TOOLS - {"openscad"}))["cad"].why_not, "install openscad")

    def test_session_from_display_variables(self):
        self.assertEqual(host(env={"WAYLAND_DISPLAY": "wayland-0"}).session, "wayland")
        self.assertEqual(host(env={}).session, "")


class CapabilityTests(unittest.TestCase):
    def test_gnome_on_wayland(self):
        h = host(UBUNTU, {"XDG_SESSION_TYPE": "wayland", "XDG_CURRENT_DESKTOP": "ubuntu:GNOME"})
        caps = by_key(h)
        self.assertTrue(caps["apps"].ok)  # launching works on Wayland (P4, 2026-10-19)
        self.assertEqual(caps["apps"].partial, "but I can't see app windows here, so I can't check, switch or close them")
        self.assertFalse(caps["click_by_sight"].ok)
        self.assertIn("display settings own the screen", caps["screen"].why_not)
        self.assertTrue(caps["readability"].ok)
        self.assertEqual(caps["readability"].partial, "but not hide the panel")
        self.assertEqual(caps["app_controls"].partial, "only in the window list I can see")
        self.assertIn("I can't click things by looking at the screen, because it needs an X11 desktop session",
                      spoken_report(h, list(caps.values())))

    def test_kde_and_headless(self):
        kde = by_key(host(env={"XDG_SESSION_TYPE": "x11", "XDG_CURRENT_DESKTOP": "KDE", "DISPLAY": ":0"},
                          tools=ALL_TOOLS - {"xfconf-query", "gsettings"}))
        self.assertEqual(kde["readability"].why_not, "I don't know how to change that on KDE Plasma yet")
        headless = by_key(host(env={}, tools={"pactl", "ollama", "apt-get"}, pyatspi=False))
        self.assertFalse(any(headless[k].ok for k in ("apps", "app_controls", "click_by_sight", "screen")))
        self.assertTrue(headless["sound"].ok)

    def test_too_little_memory_is_said_plainly(self):
        caps = by_key(host(ram=15.6))
        self.assertEqual(caps["models"].why_not, "this computer has 16 GB of memory and I need 28")
        self.assertFalse(caps["click_by_sight"].ok)

    def test_install_advice_matches_the_distro(self):
        apt = by_key(host(tools=ALL_TOOLS - {"pactl"}))
        self.assertEqual(apt["sound"].why_not, "install pulseaudio-utils")
        fedora = by_key(host(FEDORA, tools=(ALL_TOOLS - {"apt-get", "pactl"}) | {"dnf"}))
        self.assertEqual(fedora["sound"].why_not, "it needs pactl")

    def test_vision_files_missing(self):
        self.assertEqual(by_key(host(), vision_files=False)["click_by_sight"].why_not,
                         "the vision helper's files aren't installed")

    def test_printed_report(self):
        text = printed_report(host(ram=15.6), capabilities(host(ram=15.6)))
        self.assertIn("NO   run my brain models: this computer has 16 GB of memory and I need 28", text)
        self.assertIn("Tools not found: xprintidle", text)


class QuestionTests(unittest.TestCase):
    def test_which_questions_get_the_report(self):
        src = (ROOT / "systems" / "chat.py").read_text()
        chat = next(n for n in ast.parse(src).body if isinstance(n, ast.ClassDef) and n.name == "Chat")
        node = next(n for n in chat.body if isinstance(n, ast.Assign) and n.targets[0].id == "_CAPABILITY_QUESTION")
        ns = {"re": re}
        exec(compile(ast.fix_missing_locations(ast.Module(
            body=[ast.Assign(targets=[ast.Name("q", ast.Store())], value=node.value)], type_ignores=[])), "q", "exec"), ns)
        q = ns["q"]
        for asked in ("what can you do on this computer", "what can you control here", "what computer is this",
                      "list your capabilities", "capability report"):
            self.assertTrue(q.search(asked), asked)
        for other in ("what can you do", "what can you do for me today", "what computer should i buy",
                      "tell me a joke"):
            self.assertFalse(q.search(other), other)


if __name__ == "__main__":
    unittest.main()
