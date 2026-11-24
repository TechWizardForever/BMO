"""Which command each sentence reaches, in each situation (2026-11-24 command check). Every wording here goes through
BMO's real command handling (tests/command_router.py fakes only what's outside him), so a new command that takes
another's words, or a wording that stops working, shows up here.

Each row: the situation, what was said, and where it must go: a _handle_* method's name (without "_handle_"),
"BRAIN" for the main brain, or "said:<start>" for a command answered inline.
"""
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import command_router as router  # noqa: E402
import config  # noqa: E402

SLEEP = "said:Going to sleep"
WEATHER = "said:I don't have a weather location yet"

ROWS = (
    # Sleep, in the ways people say it and Whisper writes it
    ("idle", "go to sleep.", SLEEP), ("idle", "go to bed", SLEEP), ("idle", "time for bed", SLEEP),
    ("idle", "nap time", SLEEP), ("idle", "goodnight", "BRAIN"), ("idle", "good night BMO", SLEEP), ("idle", "sleep", SLEEP),
    ("idle", "you can go to sleep now", SLEEP), ("idle", "go to sleeps", SLEEP),
    # Captions; "subtitles" too while no movie is on
    ("idle", "captions on", "captions_command"), ("idle", "Captions, on.", "captions_command"),
    ("idle", "turn on subtitles", "captions_command"), ("idle", "captions of", "captions_command"),
    ("movie", "turn on subtitles", "BRAIN"),
    # The radio, and styles alone ("play some jazz" once launched a game)
    ("idle", "play jazz radio", "radio_command"), ("idle", "play some jazz", "radio_command"),
    ("idle", "play jazz music", "radio_command"), ("idle", "put on some jazz", "radio_command"),
    ("idle", "play the news", "radio_command"), ("idle", "play the radial", "radio_command"),
    ("idle", "radio of", "radio_command"), ("radio", "turn the radio of", "radio_command"),
    ("radio", "next station", "radio_command"), ("radio", "what station is this", "radio_command"),
    ("radio", "next", "music_command"), ("radio", "go back", "music_command"), ("radio", "close", "music_command"),
    ("radio", "stop", "music_command"), ("radio", "what's playing", "music_command"),
    # Music
    ("idle", "play music", "music_command"), ("idle", "music please", "music_command"),
    ("idle", "shovel my music", "music_command"), ("idle", "what music do I have", "library_command"),
    ("music", "pause", "music_command"), ("music", "next", "music_command"), ("music", "skip", "music_command"),
    ("music", "go back", "music_command"), ("music", "close", "music_command"), ("music", "turn the music of", "music_command"),
    ("music", "what song is this", "music_command"),
    # Movies and shows
    ("idle", "play Spirited Away", "media_command"), ("idle", "watch Spirited Away", "media_command"),
    ("idle", "play the next episode of Jimmy Neutron", "media_command"), ("idle", "continue watching", "media_command"),
    ("idle", "show my movies", "library_command"), ("idle", "shoe my movies", "library_command"),
    ("idle", "show me my moves", "library_command"), ("idle", "what movies do I have", "library_command"),
    ("movie", "pause", "media_command"), ("movie", "Paws.", "media_command"), ("movie", "hold on", "media_command"),
    ("movie", "stop", "media_command"), ("movie", "turn it of", "media_command"), ("movie", "next", "media_command"),
    ("movie", "skip ahead thirty seconds", "media_command"), ("movie", "rewind", "media_command"),
    ("movie", "what am I watching", "media_command"), ("movie", "what's playing", "media_command"),
    ("movie", "stop the timer", "reminder_command"), ("movie", "turn it up", "sound_command"),
    # Menus on the screen
    ("library:movies", "scroll down", "library_command"), ("library:movies", "school down", "library_command"),
    ("library:movies", "play number three", "library_command"), ("library:movies", "play number tree", "library_command"),
    ("library:movies", "the third one", "library_command"), ("library:movies", "clothes", "library_command"),
    ("library:weather", "close the forecast", "library_command"),
    # Weather
    ("idle", "what's the weather", WEATHER), ("idle", "whats the whether", WEATHER), ("idle", "how hot is it", WEATHER),
    ("idle", "will it rain tomorrow", WEATHER),
    # Timers, volume, help, memory, the screen
    ("idle", "set a timer for 5 minutes", "reminder_command"), ("idle", "set a time for 5 minutes", "reminder_command"),
    ("idle", "5 minute timer", "reminder_command"), ("idle", "cancel the timer", "reminder_command"),
    ("idle", "louder", "sound_command"), ("idle", "I can't hear you", "sound_command"), ("idle", "speak up", "sound_command"),
    ("idle", "it's too loud", "sound_command"), ("idle", "mute", "sound_command"),
    ("idle", "help", "help"), ("idle", "what can you do", "help"), ("idle", "weather commands", "help"),
    ("idle", "radio commands", "help"),
    ("idle", "my name is Sam", "personal_memory"), ("idle", "what do you remember about me", "personal_memory"),
    ("idle", "text mode", "interface_command"), ("idle", "show your face", "show_face"),
    # Nothing on: a bare control word is for the brain, not a command
    ("idle", "pause", "BRAIN"), ("idle", "next", "BRAIN"), ("idle", "skip", "BRAIN"), ("idle", "go back", "BRAIN"),
    ("idle", "what is the capital of France", "BRAIN"), ("idle", "the captain is on the ship", "BRAIN"),
)


