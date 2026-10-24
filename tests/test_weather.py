"""Opt-in weather: lookup, reports, failures, and the voice handlers (no network)."""
import ast
import os
from pathlib import Path
import re
import sys
import tempfile
import types
import unittest

ROOT = Path(os.environ.get("BEMO_TEST_CODE_ROOT", Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(ROOT))
import config
from systems.grounding import HONEST_REPLIES, live_data_topic
from systems.weather import Place, WeatherError, WeatherService, WeatherSettings, geocode

GEO = {"results": [
    {"name": "Springfield", "admin1": "Oregon", "country": "United States", "country_code": "US",
     "latitude": 44.0462, "longitude": -123.0220, "timezone": "America/Los_Angeles", "postcodes": ["97477"]},
    {"name": "Springfield", "admin1": "Illinois", "country": "United States", "country_code": "US",
     "latitude": 39.7817, "longitude": -89.6501, "timezone": "America/Chicago", "postcodes": ["62701"]},
]}
FORECAST = {
    "current": {"time": "2026-10-18T15:00", "temperature_2m": 53.8, "apparent_temperature": 53.2,
                "weather_code": 45, "wind_speed_10m": 3.7},
    "daily": {"time": ["2026-10-18", "2026-10-19", "2026-10-20"], "temperature_2m_max": [54.3, 59.1, 60.0],
              "temperature_2m_min": [50.2, 47.1, 48.0], "precipitation_probability_max": [0, 70, 10],
              "weather_code": [45, 61, 3]},
}
SPRINGFIELD = Place("Springfield", "Oregon", "United States", 44.05, -123.02, "America/Los_Angeles")


def fake(data=None, calls=None, error=False):
    def fetch(url, params, timeout):
        if calls is not None:
            calls.append(params)
        if error:
            raise WeatherError("I couldn't reach the weather service")
        return data
    return fetch


class GeocodeTests(unittest.TestCase):
    def test_state_hint_and_zip_choose_the_right_town(self):
        self.assertEqual(geocode("Springfield, IL", fetch=fake(GEO)).region, "Illinois")
        self.assertEqual(geocode("62701", fetch=fake(GEO)).region, "Illinois")
        place = geocode("Springfield, OR.", fetch=fake(GEO))
        self.assertEqual((place.region, place.latitude, place.longitude), ("Oregon", 44.05, -123.02))  # ~1 km

    def test_unknown_place_is_none_not_a_guess(self):
        self.assertIsNone(geocode("Springfield, ZZ", fetch=fake(GEO)))
        self.assertIsNone(geocode("Nowhere", fetch=fake({"results": []})))


class ReportTests(unittest.TestCase):
    def test_today_and_tomorrow_name_source_and_time(self):
        ws = WeatherService(fetch=fake(FORECAST))
        today = ws.report(SPRINGFIELD, "what's the weather like today")
        self.assertEqual(today, "Right now in Springfield it's 54 degrees and foggy, wind 4 miles per hour. "
                                "Today: foggy, high 54, low 50, 0 percent chance of rain. "
                                "That's from Open-Meteo, updated at 3:00 PM.")
        self.assertTrue(ws.report(SPRINGFIELD, "is it going to rain tomorrow").startswith(
            "Tomorrow in Springfield: light rain, high 59, low 47, 70 percent chance of rain."))

    def test_cache_avoids_repeat_requests_and_rounds_coordinates(self):
        calls = []
        ws = WeatherService(fetch=fake(FORECAST, calls), clock=lambda: 100.0)
        ws.report(SPRINGFIELD, "today")
        ws.report(SPRINGFIELD, "tomorrow")
        self.assertEqual(len(calls), 1)
        self.assertEqual((calls[0]["latitude"], calls[0]["longitude"]), ("44.05", "-123.02"))

    def test_bad_answer_is_an_error(self):
        with self.assertRaises(WeatherError):
            WeatherService(fetch=fake({"error": True})).report(SPRINGFIELD)


class SettingsTests(unittest.TestCase):
    def test_save_load_forget(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings = WeatherSettings(Path(tmp) / "weather.json")
            self.assertIsNone(settings.load())
            settings.save(SPRINGFIELD)
            self.assertEqual(settings.load(), SPRINGFIELD)
            self.assertTrue(settings.forget())
            self.assertIsNone(settings.load())


class QuestionTests(unittest.TestCase):
    def test_weather_questions_never_reach_a_model(self):
        for q in ("what's the weather like today?", "how's the weather?", "is it raining outside",
                  "what is it like outside", "give me the weather report", "is it gonna rain tomorrow"):
            self.assertEqual(live_data_topic(q), "weather", q)
        for q in ("how does weather work?", "write a weather app in python", "set my weather location to Springfield"):
            self.assertIsNone(live_data_topic(q), q)


class VoiceTests(unittest.TestCase):
    def setUp(self):
        src = (ROOT / "systems" / "chat.py").read_text()
        chat = next(n for n in ast.parse(src).body if isinstance(n, ast.ClassDef) and n.name == "Chat")
        keep = {"_handle_weather_setup", "_weather_reply", "_weather_tools", "_WEATHER_SET",
                "_WEATHER_ASK_SAVED", "_WEATHER_FORGET", "_WEATHER_SETUPISH", "_weather_words",
                "_normalize_command_text", "_say_local"}
        body = [n for n in chat.body if (isinstance(n, ast.FunctionDef) and n.name in keep)
                or (isinstance(n, ast.Assign) and n.targets[0].id in keep)]
        self.tmp = tempfile.TemporaryDirectory()
        cfg = types.SimpleNamespace(ENABLE_WEATHER=True, WEATHER_FAHRENHEIT=True,
                                    WEATHER_SETTINGS_PATH=Path(self.tmp.name) / "weather.json")
        self.geo = {"result": SPRINGFIELD}
        ns = {"config": cfg, "re": re, "State": types.SimpleNamespace(RESPONDING="r"),
              "logger": types.SimpleNamespace(info=lambda *a, **k: None, warning=lambda *a, **k: None),
              "WeatherSettings": WeatherSettings, "WeatherError": WeatherError, "HONEST_REPLIES": HONEST_REPLIES,
              "WeatherService": lambda fahrenheit: WeatherService(fetch=fake(FORECAST)),
              "geocode": lambda q: self.geo.setdefault("asked", []).append(q) or self.geo["result"],
              "live_data_topic": live_data_topic}
        exec(compile(ast.fix_missing_locations(ast.Module(body=[ast.ClassDef(
            name="Mini", bases=[], keywords=[], body=body, decorator_list=[])], type_ignores=[])), "mini", "exec"), ns)
        self.c = ns["Mini"]()
        self.c.spoken = []
        self.c.speak = self.c.spoken.append
        self.c._archive_local_exchange = lambda *a: None
        self.c._confirm_by_voice = lambda question, **kw: self.c.spoken.append(question) or self.approve
        self.approve = True

    def tearDown(self):
        self.tmp.cleanup()

    def test_no_location_yet_explains_how_to_set_it(self):
        self.assertIn("set my weather location", self.c._weather_reply("weather", "what's the weather"))
        self.assertEqual(self.c._weather_reply("location", "where am i"), HONEST_REPLIES["location"])

    def test_set_location_is_confirmed_before_saving(self):
        self.assertTrue(self.c._handle_weather_setup("Set my weather location to Springfield, OR."))
        self.assertIn("I found Springfield, Oregon, United States.", self.c.spoken[0])
        self.assertEqual(self.c.spoken[-1], "Saved. I'll use Springfield for the weather.")
        self.assertTrue(self.c._weather_reply("weather", "how's the weather").startswith("Right now in Springfield"))
        self.assertIn("saved weather location is Springfield", self.c._weather_reply("location", "where am i"))

    def test_declined_or_unknown_place_is_not_saved(self):
        self.approve = False
        self.c._handle_weather_setup("set my weather location to Springfield")
        self.assertEqual(self.c.spoken[-1], "Okay, I didn't save it.")
        self.geo["result"] = None
        self.c._handle_weather_setup("my weather location is Atlantis")
        self.assertIn("couldn't find a place called Atlantis", self.c.spoken[-1])
        self.assertIsNone(WeatherSettings(Path(self.tmp.name) / "weather.json").load())

    def test_report_and_forget(self):
        WeatherSettings(Path(self.tmp.name) / "weather.json").save(SPRINGFIELD)
        self.c._handle_weather_setup("What's my weather location?")
        self.assertEqual(self.c.spoken[-1], "My weather location is Springfield, Oregon, United States.")
        self.c._handle_weather_setup("forget my weather location")
        self.assertEqual(self.c.spoken[-1], "Okay, I forgot the weather location.")

    def test_offline_is_honest(self):
        WeatherSettings(Path(self.tmp.name) / "weather.json").save(SPRINGFIELD)
        self.c._weather_service = WeatherService(fetch=fake(error=True))
        self.c._weather_settings = WeatherSettings(Path(self.tmp.name) / "weather.json")
        self.assertEqual(self.c._weather_reply("weather", "weather?"),
                         "I couldn't reach the weather service, so I can't tell you the weather right now.")


class LiveTestPhrases(VoiceTests):
    """Exact transcripts from the 2026-10-18 weather test; Main answered all of
    them before this fix, inventing a saved ZIP, a town and the weather."""

    SALEM = Place("Salem", "Oregon", "United States", 44.94, -123.03, "America/Los_Angeles")

    def test_misspelled_and_casual_set_requests_reach_the_tool(self):
        self.geo["result"] = self.SALEM
        for heard, place in (("set my wether location to 97301", "97301"),
                             ("I want the weather set for Salem, Oregon 97301", "Salem, Oregon 97301"),
                             ("set the weather to Bridgevale please.", "Bridgevale"),
                             ("Use Salem for the weather", "Salem")):
            self.geo["asked"] = []
            self.assertTrue(self.c._handle_weather_setup(heard), heard)
            self.assertEqual(self.geo["asked"], [place], heard)

    def test_asking_which_town_was_set_reads_the_saved_file(self):
        self.assertTrue(self.c._handle_weather_setup("what town do you think I just set for the weather?"))
        self.assertEqual(self.c.spoken[-1], "I don't have a weather location yet.")

    def test_other_setup_talk_gets_instructions_not_a_model(self):
        self.assertTrue(self.c._handle_weather_setup("can you change the weather zip code"))
        self.assertIn("set my weather location to", self.c.spoken[-1])
        self.assertFalse(self.c._handle_weather_setup("how's the weather today?"))  # a forecast question

    def test_weather_in_another_place_does_not_change_the_saved_one(self):
        WeatherSettings(Path(self.tmp.name) / "weather.json").save(SPRINGFIELD)
        self.geo["result"] = Place("Paris", "Île-de-France", "France", 48.85, 2.35, "Europe/Paris")
        self.assertTrue(self.c._weather_reply("weather", "what's the weather in Paris?").startswith("Right now in Paris"))
        self.assertEqual(WeatherSettings(Path(self.tmp.name) / "weather.json").load(), SPRINGFIELD)
        self.assertEqual(live_data_topic("what's the weather in Paris?"), "weather")

    def test_the_models_invented_replies_are_blocked(self):
        from systems.grounding import fabricated_live_claim
        self.assertEqual(fabricated_live_claim("Got it. Weather for 97301 (Silverton, OR) is set."), "unsaved")
        self.assertEqual(fabricated_live_claim("Sutter Creek, CA — confirmed. Weather’s sunny with a high of 68°F."), "fabricated")
        self.assertEqual(fabricated_live_claim("Got it. Weather set for Salem, OR (97302)."), "unsaved")
        for fine in ("Water boils at 100 °C at sea level.", "Rain forms when water vapour condenses.",
                     "I set up the project for you."):
            self.assertIsNone(fabricated_live_claim(fine), fine)


class ZipTests(unittest.TestCase):
    ZIPS = {"results": [
        {"name": "Salem", "admin1": "Oregon", "country": "United States", "country_code": "US",
         "latitude": 44.9429, "longitude": -123.0351, "timezone": "America/Los_Angeles", "postcodes": ["97301"]}]}

    def test_zip_anywhere_must_match_exactly(self):
        self.assertEqual(geocode("Salem, Oregon 97301", fetch=fake(self.ZIPS)).name, "Salem")
        self.assertIsNone(geocode("97302", fetch=fake(self.ZIPS)))  # no exact postcode: no guess


if __name__ == "__main__":
    unittest.main()
