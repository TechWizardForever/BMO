"""What a full backup carries (untouched-systems check, 2026-10-19)."""
import os
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(os.environ.get("BEMO_TEST_CODE_ROOT", Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(ROOT))
from systems.backup import FullBackupManager


class FakeMemory:
    def backup_to(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_bytes(b"sqlite")


class BackupContentsTests(unittest.TestCase):
    def test_downloaded_and_rebuilt_things_stay_out_and_everything_else_goes_in(self):
        base = Path(tempfile.mkdtemp())
        data, app, usb = base / ".bemo", base / "app", base / "usb"
        for rel in ("models/gui/MAI-UI-2B.gguf", "install-cache/savers.zip", "voice/piper/bemo.onnx",
                    "voice/models/keep.bin", "projects/demo/main.py", "games/dolphin/User/save.gci",
                    # 2026-10-23: the Wine layer and other rebuilt things made a USB backup take an hour.
                    "wine-screensavers/drive_c/windows/system32/msvcrtd.dll", "cache/part_gallery/cone/00.png",
                    "source-upgrade-backups/v1.28/config.py", "screensavers/windows/legacy-pack/Space.scr",
                    "models/wakeword/hey_bmo.onnx", "wake-samples/1.wav"):
            (data / rel).parent.mkdir(parents=True, exist_ok=True)
            (data / rel).write_text("x")
        (app / "config.py").parent.mkdir(parents=True)
        (app / "config.py").write_text("APP = 1\n")
        usb.mkdir()
        result = FullBackupManager(data_root=data, app_root=app, removable_provider=lambda: [usb]).create(
            memory_store=FakeMemory(), create_zip=False)
        self.assertTrue(result.get("ok"), result)
        saved = Path(result["folder"]) / "data"
        for rel in ("models/gui", "install-cache", "wine-screensavers", "cache", "source-upgrade-backups"):
            self.assertFalse((saved / rel).exists(), rel)
        for rel in ("voice/piper/bemo.onnx", "voice/models/keep.bin", "projects/demo/main.py",
                    "games/dolphin/User/save.gci", "memory/conversation.sqlite3",
                    "screensavers/windows/legacy-pack/Space.scr", "models/wakeword/hey_bmo.onnx",
                    "wake-samples/1.wav"):
            self.assertTrue((saved / rel).is_file(), rel)
        self.assertTrue(FullBackupManager.verify_folder(result["folder"]).get("ok"))



class DriveOverrideTests(unittest.TestCase):
    def test_the_test_drive_replaces_real_usb_sticks(self):
        # 2026-10-23: with the owner's stick plugged in, self_test imported its 96 files as well.
        from unittest import mock
        from systems.games import discover_removable_drives
        fake = Path(tempfile.mkdtemp())
        with mock.patch.dict(os.environ, {"BEMO_REMOVABLE_ROOTS": str(fake)}):
            self.assertEqual(discover_removable_drives(), [fake.resolve()])



class ImportNamesTests(unittest.TestCase):
    """USB test, 2026-10-23: "imported 2 files", then the brain named a file that wasn't imported."""
    def test_the_import_reports_its_files(self):
        sys.path.insert(0, str(ROOT))
        import config
        from systems.projects import ProjectManager
        root = Path(tempfile.mkdtemp())
        pm = ProjectManager(
            data_root=root, projects_root=root / "projects", index_extensions=config.PROJECT_INDEX_EXTENSIONS,
            exclude_dirs=config.PROJECT_EXCLUDE_DIRS, max_file_bytes=config.PROJECT_FILE_MAX_BYTES,
            context_char_budget=config.PROJECT_CONTEXT_CHAR_BUDGET, retrieval_chunks=config.PROJECT_RETRIEVAL_CHUNKS,
            reindex_interval_seconds=0, auto_git=False)
        pm.create_project("Garden")
        usb = root / "usb"
        (usb / "Bemo Backups").mkdir(parents=True)
        (usb / "Bemo Backups" / "old.zip").write_text("skip")
        (usb / "36d7b50f132ad926b168933cb306de3209bedcec.JPG").write_text("a")
        (usb / "plan.pdf").write_text("b")
        result = pm.import_files_from_removable([usb])
        self.assertEqual(sorted(result["imported_files"]), ["36d7b50f132ad926b168933cb306de3209bedcec.JPG", "plan.pdf"])

    def test_what_bmo_says(self):
        from systems.chat import Chat
        self.assertEqual(Chat._describe_files(["36d7b50f132ad926b168933cb306de3209bedcec.JPG",
                                               "67e852d838df60d881c569d1acd4a6af.jpg"]), "2 pictures")
        self.assertEqual(Chat._describe_files(["cup.stl", "notes.txt"]), "cup.stl and notes.txt")
        self.assertEqual(Chat._describe_files(["a.jpg", "b.jpg", "c.pdf", "d.stl"]), "2 pictures, a PDF and a 3D model")
        self.assertEqual(Chat._describe_files(["x.stl", "y.stl", "z.stl", "w.txt", "q.zzz"]),
                         "3 3D models, a text file and another file")
        source = (ROOT / "systems" / "chat.py").read_text()
        self.assertIn('listed = (f"{response} The files: {\', \'.join(names[:40])}"', source)


if __name__ == "__main__":
    unittest.main()
