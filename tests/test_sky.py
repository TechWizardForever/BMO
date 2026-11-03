"""The sky (owner 2026-11-03): real eclipses at the right times, passes only when you can see them, alerts that keep quiet at
night and over movies, and the screens, with no network."""
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
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
import visual_smoke_test as smoke
from systems import library as lib, sky, sky_screen
from systems.chat import Chat
from systems.render import FaceRenderer
from systems.weather import Place

UTC = timezone.utc
# An orbit from 2026-11-02, as CelesTrak gives it.
ORBIT = ["ISS (ZARYA)",
         "1 25544U 98067A   26306.75682168  .00005437  00000+0  10620-3 0  9990",
         "2 25544  51.6331 334.3796 0007124 340.7366  19.3352 15.49154261593232"]
ORBIT_DAY = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)
KANSAS_CITY = Place("Kansas City", "Missouri", "United States", 39.1, -94.6, "America/Chicago")
CITY = sky.Sky(39.1, -94.6, "America/Chicago")


def utc(*parts):
    return datetime(*parts, tzinfo=UTC)


class EclipseTests(unittest.TestCase):
    """Against eclipses that are in the record books."""

    def peak(self, found):
        self.assertEqual(len(found), 1, found)
        return found[0]

    def test_solar_2024_total_in_dallas_partial_in_kansas_city_nothing_in_paris(self):
        dallas = self.peak(sky.Sky(32.78, -96.80, "America/Chicago").solar_eclipses(utc(2024, 4, 1), 30))
        self.assertEqual(dallas["title"], "Total eclipse of the sun")
        self.assertEqual(dallas["peak"].strftime("%m-%d %H"), "04-08 13")
        city = self.peak(CITY.solar_eclipses(utc(2024, 4, 1), 30))
        self.assertEqual(city["title"], "Partial eclipse of the sun")
        self.assertTrue(85 <= city["covered"] <= 95, city["covered"])
        self.assertEqual(sky.Sky(48.85, 2.35, "Europe/Paris").solar_eclipses(utc(2024, 4, 1), 30), [])

    def test_solar_2023_annular_in_oregon(self):
        eugene = self.peak(sky.Sky(44.05, -123.09, "America/Los_Angeles").solar_eclipses(utc(2023, 10, 1), 30))
        self.assertEqual(eugene["title"], "Annular eclipse of the sun")

    def test_lunar_2026_total_seen_in_the_west_not_in_paris(self):
        west = self.peak(sky.Sky(34.05, -118.24, "America/Los_Angeles").lunar_eclipses(utc(2026, 2, 25), 20))
        self.assertTrue(west["total"])
        self.assertEqual(west["peak"].strftime("%m-%d %H"), "03-03 03")
        self.assertEqual(sky.Sky(48.85, 2.35, "Europe/Paris").lunar_eclipses(utc(2026, 2, 25), 20), [])

    def test_lunar_2026_partial(self):
        partial = self.peak(CITY.lunar_eclipses(utc(2026, 8, 20), 20))
        self.assertFalse(partial["total"])

    def test_a_penumbral_one_is_not_news(self):
        self.assertEqual(CITY.lunar_eclipses(utc(2027, 2, 10), 20), [])


