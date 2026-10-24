"""The installer's pre-check: one clear OK / NOT OK before anything is changed (installer plan I1)."""
import json
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest

ROOT = Path(os.environ.get("BEMO_TEST_CODE_ROOT", Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "installer"))
import precheck as pc
from precheck import FAIL, NOTE, OK

MINT = {"version_id": "22.3", "problems": []}


def meminfo(gib):
    return f"MemTotal:       {int(gib * 1024 * 1024)} kB\nMemFree:  1 kB\n"


class FakeDisk:
    """Two folders, each on a device with some GB free."""
    def __init__(self, devices):
        self.devices = devices  # path -> (device, free_gb)

    def stat(self, path):
        return types.SimpleNamespace(st_dev=self.devices[str(path)][0])

    def statvfs(self, path):
        return types.SimpleNamespace(f_bavail=int(self.devices[str(path)][1] * 1e9 / 4096), f_frsize=4096)


class PrecheckTests(unittest.TestCase):
    def test_same_minimum_memory_as_bmo_itself(self):
        from systems.host import MIN_RAM_GIB
        self.assertEqual(pc.MIN_RAM_GIB, MIN_RAM_GIB)

    def test_memory(self):
        self.assertEqual(pc.check_memory(meminfo(28.2), False).status, OK)
        self.assertEqual(pc.check_memory(meminfo(15.5), False).status, FAIL)
        self.assertIn("test override", pc.check_memory(meminfo(15.5), True).detail)

    def test_system(self):
        self.assertEqual(pc.check_os(MINT, "x86_64", True, False).status, OK)
        ubuntu = {"problems": ["expected Linux Mint 22.3, found Ubuntu 24.04"]}
        self.assertEqual(pc.check_os(ubuntu, "x86_64", True, False).status, FAIL)
        self.assertEqual(pc.check_os(ubuntu, "x86_64", True, True).status, NOTE)
        self.assertEqual(pc.check_os(MINT, "aarch64", True, True).status, FAIL)  # no override for the processor
        self.assertEqual(pc.check_python((3, 12, 3)).status, OK)
        self.assertEqual(pc.check_python((3, 11, 9)).status, FAIL)

    def test_disk_adds_up_when_everything_is_on_one_drive(self):
        with tempfile.TemporaryDirectory() as models, tempfile.TemporaryDirectory() as home:
            def check(devices, override=False):
                disk = FakeDisk({models: devices[0], home: devices[1]})
                return pc.check_disk(Path(models), Path(home), 94.5, override, statvfs=disk.statvfs,
                                     stat=disk.stat, mount=lambda p: p)
            same = ((1, 110), (1, 110))
            self.assertEqual(check(same).status, FAIL)  # 94.5 + 25 > 110 on the one drive
            self.assertIn("needs 120 GB", check(same).detail)
            self.assertEqual(check(((1, 110), (2, 30))).status, OK)  # two drives, each has room
            self.assertEqual(check(same, override=True).status, NOTE)

    def test_brains_already_downloaded_need_no_room(self):
        from model_installer import MODELS
        with tempfile.TemporaryDirectory() as temp:
            state = Path(temp) / "model-roster.json"
            self.assertAlmostEqual(pc.models_still_to_download(state), sum(s.approx_gb for s in MODELS))
            state.write_text(json.dumps({"models": {s.alias: {"complete": True} for s in MODELS[1:]}}))
            self.assertAlmostEqual(pc.models_still_to_download(state), MODELS[0].approx_gb)

    def test_desktop_microphone_processor(self):
        self.assertEqual(pc.check_session({"XDG_SESSION_TYPE": "x11"}).status, OK)
        self.assertIn("keyboard and mouse", pc.check_session({"XDG_SESSION_TYPE": "wayland"}).detail)
        run = lambda *a, **k: types.SimpleNamespace(stdout="card 1: Solo [Scarlett Solo], device 0: USB Audio\n")
        self.assertEqual(pc.check_microphone(which=lambda n: "/usr/bin/arecord", run=run).status, OK)
        none = lambda *a, **k: types.SimpleNamespace(stdout="")
        self.assertEqual(pc.check_microphone(which=lambda n: "/usr/bin/arecord", run=none).status, NOTE)
        self.assertEqual(pc.check_cpu("flags\t: fpu sse avx2\n", 16).status, OK)
        self.assertEqual(pc.check_cpu("flags\t: fpu sse\n", 16).status, NOTE)

    def test_internet_and_tools(self):
        def refuse(address, timeout):
            raise OSError("no route")
        self.assertEqual(pc.check_internet(connect=refuse).status, FAIL)
        self.assertEqual(pc.check_tools(which=lambda n: None, systemd_dir="/nonexistent").status, FAIL)

    def test_report_says_why_and_that_nothing_changed(self):
        text = pc.report([pc.Check("Memory", FAIL, "15.5 GiB; BMO's brains need about 28 GB"),
                          pc.Check("Desktop", NOTE, "Wayland")])
        self.assertIn("nothing has been changed", text)
        self.assertIn("This PC: NOT OK, because memory: 15.5 GiB", text)
        self.assertIn("This PC: OK (1 note above).", pc.report([pc.Check("Desktop", NOTE, "Wayland")]))

    def test_runs_with_the_system_python_alone(self):
        # Before the venv exists: no third-party imports anywhere in the pre-check's path.
        import ast
        for name in ("installer/precheck.py", "mint_appliance.py", "model_installer.py"):
            tree = ast.parse((ROOT / name).read_text())
            tops = {(n.module or "").split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
            tops |= {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
            self.assertLessEqual(tops - {"", "__future__", "model_installer", "mint_appliance"},
                                 set(sys.stdlib_module_names), name)


if __name__ == "__main__":
    unittest.main()
