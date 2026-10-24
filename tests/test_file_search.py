"""find_files reaches the owner's folders (live 2026-10-19: it found nothing for "snake_game")."""
import os
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(os.environ.get("BEMO_TEST_CODE_ROOT", Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(ROOT))
from systems.assets import FileLocator


class FileSearchTests(unittest.TestCase):
    def test_shallow_folders_before_huge_hidden_ones(self):
        home = Path(tempfile.mkdtemp())
        # A huge hidden folder and a virtual environment, as on the owner's PC (~/.local 69k files,
        # BMO's .venv in ~/Downloads 67k), and the code folder with the game.
        for i in range(400):
            (home / ".local" / "lib" / f"d{i}").mkdir(parents=True)
            (home / ".local" / "lib" / f"d{i}" / "snake_helper.py").write_text("x")
        venv = home / "Downloads" / "bemo-master" / ".venv"
        (venv / "lib").mkdir(parents=True)
        (venv / "pyvenv.cfg").write_text("home = /usr/bin\n")
        for i in range(400):
            (venv / "lib" / f"snake_{i}.py").write_text("x")
        (home / "BMO-code").mkdir()
        (home / "BMO-code" / "snake_game_3.py").write_text("print(1)\n")
        locator = FileLocator(home_root=home, removable_provider=lambda: [], exclude_dirs=(),
                              system_roots=(), enable_full_pc_search=False, max_scan_files=300,
                              search_timeout_seconds=4.0, max_image_bytes=1)
        found = [Path(item["path"]).name for item in locator.search("snake_game", scope="home")]
        self.assertEqual(found, ["snake_game_3.py"])


if __name__ == "__main__":
    unittest.main()
