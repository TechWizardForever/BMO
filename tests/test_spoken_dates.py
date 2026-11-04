"""Dates for the weather, and the weather for what's coming up in the sky (2026-11-03). Heard live: "what will the
weather be like on November 17th?" got today's weather, and "the forecast for Springfield, Oregon on November 8th" was looked
up as a town with the date on it. A date the forecast doesn't reach never gets today's in its place."""
from datetime import date, datetime, timedelta, timezone
import os
from pathlib import Path
import sys
import unittest
from unittest import mock

os.environ["SDL_VIDEODRIVER"] = "dummy"
os.environ["SDL_AUDIODRIVER"] = "dummy"
ROOT = Path(os.environ.get("BEMO_TEST_CODE_ROOT", Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(ROOT))
from systems import help_center, sky, spoken_dates, weather as wx
from systems.chat import Chat
from systems.grounding import live_data_topic
from systems.sound_alikes import put_right
from systems.weather import Place, WeatherService

TODAY = date(2026, 11, 3)
DATES = [(TODAY + timedelta(days=n)).isoformat() for n in range(15)]  # through November 17th
SPRINGFIELD = Place("Springfield", "Oregon", "United States", 44.05, -123.02, "America/Los_Angeles")


def forecast():
    count = len(DATES)
    hours = [f"{day}T{hour:02d}:00" for day in DATES for hour in range(24)]
    return {"current": {"time": "2026-11-03T13:00", "temperature_2m": 50.0, "weather_code": 1, "wind_speed_10m": 4.0},
            "daily": {"time": list(DATES), "temperature_2m_max": [55.0 + n for n in range(count)],
                      "temperature_2m_min": [35.0 + n for n in range(count)], "precipitation_probability_max": [10] * count,
                      "weather_code": [3] * count},
            "hourly": {"time": hours, "cloud_cover": [10] * len(hours)}}


def service():
    return WeatherService(fetch=lambda url, params, timeout: forecast())


class Reading(unittest.TestCase):
    def days(self, said):
        return wx.days_asked(said, DATES)

    def test_heard_live(self):
        for said, day in (("what will the weather be like on November 17th?", 14), ("what's the forecast for November 8th?", 5),
                          ("what's the forecast for Springfield, Oregon on November 8th?", 5)):
            self.assertEqual(live_data_topic(said), "weather", said)
            self.assertEqual(self.days(said), [day], said)

    def test_ways_to_say_a_date(self):
        for said in ("weather on November 8th", "weather on November 8", "the weather November eighth", "weather for November eight", "weather November the 8th",
                     "the weather for the 8th of November", "the weather for the eighth of November", "weather for the 8th November", "the weather on Nov 8",
                     "the weather on Nov. 8th", "weather on 11/8", "the weather on the 8th", "forecast for the eighth", "Forecast for November 8th, 2026.",
                     "weather for november 8 th", "what's the weather going to be on Sunday, November 8th?", "no member 8 weather",
                     "the weather on the 8th please", "what's the weather on the 8th, BMO?", "weather on the 8th thanks", "weather on the 8th of this month",
                     "the weather on Sunday the 8th", "the weather for Sunday the eighth"):
            self.assertEqual(self.days(said), [5], said)

    def test_more_than_one_day(self):
        for said, days in (("the forecast for the week of November 8th", list(range(5, 12))), ("the weather for the weekend of November 7th", [4, 5]),
                           ("weather November 8th through the 10th", [5, 6, 7]), ("weather November 8-10", [5, 6, 7]), ("weather November 8th to 10th", [5, 6, 7]),
                           ("weather November 8th and 9th", [5, 6]), ("weather from the 8th to the 10th", [5, 6, 7]), ("weather on November 8th and November 12th", [5, 9]),
                           ("the weather for the rest of November", list(range(15))), ("weather November 15th through the 20th", [12, 13, 14]), ("weather November 4th through the 9th", [1, 2, 3, 4, 5, 6]),
                           ("weather November 4th to November 9th", [1, 2, 3, 4, 5, 6])):
            self.assertEqual(self.days(said), days, said)

    def test_counted_from_now(self):
        for said, day in (("the weather in five days", 5), ("weather in 5 days", 5), ("weather five days from now", 5), ("the weather in a week", 7),
                          ("weather a week from today", 7), ("weather a week from tomorrow", 8), ("the weather a week from Friday", 10), ("weather in two weeks", 14)):
            self.assertEqual(self.days(said), [day], said)

    def test_what_it_was_before_still_works(self):
        for said, days in (("the weather in the next five days", [0, 1, 2, 3, 4]), ("the weather tomorrow", [1]), ("weather on Friday", [3]), ("the ten day forecast", list(range(10))),
                           ("the two week forecast", list(range(15))), ("what's the weather", [0]), ("this weekend's weather", [4, 5])):
            self.assertEqual(self.days(said), days, said)

    def test_not_dates(self):
        for said in ("the weather for the first time", "the weather may be nice", "what's the weather in Marchfield", "the weather in May, Oklahoma", "may one day it rains",
                     "the second week of forecast", "the weather at 8", "weather in Decatur"):
            self.assertIsNone(spoken_dates.read(put_right(said), TODAY), said)

    def test_the_next_one_of_a_date(self):
        self.assertEqual(spoken_dates.read("on the 2nd", TODAY).dates, [date(2026, 12, 2)])
        self.assertEqual(spoken_dates.read("for tuesday the 10th", TODAY).dates, [date(2026, 11, 10)])  # today's a Tuesday: never today
        self.assertEqual(spoken_dates.read("january 5th", TODAY).dates, [date(2027, 1, 5)])
        self.assertEqual(spoken_dates.read("november 30th to december 2nd", TODAY).dates, [date(2026, 11, 30), date(2026, 12, 1), date(2026, 12, 2)])
        self.assertEqual(spoken_dates.read("december 30th through the 2nd", TODAY).dates[-1], date(2027, 1, 2))

    def test_said_with_the_right_ending(self):
        for n, ending in ((1, "1st"), (2, "2nd"), (3, "3rd"), (4, "4th"), (11, "11th"), (12, "12th"), (13, "13th"), (21, "21st"), (22, "22nd"), (23, "23rd"), (30, "30th"), (31, "31st")):
            self.assertEqual(spoken_dates.ordinal(n), ending)


class WhatHeSays(unittest.TestCase):
    def test_a_date_is_said_with_its_date(self):
        said = service().report(SPRINGFIELD, "what's the weather for November 8th")
        self.assertTrue(said.startswith("Sunday, November 8th in Springfield: "), said)
        self.assertNotIn("rough guide", said)

    def test_far_out_is_a_rough_guide(self):
        self.assertIn("rough guide", service().report(SPRINGFIELD, "the weather on November 17th"))

    def test_too_far_never_says_today(self):
        for said, words in (("the weather on December 14th", "December 14th is too far out"), ("the weather in December", "December is too far out")):
            reply = service().report(SPRINGFIELD, said)
            self.assertIn(words, reply)
            self.assertIn("only goes through November 17th", reply)
            self.assertIn("Ask me again around", reply)
            self.assertNotIn("Right now", reply)
        self.assertIn("Ask me again around November 30th", service().report(SPRINGFIELD, "the weather on December 14th"))

    def test_gone_and_impossible_dates(self):
        self.assertIn("November 1st has already passed", service().report(SPRINGFIELD, "the weather on November 1st"))
        self.assertEqual(service().report(SPRINGFIELD, "the weather on November 31st"), "There's no November 31st.")

    def test_part_of_a_range_in_the_forecast(self):
        said = service().report(SPRINGFIELD, "the weather November 15th through the 20th")
        self.assertIn("November 15th to", said)
        self.assertIn("My forecast stops at November 17th", said)


class TheTown(unittest.TestCase):
    def test_a_date_is_not_a_town(self):
        for said in ("November 8th", "the 8th", "the 8th of November", "November 8th through the 10th", "November 8-10", "the rest of November", "December", "the night of November 8th", "11/8",
                     "the week of November 8th", "the weekend of November 7th", "Tuesday the 10th", "November 8th please", "November 8th, BMO", "the 8th please", "December please", "Friday please"):
            self.assertEqual(wx.without_when_words(said), "", said)

    def test_the_town_is_left(self):
        for said, town in (("November 8th in Salem", "Salem"), ("Salem on November 8th", "Salem"), ("Salem, Oregon on November 8th", "Salem, Oregon"), ("Salem for the 8th", "Salem"),
                           ("Friday in Salem", "Salem"), ("Marchfield", "Marchfield"), ("May, Oklahoma", "May, Oklahoma"),
                           ("Salem please", "Salem"), ("Salem, BMO", "Salem"), ("Salem on the 8th please", "Salem"), ("Tulsa, OK", "Tulsa, OK"), ("Salem thank you", "Salem")):
            self.assertEqual(wx.without_when_words(said), town, said)


class Routing(unittest.TestCase):
    def test_a_weather_question_with_a_date(self):
        for said in ("what will the weather be on November 8th", "what will the weather be in Paris", "the forecast for November 8th", "weather on the 8th", "weather on 11/8",
                     "weather from the 8th to the 10th", "will it rain on November 8th", "is it going to be cold on the 8th", "is it gonna snow on November 8th",
                     "how cold will it be on November 8th", "what's the temperature on November 8th", "the weather in December", "the weather in five days",
                     "what's the weather on the 8th please", "what's the weather on the 8th, BMO?", "any rain on November 8th", "will there be rain on the 8th", "what's the high on November 8th",
                     "what's it going to be like on November 8th", "how's November 8th looking", "the forecast for Tuesday the 10th"):
            self.assertEqual(live_data_topic(said), "weather", said)

    def test_a_date_without_the_weather_stays_out(self):
        for said in ("what happened on November 8th", "set up a meeting for November 8th", "who was born on the 8th", "is the store open on November 8th",
                     "is it true that it was cold on November 8th 1990", "tell me about May 8th"):
            self.assertIsNone(live_data_topic(said), said)


class TheSky(unittest.TestCase):
    NOW = datetime(2026, 11, 3, 13, 0, tzinfo=timezone.utc)

    def test_asking_about_the_weather_for_one(self):
        for said in ("what will the weather be for the land aids meteor shower?", "what's the weather for the Leonids", "will it be clear for the Leonids?", "the Geminids forecast",
                     "is it going to be cloudy for the eclipse?", "what's the weather for the meteor shower", "will the meteor shower be rained out", "weather during the lunar eclipse",
                     "how's the weather looking for the shooting stars", "any clouds for the Perseids", "what's the forecast for the next eclipse", "the weather for the Ursids in Salem"):
            self.assertTrue(sky.weather_for_sight(said), said)

    def test_not_asking_that(self):
        for said in ("what's the weather", "what is a meteor shower", "close the meteor shower", "play the weather for the meteor shower", "clear my calendar for the eclipse",
                     "turn on weather alerts for the eclipse", "meteor shower commands", "the weather for the northern lights", "will it be clear tomorrow", "when's the next eclipse",
                     "whether or not the eclipse is real"):
            self.assertFalse(sky.weather_for_sight(said), said)

    def test_a_shower_by_name(self):
        for said, name in (("what about the Ursids Meteor Shower?", "Ursids"), ("what about the Leonids Meteor Shower?", "Leonids"), ("what about the Leonades Meteor Shower?", "Leonids"),
                           ("when are the Geminids", "Geminids"), ("what about the land aids meteor shower", "Leonids"), ("the Perseids", "Perseids"), ("the lyrics meteor shower", "Lyrids"),
                           ("tell me about the Orionids", "Orionids"), ("next Quadrantids", "Quadrantids")):
            self.assertEqual(sky.request(said), "events", said)
            self.assertEqual(sky.shower_named(said), name, said)
        for said in ("the lyrics to that song", "what are the lyrics", "quadrants of a circle", "a land aids charity", "Orion's belt"):
            self.assertIsNone(sky.shower_named(said), said)

    def test_the_town(self):
        for said, town in (("the weather for the meteor shower in Salem, Oregon", "salem oregon"), ("weather in Salem for the next eclipse", "salem"),
                           ("the weather for the Leonids at night", ""), ("the weather for the meteor shower in the evening", ""), ("what's the weather for the meteor shower", ""),
                           ("the weather for the eclipse over my house", "")):
            self.assertEqual(sky.place_for_sight(said), town, said)

    @unittest.skipUnless(sky.available(), "no star charts")
    def test_in_the_forecast(self):
        here = sky.Sky(44.05, -123.02, "America/Los_Angeles")
        now = here.now(self.NOW)
        event = sky.chosen_sight(here, now, "what will the weather be for the land aids meteor shower?")
        self.assertEqual(event["name"], "Leonids")
        line, day = sky.sight_weather_words(event, forecast(), now, "Springfield", wx._day_phrase)
        self.assertEqual(day, date(2026, 11, 17))
        self.assertIn("The Leonids meteor shower peaks the night of November 17th.", line)
        self.assertIn("In Springfield, the sky looks clear around 9", line)
        self.assertIn("rough guide", line)

    @unittest.skipUnless(sky.available(), "no star charts")
    def test_too_far_out_says_when_to_ask(self):
        here = sky.Sky(44.05, -123.02, "America/Los_Angeles")
        now = here.now(self.NOW)
        line, day = sky.sight_weather_words(sky.chosen_sight(here, now, "the weather for the Geminids"), forecast(), now, "Springfield", wx._day_phrase)
        self.assertIsNone(day)
        self.assertIn("The Geminids meteor shower peaks the night of December 14th.", line)
        self.assertIn("too far out", line)
        self.assertIn("Ask me again around November 30th", line)

    @unittest.skipUnless(sky.available(), "no star charts")
    def test_the_eclipse_asked_about(self):
        here = sky.Sky(44.05, -123.02, "America/Los_Angeles")
        now = here.now(self.NOW)
        self.assertEqual(sky.chosen_sight(here, now, "weather for the lunar eclipse")["kind"], "lunar eclipse")
        self.assertEqual(sky.chosen_sight(here, now, "weather for the solar eclipse")["kind"], "solar eclipse")

    def test_years_off_says_the_year(self):
        now = datetime(2026, 11, 3, 13, 0, tzinfo=timezone.utc)
        event = {"kind": "lunar eclipse", "title": "Partial eclipse of the moon", "start": datetime(2028, 1, 11, 20, 0, tzinfo=timezone.utc),
                 "peak": datetime(2028, 1, 11, 20, 12, tzinfo=timezone.utc)}
        line, day = sky.sight_weather_words(event, forecast(), now, "Springfield", wx._day_phrase)
        self.assertIsNone(day)
        self.assertIn("on January 11, 2028", line)
        self.assertIn("Ask me again around December 28th, 2027", line)
        self.assertIn("on December 14.", sky.event_when({"start": datetime(2026, 12, 14, 21, 0, tzinfo=timezone.utc)}, now) + ".")


class WhatCanYouDo(unittest.TestCase):
    """2026-11-03: "Hey BMO, what can you do?" gets the list; in the middle of a conversation it's part of the conversation."""

    def chat(self, followup):
        chat = Chat.__new__(Chat)
        chat.library_view = mock.Mock(active=False)
        chat.managed_display_active = lambda: False
        chat._current_turn_followup = followup
        chat._open_help = mock.Mock(return_value=True)
        return chat

    def test_after_hey_bmo_it_opens(self):
        chat = self.chat(False)
        self.assertTrue(chat._handle_help("what can you do?"))
        chat._open_help.assert_called_once_with("what can you do?", help_center.HOME)

    def test_as_a_follow_up_it_goes_to_the_brain(self):
        for said in ("what can you do?", "what else can you do", "how can you help me", "tell me what you can do", "what do you do"):
            chat = self.chat(True)
            self.assertFalse(chat._handle_help(said), said)
            chat._open_help.assert_not_called()

    def test_plain_help_still_works_as_a_follow_up(self):
        for said in ("help", "what can I say", "list commands", "weather commands"):
            self.assertTrue(self.chat(True)._handle_help(said), said)

    def test_the_turn_loop_marks_a_follow_up(self):
        source = (ROOT / "systems" / "chat.py").read_text()
        self.assertIn('self._current_turn_followup = (not self._current_turn_explicit_wake\n', source)
        self.assertIn('and getattr(self, "_answer_followup_open", False))', source)


if __name__ == "__main__":
    unittest.main()
