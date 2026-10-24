"""BMO's disk image (installer plan I8): the build script's checks and the first start of an installed PC.
Nothing here needs root or the Mint ISO: the build itself is run by the owner with sudo."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(os.environ.get("BEMO_TEST_CODE_ROOT", Path(__file__).resolve().parents[1]))
IMAGE = ROOT / "tools" / "image"
sys.path.insert(0, str(IMAGE))
import firstboot

PASSWD = """root:x:0:0:root:/root:/bin/bash
syslog:x:104:111::/home/syslog:/usr/sbin/nologin
ollama:x:998:998::/usr/share/ollama:/bin/false
pat:x:1000:1000:Pat,,,:/home/pat:/bin/bash
sam:x:1001:1001::/home/sam:/bin/bash
nobody:x:65534:65534:nobody:/nonexistent:/usr/sbin/nologin
"""


class FirstBootTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_the_owner_is_the_first_ordinary_account(self):
        self.assertEqual(firstboot.owner_account(PASSWD), ("pat", 1000, 1000, Path("/home/pat")))
        self.assertIsNone(firstboot.owner_account(PASSWD.replace("pat:", "#").replace("sam:", "#")))

    def test_seed_files_move_in_without_replacing_anything(self):
        seed, home = self.tmp / "seed", self.tmp / "home"
        (seed / ".bemo" / "voice").mkdir(parents=True)
        (seed / ".bemo" / "voice" / "bmo.onnx").write_text("voice")
        (seed / ".bemo" / "settings.json").write_text("seed")
        snap = seed / ".cache" / "huggingface" / "snapshots"
        snap.mkdir(parents=True)
        (seed / ".cache" / "huggingface" / "blob").write_text("model")
        (snap / "model.bin").symlink_to("../blob")
        (home / ".bemo").mkdir(parents=True)
        (home / ".bemo" / "settings.json").write_text("mine")
        added = firstboot.move_seed(seed, home, os.getuid(), os.getgid())
        self.assertEqual(added, [".cache"])
        self.assertEqual((home / ".bemo" / "settings.json").read_text(), "mine")
        self.assertEqual((home / ".bemo" / "voice" / "bmo.onnx").read_text(), "voice")
        self.assertEqual((home / ".cache" / "huggingface" / "snapshots" / "model.bin").read_text(), "model")
        self.assertTrue((home / ".cache" / "huggingface" / "snapshots" / "model.bin").is_symlink())
        self.assertFalse(seed.exists())

    def test_first_boot_hands_bmo_to_the_account(self):
        passwd, app, marker = self.tmp / "passwd", self.tmp / "opt-bemo", self.tmp / "lib" / "firstboot-done"
        passwd.write_text(PASSWD)
        app.mkdir()
        ran = []
        self.assertEqual(firstboot.main(run=lambda argv, **kw: ran.append(argv), passwd=passwd, app=app,
                                        seed=self.tmp / "no-seed", marker=marker), 0)
        self.assertIn(["usermod", "-a", "-G", "ollama", "pat"], ran)
        self.assertIn(["python3", str(app / "mint_appliance.py"), "configure-autologin", "--user", "pat"], ran)
        autostart = next(argv for argv in ran if argv[0] == "runuser")
        self.assertEqual(autostart[:3] + autostart[-2:], ["runuser", "-u", "pat", str(app / "autostart_cli.py"), "install"])
        # BMO's spoken first boot is opened for the account (found missing 2026-10-21: nothing opened it).
        self.assertEqual(ran[-1][:3] + ran[-1][-2:], ["runuser", "-u", "pat", str(app / "first_boot_cli.py"), "start"])
        self.assertEqual(marker.read_text(), "pat\n")

    def test_no_account_yet_means_try_again_next_start(self):
        passwd = self.tmp / "passwd"
        passwd.write_text("root:x:0:0:root:/root:/bin/bash\n")
        marker = self.tmp / "done"
        self.assertEqual(firstboot.main(run=lambda *a, **k: None, passwd=passwd, app=self.tmp, seed=self.tmp / "s",
                                        marker=marker), 1)
        self.assertFalse(marker.exists())

    def test_the_service_runs_once_and_never_in_the_live_session(self):
        unit = (IMAGE / "bemo-firstboot.service").read_text()
        self.assertIn("ConditionPathExists=!/var/lib/bemo/firstboot-done", unit)
        self.assertIn("ConditionKernelCommandLine=!boot=casper", unit)
        self.assertIn("Before=display-manager.service", unit)
        self.assertIn(str(firstboot.MARKER), unit)


class BuildScriptTests(unittest.TestCase):
    def test_scripts_parse(self):
        for name in ("build_image.sh", "in_chroot.sh"):
            done = subprocess.run(["bash", "-n", str(IMAGE / name)], capture_output=True, text=True)
            self.assertEqual(done.returncode, 0, done.stderr)

    def test_check_needs_no_password_and_says_what_is_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = dict(os.environ, BEMO_IMAGE_DIR=tmp, BEMO_MINT_ISO=f"{tmp}/none.iso",
                       BEMO_GUI_VISION_DIR=f"{tmp}/no-gui")
            done = subprocess.run(["bash", str(IMAGE / "build_image.sh"), "--check"], env=env,
                                  capture_output=True, text=True, timeout=60)
        self.assertEqual(done.returncode, 1)
        self.assertIn("MISSING the Mint ISO", done.stdout)
        self.assertIn("Click-by-sight: not built in", done.stdout)
        self.assertFalse(Path(tmp).exists())

    def test_only_committed_public_code_goes_in(self):
        script = (IMAGE / "build_image.sh").read_text()
        self.assertIn('git clone -q --branch main --single-branch "file://$REPO"', script)
        self.assertIn('remote set-url origin "$PUBLIC_REPO"', script)
        # The only thing taken from this PC's ~/.bemo is click-by-sight, and only when byte-identical.
        self.assertEqual(script.count("HOME/.bemo"), 1)
        self.assertIn('GUI_VISION="${BEMO_GUI_VISION_DIR:-$OWNER_HOME/.bemo/models/gui}"', script)
        self.assertIn("gui_vision_ok", script)

    def test_a_rerun_refreshes_the_code_in_place_and_repacks(self):
        # 2026-10-21: the first image was built from 8531b9d; main moved on while it built.
        script = (IMAGE / "build_image.sh").read_text()
        self.assertIn('[ "$MAIN_COMMIT" != "$(cat "$WORK/bemo-commit" 2>/dev/null)" ]', script)
        self.assertIn('rsync -a --delete --exclude /.venv', script)  # the Python environment stays
        self.assertIn('rm -f "$(done_marker squash)" "$(done_marker iso)"', script)
        self.assertNotIn('rm -rf "$WORK/bemo-src" "$ROOTFS/opt/bemo"', script)

    def test_the_mint_iso_is_pinned(self):
        self.assertIn('MINT_SHA256="45a835b5dddaf40e84d776549e0b19b3fbd49673b6cc6434ebddbfcd217df776"',
                      (IMAGE / "build_image.sh").read_text())

    def test_inside_the_image_matches_install_sh(self):
        inside = (IMAGE / "in_chroot.sh").read_text()
        for piece in ("installer/apt-packages.txt", "installer/ollama.sh", "installer/requirements.lock",
                      "installer/voice.sh", "prefetch_runtime_models.py", "systemctl enable bemo-firstboot.service"):
            self.assertIn(piece, inside)
        self.assertNotIn("model_installer.py", inside)  # the brains download at first boot
        # Rehearsal 2026-10-21: onnxruntime wrote ~/.cache/Microsoft/DeveloperTools/.onnxruntime/deviceid.
        self.assertIn('"$SEED/.cache/Microsoft"', inside)
        self.assertIn('find "$SEED" -name deviceid -delete', inside)

    def test_install_sh_opens_the_first_boot_only_on_a_new_install(self):
        # Found 2026-10-21: neither installer opened ~/.bemo/install/first-boot.json.
        text = (ROOT / "install.sh").read_text()
        self.assertIn('[ ! -e "$BEMO_DATA_DIR/install/first-boot.json" ] && '
                      '[ ! -e "$BEMO_DATA_DIR/memory/conversation.sqlite3" ]', text)
        self.assertIn("python first_boot_cli.py start", text)

    def test_the_image_is_left_as_mint_ships_it(self):
        script = (IMAGE / "build_image.sh").read_text()
        for piece in ("policy-rc.d", "etc/machine-id", "resolv.conf", "umount -lf"):
            self.assertIn(piece, script)


class NothingToShareTests(unittest.TestCase):
    """2026-10-24: no Windows screensavers, games or videos in the image (an older seed had the screensaver pack)."""
    def guard(self, rootfs):
        script = (IMAGE / "build_image.sh").read_text()
        start = script.index("for leftover in ")
        end = script.index('[ -z "$shipped" ] ||')
        end = script.index("\n", end) + 1
        snippet = ('WORK="$1/work"; ROOTFS="$1/rootfs"; mkdir -p "$WORK"; touch "$WORK/.done-squash"\n'
                   'done_marker() { echo "$WORK/.done-$1"; }\nfail() { echo "FAIL: $*"; exit 3; }\n'
                   + script[start:end] + 'echo clean; [ -f "$WORK/.done-squash" ] && echo kept-squash || true\n')
        return subprocess.run(["bash", "-c", snippet, "guard", str(rootfs)], capture_output=True, text=True)

    def test_old_screensavers_are_removed_and_the_image_repacked(self):
        root = Path(tempfile.mkdtemp())
        saver = root / "rootfs" / "opt" / "bemo-seed" / ".bemo" / "screensavers" / "windows" / "Mystify.scr"
        saver.parent.mkdir(parents=True)
        saver.write_text("x")
        done = self.guard(root)
        self.assertEqual(done.stdout.split(), ["clean"], done.stdout + done.stderr)
        self.assertFalse(saver.exists())

    def test_a_game_or_video_stops_the_build(self):
        root = Path(tempfile.mkdtemp())
        for name in ("opt/bemo-seed/.bemo/games/roms/gamecube/Melee.rvz", "home/bemo/Videos/show.mkv"):
            (root / "rootfs" / name).parent.mkdir(parents=True, exist_ok=True)
            (root / "rootfs" / name).write_text("x")
            done = self.guard(root)
            self.assertEqual(done.returncode, 3, name)
            self.assertIn("mustn't share", done.stdout)
            (root / "rootfs" / name).unlink()


if __name__ == "__main__":
    unittest.main()
