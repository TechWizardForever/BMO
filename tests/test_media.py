"""Movies and shows by voice (2026-10-20), with a fake Videos folder and a fake MPRIS connection."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(os.environ.get("BEMO_TEST_CODE_ROOT", Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(ROOT))
from systems import media as m

MOVIES = ["Spirited.Away.2001.1080p.BDRip.Dual.Audio.AAC5.1.10bits.x265-Rapta.mkv",
          "Howl's.Moving.Castle.2004.1080p.BDRip.Dual.Audio.AAC5.1.10bits.x265-Rapta.mkv",
          "My.Neighbor.Totoro.1988.1080p.BDRip.Dual.Audio.10bits.x265-Rapta.mkv",
          "Lupin.III.The.Castle.of.Cagliostro.1979.1080p.BDRip.Dual.Audio.10bits.x265-Rapta.mkv"]
SHOW = "The Adventures of Jimmy Neutron Boy Genius"
EPISODES = {"Season 1": ["S01E01 - When Pants Attack", "S01E02-E03 - Normal Boy & Birth of a Salesman",
                         "S01E04 - Brobot"],
            "Season 2": ["S02E01 - Krunch Time", "S02E02-E03 - Sheen's Brain & Out, Darn Spotlight"],
            "Specials": ["S00E01 - Pilot - Runaway Rocketboy!"]}


class MediaTests(unittest.TestCase):
    def setUp(self):
        self.videos = Path(tempfile.mkdtemp())
        (self.videos / "Movies" / "Studio Ghibli").mkdir(parents=True)
        for name in MOVIES:
            (self.videos / "Movies" / "Studio Ghibli" / name).write_bytes(b"x")
        for folder, names in EPISODES.items():
            (self.videos / "TV Shows" / "Jimmy" / folder).mkdir(parents=True)
            for name in names:
                base = self.videos / "TV Shows" / "Jimmy" / folder / f"{SHOW} - {name} (480p x265 EDGE2020)"
                base.with_suffix(".mkv").write_bytes(b"x")
                Path(str(base) + ".eng.srt").write_text("1")
        self.lib = m.library([self.videos])

    def test_the_library_reads_titles_and_episodes(self):
        self.assertEqual(len(self.lib), len(MOVIES) + 6)  # subtitles aren't videos
        titles = {v.title for v in self.lib if not v.show}
        self.assertIn("Howl's Moving Castle", titles)
        self.assertIn("Lupin III The Castle of Cagliostro", titles)
        double = next(v for v in self.lib if v.episodes == (2, 3) and v.season == 1)
        self.assertEqual(double.spoken, "Jimmy Neutron, season 1, episodes 2 and 3")

    def test_finding_what_was_said(self):
        for said, title in (("spirited away", "Spirited Away"), ("howls moving castle", "Howl's Moving Castle"),
                            ("totoro", "My Neighbor Totoro"), ("the castle of cagliostro", "Lupin III The Castle of Cagliostro")):
            self.assertEqual(m.find(said, self.lib).title, title, said)
        self.assertIsNone(m.find("mario kart", self.lib))  # a game: left for "play <game>"

    def test_episodes_by_number_in_words_or_digits(self):
        self.assertEqual(m.find("jimmy neutron season two episode one", self.lib).spoken,
                         "Jimmy Neutron, season 2, episode 1")
        self.assertEqual(m.find("jimmy neutron season 1 episode 3", self.lib).episodes, (2, 3))
        self.assertIsNone(m.find("jimmy neutron season 9 episode 1", self.lib))

    def test_a_show_carries_on_where_it_left_off(self):
        self.assertEqual(m.find("jimmy neutron", self.lib).spoken, "Jimmy Neutron, season 1, episode 1")
        memory = m.LastPlayed(self.videos / "media.json")
        memory.remember(m.find("jimmy neutron season 1 episode 4", self.lib))
        self.assertEqual(m.find("jimmy neutron", self.lib, memory.load()).spoken, "Jimmy Neutron, season 2, episode 1")

    def test_an_episode_queues_the_rest_of_its_season(self):
        queue = m.queue_for(m.find("jimmy neutron season 1 episode 2", self.lib), self.lib)
        self.assertEqual([p.name.split(" - ")[1] for p in queue], ["S01E02-E03", "S01E04"])
        self.assertEqual(len(m.queue_for(m.find("totoro", self.lib), self.lib)), 1)

    def test_player_commands(self):
        for said, expected in (("pause", ("Pause", 0.0)), ("pause the movie", ("Pause", 0.0)), ("Resume.", ("Play", 0.0)),
                               ("next episode", ("Next", 0.0)), ("skip ahead 30 seconds", ("Seek", 30.0)),
                               ("go back two minutes", ("Seek", -120.0)), ("rewind 10 seconds", ("Seek", -10.0)),
                               ("stop the movie", ("Stop", 0.0))):
            self.assertEqual(m.control_command(said), expected, said)
        for said in ("pause for a second okay", "play spirited away", "what's next", "stop talking"):
            self.assertIsNone(m.control_command(said), said)

    def test_the_owners_phrases_from_the_live_test(self):
        # Live 2026-10-20: these went to Main instead of the player.
        titles = {"Jimmy Neutron", "Spirited Away"}
        for said, expected in (("Skip", ("Seek", 30.0)), ("Hibemo, stop the show.", ("Stop", 0.0)),
                               ("stops the show.", ("Stop", 0.0)), ("We must stop the movie.", ("Stop", 0.0)),
                               ("stop Jimmy Neutron", ("Stop", 0.0)), ("Clothes Jimi Neutron", ("Stop", 0.0)),
                               ("turn it off", ("Stop", 0.0)), ("stop", ("Stop", 0.0)), ("rewind", ("Seek", -10.0)),
                               ("next", ("Next", 0.0))):
            self.assertEqual(m.control_command(said, titles), expected, said)
        self.assertIsNone(m.control_command("stop Mario Kart", titles))
        self.assertEqual(m.find("the first episode of Jimmy Neutron", self.lib).spoken,
                         "Jimmy Neutron, season 1, episode 1")
        self.assertEqual(m.find("episode 1 of season 2 of jimmy neutron", self.lib).spoken,
                         "Jimmy Neutron, season 2, episode 1")
        self.assertEqual(m.find("season 2 of jimmy neutron", self.lib).season, 2)
        self.assertEqual(m.play_request("Play Spirited Away."), "spirited away")
        self.assertIsNone(m.play_request("what's playing"))


class BareCommandTests(unittest.TestCase):
    def test_pause_and_play_without_hey_bmo_only_when_they_make_sense(self):
        # 2026-10-20: pause/play took a few tries; these two now work without Hey BMO during a video.
        self.assertEqual(m.bare_command("Pause", "Playing"), "Pause")
        self.assertEqual(m.bare_command("Play?", "Paused"), "Play")
        self.assertEqual(m.bare_command("resume the movie", "Paused"), "Play")
        self.assertIsNone(m.bare_command("Pause", "Paused"))
        self.assertIsNone(m.bare_command("Play", "Playing"))   # the movie's own dialogue can't restart it
        self.assertIsNone(m.bare_command("Stop!", "Playing"))  # closing still needs Hey BMO
        self.assertIsNone(m.bare_command("Pause", None))       # no player open

    def test_misheard_wake_before_a_command(self):
        self.assertEqual(m.control_command("He be my pause."), ("Pause", 0.0))

    def test_checked_before_speech_without_hey_bmo_is_ignored(self):
        chat = (ROOT / "systems" / "chat.py").read_text()
        self.assertLess(chat.index("if gated_prompt is None and (self._bare_player_command(prompt)"),
                        chat.index('logger.trace("Ignored speech without Hey Bemo prefix'))


class FakeVolumePlayer:
    def __init__(self):
        self.level, self.sets = 0.8, []

    def volume(self):
        return self.level

    def set_volume(self, value):
        self.sets.append(round(value, 3))
        self.level = value
        return True


class DuckerTests(unittest.TestCase):
    """2026-10-20: turn the movie down while I'm speaking to him."""

    def test_hey_bmo_turns_the_movie_down_and_restore_puts_it_back(self):
        import numpy as np
        player = FakeVolumePlayer()
        scores = iter([0.1, 0.2, 0.9] + [0.0] * 50)
        ducker = m.Ducker(player, lambda frame: next(scores))
        ducker.feed(np.zeros(1280 * 2, dtype="<i2").tobytes(), 16000, 2)
        self.assertFalse(ducker.ducked)
        ducker.feed(np.zeros(1280, dtype="<i2").tobytes(), 16000, 2)
        self.assertTrue(ducker.ducked)
        self.assertEqual(player.sets, [0.2])  # a quarter of 0.8
        ducker.feed(np.zeros(1280 * 3, dtype="<i2").tobytes(), 16000, 2)
        self.assertEqual(player.sets, [0.2])  # once per turn
        ducker.restore()
        self.assertEqual(player.sets, [0.2, 0.8])
        self.assertFalse(ducker.ducked)

    def test_movie_dialogue_alone_does_not_duck(self):
        import numpy as np
        player = FakeVolumePlayer()
        ducker = m.Ducker(player, lambda frame: 0.1)
        ducker.feed(np.zeros(16000, dtype="<i2").tobytes(), 16000, 2)
        self.assertEqual(player.sets, [])

    def test_other_sample_rates_are_converted(self):
        import numpy as np
        frames = []
        ducker = m.Ducker(FakeVolumePlayer(), lambda frame: frames.append(len(frame)) or 0.0)
        ducker.feed(np.zeros(48000, dtype="<i2").tobytes(), 48000, 2)  # one second at 48 kHz
        self.assertEqual(frames, [1280] * 12)

    def test_after_a_bare_hey_bmo_the_movie_stays_down_for_the_command(self):
        chat = (ROOT / "systems" / "chat.py").read_text()
        self.assertIn("self._keep_movie_ducked = True", chat)
        self.assertIn("if ducker and keep and ducker.ducked:", chat)

    def test_wired_into_listening(self):
        chat = (ROOT / "systems" / "chat.py").read_text()
        self.assertIn("endpointer.on_audio = ducker.feed if ducker is not None else None", chat)
        self.assertIn("self.on_audio(b\"\".join(step), rate, width)", (ROOT / "systems" / "vad_capture.py").read_text())


