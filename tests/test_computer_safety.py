"""What Main's and Coding's computer_control may do without asking (safety review 2026-10-19)."""
import os
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(os.environ.get("BEMO_TEST_CODE_ROOT", Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(ROOT))
from systems.computer import ComputerController

HOME = Path.home()


class SafetyReviewTests(unittest.TestCase):
    def setUp(self):
        self.app = Path(tempfile.mkdtemp()) / "bemo-master"
        self.cc = ComputerController(screenshot_dir=tempfile.mkdtemp(), guarded_paths=(self.app,),
                                     python_executable="/opt/bemo/.venv/bin/python")

    def run_asks(self, *argv):
        return self.cc.assess("run", {"argv": list(argv)})

    def assertAsked(self, verdict, critical=None):
        self.assertTrue(verdict.get("confirmation_required") or verdict.get("blocked"), verdict)
        if critical is not None:
            self.assertEqual(bool(verdict.get("critical")), critical, verdict)

    def assertFree(self, verdict):
        self.assertFalse(verdict.get("confirmation_required") or verdict.get("blocked"), verdict)

    def test_wrappers_are_judged_by_the_command_they_run(self):
        self.assertAsked(self.run_asks("env", "FOO=1", "bash", "-c", "rm -rf ~"))
        self.assertAsked(self.run_asks("nohup", "rm", "x"))
        self.assertAsked(self.run_asks("timeout", "10", "python3", "evil.py"))
        self.assertAsked(self.run_asks("nice", "-n", "5", "sh", "-c", "x"))
        self.assertAsked(self.run_asks("env", "-S", "bash -c x"))
        self.assertFree(self.run_asks("timeout", "5s", "ls", "-la"))
        self.assertFree(self.run_asks("env", "LANG=C", "date"))

    def test_commands_that_used_to_run_without_a_question(self):
        for argv in (["xargs", "rm"], ["find", ".", "-delete"], ["find", ".", "-exec", "rm", "{}", ";"],
                     ["awk", "BEGIN{system(\"x\")}"], ["python3.12", "x.py"], ["curl", "-o", "x", "http://e"],
                     ["wget", "http://e"], ["ssh", "host"], ["rsync", "a", "b"], ["pip", "install", "x"],
                     ["npm", "install"], ["git", "push"], ["git", "-c", "core.pager=sh", "log"],
                     ["tar", "-xf", "a.tar"], ["unzip", "a.zip"], ["xdotool", "type", "rm -rf ~"],
                     ["secret-tool", "lookup", "x", "y"], ["systemctl", "--user", "stop", "bemo.service"],
                     ["sed", "--in-place", "s/a/b/", "f"], ["xrandr", "--output", "HDMI-1", "--off"],
                     ["wmctrl", "-c", "Terminal"], ["make"], ["gio", "trash", "x"]):
            self.assertAsked(self.run_asks(*argv))
        self.assertAsked(self.run_asks("trash-empty"), critical=True)

    def test_everyday_read_only_commands_stay_free(self):
        for argv in (["ls", "-la"], ["git", "status"], ["git", "log", "-5"], ["pip", "list"], ["pip3", "show", "pygame"],
                     ["tar", "-tf", "a.tar"], ["unzip", "-l", "a.zip"], ["systemctl", "--user", "status", "bemo"],
                     ["xrandr", "--query"], ["xrandr"], ["wmctrl", "-l"], ["gio", "info", "x"], ["df", "-h"]):
            self.assertFree(self.run_asks(*argv))

    def test_checking_a_module_is_free_but_code_is_not(self):
        # Headless bench 2026-10-19: Coding's first step for a snake game was this check.
        self.assertFree(self.run_asks("python3", "-c", "import pygame; print('Pygame is installed')"))
        self.assertFree(self.run_asks("python3", "-c", "import pygame;print(pygame.__version__)"))
        self.assertAsked(self.run_asks("python3", "-c", "import os; os.system('rm -rf ~')"))
        self.assertAsked(self.run_asks("python3", "-c", "__import__('os').system('x')"))

    def test_module_check_cannot_run_a_planted_file(self):
        # Review 2026-10-19: write evil.py, then "python3 -c 'import evil'" (free) would run it.
        self.assertTrue(self.cc.assess("run", {"argv": ["python3", "-c", "import evil"], "cwd": "/tmp"})
                        .get("confirmation_required"))
        self.assertTrue(self.cc._module_check("python", ["/x/python", "-I", "-c", "import pygame"]))

    def test_tar_extract_with_a_file_named_test_is_asked(self):
        self.assertAsked(self.run_asks("tar", "-xf", "a.tar", "test"))
        self.assertFree(self.run_asks("tar", "-tf", "a.tar"))

    def test_terminal_only_programs_are_refused_with_a_reason(self):
        # Live 2026-10-19: "run nano love_note.txt" hung while Main tried to type into the text editor.
        for program in ("nano", "vim", "less", "top"):
            verdict = self.run_asks(program, "x.txt")
            self.assertTrue(verdict.get("blocked"), program)
            self.assertIn("terminal", verdict.get("summary", ""))

    def test_python_means_bemos_own_python(self):
        self.assertEqual(self.cc._resolve_program(["python3", "game.py"]), ["/opt/bemo/.venv/bin/python", "game.py"])

    def test_startup_places_keys_and_bemo_itself_are_critical(self):
        for path in (HOME / ".config/autostart/x.desktop", HOME / ".config/systemd/user/x.service",
                     HOME / ".local/bin/ls", HOME / ".bash_aliases", HOME / ".ssh/authorized_keys",
                     self.app / "config.py"):
            self.assertAsked(self.cc.assess("write_text", {"path": str(path), "content": "x"}), critical=True)
            self.assertAsked(self.cc.assess("copy", {"path": "/etc/hostname", "destination": str(path)}), critical=True)
            self.assertAsked(self.cc.assess("delete", {"path": str(path)}), critical=True)
        self.assertFree(self.cc.assess("write_text", {"path": str(HOME / "BMO-code/never-existing-x.py"), "content": "x"}))

    def test_private_keys_and_passwords(self):
        self.assertTrue(self.cc.assess("read_text", {"path": str(HOME / ".ssh/id_ed25519")}).get("blocked"))
        self.assertTrue(self.cc.assess("read_text", {"path": str(HOME / ".git-credentials")}).get("blocked"))
        self.assertAsked(self.run_asks("cat", str(HOME / ".ssh/id_ed25519")), critical=True)
        self.assertAsked(self.run_asks("cat", "~/.netrc"), critical=True)
        self.assertAsked(self.cc.assess("copy", {"path": str(HOME / ".ssh/id_ed25519"), "destination": "/tmp/k"}),
                         critical=True)
        self.assertFree(self.cc.assess("read_text", {"path": str(HOME / "notes.txt")}))
        self.assertFree(self.cc.assess("list", {"path": str(HOME / ".ssh")}))


if __name__ == "__main__":
    unittest.main()
