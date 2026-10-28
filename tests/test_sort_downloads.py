"""'Hey BMO, sort my downloads' (2026-10-28): what goes where, what's left alone, the moving, and the chat side."""
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
import zipfile

ROOT = Path(os.environ.get("BEMO_TEST_CODE_ROOT", Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(ROOT))
from systems import sort_downloads as sd

OLD = time.time() - 3600


def touch(path, data=b"x", when=OLD):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    os.utime(path, (when, when))
    return path


class PlanTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.downloads, self.videos, self.games = self.root / "Downloads", self.root / "Videos", self.root / "games"
        touch(self.videos / "TV Shows" / "The Adventures of Jimmy Neutron" / "Season 1" /
              "The Adventures of Jimmy Neutron Boy Genius - S01E01 - When Pants Attack.mkv")
        self.games.mkdir()

    def plan(self, **kw):
        return sd.plan(self.downloads, self.videos, self.games, **kw)

    def test_games_movies_and_episodes_go_where_they_belong(self):
        touch(self.downloads / "Custom Robo (USA).rvz")
        movie = touch(self.downloads / "Ponyo.2008.1080p" / "Ponyo.2008.1080p.mkv")
        touch(self.downloads / "Ponyo.2008.1080p" / "Ponyo.2008.1080p.eng.srt")
        touch(self.downloads / "Ponyo.2008.1080p" / "Subs" / "2_English.srt")
        touch(self.downloads / "Ponyo.2008.1080p" / "poster.jpg")
        touch(self.downloads / "Ponyo.2008.1080p" / "readme.nfo")
        touch(self.downloads / "Jimmy Neutron Boy Genius - S02E03 - Krunch Time.mkv")
        touch(self.downloads / "Jimmy Neutron Boy Genius - S00E01 - Pilot.mkv")
        plan = self.plan()
        where = {m.source.name: m.target.relative_to(self.root) for m in plan.moves}
        self.assertEqual(where["Custom Robo (USA).rvz"], Path("games/Custom Robo (USA).rvz"))
        self.assertEqual(where["Ponyo.2008.1080p.mkv"], Path("Videos/Movies/Ponyo.2008.1080p.mkv"))
        self.assertEqual(where["Jimmy Neutron Boy Genius - S02E03 - Krunch Time.mkv"],
                         Path("Videos/TV Shows/The Adventures of Jimmy Neutron/Season 2/"
                              "Jimmy Neutron Boy Genius - S02E03 - Krunch Time.mkv"))  # the show's own folder
        self.assertIn("Specials", str(where["Jimmy Neutron Boy Genius - S00E01 - Pilot.mkv"]))
        ponyo = next(m for m in plan.moves if m.source == movie)
        self.assertEqual(sorted(to.name for _, to in ponyo.extras),
                         ["Ponyo.2008.1080p.2_English.srt", "Ponyo.2008.1080p.eng.srt", "Ponyo.2008.1080p.jpg"])
        self.assertEqual(plan.summary(), "a game, a movie and 2 episodes of Jimmy Neutron")

    def test_what_is_left_alone(self):
        touch(self.downloads / "Torrent" / "Big.Movie.2020.mkv")
        touch(self.downloads / "Torrent" / "Big.Movie.2020.mkv.part")          # still downloading
        touch(self.downloads / "Just.Finished.2021.mkv", when=time.time())     # changed a moment ago
        touch(self.downloads / "Movie.2019" / "movie.2019-sample.mkv")         # a sample clip
        touch(self.downloads / "bemo-master" / ".git" / "HEAD")                # code
        touch(self.downloads / "bemo-master" / "tests" / "clip.mp4")
        touch(self.downloads / ".cache" / "hidden.mkv")
        touch(self.downloads / "Custom Robo (USA).rvz", b"same")
        touch(self.games / "Custom Robo (USA).rvz", b"same")                   # already in the library
        touch(self.downloads / "Mine" / "keep.mkv")
        plan = self.plan(skip=[self.downloads / "Mine"])
        self.assertEqual(plan.moves, [])
        reasons = sorted(why for _, why in plan.skipped)
        self.assertEqual(reasons, ["already there", "downloading", "downloading", "sample"])

    def test_a_zip_of_games_is_unpacked_and_the_zip_goes_to_the_trash(self):
        archive = self.downloads / "Wind Waker.zip"
        archive.parent.mkdir(parents=True)
        with zipfile.ZipFile(archive, "w") as z:
            z.writestr("Wind Waker (USA).rvz", b"game" * 100)
            z.writestr("readme.txt", "hi")
        os.utime(archive, (OLD, OLD))
        plan = self.plan()
        self.assertEqual([(m.kind, m.members) for m in plan.moves], [("game", ["Wind Waker (USA).rvz"])])
        trashed = []
        result = sd.run(plan, trash=trashed.append)
        self.assertEqual(result["moved"]["game"], 1)
        self.assertEqual((self.games / "Wind Waker (USA).rvz").read_bytes(), b"game" * 100)
        self.assertEqual(trashed, [archive])
        self.assertEqual([p.name for p in self.games.iterdir()], ["Wind Waker (USA).rvz"])  # no temp folder left

    def test_moving_keeps_subtitles_and_never_overwrites(self):
        touch(self.downloads / "Ponyo.2008" / "Ponyo.2008.mkv", b"new movie")
        touch(self.downloads / "Ponyo.2008" / "Ponyo.2008.srt")
        touch(self.videos / "Movies" / "Ponyo.2008.mkv", b"a different, older file")
        result = sd.run(self.plan())
        self.assertEqual(result, {"moved": {"game": 0, "movie": 1, "episode": 0}, "failed": []})
        self.assertEqual((self.videos / "Movies" / "Ponyo.2008 (2).mkv").read_bytes(), b"new movie")
        self.assertEqual((self.videos / "Movies" / "Ponyo.2008.mkv").read_bytes(), b"a different, older file")
        self.assertTrue((self.videos / "Movies" / "Ponyo.2008.srt").exists())
        self.assertFalse((self.downloads / "Ponyo.2008" / "Ponyo.2008.mkv").exists())

    def test_a_failure_is_reported_and_the_rest_carry_on(self):
        touch(self.downloads / "A.2001.mkv")
        touch(self.downloads / "B.2002.mkv")
        plan = self.plan()
        plan.moves[0].source.unlink()  # gone before the move
        result = sd.run(plan)
        self.assertEqual((result["moved"]["movie"], len(result["failed"])), (1, 1))

    def test_phrases(self):
        for said in ("sort my downloads", "Hey BMO, organize the downloads folder", "put away my downloads",
                     "sort the games and movies in my downloads", "tidy up my downloads"):
            self.assertTrue(sd.is_request(said), said)
        for said in ("how are the downloads", "sort my files", "download a movie"):
            self.assertFalse(sd.is_request(said), said)


class ChatTests(unittest.TestCase):
    def setUp(self):
        from unittest import mock
        import config
        from systems.chat import Chat
        PlanTests.setUp(self)
        chat = self.chat = Chat.__new__(Chat)
        self.said, self.asked, self.refreshed = [], [], []
        chat._say_local = lambda command, text: self.said.append(text) or True
        chat.speak = lambda text: self.said.append(text)
        self.answer = True
        chat._confirm_by_voice = lambda question, **kw: self.asked.append(question) or self.answer
        chat.supervisor = type("S", (), {"refresh_games": lambda s: self.refreshed.append(1) or {"ok": True}})()
        for name, value in (("MEDIA_DIRS", [self.videos]), ("GAME_GAMECUBE_ROM_DIR", self.games)):
            patcher = mock.patch.object(config, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = mock.patch.object(sd, "downloads_folder", lambda: self.downloads)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_asks_first_then_moves_and_refreshes_the_games(self):
        touch(self.downloads / "Custom Robo (USA).rvz")
        touch(self.downloads / "Ponyo.2008.mkv")
        self.answer = False
        self.assertTrue(self.chat._handle_sort_downloads("Hey BMO, sort my downloads"))
        self.assertEqual(self.asked, ["I found a game and a movie in Downloads. Should I move them where they go? "
                                      "Say, Hey BMO, yes."])
        self.assertEqual(self.said[-1], "Okay, I left them where they are.")
        self.assertTrue((self.downloads / "Ponyo.2008.mkv").exists())
        self.answer = True
        self.chat._handle_sort_downloads("Hey BMO, sort my downloads")
        self.assertEqual(self.said[-1], "Done. The movies and shows are in your Videos folder and the games are in "
                                        "my games folder.")
        self.assertTrue((self.games / "Custom Robo (USA).rvz").exists())
        self.assertEqual(self.refreshed, [1])

    def test_one_file_is_it(self):
        touch(self.downloads / "Ponyo.2008.mkv")
        self.answer = False
        self.chat._handle_sort_downloads("sort my downloads")
        self.assertEqual(self.asked[-1], "I found a movie in Downloads. Should I move it where it goes? "
                                         "Say, Hey BMO, yes.")
        self.assertEqual(self.said[-1], "Okay, I left it where it is.")

    def test_nothing_to_sort_and_downloads_in_progress(self):
        touch(self.downloads / "Big.Movie.2020.mkv", when=time.time())
        self.assertTrue(self.chat._handle_sort_downloads("sort my downloads"))
        self.assertEqual(self.said[-1], "I didn't find any games or movies to sort in Downloads. One is still "
                                        "downloading, so I'll leave it.")
        self.assertEqual(self.asked, [])
        self.assertFalse(self.chat._handle_sort_downloads("what's the weather"))

    def test_wired_before_the_player(self):
        chat = (ROOT / "systems" / "chat.py").read_text()
        self.assertLess(chat.index("if self._handle_sort_downloads(command):"),
                        chat.index("if self._handle_media_command(command):"))


if __name__ == "__main__":
    unittest.main()
