"""After BMO writes files in a project, the index catches up right away but only re-reads what changed (2026-10-31: every write re-read the whole project; 300 files took 2.3 s against 0.04 s)."""
import os
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(os.environ.get("BEMO_TEST_CODE_ROOT", Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(ROOT))
import config
from systems.projects import ProjectManager


class IndexNowTests(unittest.TestCase):
    def setUp(self):
        root = Path(tempfile.mkdtemp())
        self.pm = ProjectManager(data_root=root / "data", projects_root=root / "projects",
                                 index_extensions=config.PROJECT_INDEX_EXTENSIONS,
                                 exclude_dirs=config.PROJECT_EXCLUDE_DIRS, max_file_bytes=config.PROJECT_FILE_MAX_BYTES,
                                 context_char_budget=4000, retrieval_chunks=6, reindex_interval_seconds=3600,
                                 auto_git=False)
        self.slug = self.pm.create_project("Shelf")["slug"]
        self.work = self.pm._workspace(self.slug)
        for n in range(20):
            (self.work / f"part{n}.py").write_text(f"def part_{n}():\n    return {n}\n")
        self.pm.index_project(self.slug, force=True)

    def test_now_skips_the_wait_but_rereads_only_what_changed(self):
        self.assertTrue(self.pm.index_project(self.slug).get("skipped"))  # the normal call waits an hour here
        (self.work / "part3.py").write_text("def part_3():\n    return 'walnut shelf'\n")
        (self.work / "new_part.py").write_text("def bracket():\n    return 'zebra quokka'\n")
        (self.work / "part7.py").unlink()
        result = self.pm.index_project(self.slug, now=True)
        self.assertFalse(result.get("skipped"))
        self.assertEqual((result["changed"], result["removed"]), (2, 1))
        self.assertIn("zebra quokka", str(self.pm.context_for_query("zebra quokka bracket")))

    def test_bmo_uses_it_after_his_own_writes(self):
        chat = (ROOT / "systems" / "chat.py").read_text()
        self.assertEqual(chat.count("index_project(now=True)"), 3)
        self.assertEqual(chat.count("index_project(force=True)"), 1)  # the "reindex project" command


if __name__ == "__main__":
    unittest.main()