class MoonAndPlanetTests(unittest.TestCase):
    def test_names_through_the_month(self):
        self.assertEqual(CITY.moon(utc(2026, 8, 28, 4))["name"], "full moon")
        self.assertEqual(CITY.moon(utc(2026, 8, 12, 18))["name"], "new moon")
        self.assertEqual(CITY.moon(utc(2026, 8, 20, 12))["name"], "first quarter")
        moon = CITY.moon(utc(2026, 8, 28, 4))
        self.assertGreater(moon["lit"], 97)
        self.assertLess(moon["next_new"], moon["next_new"] + timedelta(days=1))

    def test_the_planets_are_brightest_first_and_up(self):
        seen = CITY.planets(ORBIT_DAY)
        self.assertTrue(seen)
        self.assertEqual([p["mag"] for p in seen], sorted(p["mag"] for p in seen))
        for planet in seen:
            self.assertGreaterEqual(planet["height"], 12)

    def test_showers_and_meetings(self):
        showers = CITY.meteor_showers(ORBIT_DAY)
        self.assertIn("Geminids", [s["name"] for s in showers])
        self.assertTrue(next(s for s in showers if s["name"] == "Geminids")["important"])
        self.assertFalse(next(s for s in showers if s["name"] == "Leonids")["important"])
        events = CITY.events(ORBIT_DAY)
        self.assertEqual([e["start"] for e in events], sorted(e["start"] for e in events))


class StationTests(unittest.TestCase):
    def test_passes_are_in_the_dark_and_high_enough(self):
        passes = CITY.iss_passes(ORBIT, ORBIT_DAY, days=5)
        self.assertTrue(passes)
        for found in passes:
            self.assertGreaterEqual(found["height"], sky.PASS_MIN_HEIGHT)
            self.assertLess(found["start"], found["end"])
            look = CITY._site(found["start"])
            self.assertLess(sky._degrees(sky.ephem.Sun(look).alt), -3.5)  # you're in the dark

    def test_where_it_is_now(self):
        now = sky.Sky.iss_now(ORBIT, ORBIT_DAY)
        self.assertTrue(200 <= now["miles_up"] <= 300, now)
        self.assertRegex(now["over"], r"^\d+[NS] \d+[EW]$")

    def test_orbit_is_kept_and_not_asked_for_too_often(self):
        calls = []
        now = [ORBIT_DAY.timestamp()]

        def fetch(url):
            calls.append(url)
            return "\n".join(ORBIT)
        with tempfile.TemporaryDirectory() as folder:
            store = sky.OrbitStore(Path(folder) / "orbit.json", fetch=fetch, clock=lambda: now[0])
            self.assertEqual(store.get(), ORBIT)
            self.assertEqual(store.get(), ORBIT)
            self.assertEqual(len(calls), 1)
            now[0] += 13 * 3600
            store.get()
            self.assertEqual(len(calls), 2)

    def test_a_stale_orbit_is_no_orbit(self):
        now = [ORBIT_DAY.timestamp()]

        def down(url):
            raise OSError("down")
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "orbit.json"
            path.write_text(json.dumps({"orbit": ORBIT, "fetched": now[0]}))
            store = sky.OrbitStore(path, fetch=down, clock=lambda: now[0])
            self.assertEqual(store.get(), ORBIT)
            now[0] += 3 * 86400  # the service is down, the one he has will do
            self.assertEqual(store.get(), ORBIT)
            now[0] += 8 * 86400  # too old to trust
            self.assertIsNone(store.get())

    def test_a_bad_answer_is_not_saved(self):
        with tempfile.TemporaryDirectory() as folder:
            store = sky.OrbitStore(Path(folder) / "orbit.json", fetch=lambda url: "<html>busy</html>", clock=ORBIT_DAY.timestamp)
            self.assertIsNone(store.get())


class AuroraAndCloudTests(unittest.TestCase):
    def test_how_far_a_storm_reaches(self):
        rows = [{"time_tag": "2026-11-03T15:00:00", "kp": 7.3}]
        self.assertEqual(sky.aurora_chance(rows, 48.0, ORBIT_DAY), 7.3)
        self.assertIsNone(sky.aurora_chance(rows, 39.0, ORBIT_DAY))
        self.assertIsNone(sky.aurora_chance(rows, 20.0, ORBIT_DAY))
        self.assertIsNone(sky.aurora_chance([{"time_tag": "2026-11-01T15:00:00", "kp": 9.0}], 48.0, ORBIT_DAY))  # old
        self.assertIsNone(sky.aurora_chance([{"nonsense": 1}], 48.0, ORBIT_DAY))
        self.assertIsNone(sky.aurora_chance(None, 48.0, ORBIT_DAY))

    def test_clouds(self):
        forecast = {"hourly": {"time": ["2026-11-03T19:00", "2026-11-03T20:00"], "cloud_cover": [10, 90]}}
        when = datetime(2026, 11, 3, 20, 30, tzinfo=UTC)
        self.assertEqual(sky.clouds_at(forecast, when), 90)
        self.assertIsNone(sky.clouds_at({}, when))
        self.assertEqual(sky.sky_words(10), "clear")
        self.assertEqual(sky.sky_words(90), "overcast")
        self.assertEqual(sky.sky_words(None), "")


