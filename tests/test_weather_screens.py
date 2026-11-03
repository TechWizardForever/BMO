"""The long-term forecast, the weather windows and the radar (owner 2026-11-02), with no network."""
import io
import os
from pathlib import Path
import sys
import tempfile
import threading
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
from systems import library as lib, radar, weather as wx, weather_screen
from systems.chat import Chat
from systems.grounding import live_data_topic
from systems.render import FaceRenderer

MONDAY = "2026-11-02"
DATES = [f"2026-11-{day:02d}" for day in range(2, 18)]  # 16 days, starting on a Monday
SPRINGFIELD = wx.Place("Springfield", "Oregon", "United States", 44.05, -123.02, "America/Los_Angeles")


def forecast(days=16):
    """As Open-Meteo answers: a wide spread of skies, and a little rain on most days."""
    codes = [0, 2, 3, 45, 61, 65, 66, 73, 95, 1, 80, 85, 99, 51, 48, 75][:days]
    return {
        "current": {"time": f"{MONDAY}T15:00", "temperature_2m": 53.8, "apparent_temperature": 51.2, "weather_code": 3,
                    "wind_speed_10m": 6.4, "relative_humidity_2m": 65, "dew_point_2m": 42.1, "is_day": 1,
                    "cloud_cover": 100, "pressure_msl": 1016.1, "wind_direction_10m": 135, "wind_gusts_10m": 10.2,
                    "visibility": 104658.8, "uv_index": 0.0},
        "current_units": {"visibility": "ft"},
        "daily": {"time": DATES[:days], "weather_code": codes,
                  "temperature_2m_max": [60 + n for n in range(days)], "temperature_2m_min": [40 + n for n in range(days)],
                  "precipitation_probability_max": [(n * 7) % 100 for n in range(days)],
                  "precipitation_sum": [0.1] * days, "snowfall_sum": [0.0] * days, "wind_speed_10m_max": [8.0] * days,
                  "wind_gusts_10m_max": [15.0] * days, "wind_direction_10m_dominant": [270] * days,
                  "uv_index_max": [3.2] * days, "sunrise": [f"{d}T06:46" for d in DATES[:days]],
                  "sunset": [f"{d}T17:10" for d in DATES[:days]]},
        "hourly": {"time": [f"{d}T{h:02d}:00" for d in DATES[:days] for h in range(24)],
                   "temperature_2m": [50.0 + h % 12 for _ in range(days) for h in range(24)],
                   "precipitation_probability": [h * 3 for _ in range(days) for h in range(24)],
                   "weather_code": [3 for _ in range(days * 24)]},
    }


class WhichDaysTests(unittest.TestCase):
    def test_days_mentioned(self):
        for said, days in (("what's the weather", [0]), ("how's the weather today", [0]), ("tonight's forecast", [0]),
                           ("weather tomorrow", [1]), ("the day after tomorrow", [2]),
                           ("today and tomorrow", [0, 1]), ("what's the forecast on Friday", [4]),
                           ("Friday and Saturday", [4, 5]), ("friday's weather", [4]), ("Monday", [0]),
                           ("next Monday", [7]), ("this weekend", [5, 6]), ("the weekend forecast", [5, 6]),
                           ("this week", list(range(7))), ("7 day forecast", list(range(7))),
                           ("seven-day forecast", list(range(7))), ("next week", list(range(7, 14))),
                           ("the next three days", [0, 1, 2]), ("ten day forecast", list(range(10))),
                           ("long term forecast", list(range(16))), ("the extended forecast", list(range(16))),
                           ("long-range forecast", list(range(16))), ("two week forecast", list(range(16))),
                           ("16 day forecast", list(range(16)))):
            self.assertEqual(wx.days_asked(said, DATES), days, said)

    def test_never_past_the_forecast(self):
        self.assertEqual(wx.days_asked("99 day forecast", DATES), list(range(16)))
        self.assertEqual(wx.days_asked("long term", DATES[:3]), [0, 1, 2])
        self.assertEqual(wx.days_asked("next week", DATES[:3]), [0])  # no such days: today, not a guess
        self.assertEqual(wx.days_asked("friday", []), [0])

    def test_a_weekend_seen_from_the_weekend(self):
        saturday = [f"2026-11-{day:02d}" for day in range(7, 23)]
        self.assertEqual(wx.days_asked("this weekend", saturday), [0, 1])
        sunday = [f"2026-11-{day:02d}" for day in range(8, 24)]
        self.assertEqual(wx.days_asked("this weekend", sunday), [0])

    def test_when_words_leave_the_town(self):
        for said, left in (("Friday", ""), ("this weekend", ""), ("the next ten days", ""), ("tomorrow", ""),
                           ("Paris this week", "Paris"), ("Salem, Oregon next week", "Salem, Oregon"),
                           ("Boston", "Boston")):
            self.assertEqual(wx.without_when_words(said), left, said)


