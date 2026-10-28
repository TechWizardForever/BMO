"""Many ways to say the movie, show, game and library commands (owner 2026-10-28: "can we add a substantial amount of
similar phrases so I don't have to say just one exact one"). Every line is something a person might say; the
mishearings are from BMO's own logs."""
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(os.environ.get("BEMO_TEST_CODE_ROOT", Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(ROOT))
from systems import library as lib
from systems import media as m
from systems import media_phrases as mp

MOVIES = ["Spirited.Away.2001.1080p.BDRip.x265.mkv", "Howl's.Moving.Castle.2004.1080p.BDRip.x265.mkv",
          "My.Neighbor.Totoro.1988.1080p.BDRip.x265.mkv", "Ponyo.2008.1080p.BDRip.x265.mkv"]
SHOW = "The Adventures of Jimmy Neutron Boy Genius"


def videos():
    root = Path(tempfile.mkdtemp())
    for name in MOVIES:
        path = root / "Movies" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")
    for season, eps in ((1, (1, 2, 3)), (2, (1, 2))):
        for ep in eps:
            path = root / "TV Shows" / "Jimmy" / f"Season {season}" / f"{SHOW} - S0{season}E0{ep} - Title {ep}.mkv"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"x")
    return root


class CleanTests(unittest.TestCase):
    def test_what_is_left_to_match(self):
        for heard, words in (
                ("Hey BMO, can you please pause the movie for me?", "pause the movie"),
                ("Heed Bemo play Arietti", "play arietti"),                  # live 2026-10-28
                ("Hibemo, stop the show.", "stop the show"),                # live 2026-10-20
                ("Clothes", "close"),                                       # live 2026-10-28
                ("Say number one.", "play number one"),                     # live 2026-10-27
                ("post the game!", "close the game"),                       # live 2026-10-20
                ("close the cane", "close the game"),                       # live 2026-10-22
                ("Um, okay so, I want to watch Ponyo please", "watch ponyo"),
                ("Could you put on Spirited Away for me right now", "put on spirited away"),
                ("Let's watch Totoro", "watch totoro"),
                ("I'd like to watch Howl's Moving Castle", "watch howls moving castle"),
                ("Go ahead and play Jimmy Neutron", "play jimmy neutron"),
                ("Would you mind skipping ahead?", "skip ahead")):
            self.assertEqual(mp.clean(heard), words, heard)

    def test_a_stray_first_word_is_a_second_try(self):
        self.assertEqual(mp.variants("Remonk Show Me My Movies"), ["remonk show me my movies", "show me my movies"])
        self.assertEqual(mp.variants("jump back half a minute"), ["jump back half a minute"])  # jump is no stray

    def test_a_meaningful_first_word_stays(self):
        self.assertEqual(mp.clean("Don't play the movie"), "dont play the movie")
        self.assertEqual(mp.clean("Why stop the show"), "why stop the show")


class PlayerTests(unittest.TestCase):
    """While a movie or show is on (with Hey BMO)."""
    WAYS = {
        "Pause": ("pause", "pause it", "pause the movie", "Pause the show please", "hold on", "hold on a second",
                  "wait", "wait a minute", "one second", "hang on", "freeze", "give me a second",
                  "stop for a second", "can you pause it please", "paws", "pause for a second okay"),
        "Play": ("resume", "keep going", "keep watching", "unpause", "continue", "carry on", "play it", "press play",
                 "go ahead", "start it again", "resume the movie", "keep playing"),
        "Stop": ("stop", "stop it", "stop the movie", "turn it off", "turn the movie off", "shut it off", "kill it",
                 "I'm done watching", "we're done", "that's enough", "no more tv", "close the player", "quit",
                 "exit", "stop watching", "end the show", "close it", "stops the show.", "We must stop the movie."),
        "Next": ("next", "next episode", "next one", "skip this episode", "play the next episode",
                 "go to the next one", "another one", "move on", "skip to the next episode"),
        "Previous": ("previous", "previous episode", "last episode", "go back an episode", "the one before",
                     "play the previous one"),
        "Restart": ("restart", "restart it", "restart the movie", "start over", "start it over", "from the top",
                    "back to the beginning", "start the movie from the beginning", "begin again",
                    "rewind to the start", "take it back to the beginning"),
    }

    def test_many_ways(self):
        for method, phrases in self.WAYS.items():
            for said in phrases:
                self.assertEqual(m.control_command(said), (method, 0.0), said)

    def test_skipping_and_going_back(self):
        for said, seconds in (("skip", 30), ("skip ahead", 30), ("fast forward", 30), ("jump ahead a bit", 30),
                              ("rewind", -10), ("go back", -10), ("what did he say", -10), ("I missed that", -10),
                              ("say that again", -10), ("back up a bit", -10),
                              ("skip ahead 5 minutes", 300), ("fast forward two minutes", 120),
                              ("go back thirty seconds", -30), ("rewind a minute", -60),
                              ("jump back half a minute", -30), ("skip forward twenty five seconds", 25),
                              ("go back a couple of minutes", -120), ("skip 10 minutes", 600),
                              ("skip a few minutes ahead", 180), ("rewind 15 seconds", -15)):
            self.assertEqual(m.control_command(said), ("Seek", float(seconds)), said)

    def test_not_player_commands(self):
        for said in ("what's next", "stop talking", "play spirited away", "what did you say", "pause the music in "
                     "spotify and open firefox", "next week is my birthday"):
            self.assertIsNone(m.control_command(said), said)


