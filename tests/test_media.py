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
        self.assertLess(chat.index("if gated_prompt is None and self._bare_player_command(prompt):"),
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


if __name__ == "__main__":
    unittest.main()