class QuestionTests(unittest.TestCase):
    def test_new_questions_never_reach_a_model(self):
        for said in ("what's the long term forecast", "give me the extended forecast", "the seven day forecast",
                     "what's the 10-day forecast", "what's the weather this weekend", "weather on Friday",
                     "forecast for next week", "show me the weather", "show the forecast", "bring up the weather",
                     "show me the radar", "show the weather radar", "pull up the rain radar", "weather station",
                     "show me the weather map", "tomorrow's forecast"):
            self.assertEqual(live_data_topic(said), "weather", said)

    def test_still_not_weather(self):
        for said in ("how does weather work?", "write a weather app in python", "what is radar", "how does radar work",
                     "what does a forecast mean", "show me my projects", "set a timer for ten minutes"):
            self.assertIsNone(live_data_topic(said), said)

    def test_radar_words(self):
        self.assertTrue(radar.asked_for("show me the radar"))
        self.assertTrue(radar.asked_for("weather map"))
        self.assertFalse(radar.asked_for("what's the weather"))


class ReportTests(unittest.TestCase):
    def service(self, data):
        return wx.WeatherService(fetch=lambda url, params, timeout: data)

    def test_asks_for_everything_the_screens_show(self):
        seen = {}

        def fetch(url, params, timeout):
            seen.update(params)
            return forecast()
        wx.WeatherService(fetch=fetch).forecast(SPRINGFIELD)
        self.assertEqual(seen["forecast_days"], 16)
        for name in ("relative_humidity_2m", "pressure_msl", "wind_gusts_10m", "visibility", "uv_index"):
            self.assertIn(name, seen["current"])
        for name in ("precipitation_sum", "wind_gusts_10m_max", "uv_index_max", "sunrise", "sunset"):
            self.assertIn(name, seen["daily"])
        self.assertIn("precipitation_probability", seen["hourly"])

    def test_several_days_said_short_and_named(self):
        said = self.service(forecast()).report(SPRINGFIELD, "this week")
        self.assertTrue(said.startswith("The next 7 days in Springfield: highs from 60 to 66, lows from 40 to 46."), said)
        self.assertIn("Rain is most likely Sunday, at 42 percent.", said)
        self.assertNotIn("rough guide", said)
        self.assertIn("That's from Open-Meteo", said)
        self.assertIn("rough guide", self.service(forecast()).report(SPRINGFIELD, "long term forecast"))

    def test_named_days_said_one_by_one(self):
        said = self.service(forecast()).report(SPRINGFIELD, "Friday and Saturday")
        self.assertTrue(said.startswith("Friday in Springfield:"), said)
        self.assertIn("Saturday in Springfield:", said)

    def test_a_missing_number_stays_missing(self):
        card = wx.day_card({"daily": {"time": [MONDAY]}}, 0)
        self.assertIsNone(card["high"])
        self.assertIsNone(card["rain"])
        self.assertEqual(weather_screen.dash(card["high"]), "--")
        self.assertEqual(wx.station({})["temp"], None)

    def test_rain_and_snow_amounts_read_cleanly(self):
        data = forecast()
        data["daily"]["precipitation_sum"][0] = 0.177
        data["daily"]["snowfall_sum"][0] = 2.54
        data["daily_units"] = {"precipitation_sum": "inch", "snowfall_sum": "cm"}
        card = wx.day_card(data, 0)
        self.assertEqual((card["precip"], card["snow"]), ("0.18 in", "2.5 cm"))

    def test_the_station_numbers(self):
        now = wx.station(forecast())
        self.assertEqual((now["temp"], now["feels"], now["humidity"], now["wind"], now["wind_from"]), (54, 51, 65, 6, "SE"))
        self.assertEqual((now["pressure"], now["visibility"]), ("30.01 in", "20 mi"))
        self.assertEqual(wx.station(forecast(), False)["pressure"], "1016 hPa")

    def test_hours(self):
        rows = wx.hours(forecast(), 0)
        self.assertEqual((rows[0]["hour"], len(rows)), ("3p", 12))  # from now
        self.assertEqual(wx.hours(forecast(), 2)[0]["hour"], "7a")  # another day: its daytime

    def test_every_code_has_a_sky(self):
        for code in (0, 1, 2, 3, 45, 48, 51, 61, 63, 66, 71, 77, 80, 82, 85, 95, 99, None, 1234):
            self.assertIn(wx.sky_kind(code), {"clear", "partly", "cloudy", "fog", "light rain", "rain", "ice", "snow", "storm"})


class ScreenTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        pygame.init()

    @classmethod
    def tearDownClass(cls):
        pygame.quit()

    def renderer(self, size):
        app = smoke.FakeApp()
        app.SCREEN_WIDTH, app.SCREEN_HEIGHT = size
        app._screen = pygame.Surface(size)
        return app, FaceRenderer(app)

    def view(self, days, data=None):
        data = data or forecast()
        cards = [wx.day_card(data, day) for day in days]
        extra = {"place": "SPRINGFIELD", "source": "Open-Meteo 3:00 PM", "all_days": cards}
        if len(days) == 1:
            extra.update(station=True, hours=wx.hours(data, days[0]), current=wx.station(data) if days[0] == 0 else None)
        view = lib.LibraryView()
        view.open("weather", [lib.Item(c["name"], "weather", str(d), data=c) for d, c in zip(days, cards)],
                  grid=False, per_page=8, extra=extra)
        return view

    def test_every_layout_draws_at_every_size(self):
        for size in ((640, 480), (1024, 768), (1280, 720), (1920, 1080)):
            app, renderer = self.renderer(size)
            for days in ([0], [1], [1, 2], [0, 1, 2], list(range(7)), list(range(16))):
                view = self.view(days)
                for _ in range(2 if len(days) > 8 else 1):
                    weather_screen.render(renderer, view.snapshot(), 12.3)
                    view.scroll(1)

    def test_a_forecast_with_gaps_still_draws(self):
        app, renderer = self.renderer((640, 480))
        empty = {"daily": {"time": DATES[:2]}}
        for days in ([0], [1], [0, 1]):
            weather_screen.render(renderer, self.view(days, empty).snapshot(), 1.0)

    def test_the_sky_moves(self):
        app, renderer = self.renderer((640, 480))
        rect = pygame.Rect(50, 50, 200, 200)
        for kind in ("clear", "partly", "cloudy", "fog", "light rain", "rain", "ice", "snow", "storm"):
            frames = set()
            for now in (0.0, 1.5):
                app._screen.fill((0, 0, 0))
                weather_screen.draw_icon(app._screen, kind, rect, now, seed=1)
                frames.add(pygame.image.tostring(app._screen, "RGB"))
            self.assertEqual(len(frames), 2, kind)
            weather_screen.draw_icon(app._screen, kind, rect, 0.0, night=True)  # and it leaves the clip alone
            self.assertEqual(app._screen.get_clip(), app._screen.get_rect())

    def test_the_face_comes_back_when_it_closes(self):
        app, renderer = self.renderer((640, 480))
        view = self.view([0])
        app.chat.status["library"] = view.snapshot()
        renderer._saver_guard = type("QuietGuard", (), {"hold_off": lambda self: None})()
        with mock.patch.object(weather_screen, "render") as drawn:
            renderer.render()
        drawn.assert_called_once()
        view.close()
        app.chat.status["library"] = view.snapshot()
        with mock.patch.object(weather_screen, "render") as drawn:
            renderer.render()
        drawn.assert_not_called()

    def test_radar_screen_states(self):
        app, renderer = self.renderer((1024, 768))
        view = lib.LibraryView()
        view.open("radar", [], grid=False, extra={"place": "SPRINGFIELD", "loading": True})
        weather_screen.render(renderer, view.snapshot(), 1.0)
        view.update_extra(view.generation, loading=False)
        weather_screen.render(renderer, view.snapshot(), 1.0)
        picture = pygame.Surface((1024, 768))
        view.update_extra(view.generation, radar={"frames": [(10, picture), (0, picture)], "marker": (500, 400), "size": (1024, 768)})
        for now in (0.1, 1.0, 5.0):
            weather_screen.render(renderer, view.snapshot(), now)