class PlayTests(unittest.TestCase):
    def setUp(self):
        self.lib = m.library([videos()])

    def found(self, said):
        wanted = m.play_request(said)
        video = m.find(wanted, self.lib) if wanted else None
        return video.spoken if video else None

    def test_ways_to_ask_for_a_title(self):
        for said, spoken in (
                ("I want to watch Spirited Away", "Spirited Away"),
                ("Can you put on Totoro for me", "My Neighbor Totoro"),
                ("Let's watch Howl's Moving Castle", "Howl's Moving Castle"),
                ("Pull up Ponyo", "Ponyo"), ("throw on ponyo", "Ponyo"), ("queue up totoro", "My Neighbor Totoro"),
                ("play spirited away on the tv", "Spirited Away"),
                ("watch Spirited Away from where I left off", "Spirited Away"),
                ("I'd like to watch the first episode of Jimmy Neutron", "Jimmy Neutron, season 1, episode 1"),
                ("watch Jimmy Neutron season two episode one", "Jimmy Neutron, season 2, episode 1"),
                ("play jimmy neutron s2e2", "Jimmy Neutron, season 2, episode 2"),
                ("continue Jimmy Neutron", "Jimmy Neutron, season 1, episode 1"),
                ("Hey BMO, could you play the movie Ponyo please", "Ponyo"),
                ("Well Play Jimmy Neutron Season 1 Episode 1", "Jimmy Neutron, season 1, episode 1")):
            self.assertEqual(self.found(said), spoken, said)

    def test_a_title_must_be_there(self):
        for said in ("open firefox", "turn on the lights", "resume task", "start a timer", "show me the shapes"):
            self.assertIsNone(self.found(said), said)

    def test_carrying_on(self):
        for said in ("continue watching", "We will continue watching.", "we should keep watching",  # live 2026-10-28
                     "I'll continue watching", "keep watching", "pick up where I left off", "resume my show",
                     "go back to watching", "Hey BMO, can we continue watching?", "play where we left off"):
            self.assertTrue(mp.continue_watching(said), said)
        self.assertFalse(mp.continue_watching("continue Jimmy Neutron"))


class GameTests(unittest.TestCase):
    def test_ways_to_start_a_game(self):
        for said, expected in (("play Zelda", ("zelda", True)), ("let's play Smash Bros", ("smash bros", True)),
                               ("I wanna play some Melee", ("melee", True)),
                               ("launch the game Zelda", ("zelda", True)), ("fire up Custom Robo", ("custom robo", False)),
                               ("start Custom Robo", ("custom robo", False)),
                               ("boot up zelda on the gamecube", ("zelda", False)),
                               ("can we play a round of smash bros", ("smash bros", True)),
                               ("open firefox", ("firefox", False))):   # an app unless it's one of BMO's games
            self.assertEqual(mp.game_request(said), expected, said)
        for said in ("play games", "boot games", "open the game library", "play"):
            self.assertIsNone(mp.game_request(said), said)

    def test_ways_to_close_a_game(self):
        for said in ("close the game", "quit the game", "exit the game", "end the game", "turn off the game",
                     "stop playing", "I'm done playing", "turn the game off", "no more games", "post the game!",
                     "close the cane", "clothes the game", "kill the game", "game over"):
            self.assertTrue(mp.close_game(said), said)
        for said in ("close", "close again", "Close!", "quit", "close it", "that's enough", "I'm done"):
            self.assertFalse(mp.close_game(said), said)            # not when nothing is on
            self.assertTrue(mp.close_game(said, game_on=True), said)  # live 2026-10-22, 10-28
        self.assertFalse(mp.close_game("close the movie", game_on=True))


