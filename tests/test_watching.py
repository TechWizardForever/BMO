"""While a game or video is on, only BMO's own commands work and nothing goes to a brain (2026-10-20).
Live 2026-10-20 after "Hey BMO, play Smash Bros": the announcer, heard as "Thun away." and "Melee", came in
during the follow-up window and Main answered both, talking over the game."""
import ast
import os
from pathlib import Path
import re
import sys
import threading
import time
import unittest

ROOT = Path(os.environ.get("BEMO_TEST_CODE_ROOT", Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(ROOT))
import config

SOURCE = (ROOT / "systems" / "chat.py").read_text()
METHODS = {"_handle_one_request", "_game_or_video_open", "_open_answer_followup", "managed_display_active", "_player", "_watching_command",
           "_plain_words", "_WATCHING_COMMAND", "_PART_WORDS", "_3D_REQUEST", "_show_parts_menu"}


class Log:
    def __getattr__(self, name):
        return lambda *a, **k: None


def mini():
    chat = next(n for n in ast.parse(SOURCE).body if isinstance(n, ast.ClassDef) and n.name == "Chat")
    keep = [n for n in chat.body if (isinstance(n, ast.FunctionDef) and n.name in METHODS)
            or (isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") in METHODS)]
    ns = {"config": config, "re": re, "time": time, "logger": Log(), "SupervisorError": RuntimeError}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[ast.ClassDef(
        name="Mini", bases=[], keywords=[], body=keep, decorator_list=[])], type_ignores=[])), "m", "exec"), ns)
    ns["Mini"].on = _on
    m = ns["Mini"]()
    m._JUST_STARTED_SECONDS = 20.0
    m._managed_display_active, m._managed_display_cache_until = False, 0.0
    m.answered, m.said = [], []
    m._handle_local_command = lambda request: request in {"pause", "close the game", "what's the weather?",
                                                          "stop the show.", "turn the volume down"}
    m._didnt_catch_switch = lambda request: False
    m.respond = m.answered.append
    m._say_local = lambda request, line: m.said.append(line) or True
    m.stop_event = threading.Event()
    m._visual_lock = threading.Lock()
    m.voice_gate = type("Gate", (), {"followup_armed_until": 0.0, "wake": lambda self: None})()
    m.on(apps=(), video=False)
    return m


def _on(self, apps=(), video=False):
    self.supervisor = type("Sup", (), {"status": lambda s: {"apps": {name: {} for name in apps}}})()
    self._media_player = type("Player", (), {"bmo_player_open": lambda s: video})()


class WatchingTests(unittest.TestCase):
    def setUp(self):
        self.m = mini()

    def test_game_sounds_get_no_brain(self):
        self.m.on(apps=("game-session",))
        for heard in ("Thun away.", "Melee"):
            self.m._handle_one_request(heard)
        self.assertEqual(self.m.answered, [])
        self.assertEqual(self.m.said, ["I'll answer that once the game or video is off."] * 2)

    def test_game_and_video_commands_still_work(self):
        self.m.on(apps=("game-session",), video=True)
        self.m._handle_one_request("close the game")
        self.m._handle_one_request("pause")
        self.assertEqual((self.m.answered, self.m.said), ([], []))

    def test_only_game_video_sound_and_sleep_commands_during_a_game_or_show(self):
        # Live 2026-10-22: "what's the weather?" was answered by BMO's own weather command during Smash Bros
        # and during Jimmy Neutron. The rule: only game and video commands while one is on.
        for apps, video in ((("game-session",), False), ((), True)):
            self.m.on(apps=apps, video=video)
            self.m.said.clear()
            for heard in ("what's the weather?", "what's the weather? Hey Bemo what's the weather?", "what time is it?"):
                self.m._handle_one_request(heard)
            self.assertEqual(self.m.said, ["I'll answer that once the game or video is off."] * 3)
            self.m.said.clear()
            for heard in ("close the game", "stop the show.", "pause", "turn the volume down"):
                self.m._handle_one_request(heard)
            self.assertEqual(self.m.said, [])

    def test_restarting_the_movie_is_a_video_command(self):
        # 2026-10-28: "restart" / "start over" / "from the beginning" while a movie plays.
        for heard in ("restart", "restart it", "start over", "go back to the beginning", "play it from the beginning"):
            self.assertTrue(self.m._WATCHING_COMMAND.search(self.m._plain_words(heard)), heard)

    def test_an_open_video_counts(self):
        self.m.on(video=True)
        self.m._handle_one_request("what's the weather")
        self.assertEqual(self.m.answered, [])

    def test_a_browser_tab_is_not_bmos_movie(self):
        # Live 2026-10-21: with Chrome open (org.mpris.MediaPlayer2.chromium.instance101192), "make a 20mm cube
        # with a 5mm hole through the middle." got "I'll answer that once the game or video is off."
        from systems import media
        names = "('org.freedesktop.DBus', 'org.mpris.MediaPlayer2.chromium.instance101192')"
        run = lambda argv, **kw: __import__("subprocess").CompletedProcess(argv, 0, names, "")
        self.assertFalse(media.Player(run=run).bmo_player_open())
        names = "('org.mpris.MediaPlayer2.chromium.instance1', 'org.mpris.MediaPlayer2.io.github.celluloid_player.Celluloid')"
        self.assertTrue(media.Player(run=run).bmo_player_open())

    def test_the_seconds_before_the_player_shows_up_count(self):
        self.m._watch_started_at = time.monotonic()
        self.m._handle_one_request("Thun away.")
        self.assertEqual(self.m.answered, [])

    def test_with_nothing_on_the_brain_answers(self):
        self.m._watch_started_at = time.monotonic() - 60
        self.m._handle_one_request("what's the weather")
        self.assertEqual(self.m.answered, ["what's the weather"])

    def test_no_follow_up_without_hey_bmo_during_a_game(self):
        self.m.on(apps=("game-session",))
        self.m._open_answer_followup()
        self.assertFalse(getattr(self.m, "_answer_followup_open", False))
        self.m.on()
        self.m._open_answer_followup()
        self.assertTrue(self.m._answer_followup_open)

    def test_closing_clears_the_just_started_time(self):
        self.assertEqual(SOURCE.count("self._watch_started_at = None"), 2)
        self.assertIn("self.supervisor.close_game()\n                self._watch_started_at = None", SOURCE)


if __name__ == "__main__":
    unittest.main()