class PhraseTests(unittest.TestCase):
    def test_asks(self):
        for said, kind in (("What's in the sky tonight?", "tonight"), ("what can I see in the sky", "tonight"),
                           ("what planets can I see", "tonight"), ("show me the night sky", "tonight"),
                           ("what phase is the moon", "moon"), ("show me the moon", "moon"),
                           ("when's the next full moon", "moon"), ("is it a full moon tonight", "moon"),
                           ("when does the space station pass over", "iss"), ("where is the ISS", "iss"),
                           ("show me the space station", "iss"), ("when's the next eclipse", "events"),
                           ("any cool space events coming up", "events"), ("next meteor shower", "events"),
                           ("any chance of a meteor shower coming up", "events"),
                           ("turn on space alerts", "alerts on"), ("turn off sky alerts", "alerts off"),
                           ("tell me when the space station passes overhead", "ask iss")):
            self.assertEqual(sky.request(said), kind, said)

    def test_not_asks(self):
        for said in ("why is the sky blue", "what is the moon made of", "what is the space station", "what is an eclipse",
                     "how big is jupiter", "play the movie Moon", "what's the weather", "show me my projects"):
            self.assertIsNone(sky.request(said), said)


def place():
    return KANSAS_CITY


class WatcherTests(unittest.TestCase):
    def watcher(self, when, **kw):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.now = [when.timestamp()]
        self.said = []
        settings = dict(say=self.said.append, clock=lambda: self.now[0], fetch=lambda url: "[]")
        settings.update(kw)
        orbit = Path(self.folder.name) / "orbit.json"
        orbit.write_text(json.dumps({"orbit": ORBIT, "fetched": self.now[0]}))
        return sky.SkyWatcher(Path(self.folder.name) / "sky.json", orbit, place, **settings)

    def planned(self, watcher, kind, **fields):
        """A plan of one alert, as build_plan makes them."""
        when = datetime.fromtimestamp(self.now[0], UTC)
        alert = {"id": fields.pop("id", "one"), "at": when, "kind": kind, "text": "Look up.", **fields}
        watcher.build_plan = lambda p, n: [alert]
        return alert

    def test_off_until_asked_and_no_network_then(self):
        def no_network(url):
            raise AssertionError("contacted " + url)
        watcher = self.watcher(ORBIT_DAY, fetch=no_network)
        self.assertEqual(watcher.check_once(), [])
        self.assertEqual(self.said, [])
        self.assertFalse(watcher.enabled)

    def test_the_northern_lights_keep_to_the_hours_he_promised(self):
        watcher = self.watcher(ORBIT_DAY)
        place = Place("Kansas City", "Missouri", "United States", 39.1, -94.58, "America/Chicago")
        with mock.patch.object(sky, "fetch_aurora", return_value=7.3) as asked:
            night = watcher.build_plan(place, datetime(2026, 11, 4, 9, 0, tzinfo=UTC))  # 3 AM there
            self.assertNotIn("aurora", [alert["kind"] for alert in night])
            asked.assert_not_called()
            day = watcher.build_plan(place, datetime(2026, 11, 4, 18, 0, tzinfo=UTC))  # noon there
            self.assertIn("aurora", [alert["kind"] for alert in day])

    def test_says_it_once(self):
        watcher = self.watcher(ORBIT_DAY)
        watcher.set_enabled(True)
        self.planned(watcher, "iss")
        self.assertEqual(watcher.check_once(), ["Look up."])
        self.assertEqual(watcher.check_once(), [])
        watcher._planned_at = 0  # a new plan with the same alert: still once
        self.assertEqual(watcher.check_once(), [])
        self.assertEqual(self.said, ["Look up."])

    def test_it_survives_a_restart_without_repeating(self):
        watcher = self.watcher(ORBIT_DAY)
        watcher.set_enabled(True)
        self.planned(watcher, "iss")
        watcher.check_once()
        again = sky.SkyWatcher(watcher.state_path, Path(self.folder.name) / "orbit.json", place, say=self.said.append,
                               clock=lambda: self.now[0], fetch=lambda url: "[]")
        again.build_plan = watcher.build_plan
        self.assertEqual(again.check_once(), [])

    def test_a_late_alert_is_dropped(self):
        watcher = self.watcher(ORBIT_DAY)
        watcher.set_enabled(True)
        alert = self.planned(watcher, "iss")
        alert["at"] -= timedelta(minutes=sky.LATE_MINUTES + 1)
        self.assertEqual(watcher.check_once(), [])

    def test_not_over_a_movie_or_game(self):
        watcher = self.watcher(ORBIT_DAY, busy=lambda: True)
        watcher.set_enabled(True)
        self.planned(watcher, "iss")
        self.assertEqual(watcher.check_once(), [])

    def test_not_when_its_overcast(self):
        when = ORBIT_DAY
        forecast = {"hourly": {"time": [when.strftime("%Y-%m-%dT%H:00")], "cloud_cover": [95]}}
        watcher = self.watcher(when, forecast=lambda p: forecast)
        watcher.set_enabled(True)
        self.planned(watcher, "iss", check_clouds=when)
        self.assertEqual(watcher.check_once(), [])
        forecast["hourly"]["cloud_cover"] = [20]
        self.planned(watcher, "iss", check_clouds=when, id="two")
        watcher._planned_at = 0
        self.assertEqual(watcher.check_once(), ["Look up."])

    def test_a_failed_cloud_check_does_not_lose_the_alert(self):
        def down(p):
            raise OSError("down")
        watcher = self.watcher(ORBIT_DAY, forecast=down)
        watcher.set_enabled(True)
        self.planned(watcher, "iss", check_clouds=ORBIT_DAY)
        self.assertEqual(watcher.check_once(), ["Look up."])

    def test_the_real_plan_keeps_quiet_hours(self):
        watcher = self.watcher(ORBIT_DAY, hours=(8, 23))
        plan = watcher.build_plan(KANSAS_CITY, ORBIT_DAY)
        for alert in plan:
            if alert["kind"] == "iss":
                self.assertTrue(8 <= alert["at"].hour < 23, alert["at"])
                self.assertIn("space station passes over in 5 minutes", alert["text"])

    def test_an_eclipse_gets_the_evening_before_and_just_before(self):
        watcher = self.watcher(utc(2026, 8, 27, 1), hours=(8, 23))
        plan = watcher.build_plan(KANSAS_CITY, utc(2026, 8, 27, 1))
        ids = [a["id"] for a in plan if a["kind"] == "lunar eclipse"]
        self.assertEqual(len(ids), 2, ids)
        before = next(a for a in plan if a["id"].endswith("-before"))
        self.assertEqual(before["at"].hour, sky.EVENING_HOUR)
        self.assertLess(before["at"], next(a for a in plan if a["id"].endswith("-soon"))["at"])

    def test_a_solar_eclipse_warns_about_eyes(self):
        event = sky.Sky(32.78, -96.80, "America/Chicago").solar_eclipses(utc(2024, 4, 1), 30)[0]
        self.assertIn("eclipse glasses", sky.event_words(event, utc(2024, 4, 1)))

    def test_the_screen_gets_the_last_alert_once(self):
        watcher = self.watcher(ORBIT_DAY)
        watcher.set_enabled(True)
        self.planned(watcher, "iss")
        watcher.check_once()
        self.assertEqual(watcher.handle({"op": "alert"})["alert"]["kind"], "iss")
        self.assertIsNone(watcher.handle({"op": "alert"})["alert"])

    def test_requests(self):
        watcher = self.watcher(ORBIT_DAY)
        self.assertTrue(watcher.handle({"op": "set", "on": True})["ok"])
        self.assertTrue(watcher.handle({"op": "get"})["on"])
        self.assertTrue(watcher.handle({"op": "set", "on": False})["ok"])
        self.assertFalse(watcher.handle({"op": "nonsense"})["ok"])


class ScreenTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        pygame.init()

    @classmethod
    def tearDownClass(cls):
        pygame.quit()

    def renderer(self, size=(640, 480)):
        app = smoke.FakeApp()
        app.SCREEN_WIDTH, app.SCREEN_HEIGHT = size
        app._screen = pygame.Surface(size)
        return app, FaceRenderer(app)

    def lit_share(self, app, center, radius):
        count = 0
        for x in range(center[0] - radius, center[0] + radius):
            if tuple(app._screen.get_at((x, center[1])))[:3] == sky_screen.MOON_LIT:
                count += 1
        return count / (2 * radius)

    def test_the_moon_shows_its_phase(self):
        app, _ = self.renderer()
        center, radius = (200, 200), 80
        shares = {}
        for cycle in (0.0, 0.25, 0.5, 0.75):
            app._screen.fill((0, 0, 0))
            sky_screen.draw_moon(app._screen, center, radius, cycle)
            shares[cycle] = self.lit_share(app, center, radius)
        self.assertLess(shares[0.0], 0.05)
        self.assertGreater(shares[0.5], 0.9)
        self.assertTrue(0.3 < shares[0.25] < 0.7)
        self.assertTrue(0.3 < shares[0.75] < 0.7)
        # waxing lights the right, waning the left
        app._screen.fill((0, 0, 0))
        sky_screen.draw_moon(app._screen, center, radius, 0.25)
        self.assertEqual(tuple(app._screen.get_at((center[0] + 60, center[1] + 40)))[:3], sky_screen.MOON_LIT)
        app._screen.fill((0, 0, 0))
        sky_screen.draw_moon(app._screen, center, radius, 0.75)
        self.assertEqual(tuple(app._screen.get_at((center[0] - 60, center[1] + 40)))[:3], sky_screen.MOON_LIT)

    def test_the_station_art_is_square(self):
        self.assertEqual(len({len(row) for row in sky_screen.ISS_ART}), 1)
        self.assertLessEqual(set("".join(sky_screen.ISS_ART)), set("BWG."))

    def test_every_event_moves(self):
        app, _ = self.renderer()
        rect = pygame.Rect(40, 40, 200, 150)
        for kind in ("meteors", "lunar eclipse", "solar eclipse", "conjunction", "aurora", "iss"):
            frames = set()
            for now in (0.3, 2.6):
                app._screen.fill((0, 0, 0))
                sky_screen.draw_event(app._screen, kind, rect, now)
                frames.add(pygame.image.tostring(app._screen, "RGB"))
            self.assertEqual(len(frames), 2, kind)
            self.assertEqual(app._screen.get_clip(), app._screen.get_rect())

    def chat(self):
        chat = Chat.__new__(Chat)
        chat.library_view = lib.LibraryView()
        chat.managed_display_active = lambda: False
        chat.note_user_activity = lambda: None
        chat.supervisor = None
        self.said = []
        chat._say_local = lambda command, text: self.said.append(text) or True
        chat._weather_settings = mock.Mock()
        chat._weather_settings.load.return_value = KANSAS_CITY
        chat._weather_service = types.SimpleNamespace(forecast=lambda p: {"hourly": {}})
        return chat

    def test_every_screen_draws_at_every_size(self):
        chat = self.chat()
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "orbit.json"
            path.write_text(json.dumps({"orbit": ORBIT, "fetched": 9e12}))
            with mock.patch.object(config, "SKY_ORBIT_PATH", path), \
                    mock.patch.object(sky, "ORBIT_TOO_OLD_DAYS", 10 ** 6):
                for size in ((640, 480), (1024, 768), (1920, 1080)):
                    app, renderer = self.renderer(size)
                    for said in ("what's in the sky tonight", "what phase is the moon", "when does the space station pass",
                                 "any space events coming up", "when's the next eclipse"):
                        self.assertTrue(chat._handle_sky_command(said), said)
                        for now in (0.5, 9.0):
                            sky_screen.render(renderer, chat.library_view.snapshot(), now)
                    chat.library_view.open("sky", [], grid=False, extra={"mode": "alert", "event_kind": "iss", "text": "Look up!"})
                    sky_screen.render(renderer, chat.library_view.snapshot(), 1.0)
                    chat.library_view.open("sky", [], grid=False, extra={"mode": "alert", "event_kind": "meteors", "text": "Look up!"})
                    sky_screen.render(renderer, chat.library_view.snapshot(), 1.0)

    def test_the_face_screen_hands_over(self):
        app, renderer = self.renderer()
        view = lib.LibraryView()
        view.open("sky", [], grid=False, extra={"mode": "alert", "event_kind": "iss", "text": "x"})
        app.chat.status["library"] = view.snapshot()
        renderer._saver_guard = type("QuietGuard", (), {"hold_off": lambda self: None})()
        with mock.patch.object(sky_screen, "render") as drawn:
            renderer.render()
        drawn.assert_called_once()


