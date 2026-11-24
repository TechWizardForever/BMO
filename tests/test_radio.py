"""Internet radio (2026-11-24): stations by style or name from Radio Browser, played by the music player, with a pixel
radio on BMO's screen and notes popping out of it. No network here: the station directory is a fake."""
import ast
import os
from pathlib import Path
import re
import sys
import tempfile
import types
import unittest
from unittest import mock

os.environ["SDL_VIDEODRIVER"] = "dummy"
os.environ["SDL_AUDIODRIVER"] = "dummy"
ROOT = Path(os.environ.get("BEMO_TEST_CODE_ROOT", Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
import pygame
import config
from systems import music, radio, radio_screen

SOURCE = (ROOT / "systems" / "chat.py").read_text()


def row(name, url, tags="jazz", country="US"):
    return {"name": name, "url_resolved": url, "tags": tags, "countrycode": country}


class Phrases(unittest.TestCase):
    def test_ways_to_ask(self):
        for said, expected in (
                ("Hey BMO, play the radio.", ("last", "")), ("turn on the radio", ("last", "")),
                ("put on the radio", ("last", "")), ("Play ready oh.", ("last", "")),
                ("play jazz radio", ("play", "jazz")), ("Put on some 80s radio.", ("play", "80s")),
                ("play jazz on the radio", ("play", "jazz")), ("play the news on the radio", ("play", "news")),
                ("play classic rock radio please", ("play", "classic rock")), ("play smooth jazz station", ("play", "smooth jazz")),
                ("put on NPR", ("play", "npr")), ("play NPR", ("play", "npr")), ("tune in to KEXP", ("play", "kexp")),
                ("play BBC Radio 1", ("play", "bbc radio 1")), ("Play N P R.", ("play", "npr")),
                ("next station", ("next", "")), ("Change the station.", ("next", "")), ("another station", ("next", "")),
                ("previous station", ("previous", "")), ("last station", ("previous", "")),
                ("go back a station", ("previous", "")), ("back one station", ("previous", "")),
                ("the station before", ("previous", "")), ("go back to that station", ("previous", "")),
                ("what station is this?", ("what", "")), ("what's on the radio", ("what", "")),
                ("save this station", ("save", "")), ("I like this station", ("save", "")),
                ("play my stations", ("saved", "")), ("play my favorite stations", ("saved", "")),
                ("turn off the radio", ("off", "")), ("radio off", ("off", "")), ("stop the radio", ("off", "")),
                ("pause the radio", ("pause", "")), ("resume the radio", ("resume", ""))):
            self.assertEqual(radio.request(said), expected, said)

    def test_other_play_commands_are_left_alone(self):
        for said in ("play Ponyo", "play Wind Waker", "play music", "shuffle my music", "play number 3", "next song",
                     "what song is this", "pause", "play Spirited Away", "what's on TV", "the station wagon broke",
                     "show my music", "what's the weather"):
            self.assertIsNone(radio.request(said), said)


class Directory(unittest.TestCase):
    def test_a_style_is_searched_as_tags_in_the_us_first(self):
        asked = []

        def fetch(params, timeout):
            asked.append(params)
            return [row("Smooth|Jazz 🎷 FM", "http://a/1"), row("Jazz Two", "http://a/2"), row("Dupe", "http://a/1"),
                    row("No stream", "ftp://x")]
        found = radio.search("jazz", fetch=fetch)
        self.assertEqual(asked[0]["tag"], "jazz")
        self.assertEqual(asked[0]["countrycode"], "US")
        self.assertEqual((asked[0]["hidebroken"], asked[0]["order"], asked[0]["reverse"]), ("true", "votes", "true"))
        self.assertEqual([s["url"] for s in found], ["http://a/1", "http://a/2"])
        self.assertEqual(found[0]["name"], "Smooth - Jazz FM")  # no emoji or pipes for BMO to say
        self.assertEqual(found[0]["about"], "jazz - US")

    def test_a_name_and_the_fallbacks(self):
        asked = []

        def fetch(params, timeout):
            asked.append(params)
            return [row("NPR 24 Hour", "http://npr/live")] if "countrycode" not in params and "name" in params else []
        self.assertEqual(radio.search("npr", fetch=fetch)[0]["name"], "NPR 24 Hour")
        self.assertEqual([("name" in a, "countrycode" in a) for a in asked], [(True, True), (True, False)])
        self.assertEqual(radio.search("eighties", fetch=lambda p, t: [row("977 80s", "http://x")] if p.get("tag") == "80s" else [])[0]["name"],
                         "977 80s")
        self.assertEqual(radio.search("nothing at all", fetch=lambda p, t: []), [])

    def test_only_what_was_asked_is_sent(self):
        sent = []
        with mock.patch("urllib.request.urlopen", side_effect=lambda request, timeout: sent.append(request) or
                        types.SimpleNamespace(read=lambda: b"[]", __enter__=lambda s: s, __exit__=lambda *a: None)):
            try:
                radio.search("jazz")
            except Exception:
                pass
        url = sent[0].full_url
        self.assertTrue(url.startswith("https://all.api.radio-browser.info/json/stations/search?"))
        self.assertEqual(set(re.findall(r"(\w+)=", url)), {"tag", "countrycode", "limit", "hidebroken", "order", "reverse"})

    def test_last_and_saved_stations(self):
        keep = radio.Stations(Path(tempfile.mkdtemp()) / "radio.json")
        self.assertEqual((keep.last(), keep.saved()), ([], []))
        found = [{"name": n, "url": f"http://s/{n}", "about": ""} for n in "abc"]
        keep.played(found, 1)
        self.assertEqual([s["name"] for s in keep.last()], ["b", "c", "a"])  # the one playing first
        self.assertTrue(keep.save(found[1]))
        self.assertFalse(keep.save(found[1]))
        self.assertEqual([s["name"] for s in keep.saved()], ["b"])


class FakeStream:
    def __init__(self, url):
        self.url, self.pid, self.code = url, 99, None

    def poll(self):
        return self.code

    def terminate(self):
        self.code = 0

    def wait(self, timeout=None):
        return 0


class Player(unittest.TestCase):
    def player(self):
        started = []

        def start(url):
            started.append(FakeStream(url))
            return started[-1]
        return music.MusicPlayer(start=start, signal_to=lambda pid, sig: None, clock=lambda: 0.0), started

    def test_the_radio_plays_with_its_station(self):
        player, started = self.player()
        stations = [{"name": "Jazz One", "url": "http://a/1", "about": "jazz"}, {"name": "Jazz Two", "url": "http://a/2"}]
        state = player.handle({"op": "play", "paths": [s["url"] for s in stations], "stations": stations})
        self.assertTrue(state["radio"])
        self.assertEqual((state["song"], state["station"]["about"]), ("Jazz One", "jazz"))
        self.assertEqual(player.handle({"op": "next"})["song"], "Jazz Two")
        music_state = player.handle({"op": "play", "paths": ["/m/a.mp3"]})
        self.assertFalse(music_state["radio"])  # songs again: not the radio

    def test_a_dropped_stream_is_reconnected_then_the_next_station_tried(self):
        player, started = self.player()
        player.handle({"op": "play", "paths": ["http://a/1", "http://a/2"],
                       "stations": [{"name": "One", "url": "http://a/1"}, {"name": "Two", "url": "http://a/2"}]})
        for _ in range(music.MusicPlayer.RADIO_RETRIES):
            started[-1].code = 1
            player.tick()
            self.assertEqual(started[-1].url, "http://a/1")
        started[-1].code = 1
        player.tick()
        self.assertEqual(started[-1].url, "http://a/2")

    def test_streams_reconnect_by_themselves(self):
        with mock.patch("subprocess.Popen") as popen:
            music.MusicPlayer()._start("http://a/1")
            music.MusicPlayer()._start("/m/song.mp3")
        stream, song = popen.call_args_list[0][0][0], popen.call_args_list[1][0][0]
        self.assertIn("-reconnect_streamed", stream)
        self.assertNotIn("-reconnect", song)


class Saying(unittest.TestCase):
    def chat(self, status=None, found=None):
        cls = next(n for n in ast.parse(SOURCE).body if isinstance(n, ast.ClassDef) and n.name == "Chat")
        keep = [n for n in cls.body if (isinstance(n, ast.FunctionDef) and n.name in ("_handle_radio_command", "_radio_play"))
                or (isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") == "_MUSIC_DOWN")]
        cfg = types.SimpleNamespace(ENABLE_RADIO=True, RADIO_STATE_PATH=Path(tempfile.mkdtemp()) / "radio.json")
        ns = {"config": cfg, "logger": types.SimpleNamespace(warning=lambda *a, **k: None),
              "State": types.SimpleNamespace(READY="ready")}
        exec(compile(ast.fix_missing_locations(ast.Module(body=[ast.ClassDef(
            name="Mini", bases=[], keywords=[], body=keep, decorator_list=[])], type_ignores=[])), "m", "exec"), ns)
        chat = ns["Mini"]()
        chat.said, chat.sent = [], []
        chat._say_local = lambda command, text: chat.said.append(text) or True

        def player(op, **payload):
            chat.sent.append((op, payload))
            if op == "play":
                return {"ok": True}
            return dict(status or {"ok": True, "radio": False})
        chat._music = player
        chat.cfg = cfg
        return chat

    def test_play_a_style(self):
        chat = self.chat()
        found = [{"name": "Jazz One", "url": "http://a/1", "about": "jazz - US"}]
        with mock.patch.object(radio, "search", return_value=found):
            self.assertTrue(chat._handle_radio_command("play jazz radio"))
        self.assertEqual(chat.said, ["Tuning in jazz.", "This is Jazz One."])
        self.assertEqual(chat.sent[-1], ("play", {"paths": ["http://a/1"], "stations": found}))
        self.assertEqual(radio.Stations(chat.cfg.RADIO_STATE_PATH).last(), found)  # "play the radio" next time

    def test_no_internet_and_nothing_found(self):
        chat = self.chat()
        with mock.patch.object(radio, "search", side_effect=OSError("down")):
            chat._handle_radio_command("play jazz radio")
        self.assertIn("internet", chat.said[-1])
        with mock.patch.object(radio, "search", return_value=[]):
            chat._handle_radio_command("play polka radio")
        self.assertEqual(chat.said[-1], "I couldn't find a polka station.")

    def test_the_first_time_he_asks_what_kind(self):
        chat = self.chat()
        chat._handle_radio_command("play the radio")
        self.assertIn("What kind of radio?", chat.said[-1])

    def test_while_the_radio_plays(self):
        station = {"name": "Jazz One", "url": "http://a/1", "about": "jazz - US"}
        chat = self.chat({"ok": True, "radio": True, "station": station, "song": "Jazz Two"})
        chat._handle_radio_command("what station is this?")
        self.assertEqual(chat.said[-1], "This is Jazz One, jazz - us.")
        chat._handle_radio_command("save this station")
        self.assertTrue(chat.said[-1].startswith("Saved Jazz One."))
        chat._handle_radio_command("save this station")
        self.assertEqual(chat.said[-1], "Jazz One is already saved.")
        chat._handle_radio_command("next station")
        self.assertEqual(chat.said[-1], "Jazz Two.")
        chat._handle_radio_command("radio off")
        self.assertEqual((chat.sent[-1][0], chat.said[-1]), ("stop", "Radio off."))

    def test_with_the_radio_off(self):
        chat = self.chat({"ok": True, "radio": False})
        chat._handle_radio_command("next station")
        self.assertIn("isn't on", chat.said[-1])

    def test_wiring(self):
        self.assertLess(SOURCE.index("if self._handle_radio_command(command):"), SOURCE.index("if self._handle_library_command(command):"))
        self.assertLess(SOURCE.index("if self._handle_radio_command(command):"), SOURCE.index("if self._handle_media_command(command):"))
        self.assertIn('status["radio"] = getattr(self, "_music_station", None) if status["music"] else None', SOURCE)
        self.assertIn("radio_screen.render(self, status[\"radio\"], now, dt)", (ROOT / "systems" / "render.py").read_text())


class Screen(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        pygame.init()

    @classmethod
    def tearDownClass(cls):
        pygame.quit()

    def test_draws_at_every_size_and_the_notes_come_out_and_stay_clear(self):
        for size in ((640, 480), (1024, 768), (1920, 1080)):
            screen = pygame.Surface(size)
            renderer = types.SimpleNamespace(_app=types.SimpleNamespace(_screen=screen))
            station = {"name": "NPR 24 Hour Program Stream With A Very Long Name", "about": "news - US"}
            for frame in range(300):
                radio_screen.render(renderer, station, frame / 30, 1 / 30)
            notes = renderer._radio_notes
            self.assertEqual(len(notes._notes), notes._count)  # all born by now
            body = radio_screen.layout(*size)["body"]
            for note in notes._notes:
                self.assertTrue(0 <= note["x"] <= size[0] and 0 <= note["y"] <= size[1], size)
                pixel = notes.pixel(size[1], note["big"])
                nw = len(radio_screen.NOTES[note["kind"]][0]) * pixel
                nh = len(radio_screen.NOTES[note["kind"]]) * pixel
                inside = body.inflate(-4, -4).contains(pygame.Rect(note["x"], note["y"], nw, nh))
                self.assertFalse(inside, size)  # bounced off the radio, never inside it

    def test_the_radio_is_whole_pixels_of_its_grid(self):
        place = radio_screen.layout(1024, 768)
        self.assertGreaterEqual(place["scale"], 3)
        self.assertEqual(place["scale"], int(place["scale"]))


if __name__ == "__main__":
    unittest.main()