class PlayerTests(unittest.TestCase):
    def test_playback_status(self):
        def run(argv, **kwargs):
            out = ("(['org.mpris.MediaPlayer2.celluloid'],)" if "ListNames" in argv[-1] else "(<'Paused'>,)")
            return subprocess.CompletedProcess(argv, 0, out, "")
        self.assertEqual(m.Player(run=run).status(), "Paused")

    def test_mpris_calls(self):
        calls, started = [], []

        def run(argv, **kwargs):
            calls.append(argv)
            out = "(['org.freedesktop.DBus', 'org.mpris.MediaPlayer2.io.github.celluloid_player.Celluloid'],)\n"
            return subprocess.CompletedProcess(argv, 0, out if "ListNames" in argv[-1] else "()", "")
        player = m.Player(run=run, popen=lambda argv, **k: started.append(argv))
        self.assertTrue(player.running())
        self.assertTrue(player.seek(-30))
        self.assertIn("-30000000", calls[-1])
        self.assertIn("org.mpris.MediaPlayer2.Player.Seek", calls[-1])
        player.play_files([Path("/v/a.mkv")])
        self.assertEqual(started[0][:4], ["celluloid", "--mpv-fs=yes", "--mpv-alang=eng,en,english", "--mpv-sid=no"])
        nothing = m.Player(run=lambda argv, **k: subprocess.CompletedProcess(argv, 0, "(['org.freedesktop.DBus'],)", ""))
        self.assertFalse(nothing.running())
        self.assertFalse(nothing.control("Pause"))

    def test_wired_before_the_games(self):
        chat = (ROOT / "systems" / "chat.py").read_text()
        self.assertLess(chat.index("if self._handle_media_command(command):"),
                        chat.index("if self._handle_supervisor_command(command):"))



