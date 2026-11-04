"""Other ways to say the weather, radar, sky and project commands, and what Whisper may write for them (2026-11-03: every command gets alternates). What's said by mistake matters as much: the sky is checked early, so a loose
phrase here would take a sentence from every other command."""
import os
from pathlib import Path
import sys
import unittest

os.environ["SDL_VIDEODRIVER"] = "dummy"
os.environ["SDL_AUDIODRIVER"] = "dummy"
ROOT = Path(os.environ.get("BEMO_TEST_CODE_ROOT", Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(ROOT))
from systems import library, radar, sky, weather as wx
from systems.grounding import live_data_topic
from systems.sound_alikes import put_right

DATES = [f"2026-11-{day:02d}" for day in range(2, 18)]


class WeatherPhrases(unittest.TestCase):
    def test_heard_live(self):
        for said in ("what's the weather?", "show me the long-term forecast.", "Can you show me the long term forecast?"):
            self.assertEqual(live_data_topic(said), "weather", said)

    def test_ways_to_ask(self):
        for said in ("The weather.", "Weather.", "Forecast.", "the forecast", "tell me the forecast", "check the weather",
                     "get the forecast", "what's the weather doing", "what's it doing outside", "current conditions",
                     "what's the temperature", "what's the temperature today", "how hot is it going to get",
                     "how cold will it get", "is rain coming", "is the rain coming today", "any rain this week",
                     "do I need an umbrella", "should I bring a jacket", "how's the week looking",
                     "what's the week looking like", "how's the weekend looking", "what's the weekend look like",
                     "the extended outlook", "weather outlook", "two-week outlook", "two weeks of weather",
                     "forecast for the next two weeks", "weather stations", "the weather station", "the weather report"):
            self.assertEqual(live_data_topic(said), "weather", said)

    def test_what_whisper_may_write(self):
        for said in ("what's the whether", "what's the whether tomorrow", "how's the whether looking", "the whether",
                     "Whether.", "weather for cast", "what's the for cast", "the four cast for this week",
                     "long-term for cast", "long term whether", "long turn forecast", "longer forecast"):
            self.assertEqual(live_data_topic(said), "weather", said)

    def test_the_long_term_words_still_mean_every_day(self):
        for said in ("long turn forecast", "longer forecast", "long-term for cast", "extended outlook", "two-week outlook",
                     "long term whether"):
            self.assertEqual(wx.days_asked(said, DATES), list(range(16)), said)

    def test_when_words_never_become_a_town(self):
        for said in ("the week ahead", "the weekend looking like", "next week going to be", "tomorrow looking like"):
            self.assertEqual(wx.without_when_words(said), "", said)
        self.assertEqual(wx.without_when_words("Paris next week"), "Paris")

    def test_a_town_still_comes_through_a_misheard_word(self):
        self.assertEqual(put_right("what's the whether in Paris?"), "what's the weather in Paris?")
        self.assertEqual(put_right("the for cast for Salem, Oregon"), "the forecast for Salem, Oregon")


class RadarPhrases(unittest.TestCase):
    def test_ways_to_ask(self):
        for said in ("show me the radar", "the radar", "radar.", "weather radar", "rain radar", "storm radar",
                     "doppler radar", "doppler", "show me the doppler", "rain map", "show me the rain map",
                     "precipitation map", "show me the storm map", "weather map", "what's moving in", "what's coming in",
                     "where's the rain", "where is the rain", "show me the rain"):
            self.assertEqual(live_data_topic(said), "weather", said)
            self.assertTrue(radar.asked_for(said), said)

    def test_what_whisper_may_write(self):
        for said in ("show me the raider", "show the raider", "pull up the raider", "show me the reader",
                     "show me the radder", "weather raider", "rain raider", "The raider."):
            self.assertEqual(live_data_topic(said), "weather", said)
            self.assertTrue(radar.asked_for(said), said)


class SkyPhrases(unittest.TestCase):
    def check(self, want, sentences):
        for said in sentences:
            self.assertEqual(sky.request(said), want, said)

    def test_heard_live(self):
        self.check("tonight", ["What's in the sky tonight?"])
        self.check("iss", ["when does the space station pass over?"])
        self.check("events", ["any chance of Northern Lights?"])
        self.check("moon", ["what phase is the moon?"])

    def test_tonight(self):
        self.check("tonight", ["what's in the sky", "what's in the skies tonight", "what's in the ski tonight", "the night sky",
                               "the sky tonight", "what's up there tonight", "what's overhead tonight", "what can I see tonight",
                               "star gazing", "stargazing tonight", "what stars can I see", "what planets can I see",
                               "what plants can I see tonight", "where's Mars", "where is Jupiter", "can I see Venus tonight",
                               "is Saturn out tonight", "is Mars visible", "what's visible tonight", "anything cool in the sky"])

    def test_moon(self):
        self.check("moon", ["what's the moon phase", "moon phase", "moon face", "what's the moon face", "what's the moon phrase",
                            "moon's phase", "phase of the moon", "what phase of the moon is it", "what's the moon doing",
                            "what's the moon look like tonight", "how's the moon", "show me the moon", "the moon", "The moon.",
                            "the moon tonight", "is it a full moon", "is the moon full", "is it a full noon",
                            "when's the next full moon", "next full moon", "when's the new moon", "when does the moon rise",
                            "when's moonrise", "moon rise", "how full is the moon", "where's the moon"])

    def test_space_station(self):
        self.check("iss", ["where's the space station", "where is the space station", "where's the ISS", "where is the I.S.S.",
                           "when's the next space station pass", "when's the space station", "when is the ISS",
                           "when does the ISS go over", "Space station.", "the space station", "show me the space station",
                           "track the ISS", "is the space station overhead", "is the space station going over tonight",
                           "can I see the space station", "can I see the ISS tonight", "will the space station pass over",
                           "when can I see the space station", "next ISS pass", "space station passes", "ISS passes",
                           "where's the spice station", "when does the space nation pass", "when's the base station pass over",
                           "when does the face station pass", "when does the ice station pass over",
                           "when's the international space station", "what time does the space station pass",
                           "is the space station up"])

    def test_events(self):
        self.check("events", ["space events", "any space events", "what's happening in space", "what's going on in space",
                              "what's coming up in space", "anything happening in the sky", "anything cool coming up in the sky",
                              "sky events", "astronomy events", "space weather", "when's the next eclipse", "next eclipse",
                              "Eclipse.", "is there an eclipse coming", "will there be an eclipse", "any eclipses this year",
                              "is there a solar eclipse", "when's the next the clips", "when's the next a clips",
                              "when's the next e clips", "when's the next meteor shower", "next meteor shower", "Meteor shower.",
                              "any meteor showers", "is there a meteor shower tonight", "when's the next media shower",
                              "when's the next meter shower", "when's the next metro shower", "any shooting stars",
                              "are there any shooting stars tonight", "any falling stars", "Northern lights.",
                              "will I see the northern lights", "can I see the northern lights tonight",
                              "are the northern lights out", "northern light", "the aurora", "aurora tonight", "any aurora",
                              "chance of aurora"])

    def test_alerts_on(self):
        self.check("alerts on", ["turn on space alerts", "turn on the space alerts", "turn space alerts on",
                                 "turn the space alerts on", "turn on sky alerts", "turn on the space alert", "enable space alerts",
                                 "turn on space notifications", "turn on ISS alerts", "turn on space station alerts",
                                 "turn on alerts for the space station",
                                 "space alerts on", "turn on space a lurch", "turn on space a lerts", "turn on Skype alerts"])

    def test_might_mean_the_alerts(self):
        self.check("ask iss", ["let me know when the space station passes", "tell me when the space station passes", "tell me when the ISS goes over",
                               "tell me when the space station is overhead", "alert me when the space station passes", "warn me if the space station passes"])
        self.check("ask events", ["let me know when something's happening in the sky", "tell me if anything's happening in space"])

    def test_alerts_off(self):
        self.check("alerts off", ["turn off space alerts", "turn off the space alerts", "turn space alerts off",
                                  "turn the space alerts off", "space alerts off", "stop the space alerts", "no more space alerts",
                                  "disable sky alerts", "shut off the space alerts", "mute the space alerts", "cancel space alerts",
                                  "stop telling me about the space station", "stop telling me about the sky",
                                  "don't tell me about the space station", "don't tell me when the space station passes"])


class ProjectPhrases(unittest.TestCase):
    def test_ways_to_ask(self):
        for said in ("Hey BMO, show me my projects", "show my projects", "my projects", "show me my project", "my project list",
                     "show me the project list", "list my projects", "the project list", "Projects.", "show projects",
                     "show all my projects", "show me all of my projects", "show me my project folders", "my project folders",
                     "show me the project folders", "what projects do I have", "bring up my projects",
                     "pull up my projects", "show me my prospects", "show me my products", "show me my objects"):
            self.assertEqual(library.open_request(said), "projects", said)

    def test_older_commands_keep_their_meaning(self):
        for said in ("open project garden", "Open Project Garden.", "close project", "Current Project", "create project garden",
                     "what files are in this project?", "import files into current project.", "Export Project", "save project snapshot"):
            self.assertIsNone(library.open_request(said), said)
        self.assertEqual(library.open_request("show my movies"), "movies")
        self.assertEqual(library.open_request("show my games"), "games")


class ScreenPhrases(unittest.TestCase):
    def test_scroll(self):
        for said in ("scroll down", "scrolled down", "school down", "stroll down", "scroll town", "next page", "more"):
            self.assertEqual(library.command(said), ("down", 0), said)
        for said in ("scroll up", "scrolled up", "school up", "stroll up", "scroll app", "go back", "previous page"):
            self.assertEqual(library.command(said), ("up", 0), said)

    def test_close(self):
        for said in ("close", "Clothes.", "close it", "close the weather", "close the forecast", "close the radar",
                     "clothes the radar", "close the sky", "close the moon", "close the window", "close the space station",
                     "close the weather station", "get rid of it", "get rid of the radar", "take that away", "turn it off",
                     "turn off the radar", "that's enough", "enough", "I'm done", "never mind"):
            self.assertEqual(library.command(said), ("close", 0), said)


class NotTheseCommands(unittest.TestCase):
    """Sentences that must keep going where they went: to the brain, or to another of BMO's commands."""

    def test_not_weather(self):
        for said in ("how does weather work?", "write a weather app in python", "what is radar", "how does radar work",
                     "what does a forecast mean", "tell me whether it matters", "i wonder whether today is a holiday",
                     "whether or not it works", "the whether or not question", "show me the cast list", "auditions for cast members",
                     "open the weather app", "who is the reader", "read me the reader", "the raiders game", "show me the raiders",
                     "what is the doppler effect", "explain the doppler effect", "what's the temperature of the CPU",
                     "what's the temperature in the log", "what's the week of the year", "show me the storm drain plans",
                     "what's a long time", "open Outlook", "show me the outlook", "where is the rainbow",
                     "where's the storm door", "what's moving in the picture", "show me my jacket photos",
                     "show me the weather man", "show me the weather channel"):
            self.assertIsNone(live_data_topic(said), said)

    def test_closing_a_weather_screen_never_opens_one(self):
        for said in ("close the weather radar", "close the rain radar", "close the weather station", "close the forecast",
                     "close the weather map", "can you close the radar", "hide the weather", "turn off the weather station",
                     "get rid of the radar", "Clothes the weather radar."):
            self.assertIsNone(live_data_topic(said), said)
            self.assertEqual(library.command(said) or library.command(said.replace("can you ", "")), ("close", 0), said)

    def test_closing_a_sky_screen_never_opens_one(self):
        for said in ("close the night sky", "close the sky", "close the moon", "close the moon phase", "hide the moon",
                     "close the space station", "turn off the moon", "get rid of the night sky"):
            self.assertIsNone(sky.request(said), said)
            self.assertEqual(library.command(said), ("close", 0), said)

    def test_a_name_with_iss_inside_it_is_not_the_space_station(self):
        for said in ("show me Miss Congeniality", "find Miss Marple", "show me the Swiss Family Robinson", "when did I miss the call",
                     "where's my Swiss army knife", "next time I miss it", "see the kiss scene"):
            self.assertIsNone(sky.request(said), said)

    def test_a_title_or_a_movie_is_not_the_sky(self):
        for said in ("play Moonrise Kingdom", "watch Night Sky", "play Space Station 76", "put on Northern Lights", "play Eclipse",
                     "show me Twilight Eclipse", "when's New Moon playing", "show me a movie about the northern lights",
                     "any movies about eclipses", "is there an eclipse movie", "can I see the eclipse movie",
                     "is there a meteor shower movie", "show me the moon river video", "where's the moon landing movie",
                     "show me a photo of the space station", "where is mars in the movie"):
            self.assertIsNone(sky.request(said), said)

    def test_other_questions_with_sky_words_are_not_the_sky(self):
        for said in ("what stars are in Inception", "which stars are in that movie", "what planets are in Star Wars",
                     "what planets are in the solar system", "what is up there on the shelf", "what is visible on the screen",
                     "what is overhead in this budget", "how many moons does saturn have", "tell me when the sky gets dark",
                     "tell me if the sky is clear", "let me know if the sky looks clear"):
            self.assertIsNone(sky.request(said), said)

    def test_one_question_about_the_next_pass_turns_nothing_on(self):
        self.assertEqual(sky.request("tell me when the next space station pass is"), "iss")
        self.assertEqual(sky.request("watch for the space station"), None)  # no verb he knows: not a screen, not alerts

    def test_no_phrase_belongs_to_two_commands(self):
        """Every phrase in this file's lists, against the other commands' matchers."""
        import ast
        tree = ast.parse(Path(__file__).read_text())
        for group in tree.body:
            if not isinstance(group, ast.ClassDef) or group.name not in ("WeatherPhrases", "RadarPhrases", "SkyPhrases", "ProjectPhrases"):
                continue
            for method in group.body:
                if not (isinstance(method, ast.FunctionDef) and method.name in (
                        "test_heard_live", "test_ways_to_ask", "test_what_whisper_may_write", "test_tonight", "test_moon",
                        "test_space_station", "test_events", "test_alerts_on", "test_might_mean_the_alerts", "test_alerts_off")):
                    continue
                for node in ast.walk(method):
                    if not (isinstance(node, ast.Constant) and isinstance(node.value, str)) or node.value in (
                            "weather", "tonight", "moon", "iss", "events", "alerts on", "ask iss", "ask events", "alerts off", "projects"):
                        continue
                    said = node.value
                    claims = [name for name, claimed in (("sky", sky.request(said)), ("weather", live_data_topic(said) == "weather"),
                                                         ("project list", library.open_request(said) == "projects"),
                                                         ("screen", (library.command(said) or ("play",))[0] != "play")) if claimed]
                    self.assertEqual(len(claims), 1, f"{said!r} is claimed by {claims}")

    def test_not_the_sky(self):
        for said in ("what is an eclipse", "how do eclipses work", "why do meteor showers happen", "what causes the northern lights",
                     "what is the moon made of", "how far is the moon", "how is the moon formed", "how big is mars",
                     "how far away is jupiter", "is mars a planet", "tell me about jupiter", "what is the space station",
                     "how big is the space station", "who lives on the space station", "why is the sky blue",
                     "what's up", "what's up with you", "what's a clip", "skip to the next clip", "the next clip",
                     "what do you know about clips", "where is my base station", "the base station", "where's the ice",
                     "what plants can I see in the garden", "what plants do I have", "the media", "turn off the sound",
                     "turn on the lights", "mute the movie", "cancel the timer", "stop the movie", "turn off the screen",
                     "set a timer for ten minutes", "remind me at 5 PM to water the basil", "skiing at the ski lodge",
                     "the ski lift", "write a story about the moon", "the moon landing was when"):
            self.assertIsNone(sky.request(said), said)

    def test_not_the_project_list(self):
        for said in ("show me my homework", "what am I watching", "show me the products page", "what projects cost",
                     "open my project garden", "working on it", "what are you working on", "what am I working on",
                     "what project are we working on", "show me the project", "show my project files"):
            self.assertIsNone(library.open_request(said), said)

    def test_not_closing_or_scrolling_the_screen(self):
        for said in ("close the game", "close the movie", "turn off the sound", "turn off the lights", "shut the door",
                     "school starts monday", "scrolls of wisdom", "get rid of the bug", "that's enough for today",
                     "turn off", "shut", "shut off", "turn off the screen", "shut the screen", "turn off the window"):
            self.assertNotIn((library.command(said) or ("", 0))[0], ("close", "down", "up"), said)


if __name__ == "__main__":
    unittest.main()