def png(color=(10, 200, 10, 255)):
    surface = pygame.Surface((radar.TILE, radar.TILE), pygame.SRCALPHA)
    surface.fill(color)
    out = io.BytesIO()
    pygame.image.save(surface, out, "x.png")
    return out.getvalue()


class RadarTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        pygame.init()

    @classmethod
    def tearDownClass(cls):
        pygame.quit()

    def test_coverage(self):
        self.assertTrue(radar.covers(39.1, -94.6))
        for latitude, longitude in ((21.3, -157.8), (61.2, -149.9), (48.8, 2.3), (-33.9, 151.2)):
            self.assertFalse(radar.covers(latitude, longitude))

    def test_tile_arithmetic_is_the_standard_one(self):
        x, y = radar.tile_position(0.0, 0.0, 1)
        self.assertEqual((x, y), (1.0, 1.0))
        plan = radar.layout(39.1, -94.6)
        self.assertEqual(plan["size"], (radar.COLUMNS * radar.TILE, radar.ROWS * radar.TILE))
        self.assertTrue(0 <= plan["marker"][0] < plan["size"][0] and 0 <= plan["marker"][1] < plan["size"][1])

    def test_the_hour_loops_oldest_first_and_the_map_is_kept(self):
        calls = []
        lock = threading.Lock()

        def fetch(url):
            with lock:
                calls.append(url)
            return png()
        with tempfile.TemporaryDirectory() as folder:
            loop = radar.build(39.1, -94.6, Path(folder), fetch=fetch, workers=2)
            self.assertEqual([age for age, _ in loop["frames"]], list(radar.MINUTES_AGO))
            maps = [url for url in calls if "openstreetmap" in url]
            self.assertEqual(len(maps), radar.COLUMNS * radar.ROWS)
            self.assertTrue(any("nexrad-n0q-900913-m50m" in url for url in calls))
            self.assertTrue(any("nexrad-n0q-900913/" in url for url in calls))  # the latest
            calls.clear()
            radar.build(39.1, -94.6, Path(folder), fetch=fetch, workers=2)
            self.assertFalse([url for url in calls if "openstreetmap" in url])  # from the disk the second time

    def test_no_radar_is_said_not_shown_as_clear_skies(self):
        def fetch(url):
            if "nexrad" in url:
                raise OSError("down")
            return png()
        with tempfile.TemporaryDirectory() as folder, self.assertRaises(radar.RadarError):
            radar.build(39.1, -94.6, Path(folder), fetch=fetch, workers=2)

    def test_no_map_still_shows_the_rain(self):
        def fetch(url):
            if "openstreetmap" in url:
                raise OSError("down")
            return png()
        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual(len(radar.build(39.1, -94.6, Path(folder), fetch=fetch, workers=2)["frames"]), len(radar.MINUTES_AGO))