class ResumeTests(unittest.TestCase):
    """2026-10-28: a movie picks up where it was stopped; "restart" starts it over; a show carries on from the
    episode last watched (that one again if it was stopped part way, else the next), unless one is named."""
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.later = self.root / "watch-later"
        self.later.mkdir()
        MediaTests.setUp(self)

    def save(self, path, seconds):
        import hashlib
        (self.later / hashlib.md5(str(path).encode()).hexdigest().upper()).write_text(f"start={seconds:.6f}\n")

    def test_the_players_saved_place_is_read_and_forgotten(self):
        movie = m.find("spirited away", self.lib).path
        self.assertIsNone(m.saved_position(movie, self.later))
        self.save(movie, 4321.5)
        self.assertEqual(m.saved_position(movie, self.later), 4321.5)
        m.forget_position(movie, self.later)
        self.assertIsNone(m.saved_position(movie, self.later))
        self.assertIsNone(m.saved_position(movie, None))

    def test_near_the_end_and_spoken_times(self):
        self.assertTrue(m.near_end(7100, 7200))      # in the credits of a 2-hour movie
        self.assertFalse(m.near_end(4000, 7200))
        self.assertFalse(m.near_end(4000, None))     # length unknown: keep the place
        self.assertEqual(m.spoken_time(4320), "1 hour 12 minutes")
        self.assertEqual(m.spoken_time(360), "6 minutes")
        self.assertEqual(m.spoken_time(7200), "2 hours")

    def test_restart_phrases(self):
        for said in ("restart", "restart the movie", "Hey BMO, restart it", "start over", "start it over",
                     "go back to the beginning", "play it from the start", "start the movie from the beginning",
                     "from the beginning"):
            self.assertEqual(m.control_command(said), ("Restart", 0.0), said)
        self.assertIsNone(m.control_command("play spirited away from the beginning"))  # a new play, below
        for said, title in (("restart Spirited Away", "spirited away"),
                            ("play Spirited Away from the beginning", "spirited away"),
                            ("start Ponyo over", "ponyo"), ("watch totoro again from the start", "totoro")):
            self.assertEqual(m.play_request(said), title, said)
            self.assertTrue(m.from_start(said), said)
        self.assertFalse(m.from_start("play Spirited Away"))
        self.assertEqual(m.find(m.play_request("restart howls moving castle"), self.lib).title, "Howl's Moving Castle")

    def test_a_show_carries_on_from_the_episode_last_watched(self):
        memory = m.LastPlayed(self.root / "media.json")
        brobot = m.find("jimmy neutron season 1 episode 4", self.lib)
        memory.watched(brobot.path, 300.0, 1320.0)  # the player moved on to it by itself, and it was closed
        resumable = lambda path: m.saved_position(path, self.later) is not None
        self.assertEqual(m.find("jimmy neutron", self.lib, memory.load(), resumable).spoken,
                         "Jimmy Neutron, season 2, episode 1")   # no saved place: it was watched to the end
        self.save(brobot.path, 300.0)
        self.assertEqual(m.find("jimmy neutron", self.lib, memory.load(), resumable), brobot)  # stopped part way
        self.assertEqual(m.find("jimmy neutron season 2 episode 1", self.lib, memory.load(), resumable).spoken,
                         "Jimmy Neutron, season 2, episode 1")   # a named episode wins
        episodes = m.episodes_of(brobot.show, self.lib)
        self.assertEqual(m.show_episode(episodes, memory.load(), resumable), brobot)

    def test_the_memory_keeps_lengths_and_older_files_still_load(self):
        path = self.root / "media.json"
        path.write_text('{"last_played": {"Old Show": "/v/old.mkv"}}')
        memory = m.LastPlayed(path)
        movie = m.find("totoro", self.lib).path
        memory.watched(movie, 10.0, 5160.0)
        memory.remember(m.find("jimmy neutron season 1 episode 4", self.lib))
        self.assertEqual(memory.length(movie), 5160.0)
        self.assertEqual(memory.load()["Old Show"], "/v/old.mkv")
        self.assertIn(SHOW, memory.load())

    def test_watch_follows_the_player_until_it_closes(self):
        first, second = (m.find(f"jimmy neutron season 2 episode {n}", self.lib).path for n in (1, 2))

        class Fake:
            def __init__(self):
                self.script = [None, (first, 5.0, 1300.0), (first, 900.0, 1300.0), (second, 3.0, 1310.0), None]
            def now_playing(self):
                return self.script.pop(0) if self.script else None
            def running(self):
                return bool(self.script)
        memory = m.LastPlayed(self.root / "media.json")
        m.watch(Fake(), memory, sleep=lambda s: None)
        self.assertEqual(memory.load()[SHOW], str(second))  # the player moved on by itself
        self.assertEqual(memory.length(first), 1300.0)
        stopped = []
        m.watch(Fake(), memory, sleep=lambda s: None, stop=lambda: stopped.append(1) or len(stopped) > 2)
        self.assertEqual(len(stopped), 3)  # a newer play took over
        never = type("Never", (), {"now_playing": lambda self: None, "running": lambda self: False})()
        m.watch(never, memory, sleep=lambda s: None, every=5.0, wait_for=60.0)  # gives up, doesn't hang

    def test_the_player_saves_its_place_and_restart_goes_to_the_start(self):
        calls, started = [], []
        meta = ("(<{'xesam:url': <'file:///v/My%20Movie.mkv'>, 'mpris:length': <int64 120023219>, "
                "'mpris:trackid': <objectpath '/io/github/CelluloidPlayer/Celluloid/Track/0'>}>,)")

        def run(argv, **kwargs):
            calls.append(argv)
            if "ListNames" in argv[-1]:
                out = "(['org.mpris.MediaPlayer2.io.github.celluloid_player.Celluloid'],)"
            elif argv[-1] == "Metadata":
                out = meta
            elif argv[-1] == "Position":
                out = "(<int64 51480000>,)"
            else:
                out = "()"
            return subprocess.CompletedProcess(argv, 0, out, "")
        player = m.Player(run=run, popen=lambda argv, **k: started.append(argv), watch_later=self.later)
        player.play_files([Path("/v/a.mkv")])
        player.play_files([Path("/v/a.mkv")], from_start=True)
        self.assertIn("--mpv-save-position-on-quit=yes", started[0])
        self.assertIn(f"--mpv-watch-later-directory={self.later}", started[0])
        self.assertNotIn("--mpv-resume-playback=no", started[0])
        self.assertIn("--mpv-resume-playback=no", started[1])
        self.assertEqual(player.now_playing(), (Path("/v/My Movie.mkv"), 51.48, 120.023219))
        self.assertTrue(player.restart())
        self.assertEqual(calls[-1][-3:], ["org.mpris.MediaPlayer2.Player.SetPosition",
                                          "objectpath '/io/github/CelluloidPlayer/Celluloid/Track/0'", "int64 0"])


