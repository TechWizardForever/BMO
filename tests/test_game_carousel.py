"""The games as a carousel (2026-11-15), after ES-DE's: one large in the middle, the rest smaller to the sides.
"Next" and "back" move one game, "play this one" starts the one in the middle, and numbers still work."""
import os
from pathlib import Path
import sys
import unittest
from unittest import mock

os.environ["SDL_VIDEODRIVER"] = "dummy"
os.environ["SDL_AUDIODRIVER"] = "dummy"
ROOT = Path(os.environ.get("BEMO_TEST_CODE_ROOT", Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
import pygame
import visual_smoke_test as smoke
from systems import library as lib
from systems.chat import Chat
from systems.render import FaceRenderer

TITLES = ["Custom Robo", "The Legend of Zelda - Collector's Edition", "The Legend of Zelda - The Wind Waker",
          "The Legend of Zelda - Twilight Princess", "Super Smash Bros Melee"]


def games():
    view = lib.LibraryView()
    view.open("games", [lib.Item(t, "game", f"/roms/{n}.iso") for n, t in enumerate(TITLES)], carousel=True)
    return view


class Words(unittest.TestCase):
    def test_moving(self):
        for said in ("next", "right", "go right", "scroll right", "move right", "the next one", "next game", "swipe right", "one on the right"):
            self.assertEqual(lib.command(said), ("down", 0), said)
        for said in ("back", "left", "go left", "scroll left", "the previous one", "previous game", "the one on the left"):
            self.assertEqual(lib.command(said), ("up", 0), said)

    def test_this_one(self):
        for said in ("play this one", "play this", "this one", "play it", "that one", "start this game", "launch it", "the middle one",
                     "I'll play this one", "Play this one."):
            self.assertEqual(lib.command(said), ("here", 0), said)
        for said in ("this", "it", "that", "play"):
            self.assertNotEqual(lib.command(said), ("here", 0), said)
        self.assertEqual(lib.command("play number 3"), ("play", 3))


class OpeningIt(unittest.TestCase):
    def test_ways_to_ask(self):
        for said in ("show my games", "my games", "Hey BMO, show me my games", "bring up my games", "what games do I have", "what can I play",
                     "show me the game library", "game library", "my game library", "the game list", "games", "Games.", "the games", "game menu",
                     "play a game", "let's play a game", "I want to play a game", "can we play a game", "pick a game", "find me a game",
                     "game time", "it's game time", "show me what games we have", "gamecube games", "video games"):
            self.assertEqual(lib.open_request(said), "games", said)

    def test_not_these(self):
        for said in ("open games", "open the game library", "play Wind Waker", "close the game", "the game is broken", "what game is this",
                     "games are fun", "play a game of chess with me later", "game over"):
            self.assertNotEqual(lib.open_request(said), "games", said)


class ClosingIt(unittest.TestCase):
    def test_ways_to_close(self):
        for said in ("close the game library", "close the carousel", "hide my games", "put the games away", "get rid of the games",
                     "I'm done with the games", "close"):
            self.assertEqual(lib.command(said), ("close", 0), said)

    def chat(self, running=False):
        chat = Chat.__new__(Chat)
        chat.library_view = games()
        chat.note_user_activity = lambda: None
        chat._game_running = lambda: running
        return chat

    def test_it_closes_the_carousel(self):
        for said in ("close my games", "Close my games.", "close the games", "close games", "exit the games", "close the game", "quit my games"):
            chat = self.chat()
            self.assertTrue(chat._handle_library_command(said), said)
            self.assertFalse(chat.library_view.active, said)
            chat = self.chat()
            self.assertTrue(chat._bare_library_command(said), said)
            self.assertFalse(chat.library_view.active, said)

    def test_with_a_game_running_the_game_closes_not_the_screen(self):
        chat = self.chat(running=True)
        self.assertFalse(chat._closes_carousel("close the game"))

    def test_other_screens_dont_take_close_the_game(self):
        chat = self.chat()
        chat.library_view.open("weather", [], grid=False)
        self.assertFalse(chat._closes_carousel("close the game"))

    def test_a_running_game_is_still_closed_by_the_supervisor(self):
        from systems import media_phrases
        self.assertTrue(media_phrases.close_game("close my games"))  # with no carousel up, the game's own command


class TheView(unittest.TestCase):
    def test_one_at_a_time_with_neighbours(self):
        view = games()
        shot = view.snapshot()
        self.assertTrue(shot["carousel"])
        self.assertEqual([t["number"] for t in shot["tiles"]], [1])
        self.assertEqual([t["number"] for t in shot["nearby"]], [1, 2, 3, 4])
        view.scroll(1)
        view.scroll(1)
        self.assertEqual(view.snapshot()["first"], 3)
        self.assertEqual([t["number"] for t in view.snapshot()["nearby"]], [1, 2, 3, 4, 5])

    def test_other_lists_unchanged(self):
        view = lib.LibraryView()
        view.open("movies", [lib.Item(f"Movie {n}", "movie", f"/m/{n}.mkv") for n in range(12)], grid=True)
        self.assertEqual((view.per_page, view.snapshot()["carousel"], view.snapshot()["nearby"]), (lib.GRID_PAGE, False, []))


class InChat(unittest.TestCase):
    def setUp(self):
        self.chat = Chat.__new__(Chat)
        self.chat.library_view = games()
        self.chat.note_user_activity = lambda: None
        self.chat.supervisor = mock.Mock()
        self.chat.supervisor.launch_game.return_value = {"ok": True}
        self.said = []
        self.chat._say_local = lambda command, text: self.said.append(text) or True

    def test_play_the_one_in_the_middle(self):
        self.assertTrue(self.chat._handle_library_command("next"))
        self.assertTrue(self.chat._handle_library_command("play this one"))
        self.chat.supervisor.launch_game.assert_called_once_with("1")
        self.assertEqual(self.said[-1], "Launching The Legend of Zelda - Collector's Edition.")

    def test_without_hey_bmo(self):
        self.assertTrue(self.chat._bare_library_command("right"))
        self.assertTrue(self.chat._bare_library_command("play it"))
        self.chat.supervisor.launch_game.assert_called_once_with("1")

    def test_this_one_means_nothing_on_other_screens(self):
        self.chat.library_view.open("movies", [lib.Item("Ponyo", "movie", "/m/ponyo.mkv")], grid=False)
        self.chat._play_video = mock.Mock(return_value=True)
        self.assertFalse(self.chat._handle_library_command("play this one"))
        self.chat._play_video.assert_not_called()

    def test_opening_it(self):
        chat = self.chat
        chat.library_view = lib.LibraryView()
        with mock.patch.object(lib, "game_items", return_value=[lib.Item(t, "game", f"/roms/{n}.iso") for n, t in enumerate(TITLES)]):
            chat._open_library("show my games", "games")
        self.assertTrue(chat.library_view.carousel)
        self.assertIn("Say next or back to look, and play this one to start it.", self.said[-1])


class TheScreen(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        pygame.init()

    @classmethod
    def tearDownClass(cls):
        pygame.quit()

    def test_draws_at_every_size_and_count(self):
        for size in ((640, 480), (800, 600), (1024, 768), (1920, 1080)):
            for count in (1, 2, 5):
                app = smoke.FakeApp()
                app.SCREEN_WIDTH, app.SCREEN_HEIGHT = size
                app._screen = pygame.Surface(size)
                renderer = FaceRenderer(app)
                view = lib.LibraryView()
                view.open("games", [lib.Item(t, "game", f"/r/{n}.iso") for n, t in enumerate(TITLES[:count])], carousel=True)
                for step in range(count + 1):
                    for frame in range(5):
                        renderer._render_library(view.snapshot(), 10.0 + step + frame / 30)
                    view.scroll(1)

    def test_initials(self):
        self.assertEqual(FaceRenderer._game_initials("The Legend of Zelda - The Wind Waker"), "WW")
        self.assertEqual(FaceRenderer._game_initials("Custom Robo"), "CR")
        self.assertEqual(FaceRenderer._game_initials("Super Smash Bros Melee"), "SSB")
        self.assertEqual(FaceRenderer._game_initials(""), "?")


class TheController(unittest.TestCase):
    """2026-11-17: left and right (or up and down) move, A plays the one in the middle, B closes."""

    def events(self, *made):
        from systems.pad_input import PadNavigator
        pad = PadNavigator()
        return [pad.action(event) for event in made]

    def test_dpad_and_buttons(self):
        E = pygame.event.Event
        self.assertEqual(self.events(E(pygame.JOYHATMOTION, value=(1, 0)), E(pygame.JOYHATMOTION, value=(-1, 0)),
                                     E(pygame.JOYHATMOTION, value=(0, -1)), E(pygame.JOYHATMOTION, value=(0, 1)),
                                     E(pygame.JOYHATMOTION, value=(0, 0)), E(pygame.JOYBUTTONDOWN, button=0),
                                     E(pygame.JOYBUTTONDOWN, button=1), E(pygame.JOYBUTTONDOWN, button=3),
                                     E(pygame.JOYBUTTONDOWN, button=5)),
                         ["next", "previous", "next", "previous", None, "select", "back", "shuffle", None])

    def test_the_stick_counts_once_per_push(self):
        E = pygame.event.Event
        stick = [E(pygame.JOYAXISMOTION, axis=0, value=v, instance_id=0) for v in (0.2, 0.7, 0.9, 1.0, 0.1, 0.8, -0.1, -0.95)]
        self.assertEqual(self.events(*stick), [None, "next", None, None, None, "next", None, "previous"])

    def test_it_moves_and_plays(self):
        chat = Chat.__new__(Chat)
        chat.library_view = games()
        chat.note_user_activity = lambda: None
        chat.supervisor = mock.Mock()
        chat.supervisor.launch_game.return_value = {"ok": True}
        chat._say_local = lambda command, text: True
        self.assertTrue(chat.library_pad("next"))
        self.assertEqual(chat.library_view.snapshot()["first"], 2)
        with mock.patch("threading.Thread") as thread:
            self.assertTrue(chat.library_pad("select"))
            target, args = thread.call_args.kwargs["target"], thread.call_args.kwargs["args"]
        target(*args)
        chat.supervisor.launch_game.assert_called_once_with("1")
        chat.library_view = games()
        self.assertTrue(chat.library_pad("back"))
        self.assertFalse(chat.library_view.active)
        self.assertFalse(chat.library_pad("next"))  # nothing up: the controller is the game's

    def test_a_on_a_list_doesnt_guess(self):
        chat = Chat.__new__(Chat)
        chat.library_view = lib.LibraryView()
        chat.library_view.open("movies", [lib.Item("Ponyo", "movie", "/m/p.mkv")], grid=False)
        chat.note_user_activity = lambda: None
        self.assertFalse(chat.library_pad("select"))

    def test_the_window_passes_it_on(self):
        src = (ROOT / "app.py").read_text()
        self.assertIn("self.chat.library_pad(moved)", src)
        self.assertIn("view.active and not (setup is not None and setup.active)", src)


if __name__ == "__main__":
    unittest.main()