class ChatTests(unittest.TestCase):
    def setUp(self):
        chat = self.chat = Chat.__new__(Chat)
        chat.library_view = lib.LibraryView()
        chat.managed_display_active = lambda: False
        chat.note_user_activity = lambda: None
        self.said = []
        chat._say_local = lambda command, text: self.said.append(text) or True
        chat._weather_service = wx.WeatherService(fetch=lambda url, params, timeout: forecast())
        chat._weather_settings = mock.Mock()
        chat._weather_settings.load.return_value = SPRINGFIELD

    def ask(self, said):
        reply = self.chat._weather_reply("weather", said)
        self.chat._show_weather(said)
        return reply

    def test_just_today_gets_its_window(self):
        self.assertTrue(self.ask("what's the weather").startswith("Right now in Springfield"))
        shot = self.chat.library_view.snapshot()
        self.assertEqual((shot["kind"], shot["total"]), ("weather", 1))
        self.assertTrue(shot["extra"]["station"])
        self.assertEqual(shot["extra"]["current"]["temp"], 54)
        self.assertEqual(len(shot["extra"]["hours"]), 12)

    def test_another_single_day_has_no_right_now(self):
        self.ask("what's the weather tomorrow")
        shot = self.chat.library_view.snapshot()
        self.assertEqual(shot["tiles"][0]["data"]["name"], "Tomorrow")
        self.assertIsNone(shot["extra"]["current"])

    def test_each_day_mentioned_gets_a_box(self):
        self.ask("weather on Friday and Saturday")
        shot = self.chat.library_view.snapshot()
        self.assertEqual([t["data"]["name"] for t in shot["tiles"]], ["Fri", "Sat"])
        self.assertNotIn("station", shot["extra"])

    def test_the_long_term_forecast_scrolls_without_hey_bmo(self):
        self.ask("the long term forecast")
        self.assertEqual(self.chat.library_view.snapshot()["total"], 16)
        self.assertTrue(self.chat._bare_library_command("down"))
        self.assertEqual(self.chat.library_view.snapshot()["first"], 9)
        self.assertTrue(self.chat._bare_library_command("up"))
        self.assertEqual(self.chat.library_view.snapshot()["first"], 1)
        self.assertFalse(self.chat._bare_library_command("three"))  # no numbers to pick here
        self.assertFalse(self.chat._handle_library_command("number three"))
        self.assertTrue(self.chat._bare_library_command("close"))
        self.assertFalse(self.chat.library_view.active)

    def test_a_movie_or_game_keeps_the_screen(self):
        self.chat.managed_display_active = lambda: True
        self.assertIn("Right now", self.ask("what's the weather"))
        self.assertFalse(self.chat.library_view.active)

    def test_a_short_answer_goes_back_to_his_face_sooner(self):
        now = [0.0]
        self.chat.library_view = lib.LibraryView(clock=lambda: now[0])
        self.ask("what's the weather")
        now[0] = 59.0
        self.assertTrue(self.chat.library_view.active)
        now[0] = 61.0
        self.assertFalse(self.chat.library_view.active)
        self.ask("the long term forecast")
        now[0] += 100.0
        self.assertTrue(self.chat.library_view.active)

    def test_a_failed_forecast_opens_nothing(self):
        def down(url, params, timeout):
            raise wx.WeatherError("I couldn't reach the weather service")
        self.chat._weather_service = wx.WeatherService(fetch=down)
        self.assertIn("couldn't reach", self.ask("what's the weather"))
        self.assertFalse(self.chat.library_view.active)

    def test_radar_opens_loading_and_only_closes(self):
        started = []
        with mock.patch("threading.Thread", lambda **kw: type("T", (), {"start": lambda s: started.append(kw)})()):
            reply = self.ask("show me the radar")
        self.assertIn("radar for the last hour", reply)
        self.assertIn("what's already happened", reply)  # it never pretends to predict
        self.assertEqual(started[0]["name"], "bemo-radar-load")
        shot = self.chat.library_view.snapshot()
        self.assertEqual((shot["kind"], shot["extra"]["loading"]), ("radar", True))
        self.assertFalse(self.chat._bare_library_command("down"))
        self.assertFalse(self.chat._bare_library_command("three"))
        self.assertTrue(self.chat._bare_library_command("close"))

    def test_radar_failure_shows_none(self):
        with mock.patch.object(radar, "build", side_effect=radar.RadarError("no")):
            self.chat._open_radar(SPRINGFIELD)
            for thread in threading.enumerate():
                if thread.name == "bemo-radar-load":
                    thread.join(5)
        self.assertFalse(self.chat.library_view.snapshot()["extra"]["loading"])
        self.assertNotIn("radar", self.chat.library_view.snapshot()["extra"])

    def test_no_radar_outside_the_lower_48(self):
        honolulu = wx.Place("Honolulu", "Hawaii", "United States", 21.31, -157.86, "Pacific/Honolulu")
        self.assertIn("lower 48", self.chat._open_radar(honolulu))
        self.assertFalse(self.chat.library_view.active)

    def test_wired_in(self):
        src = (ROOT / "systems" / "chat.py").read_text()
        self.assertIn('self._show_weather(command)', src)
        render = (ROOT / "systems" / "render.py").read_text()
        self.assertIn("weather_screen.render(self, status[\"library\"], now)", render)


class CommandListTests(unittest.TestCase):
    def test_listed(self):
        for phrase in ("long term forecast", "radar", "this weekend"):
            self.assertIn(phrase, (ROOT / "VOICE_COMMANDS.md").read_text())
            self.assertIn(phrase, (ROOT / "voice_commands.py").read_text())


if __name__ == "__main__":
    unittest.main()
