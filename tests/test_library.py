"""The movie and game libraries on BMO's screen (2026-10-28): what's listed, the pictures, the stills, the pages,
the phrases, and the chat side (scrolling without Hey BMO, play number 3)."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(os.environ.get("BEMO_TEST_CODE_ROOT", Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(ROOT))
from systems import library as lib


def touch(path, data=b"x"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class ItemsTests(unittest.TestCase):
    def setUp(self):
        self.videos = Path(tempfile.mkdtemp())
        ghibli = self.videos / "Movies" / "Studio Ghibli"
        touch(ghibli / "Spirited.Away.2001.1080p.BDRip.x265.mkv")
        touch(ghibli / "Spirited.Away.2001.1080p.BDRip.x265.jpg")          # a poster named like the movie
        touch(ghibli / "The.Cat.Returns.2002.1080p.BDRip.x265.mkv")
        touch(ghibli / "folder.jpg")                                       # the collection's picture, not one movie's
        touch(self.videos / "Movies" / "Ponyo (2008)" / "Ponyo.2008.1080p.mkv")
        touch(self.videos / "Movies" / "Ponyo (2008)" / "poster.png")      # a folder for one movie
        touch(self.videos / "TV Shows" / "Jimmy" / "Season 1" / "Jimmy Neutron - S01E01 - Pilot.mkv")
        touch(self.videos / "TV Shows" / "Jimmy" / "Season 1" / "Jimmy Neutron - S01E02 - Brobot.mkv")
        touch(self.videos / "TV Shows" / "Jimmy" / "Specials" / "Jimmy Neutron - S00E01 - Runaway.mkv")
        touch(self.videos / "TV Shows" / "Jimmy" / "poster.jpg")

    def test_movies_and_one_tile_per_show_by_name(self):
        items = lib.movie_items([self.videos])
        self.assertEqual([i.title for i in items], ["The Cat Returns", "Jimmy Neutron", "Ponyo", "Spirited Away"])
        by = {i.title: i for i in items}
        self.assertEqual(by["Spirited Away"].picture.name, "Spirited.Away.2001.1080p.BDRip.x265.jpg")
        self.assertIsNone(by["The Cat Returns"].picture)   # folder.jpg belongs to the whole folder
        self.assertEqual(by["Ponyo"].picture.name, "poster.png")
        show = by["Jimmy Neutron"]
        self.assertEqual((show.kind, show.picture.name), ("show", "poster.jpg"))
        self.assertIn("S01E01", show.still_from.name)       # a still from the first regular episode
        self.assertEqual(by["The Cat Returns"].still_from.name, "The.Cat.Returns.2002.1080p.BDRip.x265.mkv")

    def test_games_with_es_de_covers(self):
        root = Path(tempfile.mkdtemp())
        roms, art = root / "roms" / "gamecube", root / "artwork"
        zelda = touch(roms / "Legend of Zelda, The - The Wind Waker (USA).rvz")
        robo = touch(roms / "Custom Robo (USA).rvz")
        touch(art / "gc" / "covers" / "Legend of Zelda, The - The Wind Waker (USA).png")
        entry = type("Game", (), {})

        class Games:
            gamecube_dir, artwork_dir = roms, art

            def scan(self):
                out = []
                for path, title in ((zelda, "The Legend of Zelda - The Wind Waker"), (robo, "Custom Robo")):
                    game = entry()
                    game.path, game.title = str(path), title
                    out.append(game)
                return out
        items = lib.game_items(Games())
        self.assertEqual([i.title for i in items], ["Custom Robo", "The Legend of Zelda - The Wind Waker"])
        self.assertIsNone(items[0].picture)
        self.assertEqual(items[1].picture.parent.name, "covers")
        self.assertEqual(items[1].key, str(zelda))


class StillTests(unittest.TestCase):
    def test_a_solid_colour_frame_is_replaced_by_a_later_one(self):
        # Owner 2026-10-28: never a solid colour. Any solid colour is ~1 KB, a title card ~3.4 KB, a scene 13 KB+.
        folder = Path(tempfile.mkdtemp())
        sizes, calls = [985, 3340, 30000], []

        def run(argv, **kwargs):
            calls.append(argv)
            Path(argv[argv.index("-o") + 1]).write_bytes(b"j" * sizes[len(calls) - 1])
            return subprocess.CompletedProcess(argv, 0, b"", b"")
        out = folder / "still.jpg"
        self.assertTrue(lib.make_still(Path("/v/movie.mkv"), out, run=run, which=lambda name: "/usr/bin/" + name))
        self.assertEqual([c[c.index("-t") + 1] for c in calls], ["15%", "30%", "45%"])
        self.assertEqual(out.stat().st_size, 30000)
        self.assertEqual(sorted(p.name for p in folder.iterdir()), ["still.jpg"])  # no leftovers

    def test_when_every_spot_is_plain_the_most_detailed_is_kept(self):
        sizes, calls = [985, 3340, 2000, 1500], []

        def run(argv, **kwargs):
            calls.append(argv)
            Path(argv[argv.index("-o") + 1]).write_bytes(b"j" * sizes[len(calls) - 1])
            return subprocess.CompletedProcess(argv, 0, b"", b"")
        out = Path(tempfile.mkdtemp()) / "still.jpg"
        self.assertTrue(lib.make_still(Path("/v/m.mkv"), out, run=run, which=lambda name: "/usr/bin/" + name))
        self.assertEqual((len(calls), out.stat().st_size), (4, 3340))

    def test_without_ffmpeg_there_is_no_still(self):
        self.assertFalse(lib.make_still(Path("/v/m.mkv"), Path(tempfile.mkdtemp()) / "s.jpg", which=lambda n: None))

    def test_stills_are_made_page_first_and_show_up_on_the_tiles(self):
        folder = Path(tempfile.mkdtemp())
        items = [lib.Item(f"Movie {n}", "movie", f"/v/{n}.mkv", None, Path(f"/v/{n}.mkv")) for n in range(12)]
        view = lib.LibraryView()
        view.open("movies", items)
        view.scroll(1)
        made = []

        def make(video, out):
            made.append(video.name)
            out.write_bytes(b"j")
            return True
        self.assertEqual(lib.make_stills(view, items, folder, make=make), 12)
        self.assertEqual(made[:4], ["8.mkv", "9.mkv", "10.mkv", "11.mkv"])  # the page on screen first
        self.assertTrue(all(tile["picture"] for tile in view.snapshot()["tiles"]))
        self.assertTrue(all(i.picture for i in lib.with_stills(items, folder)))  # kept for next time
        view.open("movies", items)
        self.assertEqual(lib.make_stills(view, items, folder, make=make, stop=lambda: True), 0)


class ViewTests(unittest.TestCase):
    def items(self, n, picture=True):
        return [lib.Item(f"T{i}", "movie", f"/v/{i}", Path("/p.jpg") if picture else None) for i in range(1, n + 1)]

    def test_pages_numbers_and_the_ends(self):
        view = lib.LibraryView()
        view.open("movies", self.items(20))
        snap = view.snapshot()
        self.assertEqual((snap["first"], snap["total"], len(snap["tiles"]), snap["grid"]), (1, 20, 8, True))
        self.assertFalse(view.scroll(-1))   # already at the top
        self.assertTrue(view.scroll(1))
        self.assertTrue(view.scroll(1))
        self.assertEqual([t["number"] for t in view.snapshot()["tiles"]], [17, 18, 19, 20])
        self.assertFalse(view.scroll(1))    # the end
        self.assertEqual(view.item(3).title, "T3")  # any number, not just the page on screen
        self.assertIsNone(view.item(21))
        view.to_top()
        self.assertEqual(view.snapshot()["first"], 1)

    def test_no_pictures_is_a_list_with_more_per_page(self):
        view = lib.LibraryView()
        view.open("games", self.items(14, picture=False))
        snap = view.snapshot()
        self.assertFalse(snap["grid"])
        self.assertEqual(len(snap["tiles"]), lib.LIST_PAGE)

    def test_it_closes_by_itself_after_a_while_without_a_word(self):
        clock = Clock()
        view = lib.LibraryView(clock=clock)
        view.open("movies", self.items(3))
        clock.now += lib.IDLE_CLOSE_SECONDS - 5
        view.scroll(1)  # a command keeps it open
        clock.now += lib.IDLE_CLOSE_SECONDS - 5
        self.assertTrue(view.active)
        clock.now += 10
        self.assertFalse(view.active)
        self.assertIsNone(view.snapshot())


class PhraseTests(unittest.TestCase):
    def test_opening(self):
        for said, kind in (("show my movies", "movies"), ("Hey BMO, show me my games.", "games"),
                           ("open the movie library", "movies"), ("what games do we have", "games"),
                           ("show my shows", "movies"), ("pull up the movie list", "movies")):
            self.assertEqual(lib.open_request(said), kind, said)
        for said in ("open games", "open the game library", "show me the shapes", "show desktop"):
            self.assertIsNone(lib.open_request(said), said)  # ES-DE and the shapes keep theirs

    def test_while_open(self):
        for said, expected in (("scroll down", ("down", 0)), ("Scroll down.", ("down", 0)), ("next page", ("down", 0)),
                               ("more", ("down", 0)), ("page up", ("up", 0)), ("go back up", ("up", 0)),
                               ("go to the top", ("top", 0)), ("close", ("close", 0)),
                               ("close the library", ("close", 0)), ("I'm done", ("close", 0)),
                               ("play number 3", ("play", 3)), ("number three", ("play", 3)),
                               ("play twenty one", ("play", 21)), ("play number to", ("play", 2))):
            self.assertEqual(lib.command(said), expected, said)
        for said in ("play spirited away", "what's the weather", "scroll down the page in firefox"):
            self.assertIsNone(lib.command(said), said)


class ChatTests(unittest.TestCase):
    def setUp(self):
        from systems.chat import Chat, State
        self.State = State
        chat = self.chat = Chat.__new__(Chat)
        chat.library_view = lib.LibraryView()
        self.said, self.played, self.launched = [], [], []
        chat._say_local = lambda command, text: self.said.append(text) or True
        chat.note_user_activity = lambda: None
        chat._play_video = lambda command, wanted=None, **kw: self.played.append(kw) or True
        chat.supervisor = type("S", (), {"launch_game": lambda s, q: self.launched.append(q) or
                                         {"ok": True, "game": {"title": q}}})()
        chat.library_view.open("movies", [
            lib.Item("Ponyo", "movie", "/v/Ponyo.mkv", Path("/p.jpg")),
            lib.Item("Jimmy Neutron", "show", "The Adventures of Jimmy Neutron", None, Path("/v/j.mkv"))]
            + [lib.Item(f"M{n}", "movie", f"/v/{n}.mkv", Path("/p.jpg")) for n in range(10)])

    def test_scrolling_is_quiet_and_the_end_is_said(self):
        self.assertTrue(self.chat._bare_library_command("scroll down"))
        self.assertEqual((self.said, self.chat.state), ([], self.State.READY))
        self.assertTrue(self.chat._bare_library_command("scroll down"))
        self.assertEqual(self.said, ["That's the end of the list."])
        self.assertFalse(self.chat._bare_library_command("tell me a story about a dragon"))

    def test_play_a_number(self):
        self.assertTrue(self.chat._bare_library_command("play number 1"))
        self.assertEqual(self.played[-1], {"path": "/v/Ponyo.mkv"})
        self.assertTrue(self.chat._handle_library_command("Hey BMO, play number two"))
        self.assertEqual(self.played[-1], {"show": "The Adventures of Jimmy Neutron"})
        self.assertTrue(self.chat._handle_library_command("number 99"))
        self.assertEqual(self.said[-1], "There's no number 99. The list goes up to 12.")

    def test_a_game_by_number_and_close(self):
        self.chat.library_view.open("games", [lib.Item("Custom Robo", "game", "/g/Custom Robo (USA).rvz")])
        self.assertTrue(self.chat._bare_library_command("number one"))
        self.assertEqual(self.launched, ["Custom Robo (USA)"])
        self.assertEqual(self.said[-1], "Launching Custom Robo.")
        self.assertFalse(self.chat.library_view.active)  # it closes when the game starts
        self.chat.library_view.open("games", [])
        self.assertTrue(self.chat._bare_library_command("close"))
        self.assertFalse(self.chat.library_view.active)

    def test_nothing_without_a_library_on_screen(self):
        self.chat.library_view.close()
        self.assertFalse(self.chat._bare_library_command("scroll down"))
        self.assertFalse(self.chat._handle_library_command("scroll down"))  # left for the desktop, say
        self.assertTrue(self.chat._handle_library_command("play number 3"))
        self.assertIn("show my movies", self.said[-1])

    def test_opening_the_movies_says_how_many_and_starts_the_stills(self):
        import config
        videos = Path(tempfile.mkdtemp())
        for n in range(10):
            touch(videos / f"Movie.Number.{n}.2001.1080p.mkv")
        started = []
        with mock.patch.object(config, "MEDIA_DIRS", [videos]), \
                mock.patch.object(config, "LIBRARY_STILLS_DIR", Path(tempfile.mkdtemp())), \
                mock.patch("threading.Thread", lambda **kw: type("T", (), {"start": lambda s: started.append(kw)})()):
            self.assertTrue(self.chat._handle_library_command("Hey BMO, show my movies"))
        self.assertEqual(self.said[-1], "Here are your movies and shows, 10 in all. Say play and a number, "
                                        "or scroll down.")
        self.assertEqual(started[0]["name"], "library-stills")
        self.assertEqual(self.chat.library_view.snapshot()["total"], 10)

    def test_wired_in(self):
        chat = (ROOT / "systems" / "chat.py").read_text()
        self.assertLess(chat.index("if self._handle_library_command(command):"),
                        chat.index("if self._handle_media_command(command):"))
        self.assertIn("or self._bare_library_command(prompt)", chat)
        self.assertIn('status["library"] = library_view.snapshot()', chat)


if __name__ == "__main__":
    unittest.main()