class ChatTests(unittest.TestCase):
    def setUp(self):
        chat = self.chat = Chat.__new__(Chat)
        chat.library_view = lib.LibraryView()
        chat.managed_display_active = lambda: False
        chat.note_user_activity = lambda: None
        chat.supervisor = None
        self.said = []
        chat._say_local = lambda command, text: self.said.append(text) or True
        chat._weather_settings = mock.Mock()
        chat._weather_settings.load.return_value = KANSAS_CITY
        chat._weather_service = types.SimpleNamespace(forecast=lambda p: {"hourly": {}})
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        path = Path(self.folder.name) / "orbit.json"
        path.write_text(json.dumps({"orbit": ORBIT, "fetched": 9e12}))
        for patcher in (mock.patch.object(config, "SKY_ORBIT_PATH", path), mock.patch.object(sky, "ORBIT_TOO_OLD_DAYS", 10 ** 6)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_other_requests_pass_through(self):
        self.assertFalse(self.chat._handle_sky_command("what's the weather"))
        self.assertFalse(self.chat._handle_sky_command("why is the sky blue"))

    def test_no_place_says_how(self):
        self.chat._weather_settings.load.return_value = None
        self.assertTrue(self.chat._handle_sky_command("what's in the sky tonight"))
        self.assertIn("set my weather location", self.said[-1])
        self.assertFalse(self.chat.library_view.active)

    def test_without_the_package_he_says_so(self):
        with mock.patch.object(sky, "ephem", None):
            self.assertTrue(self.chat._handle_sky_command("what phase is the moon"))
        self.assertIn("star charts", self.said[-1])

    def test_tonight_opens_and_only_closes(self):
        self.assertTrue(self.chat._handle_sky_command("what's in the sky tonight"))
        self.assertIn("The moon is", self.said[-1])
        shot = self.chat.library_view.snapshot()
        self.assertEqual((shot["kind"], shot["extra"]["mode"]), ("sky", "tonight"))
        self.assertFalse(self.chat._bare_library_command("down"))
        self.assertFalse(self.chat._bare_library_command("three"))
        self.assertTrue(self.chat._bare_library_command("close"))

    def test_the_long_list_scrolls(self):
        self.chat._handle_sky_command("any space events coming up")
        self.assertEqual(self.chat.library_view.snapshot()["extra"]["mode"], "events")
        self.assertGreater(self.chat.library_view.snapshot()["total"], 4)
        self.assertTrue(self.chat._bare_library_command("down"))
        self.assertEqual(self.chat.library_view.snapshot()["first"], 5)

    def test_a_movie_keeps_the_screen_but_he_still_answers(self):
        self.chat.managed_display_active = lambda: True
        self.chat._handle_sky_command("what phase is the moon")
        self.assertIn("The moon is", self.said[-1])
        self.assertFalse(self.chat.library_view.active)

    def supervisor_says(self, on):
        self.chat.supervisor = mock.Mock()
        self.chat.supervisor.request.side_effect = lambda action, **kw: {"ok": True, "on": on}

    def turned_on(self):
        return mock.call("sky", op="set", on=True) in self.chat.supervisor.request.call_args_list

    def asked(self, said="tell me when the space station passes"):
        self.assertTrue(self.chat._handle_sky_command(said))
        self.assertEqual(self.said[-1], "Do you want me to turn on space alerts, or did you just want to know the next pass?")

    def test_a_maybe_asks_and_changes_nothing(self):
        self.supervisor_says(False)
        self.asked()
        self.assertFalse(self.turned_on())

    def test_saying_to_turn_them_on(self):
        for said in ("turn them on", "Turn them on.", "turn it on", "yes, turn them on", "turn on alerts", "alerts", "the alerts", "the first one", "Hey BMO, turn them on", "alerts please", "space a lurch"):
            self.supervisor_says(False)
            self.asked()
            self.assertTrue(self.chat._handle_sky_command(said), said)
            self.assertTrue(self.turned_on(), said)

    def test_just_the_next_pass_changes_nothing(self):
        with mock.patch.object(Chat, "_sky_screen", return_value=("The pass is at nine.", {}, [], None)) as screen:
            for said in ("just the next pass", "the next pass", "next pass", "no, just the next pass", "yes", "Yeah.", "sure", "just tell me when", "I just wanted to know", "I just want to know the next pass", "the second one", "the next past"):
                self.supervisor_says(False)
                self.asked()
                self.assertTrue(self.chat._handle_sky_command(said), said)
                self.assertFalse(self.turned_on(), said)
                self.assertEqual(screen.call_args[0][0], "iss", said)
                self.assertEqual(self.said[-1], "The pass is at nine. If you want alerts too, say turn on space alerts.", said)

    def test_no_leaves_them_off(self):
        for said in ("no", "No.", "no thanks", "never mind", "neither"):
            self.supervisor_says(False)
            self.asked()
            self.assertTrue(self.chat._handle_sky_command(said), said)
            self.assertFalse(self.turned_on(), said)
            self.assertEqual(self.said[-1], "Okay, I'll leave them off.", said)

    def test_both(self):
        self.supervisor_says(False)
        with mock.patch.object(Chat, "_sky_screen", return_value=("The pass is at nine.", {}, [], None)):
            self.asked()
            self.assertTrue(self.chat._handle_sky_command("both"))
        self.assertTrue(self.turned_on())
        self.assertEqual(self.said[-1], "The pass is at nine.")

    def test_something_in_the_sky_offers_whats_coming_up(self):
        self.supervisor_says(False)
        self.chat._handle_sky_command("let me know when something's happening in the sky")
        self.assertIn("what's coming up?", self.said[-1])
        with mock.patch.object(Chat, "_sky_screen", return_value=("Soon.", {}, [], None)) as screen:
            self.chat._handle_sky_command("just what's coming up")
        self.assertEqual(screen.call_args[0][0], "events")

    def test_already_on_just_answers(self):
        self.supervisor_says(True)
        with mock.patch.object(Chat, "_sky_screen", return_value=("The pass is at nine.", {}, [], None)):
            self.chat._handle_sky_command("tell me when the space station passes")
        self.assertEqual(self.said[-1], "The pass is at nine. Space alerts are already on.")

    def test_the_question_is_over_after_anything_else(self):
        self.supervisor_says(False)
        self.asked()
        self.assertFalse(self.chat._handle_sky_command("why is the sky blue"))
        self.assertFalse(self.chat._handle_sky_command("turn them on"))  # about something else now
        self.assertFalse(self.turned_on())

    def test_the_question_runs_out(self):
        self.supervisor_says(False)
        self.asked()
        self.chat._sky_offer["at"] -= sky.OFFER_SECONDS + 1
        self.assertFalse(self.chat._handle_sky_command("turn them on"))
        self.assertFalse(self.turned_on())

    def test_without_a_question_these_mean_nothing(self):
        self.supervisor_says(False)
        for said in ("yes", "no", "turn them on", "both", "the next pass", "alerts"):
            self.assertFalse(self.chat._handle_sky_command(said), said)
        self.assertFalse(self.turned_on())

    def test_not_now_leaves_them_off(self):
        for said in ("not now", "not right now", "Not now."):
            self.assertEqual(sky.offer_reply(said), "no", said)

    def test_whisper_in_silence_doesnt_answer(self):
        for said in ("you", "Thank you.", "thanks", "Bye.", "okay", "I just want to watch a movie", "just tell me a joke", "I want pizza", "know what I mean", "what time is it",
                     "turn on the lights", "turn the lights on", "the first time", "the one with the dragon", "set a timer"):
            self.assertIsNone(sky.offer_reply(said), said)

    def test_alerts_by_voice(self):
        calls = []
        self.chat.supervisor = types.SimpleNamespace(request=lambda action, **kw: calls.append((action, kw)) or {"ok": True})
        self.chat._handle_sky_command("turn on space alerts")
        self.assertEqual(calls[-1], ("sky", {"op": "set", "on": True}))
        self.assertIn("between 8 AM and 11 PM", self.said[-1])
        self.chat._handle_sky_command("turn off space alerts")
        self.assertEqual(calls[-1], ("sky", {"op": "set", "on": False}))
        self.assertEqual(self.said[-1], "Okay, no more sky alerts.")
        self.chat.supervisor = None
        self.chat._handle_sky_command("turn on space alerts")
        self.assertIn("nothing changed", self.said[-1])

    def test_no_orbit_is_said_plainly(self):
        with mock.patch.object(sky.OrbitStore, "get", return_value=None):
            self.chat._handle_sky_command("when does the space station pass over")
        self.assertIn("can't get the space station's orbit", self.said[-1])

    def test_wired_in(self):
        chat = (ROOT / "systems" / "chat.py").read_text()
        self.assertLess(chat.index("if self._handle_sky_command(command):"), chat.index("topic = live_data_topic(command)"))
        self.assertIn('target=self._watch_sky_alerts', chat)
        supervisor = (ROOT / "supervisor.py").read_text()
        self.assertIn('if action == "sky":', supervisor)
        self.assertIn("service.sky.start()", supervisor)
        self.assertIn("ephem==", (ROOT / "installer" / "requirements.lock").read_text())
        self.assertIn("ephem==", (ROOT / "requirements.txt").read_text())
        for name in ("space events", "space alerts", "phase is the moon"):
            self.assertIn(name, (ROOT / "VOICE_COMMANDS.md").read_text())
            self.assertIn(name, (ROOT / "voice_commands.py").read_text())


if __name__ == "__main__":
    unittest.main()
