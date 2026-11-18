"""Music (2026-11-15): the songs in ~/Music as a list, and a player in the Supervisor that keeps going while BMO sleeps,
goes quiet while he listens, and never stops him being used the way a movie does."""
import os
from pathlib import Path
import signal
import sys
import tempfile
import unittest
from unittest import mock

os.environ["SDL_VIDEODRIVER"] = "dummy"
os.environ["SDL_AUDIODRIVER"] = "dummy"
ROOT = Path(os.environ.get("BEMO_TEST_CODE_ROOT", Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(ROOT))
import config
from systems import library as lib, music
from systems.chat import Chat


class FakeSong:
    count = 0

    def __init__(self, path):
        FakeSong.count += 1
        self.pid, self.path, self.done = FakeSong.count, path, False

    def poll(self):
        return 0 if self.done else None

    def terminate(self):
        self.done = True

    def wait(self, timeout=None):
        return 0


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


def player():
    signals, clock = [], Clock()
    p = music.MusicPlayer(start=FakeSong, signal_to=lambda pid, sig: signals.append((pid, sig)), clock=clock)
    return p, signals, clock


class ThePlayer(unittest.TestCase):
    def test_plays_through_the_list(self):
        p, _, _ = player()
        p.handle({"op": "play", "paths": ["/m/01 - One.mp3", "/m/02 - Two.mp3"]})
        self.assertEqual(p.status()["song"], "One")
        p._proc.done = True
        p.tick()
        self.assertEqual(p.status()["song"], "Two")
        p._proc.done = True
        p.tick()
        self.assertFalse(p.status()["playing"])  # the end of the list

    def test_pause_resume_next_previous_stop(self):
        p, signals, _ = player()
        p.handle({"op": "play", "paths": ["/m/a.mp3", "/m/b.mp3", "/m/c.mp3"], "start": 1})
        self.assertEqual(p.status()["song"], "b")
        p.handle({"op": "pause"})
        self.assertEqual(signals[-1][1], signal.SIGSTOP)
        self.assertTrue(p.status()["paused"])
        p.handle({"op": "resume"})
        self.assertEqual(signals[-1][1], signal.SIGCONT)
        self.assertEqual(p.handle({"op": "next"})["song"], "c")
        self.assertEqual(p.handle({"op": "next"})["song"], "a")  # around again
        self.assertEqual(p.handle({"op": "previous"})["song"], "c")
        p.handle({"op": "stop"})
        self.assertFalse(p.status()["playing"])
        self.assertIsNone(p.status()["song"])

    def test_quiet_while_he_listens_then_back_by_itself(self):
        p, signals, clock = player()
        p.handle({"op": "play", "paths": ["/m/a.mp3"]})
        p.handle({"op": "hold"})
        self.assertEqual(signals[-1][1], signal.SIGSTOP)
        clock.now += 5
        p.tick()
        self.assertEqual(signals[-1][1], signal.SIGSTOP)  # still listening
        clock.now += music.HOLD_SECONDS
        p.tick()
        self.assertEqual(signals[-1][1], signal.SIGCONT)  # he never said he was done: back anyway

    def test_released_after_the_answer(self):
        p, signals, clock = player()
        p.handle({"op": "play", "paths": ["/m/a.mp3"]})
        p.handle({"op": "hold", "seconds": 120})
        p.handle({"op": "release", "seconds": 1.5})
        clock.now += 2
        p.tick()
        self.assertEqual(signals[-1][1], signal.SIGCONT)

    def test_a_pause_said_to_him_stays_paused(self):
        p, signals, clock = player()
        p.handle({"op": "play", "paths": ["/m/a.mp3"]})
        p.handle({"op": "hold", "seconds": 120})
        p.handle({"op": "pause"})
        p.handle({"op": "release"})
        clock.now += 5
        p.tick()
        self.assertEqual(signals[-1][1], signal.SIGSTOP)

    def test_shuffle_keeps_every_song(self):
        p, _, _ = player()
        paths = [f"/m/{n}.mp3" for n in range(20)]
        p.handle({"op": "play", "paths": paths, "shuffle": True})
        self.assertEqual(sorted(p.queue), sorted(paths))

    def test_nothing_to_play(self):
        p, _, _ = player()
        self.assertFalse(p.handle({"op": "play", "paths": []})["ok"])
        self.assertFalse(p.handle({"op": "next"})["ok"])


class TheSongs(unittest.TestCase):
    def test_found_and_named(self):
        with tempfile.TemporaryDirectory() as folder:
            album = Path(folder) / "Band" / "Album"
            album.mkdir(parents=True)
            for name in ("01 - Band - First Song.mp3", "02. Second_Song.flac", "notes.txt", "cover.jpg"):
                (album / name).write_text("x")
            items = lib.music_items([folder])
            self.assertEqual([i.title for i in items], ["Band - First Song", "Second Song"])
            self.assertEqual(items[0].picture, album / "cover.jpg")
            self.assertEqual(items[0].kind, "song")
        self.assertEqual(lib.music_items(["/nowhere"]), [])


def _syncsafe(n):
    return bytes([(n >> 21) & 127, (n >> 14) & 127, (n >> 7) & 127, n & 127])


def tagged(path, version, title="", artist="", band="", cover=None, v1=None):
    """A small MP3 with an ID3 tag, as music downloads carry them."""
    def frame(name, body):
        return name.encode() + (_syncsafe(len(body)) if version == 4 else len(body).to_bytes(4, "big")) + b"\0\0" + body
    frames = b""
    if title:
        frames += frame("TIT2", b"\x03" + title.encode())
    if artist:
        frames += frame("TPE1", b"\x01" + artist.encode("utf-16"))
    if band:
        frames += frame("TPE2", b"\x00" + band.encode("latin-1"))
    if cover:
        frames += frame("APIC", b"\x01image/jpeg\x00\x03" + "front".encode("utf-16-le") + b"\0\0" + cover)
    frames += b"\0" * 32
    body = b"ID3" + bytes([version, 0, 0]) + _syncsafe(len(frames)) + frames if (title or artist or cover) else b""
    tail = b""
    if v1:
        tail = b"TAG" + v1[0].encode().ljust(30, b"\0") + v1[1].encode().ljust(30, b"\0") + b"\0" * 65
    Path(path).write_bytes(body + b"\xff\xfb" * 200 + tail)


class TheTags(unittest.TestCase):
    """2026-11-17: the song's name and artist come from inside the file, as downloads carry them."""

    def test_both_versions_and_the_old_one_at_the_end(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            tagged(folder / "a.mp3", 3, "Here Comes the Sun", "The Beatles")
            tagged(folder / "b.mp3", 4, "Yellow", "Coldplay")
            tagged(folder / "c.mp3", 3, v1=("Waterloo", "ABBA"))
            tagged(folder / "d.mp3", 4, "Solo Song", band="The Band")
            tagged(folder / "y2mate.com - Rolling in the Deep.mp3", 3, "Rolling in the Deep", "Adele")
            self.assertEqual(music.names(folder / "a.mp3"), ("Here Comes the Sun", "The Beatles"))
            self.assertEqual(music.names(folder / "b.mp3"), ("Yellow", "Coldplay"))
            self.assertEqual(music.names(folder / "c.mp3"), ("Waterloo", "ABBA"))
            self.assertEqual(music.names(folder / "d.mp3"), ("Solo Song", "The Band"))
            self.assertEqual(music.title(folder / "y2mate.com - Rolling in the Deep.mp3"), "Adele - Rolling in the Deep")

    def test_a_to_z_by_artist_or_name(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            tagged(folder / "1.mp3", 3, "Here Comes the Sun", "The Beatles")   # under B, not T
            tagged(folder / "2.mp3", 3, "Yellow", "Coldplay")
            tagged(folder / "3.mp3", 3, "Clocks", "Coldplay")
            (folder / "04 - Abba - Waterloo.mp3").write_bytes(b"x")
            (folder / "Banana Pancakes.mp3").write_bytes(b"x")                   # no artist: by its own name
            (folder / "sub").mkdir()
            tagged(folder / "sub" / "5.mp3", 4, "Zebra", "Daft Punk")
            self.assertEqual([i.title for i in lib.music_items([folder])],
                             ["Abba - Waterloo", "Banana Pancakes", "The Beatles - Here Comes the Sun", "Coldplay - Clocks",
                              "Coldplay - Yellow", "Daft Punk - Zebra"])
            self.assertEqual(music.songs([folder])[0].name, "04 - Abba - Waterloo.mp3")  # "play music" goes in the same order

    def test_the_cover_inside_the_song(self):
        jpeg = b"\xff\xd8\xff\xe0" + b"picture" * 20
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            tagged(folder / "a.mp3", 3, "Song", "Artist", cover=jpeg)
            (folder / "cover.jpg").write_bytes(b"folder picture")
            item = lib.music_items([folder], covers=folder / "covers")[0]
            self.assertEqual(item.picture.parent, folder / "covers")
            self.assertEqual(item.picture.read_bytes(), jpeg)
            tagged(folder / "a.mp3", 3, "Song", "Artist")
            self.assertEqual(lib.music_items([folder], covers=folder / "covers")[0].picture, folder / "cover.jpg")

    def test_cover_pictures_are_not_kept_in_memory(self):
        jpeg = b"\xff\xd8\xff\xe0" + b"picture" * 20
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            tagged(folder / "a.mp3", 3, "Song", "Artist", cover=jpeg)
            saved = music.cover_file(folder / "a.mp3", folder / "covers")
            self.assertEqual(saved.read_bytes(), jpeg)
            self.assertEqual(music.tags(folder / "a.mp3"), {"title": "Song", "artist": "Artist"})
            with mock.patch.object(music, "_read_tags", side_effect=AssertionError("read again")):
                self.assertEqual(music.cover_file(folder / "a.mp3", folder / "covers"), saved)  # already saved

    def test_a_broken_or_odd_file_is_just_a_name(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            (folder / "Broken.mp3").write_bytes(b"ID3\x03\x00\x00\x7f\x7f\x7f\x7fTIT2\xff\xff\xff\xff")
            (folder / "Empty.mp3").write_bytes(b"")
            self.assertEqual([i.title for i in lib.music_items([folder])], ["Broken", "Empty"])


class Words(unittest.TestCase):
    def test_commands(self):
        for said, op in (("play music", "play all"), ("play some music", "play all"), ("play my music", "play all"), ("put on some music", "play all"),
                         ("shuffle my music", "shuffle"), ("play music on shuffle", "shuffle"), ("shuffle", "shuffle"),
                         ("pause the music", "pause"), ("pause the song", "pause"), ("resume the music", "resume"), ("unpause the music", "resume"),
                         ("stop the music", "stop"), ("turn off the music", "stop"), ("next song", "next"), ("skip this song", "next"), ("skip song", "next"),
                         ("next son", "next"), ("previous song", "previous"), ("go back a song", "previous"), ("what song is this", "what"),
                         ("What song is this?", "what"), ("what's playing", "what"), ("what am I listening to", "what")):
            self.assertEqual(music.request(said), op, said)

    def test_more_ways_to_shuffle(self):
        for said in ("shuffle everything", "shuffle all", "shuffle it", "shuffle them", "mix it up", "put it on shuffle",
                     "play it on shuffle", "turn on shuffle", "shuffle mode", "play something random", "play a random song",
                     "random songs", "play my music in random order", "play my songs randomly", "surprise me",
                     "shovel my music", "Shuffled.", "Hey BMO, shuffle my songs"):
            self.assertEqual(music.request(said), "shuffle", said)

    def test_more_ways_to_stop(self):
        for said in ("stop playing music", "turn the music off", "shut the music off", "music off", "cut the music", "enough music",
                     "close the music", "close the music player", "turn that music off", "get rid of the music", "make the music stop",
                     "music stop", "I'm done with the music", "that's enough music", "switch off the music", "no more songs",
                     "I don't want music anymore", "stop the musics", "stop them music"):
            self.assertEqual(music.request(said), "stop", said)
        for said in ("turn it off", "stop playing", "that's enough", "kill it", "stop it"):
            self.assertIsNone(music.request(said), said)
            self.assertEqual(music.request(said, playing=True), "stop", said)
        for said in ("stop the movie", "turn off the lights", "close the game", "turn off the tv", "stop talking", "enough talking"):
            self.assertIsNone(music.request(said), said)

    def test_plain_words_only_while_it_plays(self):
        for said, op in (("pause", "pause"), ("stop", "stop"), ("next", "next"), ("skip", "next"), ("resume", "resume")):
            self.assertIsNone(music.request(said), said)
            self.assertEqual(music.request(said, playing=True), op, said)

    def test_not_music(self):
        for said in ("play Ponyo", "play number 3", "turn down the music", "music to my ears", "what is music", "play the next episode",
                     "stop the movie", "shuffle the cards", "I like this song"):
            self.assertIsNone(music.request(said), said)

    def test_opening_the_list(self):
        for said in ("show my music", "my music", "my songs", "show me my songs", "open my music", "what can I listen to", "show my playlist"):
            self.assertEqual(lib.open_request(said), "music", said)
        self.assertEqual(lib.open_request("show my movies"), "movies")
        self.assertEqual(lib.open_request("show my games"), "games")


class InChat(unittest.TestCase):
    def setUp(self):
        self.chat = Chat.__new__(Chat)
        self.chat.library_view = lib.LibraryView()
        self.chat.note_user_activity = lambda: None
        self.chat.supervisor = mock.Mock()
        self.played, self.state = None, {"ok": True, "playing": False, "paused": False, "song": None}

        def request(action, **payload):
            self.assertEqual(action, "music")
            if payload["op"] == "play":
                self.played = payload
                return {"ok": True, "song": music.title(Path(payload["paths"][payload.get("start", 0)]))}
            return dict(self.state, song="Second Song") if payload["op"] in ("next", "previous", "status") else dict(self.state)
        self.chat.supervisor.request.side_effect = request
        self.said = []
        self.chat._say_local = lambda command, text: self.said.append(text) or True

    def test_from_the_list(self):
        self.chat.library_view.open("music", [lib.Item("First Song", "song", "/m/1.mp3"), lib.Item("Second Song", "song", "/m/2.mp3")], grid=False)
        self.assertTrue(self.chat._handle_library_command("play number 2"))
        self.assertEqual((self.played["paths"], self.played["start"]), (["/m/1.mp3", "/m/2.mp3"], 1))
        self.assertEqual(self.said[-1], "Playing Second Song.")
        self.assertFalse(self.chat.library_view.active)

    def test_play_all_and_shuffle(self):
        with mock.patch.object(music, "songs", return_value=[Path("/m/01 - Band - Hello.mp3")]):
            self.chat._handle_music_command("play music")
            self.assertEqual(self.said[-1], "Playing your music, starting with Band - Hello.")
            self.chat._handle_music_command("shuffle my music")
            self.assertTrue(self.played["shuffle"])
        with mock.patch.object(music, "songs", return_value=[]):
            self.chat._handle_music_command("play music")
            self.assertEqual(self.said[-1], "I don't have any music yet. It goes in the Music folder.")

    def test_plain_pause_only_with_music_on(self):
        self.assertFalse(self.chat._handle_music_command("pause"))
        self.state["playing"] = True
        self.assertTrue(self.chat._handle_music_command("pause"))

    def test_next_says_the_song(self):
        self.chat._handle_music_command("next song")
        self.assertEqual(self.said[-1], "Second Song.")

    def test_an_old_supervisor_says_so(self):
        from systems.supervisor import SupervisorError
        self.chat.supervisor.request.side_effect = SupervisorError("unknown action")
        with mock.patch.object(music, "songs", return_value=[Path("/m/a.mp3")]):
            self.chat._handle_music_command("play music")
        self.assertEqual(self.said[-1], Chat._MUSIC_DOWN)

    def test_the_music_carousel(self):
        with mock.patch.object(lib, "music_items", return_value=[lib.Item("Abba - Waterloo", "song", "/m/1.mp3"),
                                                                 lib.Item("Coldplay - Yellow", "song", "/m/2.mp3")]):
            self.chat._open_library("show my music", "music")
        view = self.chat.library_view
        self.assertTrue(view.carousel)
        self.assertIn("A to Z", self.said[-1])
        self.assertTrue(self.chat._bare_library_command("next"))
        self.assertTrue(self.chat._bare_library_command("play this song"))
        self.assertEqual(self.played["start"], 1)
        self.assertEqual(self.said[-1], "Playing Coldplay - Yellow.")

    def test_stopping_closes_the_music_screen(self):
        self.chat.library_view.open("music", [lib.Item("Abba - Waterloo", "song", "/m/1.mp3")], carousel=True)
        self.assertTrue(self.chat._handle_music_command("close the music"))
        self.assertEqual(self.said[-1], "Music off.")
        self.assertFalse(self.chat.library_view.active)

    def test_shuffle_on_the_carousel(self):
        view = self.chat.library_view
        for said in ("shuffle", "shuffle them", "mix it up", "shovel"):
            view.open("music", [lib.Item("Abba - Waterloo", "song", "/m/1.mp3"), lib.Item("Coldplay - Yellow", "song", "/m/2.mp3")],
                      carousel=True)
            self.played = None
            self.assertTrue(self.chat._bare_library_command(said), said)
            self.assertTrue(self.played["shuffle"], said)
            self.assertEqual(sorted(self.played["paths"]), ["/m/1.mp3", "/m/2.mp3"])
            self.assertFalse(view.active)
        view.open("games", [lib.Item("Custom Robo", "game", "/g/1.iso")], carousel=True)
        self.assertFalse(self.chat._bare_library_command("shuffle"))  # the games don't shuffle

    def test_the_controller_shuffles_the_music(self):
        view = self.chat.library_view
        view.open("music", [lib.Item("Abba - Waterloo", "song", "/m/1.mp3")], carousel=True)
        with mock.patch("threading.Thread") as thread:
            self.assertTrue(self.chat.library_pad("shuffle"))
        self.assertEqual(thread.call_args.kwargs["args"], ("controller", "shuffle"))
        view.open("games", [lib.Item("Custom Robo", "game", "/g/1.iso")], carousel=True)
        self.assertFalse(self.chat.library_pad("shuffle"))

    def test_list_words_need_hey_bmo_while_music_plays(self):
        # "next", "close" or "number two" in a song would work the list, so it takes "Hey BMO" (2026-11-17).
        self.chat.library_view.open("music", [lib.Item("First Song", "song", "/m/1.mp3"), lib.Item("Second Song", "song", "/m/2.mp3")], carousel=True)
        self.state["playing"] = True
        for said in ("play number 2", "close", "down", "shuffle"):
            self.assertFalse(self.chat._bare_library_command(said), said)
        self.assertIsNone(self.played)
        self.assertTrue(self.chat.library_view.active)
        self.state["playing"] = False
        self.assertTrue(self.chat._bare_library_command("play number 2"))
        self.assertEqual(self.played["start"], 1)

    def test_the_supervisor_plays_it(self):
        src = (ROOT / "supervisor.py").read_text()
        self.assertIn('if action == "music":', src)
        self.assertIn("service.music = MusicPlayer()", src)

    def test_quiet_while_he_listens(self):
        src = (ROOT / "systems" / "chat.py").read_text()
        self.assertIn('self._music_quiet("hold")', src[src.index("def _heard_wake"):][:300])
        loop = src[src.index('self._music_quiet("hold", 120.0)'):][:500]
        self.assertIn("finally:", loop)
        self.assertIn('self._music_quiet("release", 1.5)', loop)


class TheScreen(unittest.TestCase):
    def test_drawn_with_and_without_covers(self):
        import pygame
        pygame.init()
        sys.path.insert(0, str(ROOT / "tests"))
        import visual_smoke_test as smoke
        from systems.render import FaceRenderer
        with tempfile.TemporaryDirectory() as folder:
            picture = Path(folder) / "cover.png"
            surface = pygame.Surface((40, 40))
            surface.fill((200, 40, 90))
            pygame.image.save(surface, str(picture))
            items = [lib.Item("Abba - Waterloo", "song", "/m/1.mp3", picture), lib.Item("Banana Pancakes", "song", "/m/2.mp3"),
                     lib.Item("A Very Long Artist Name Indeed - A Song With A Very Long Name That Goes On", "song", "/m/3.mp3")]
            for size in ((640, 480), (1920, 1080)):
                app = smoke.FakeApp()
                app.SCREEN_WIDTH, app.SCREEN_HEIGHT = size
                app._screen = pygame.Surface(size)
                renderer, view = FaceRenderer(app), lib.LibraryView()
                view.open("music", items, carousel=True)
                for step in range(3):
                    renderer._render_library(view.snapshot(), 10.0 + step)
                    view.scroll(1)
        self.assertIn("_album_case", (ROOT / "systems" / "render.py").read_text())


class Loudness(unittest.TestCase):
    """The music is as loud as the computer, as a movie is (2026-11-17), so these are the volume command's."""

    def test_up(self):
        from systems.sound import parse_sound_command
        for said in ("turn up the music", "turn the music up", "music up", "music louder", "make the music louder",
                     "louder music", "crank the music", "crank it up", "pump up the music", "turn the tunes up",
                     "music volume up", "the music is too quiet", "the music's too quiet", "I can't hear the music",
                     "raise the music", "boost the music", "bump it up", "turn the music back up", "turn up my songs",
                     "muse sick louder", "make it lauder", "it's too quiet"):
            self.assertGreater((parse_sound_command(said) or {}).get("delta", 0), 0, said)

    def test_down(self):
        from systems.sound import parse_sound_command
        for said in ("turn down the music", "turn the music down", "music down", "music quieter", "make the music softer",
                     "lower the music", "quiet the music down", "turn it down a notch", "take it down a notch",
                     "bring the music down", "knock it down a bit", "the music is too loud", "the music's too loud",
                     "it's too loud", "too loud", "music volume down", "reduce the music", "keep it down",
                     "keep the music down", "music softer", "soften the music", "quiet her"):
            self.assertLess((parse_sound_command(said) or {}).get("delta", 0), 0, said)

    def test_how_much(self):
        from systems.sound import parse_sound_command
        self.assertEqual(parse_sound_command("turn the music way down")["delta"], -20)
        self.assertEqual(parse_sound_command("turn the music down a notch")["delta"], -5)
        self.assertEqual(parse_sound_command("set the music volume to 40"), {"kind": "set", "level": 40})

    def test_not_loudness(self):
        from systems.sound import parse_sound_command
        for said in ("keep it up", "bring it up", "put it up on the screen", "bring up my music", "pull up my music",
                     "show my music", "play music", "turn off the music", "turn the music back on", "tune up my computer",
                     "pause the music", "next song", "what song is this", "shuffle my music", "play some loud music",
                     "take it up with him", "pump up the tires", "turn it off"):
            self.assertIsNone(parse_sound_command(said), said)


if __name__ == "__main__":
    unittest.main()
