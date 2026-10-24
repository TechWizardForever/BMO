"""Exact package versions and a movable install (installer plan, milestone I1)."""
import ast
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(os.environ.get("BEMO_TEST_CODE_ROOT", Path(__file__).resolve().parents[1]))
LOCK = ROOT / "installer" / "requirements.lock"
FROM_APT = {"pyatspi"}  # python3-pyatspi, installed by apt


def locked() -> dict:
    pins = {}
    for line in LOCK.read_text().splitlines():
        if line and not line.startswith("#"):
            name, _, version = line.partition("==")
            pins[name.lower().replace("_", "-")] = version
    return pins


def runtime_imports() -> set:
    """Third-party modules imported by what BMO runs (not tests, tools or benchmarks)."""
    local = {p.stem for p in ROOT.glob("*.py")} | {p.name for p in ROOT.iterdir() if p.is_dir()}
    found = set()
    files = list(ROOT.glob("*.py")) + list((ROOT / "systems").rglob("*.py"))
    for path in files:
        for node in ast.walk(ast.parse(path.read_text())):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else \
                [node.module] if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module else []
            for name in names:
                top = name.split(".")[0]
                if top not in sys.stdlib_module_names and top not in local:
                    found.add(top)
    return found


class LockTests(unittest.TestCase):
    def test_lock_matches_what_was_tested(self):
        done = subprocess.run([sys.executable, str(ROOT / "tools" / "make_lock.py"), "--check"],
                              capture_output=True, text=True, timeout=120)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr + " (run tools/make_lock.py)")

    def test_credits_list_every_locked_package(self):
        done = subprocess.run([sys.executable, str(ROOT / "tools" / "make_notices.py"), "--check"],
                              capture_output=True, text=True, timeout=120)
        self.assertEqual(done.returncode, 0, done.stdout + " (run tools/make_notices.py)")
        self.assertTrue((ROOT / "LICENSE").read_text().lstrip().startswith("GNU GENERAL PUBLIC LICENSE"))

    def test_every_pin_is_exact_and_nothing_dropped_comes_back(self):
        pins = locked()
        self.assertGreater(len(pins), 50)
        self.assertTrue(all(pins.values()))
        for gone in ("torch", "torchaudio", "chatterbox-tts", "transformers", "gradio"):
            self.assertNotIn(gone, pins)
        self.assertEqual(pins["openwakeword"], "0.6.0")  # the Hey BMO detector was missing from the installer

    def test_every_module_bmo_imports_is_in_the_lock(self):
        import importlib.metadata as md
        providers = md.packages_distributions()
        pins = locked()
        for module in sorted(runtime_imports() - FROM_APT):
            names = [d.lower().replace("_", "-") for d in providers.get(module, [])]
            self.assertTrue(any(n in pins for n in names), f"{module} ({names}) is imported but not locked")

    def test_installer_uses_the_lock_without_resolving(self):
        text = (ROOT / "install.sh").read_text()
        for line in ('pip install -r "$SCRIPT_DIR/installer/build-tools.lock"',
                     'pip install --no-deps -r "$SCRIPT_DIR/installer/requirements.lock"',
                     'python "$SCRIPT_DIR/installer/check_packages.py"'):
            self.assertTrue(line in text, f"install.sh lacks: {line}")
        self.assertLess(text.index("pip install -r \"$SCRIPT_DIR/installer/build-tools.lock\""),
                        text.index("pip install --no-deps -r \"$SCRIPT_DIR/installer/requirements.lock\""))
        self.assertFalse('pip install -c' in text, "install.sh still resolves packages itself")

    def test_package_check_fails_only_on_bmos_own_packages(self):
        sys.path.insert(0, str(ROOT / "installer"))
        from check_packages import judge, locked_names
        names = locked_names(LOCK.read_text())
        output = ("chatterbox-tts 0.1.7 requires gradio, which is not installed.\n"
                  "openwakeword 0.6.0 requires tflite-runtime, which is not installed.\n")
        self.assertEqual(judge(output, names), ([], ["chatterbox-tts 0.1.7 requires gradio, which is not installed."]))
        broken = "cadquery 2.4.0 has requirement nlopt>=2.9, but you have nlopt 2.7.1.\n"
        self.assertEqual(judge(broken, names)[0], [broken.strip()])
        self.assertEqual(judge("No broken requirements found.\n", names), ([], []))


class AnyFolderTests(unittest.TestCase):
    """BMO runs from wherever it is installed, including a folder with a space in its name."""

    def test_a_copy_in_another_folder_finds_itself(self):
        listed = subprocess.run(["git", "ls-files", "-co", "--exclude-standard"], cwd=ROOT, capture_output=True,
                                text=True, check=True).stdout.splitlines()
        with tempfile.TemporaryDirectory() as temp:
            copy = Path(temp) / "BMO copy" / "app"
            for name in listed:
                source = ROOT / name
                if source.is_file() and not name.startswith(("docs/live-tests/", "assets/")):
                    (copy / name).parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, copy / name)
            code = ("import config\n"
                    "from systems.autostart import AutostartManager\n"
                    "print(config.APP_ROOT)\n"
                    "print(AutostartManager(config.APP_ROOT).render_unit())\n")
            env = dict(os.environ, BEMO_DATA_DIR=str(Path(temp) / "data"))
            done = subprocess.run([sys.executable, "-c", code], cwd=copy, env=env, capture_output=True, text=True,
                                  timeout=120)
            self.assertEqual(done.returncode, 0, done.stderr[-2000:])
            lines = done.stdout.splitlines()
            self.assertEqual(lines[0], str(copy))
            self.assertIn(f"WorkingDirectory={copy}", lines)
            self.assertIn(f'ExecStart=/bin/bash "{copy}/start.sh"', lines)
            done = subprocess.run([sys.executable, "-I", str(copy / "installer" / "precheck.py"), "--offline",
                                   "--json"], env=env, capture_output=True, text=True, timeout=60)
            self.assertIn('"name": "Memory"', done.stdout, done.stderr[-2000:])


if __name__ == "__main__":
    unittest.main()
