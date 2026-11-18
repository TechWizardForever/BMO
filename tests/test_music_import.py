"""Music from Downloads and a USB stick (2026-11-17), the same way as the games and movies: BMO says what he found,
"Hey BMO, yes" moves them (copies, from a stick), and songs keep their album folders and covers in ~/Music."""
import os
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(os.environ.get("BEMO_TEST_CODE_ROOT", Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(ROOT))
from systems import sort_downloads as sorter


class Sorting(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        root = Path(self.folder.name)
        self.downloads, self.music = root / "Downloads", root / "Music"
        album = self.downloads / "Band - Album"
        album.mkdir(parents=True)
        for name in ("01 First.mp3", "02 Second.flac", "cover.jpg", "notes.txt", "voice memo.wav"):
            (album / name).write_bytes(b"x" * 2000)
        (self.downloads / "loose.mp3").write_bytes(b"y" * 2000)
        self.plan = lambda **kw: sorter.plan(self.downloads, root / "Videos", root / "games", settle=0, **kw)

    def test_found_with_their_albums(self):
        plan = self.plan(music_dir=self.music)
        targets = sorted(str(m.target.relative_to(self.music)) for m in plan.moves)
        self.assertEqual(targets, ["Band - Album/01 First.mp3", "Band - Album/02 Second.flac", "loose.mp3"])
        self.assertIn("3 songs", plan.summary())
        covers = [e for m in plan.moves for e in m.extras]
        self.assertEqual([(e[0].name, e[1].relative_to(self.music).as_posix()) for e in covers], [("cover.jpg", "Band - Album/cover.jpg")])

    def test_moved_and_copied(self):
        result = sorter.run(self.plan(music_dir=self.music))
        self.assertEqual(result["moved"]["song"], 3)
        self.assertTrue((self.music / "Band - Album" / "01 First.mp3").exists())
        self.assertTrue((self.music / "Band - Album" / "cover.jpg").exists())
        self.assertFalse((self.downloads / "loose.mp3").exists())

    def test_a_stick_keeps_its_files(self):
        result = sorter.run(self.plan(music_dir=self.music), copy=True)
        self.assertEqual(result["moved"]["song"], 3)
        self.assertTrue((self.downloads / "loose.mp3").exists())

    def test_a_music_folder_on_the_stick_isnt_doubled(self):
        nested = self.downloads / "Music" / "Album"
        nested.mkdir(parents=True)
        (nested / "song.mp3").write_bytes(b"z" * 2000)
        targets = {m.target for m in self.plan(music_dir=self.music).moves}
        self.assertIn(self.music / "Album" / "song.mp3", targets)

    def test_already_there_or_still_downloading(self):
        (self.music / "Band - Album").mkdir(parents=True)
        (self.music / "Band - Album" / "01 First.mp3").write_bytes(b"x" * 2000)
        (self.downloads / "half.mp3.part").write_bytes(b"x")
        plan = self.plan(music_dir=self.music)
        self.assertIn("already there", [why for _, why in plan.skipped])
        self.assertIn("downloading", [why for _, why in plan.skipped])

    def test_without_a_music_folder_songs_stay(self):
        self.assertEqual(self.plan().moves, [])

    def test_the_words(self):
        for said in ("sort my downloads", "sort the music in my downloads", "sort my mp3s in my downloads"):
            self.assertTrue(sorter.is_request(said), said)
        for said in ("sort my USB", "import the music from the USB", "import my music from the USB", "copy the songs off my stick",
                     "import my mp3s from the USB stick"):
            self.assertTrue(sorter.is_usb_request(said), said)
        self.assertFalse(sorter.is_usb_request("play music from the USB"))


if __name__ == "__main__":
    unittest.main()