class Routing(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        folder = Path(tempfile.mkdtemp())
        videos, songs = folder / "Videos", folder / "Music"
        (videos / "Movies").mkdir(parents=True)
        (videos / "Movies" / "Spirited.Away.2001.mkv").write_bytes(b"x")
        (videos / "TV" / "Jimmy" / "Season 1").mkdir(parents=True)
        for n in (1, 2):
            (videos / "TV" / "Jimmy" / "Season 1" / f"The Adventures of Jimmy Neutron Boy Genius - S01E0{n} - Ep.mkv").write_bytes(b"x")
        songs.mkdir()
        (songs / "Song.mp3").write_bytes(b"x")
        cls._patches = [mock.patch.object(config, "MEDIA_DIRS", [videos]), mock.patch.object(config, "MUSIC_DIRS", [songs]),
                        mock.patch.object(config, "MEDIA_STATE_PATH", folder / "media.json"),
                        mock.patch.object(config, "RADIO_STATE_PATH", folder / "radio.json")]
        for patch in cls._patches:
            patch.start()

    @classmethod
    def tearDownClass(cls):
        for patch in cls._patches:
            patch.stop()

    def test_every_row(self):
        wrong = []
        for state, said, expected in ROWS:
            handler, spoken = router.route(said, state)
            got = handler.replace("_handle_", "")
            if expected.startswith("said:"):
                ok = spoken.startswith(expected[5:])
            else:
                ok = got == expected
            if not ok:
                wrong.append(f"{state}: {said!r} went to {got} ({spoken[:50]!r}), not {expected}")
        self.assertEqual(wrong, [])


class Waking(unittest.TestCase):
    def test_whispers_spellings_of_hey_bmo_wake_him_and_room_talk_doesnt(self):
        from systems.chat import VoiceSessionGate
        gate = VoiceSessionGate(wake_aliases=config.VOICE_WAKE_ALIASES, timeout_seconds=30, followup_seconds=8)
        for said, command in (("Hibemo, go to sleep.", "go to sleep."), ("Hey Nemo, pause.", "pause."),
                              ("A Bemo, pause.", "pause."), ("Hit Bemo, next station.", "next station."),
                              ("Hey BMO, captions on", "captions on")):
            self.assertEqual(gate.parse(said), (True, command), said)
        for said in ("he'd be my friend, he said", "I watched Finding Nemo", "the demo works"):
            self.assertFalse(gate.parse(said)[0], said)


if __name__ == "__main__":
    unittest.main()