class PlayVideoTests(unittest.TestCase):
    """The chat side: resume with the time said, the credits count as finished, restart forgets the place."""
    def setUp(self):
        from unittest import mock
        from systems.chat import Chat
        ResumeTests.setUp(self)
        self.chat = Chat.__new__(Chat)
        self.said, self.played = [], []
        self.chat._say_local = lambda command, text: self.said.append(text) or True
        self.chat._start_media_watch = lambda player, memory: None
        self.chat._media_player = type("P", (), {"play_files": lambda s, q, from_start=False:
                                                 self.played.append((q, from_start))})()
        import config
        for name, value in (("MEDIA_DIRS", [self.videos]), ("MEDIA_STATE_PATH", self.root / "media.json"),
                            ("MEDIA_WATCH_LATER_DIR", self.later)):
            patcher = mock.patch.object(config, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    save = ResumeTests.save

    def test_resume_restart_and_the_credits(self):
        movie = m.find("spirited away", self.lib).path
        self.save(movie, 4330.0)
        self.assertTrue(self.chat._play_video("play spirited away", "spirited away"))
        self.assertEqual(self.said[-1], "Playing Spirited Away, from 1 hour 12 minutes in.")
        self.assertEqual(self.played[-1], ([movie], False))
        self.assertTrue(self.chat._play_video("restart spirited away", "spirited away", from_start=True))
        self.assertEqual(self.said[-1], "Playing Spirited Away from the beginning.")
        self.assertEqual(self.played[-1], ([movie], True))
        self.assertIsNone(m.saved_position(movie, self.later))
        m.LastPlayed(self.root / "media.json").watched(movie, 7000.0, 7480.0)
        self.save(movie, 7400.0)  # closed in the credits
        self.chat._play_video("play spirited away", "spirited away")
        self.assertEqual(self.said[-1], "Playing Spirited Away.")
        self.assertIsNone(m.saved_position(movie, self.later))
        self.assertFalse(self.chat._play_video("play mario kart", "mario kart"))  # left for the games

    def test_a_whole_show_from_the_library(self):
        self.assertTrue(self.chat._play_video("play number 2", show=SHOW))
        self.assertEqual(self.said[-1], "Playing Jimmy Neutron, season 1, episode 1.")


if __name__ == "__main__":
    unittest.main()