class LibraryTests(unittest.TestCase):
    def test_ways_to_open_one(self):
        for said, kind in (("what can I watch", "movies"), ("what can I play", "games"),
                           ("what movies do we have", "movies"), ("what games are there", "games"),
                           ("let me see my games", "games"), ("my movies", "movies"), ("browse my movies", "movies"),
                           ("list my games", "games"), ("show me all my movies", "movies"),
                           ("what's there to watch", "movies"), ("bring up the movie list", "movies"),
                           ("Remonk Show Me My Movies", "movies"), ("show me what games we have", None)):
            self.assertEqual(lib.open_request(said), kind, said)
        for said in ("list games", "open games", "open the game library"):  # the spoken count and ES-DE keep theirs
            self.assertIsNone(lib.open_request(said), said)

    def test_ways_to_move_and_pick(self):
        for said, expected in (("next", ("down", 0)), ("more", ("down", 0)), ("what else is there", ("down", 0)),
                               ("next page", ("down", 0)), ("keep going", ("down", 0)), ("go down", ("down", 0)),
                               ("back", ("up", 0)), ("previous page", ("up", 0)), ("go up", ("up", 0)),
                               ("first page", ("top", 0)), ("last page", ("bottom", 0)), ("the end", ("bottom", 0)),
                               ("the third one", ("play", 3)), ("I'll take five", ("play", 5)), ("three", ("play", 3)),
                               ("number twelve please", ("play", 12)), ("go with four", ("play", 4)),
                               ("the twenty first one", ("play", 21)), ("21st", ("play", 21)),
                               ("play twenty one", ("play", 21)), ("let's do number 2", ("play", 2)),
                               ("nothing", ("close", 0)), ("never mind", ("close", 0)),
                               ("back to your face", ("close", 0)), ("hide it", ("close", 0)),
                               ("that's all", ("close", 0)), ("close the movie list", ("close", 0))):
            self.assertEqual(lib.command(said), expected, said)
        for said in ("one second", "what's the weather", "play spirited away"):
            self.assertIsNone(lib.command(said), said)


class ChatTests(unittest.TestCase):
    def setUp(self):
        import config
        from systems.chat import Chat
        self.chat = Chat.__new__(Chat)
        self.said, self.played, self.launched = [], [], []
        self.chat._say_local = lambda command, text: self.said.append(text) or True
        self.chat._play_video = lambda command, wanted=None, **kw: self.played.append(kw or wanted) or True
        self.state = Path(tempfile.mkdtemp()) / "media.json"
        patcher = mock.patch.object(config, "MEDIA_STATE_PATH", self.state)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_continue_watching_plays_the_last_thing_on(self):
        self.assertTrue(self.chat._continue_watching("continue watching"))
        self.assertIn("I don't know what you were watching", self.said[-1])
        root = videos()
        episode = next((root / "TV Shows").rglob("*S01E02*"))
        m.LastPlayed(self.state).watched(episode, 100.0, 1300.0)
        self.chat._continue_watching("continue watching")
        self.assertEqual(self.played[-1], {"show": SHOW})
        movie = root / "Movies" / MOVIES[0]
        m.LastPlayed(self.state).remember(m.describe(movie))
        self.chat._continue_watching("pick up where I left off")
        self.assertEqual(self.played[-1], {"path": str(movie)})

    def test_wired_in(self):
        chat = (ROOT / "systems" / "chat.py").read_text()
        self.assertIn("media_phrases.continue_watching(command)", chat)
        self.assertIn("game = media_phrases.game_request(command)", chat)
        self.assertIn("if self._watching_command(request) and self._handle_local_command(request):", chat)


if __name__ == "__main__":
    unittest.main()
