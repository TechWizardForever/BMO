"""Severe weather alerts (owner 2026-11-03), and BMO restarting when his listening stops, with no network."""
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

os.environ["SDL_VIDEODRIVER"] = "dummy"
os.environ["SDL_AUDIODRIVER"] = "dummy"
ROOT = Path(os.environ.get("BEMO_TEST_CODE_ROOT", Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
import pygame
import visual_smoke_test as smoke
from systems import library, sky, weather_alerts as wa, weather_screen
from systems.chat import Chat
from systems.grounding import live_data_topic
from systems.render import FaceRenderer
from systems.weather import Place

SPRINGFIELD = Place("Springfield", "Illinois", "United States", 39.78, -89.65, "America/Chicago")
NOON = datetime(2026, 11, 3, 18, 0, tzinfo=timezone.utc)  # noon in Springfield
NIGHT = datetime(2026, 11, 4, 9, 0, tzinfo=timezone.utc)  # 3 AM in Springfield
TORNADO_TEXT = "Move to an interior room on the lowest floor of a well-built building\naway from windows. If you are outdoors, go in."


def feature(event="Tornado Warning", severity="Extreme", urgency="Immediate", id_="a1", *, ends="2026-11-04T23:00:00-06:00",
            status="Actual", message="Alert", references=(), instruction=TORNADO_TEXT):
    """In the shape api.weather.gov answered live on 2026-11-03."""
    return {"properties": {"id": id_, "areaDesc": "Sangamon, IL", "ends": ends, "expires": ends, "status": status,
                           "messageType": message, "severity": severity, "urgency": urgency, "event": event,
                           "instruction": instruction, "references": [{"identifier": ref} for ref in references]}}


class Service:
    def __init__(self, *features):
        self.features, self.error, self.calls = list(features), None, 0

    def __call__(self, url, params, timeout=15.0):
        self.calls += 1
        if self.error:
            raise self.error
        return {"features": self.features}


class ReadingTheFeed(unittest.TestCase):
    def read(self, *features):
        return wa.read_alerts({"features": list(features)}, NOON)

    def test_only_real_alerts_that_are_still_on(self):
        self.assertEqual(self.read(feature(event="Test Message")), [])
        self.assertEqual(self.read(feature(status="Test")), [])
        self.assertEqual(self.read(feature(status="Exercise")), [])
        self.assertEqual(self.read(feature(message="Cancel")), [])
        self.assertEqual(self.read(feature(ends="2026-11-03T11:00:00-06:00")), [])
        self.assertEqual(len(self.read(feature())), 1)

    def test_how_serious(self):
        for event, severity, urgency, want in (("Tornado Warning", "Extreme", "Immediate", "urgent"),
                                               ("Flash Flood Warning", "Severe", "Immediate", "urgent"),
                                               ("Flood Warning", "Severe", "Expected", "serious"),
                                               ("Flood Watch", "Severe", "Future", "serious"),
                                               ("Tornado Watch", "Extreme", "Immediate", "serious"),
                                               ("Wind Advisory", "Moderate", "Expected", "notice"),
                                               ("Small Craft Advisory", "Minor", "Expected", "notice"),
                                               ("Hydrologic Outlook", "Unknown", "Future", "notice")):
            self.assertEqual(self.read(feature(event, severity, urgency))[0]["level"], want, event)

    def test_most_serious_first(self):
        alerts = self.read(feature("Wind Advisory", "Moderate", "Expected", "c"), feature("Flood Watch", "Severe", "Future", "b"), feature(id_="a"))
        self.assertEqual([a["level"] for a in alerts], ["urgent", "serious", "notice"])

    def test_what_he_says(self):
        alert = self.read(feature())[0]
        said = wa.spoken_alert(alert, SPRINGFIELD, NOON)
        self.assertEqual(said, "Weather alert! Tornado Warning here, until Wednesday at 11:00 PM, from the National Weather Service. "
                               "Move to an interior room on the lowest floor of a well-built building away from windows.")
        today = self.read(feature(ends="2026-11-03T12:45:00-06:00"))[0]
        self.assertIn("here, until 12:45 PM, from", wa.spoken_alert(today, SPRINGFIELD, NOON))

    def test_shouting_and_long_or_missing_advice_still_read_well(self):
        self.assertEqual(wa._first_sentence("MOVE INDOORS NOW.\nSTAY THERE."), "Move indoors now.")
        self.assertLessEqual(len(wa._first_sentence("word " * 100)), 181)
        self.assertEqual(wa._first_sentence(None), "")
        alert = self.read(feature(instruction=None, ends=None))[0]
        self.assertEqual(wa.spoken_alert(alert, SPRINGFIELD, NOON), "Weather alert! Tornado Warning here, from the National Weather Service.")

    def test_the_answer_when_asked(self):
        self.assertEqual(wa.spoken_summary([], SPRINGFIELD, NOON), "No weather alerts for Springfield right now. That's from the National Weather Service.")
        one = wa.spoken_summary(self.read(feature(ends="2026-11-03T12:45:00-06:00")), SPRINGFIELD, NOON)
        self.assertTrue(one.startswith("Tornado Warning until 12:45 PM, for Springfield. Move to"), one)
        many = wa.spoken_summary(self.read(*[feature("Flood Watch", "Severe", "Future", str(n)) for n in range(5)]), SPRINGFIELD, NOON)
        self.assertTrue(many.startswith("5 weather alerts for Springfield:"), many)
        self.assertIn(", and 2 more.", many)

    def test_a_place_outside_the_country_is_not_a_failure(self):
        error = mock.MagicMock(side_effect=wa.urllib.error.HTTPError("u", 400, "bad", {}, None))
        with mock.patch.object(wa.urllib.request, "urlopen", error):
            with self.assertRaises(wa.OutsideCoverage):
                wa._get(wa.ALERTS_URL, {})
        error = mock.MagicMock(side_effect=wa.urllib.error.HTTPError("u", 503, "busy", {}, None))
        with mock.patch.object(wa.urllib.request, "urlopen", error):
            with self.assertRaises(wa.AlertError) as caught:
                wa._get(wa.ALERTS_URL, {})
        self.assertNotIsInstance(caught.exception, wa.OutsideCoverage)

    def test_it_sends_rounded_coordinates_only(self):
        service = Service()
        wa.fetch_alerts(39.78123, -89.65123, fetch=service.__call__, now=NOON)
        seen = {}
        wa.fetch_alerts(39.78123, -89.65123, fetch=lambda url, params: seen.update(params) or {"features": []}, now=NOON)
        self.assertEqual(seen, {"point": "39.78,-89.65"})


class TheWatcher(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.said, self.moment, self.busy = [], NOON, False
        self.service = Service()
        self.watcher = wa.AlertWatcher(Path(self.folder.name) / "alerts.json", lambda: SPRINGFIELD, say=self.said.append,
                                       busy=lambda: self.busy, fetch=self.service, clock=lambda: self.moment.timestamp())

    def on(self, *features):
        self.watcher.set_enabled(True)
        self.service.features = list(features)

    def test_off_means_nothing_is_contacted_or_said(self):
        self.service.features = [feature()]
        self.assertEqual(self.watcher.check_once(), [])
        self.assertEqual(self.service.calls, 0)
        self.assertFalse(self.watcher.enabled)

    def test_each_alert_is_said_once(self):
        self.on(feature())
        self.assertEqual(len(self.watcher.check_once()), 1)
        self.assertEqual(self.watcher.check_once(), [])
        self.assertEqual(len(self.said), 1)

    def test_once_even_after_a_restart(self):
        self.on(feature())
        self.watcher.check_once()
        again = wa.AlertWatcher(self.watcher.state_path, lambda: SPRINGFIELD, say=self.said.append, fetch=self.service,
                                clock=lambda: self.moment.timestamp())
        self.assertEqual(again.check_once(), [])

    def test_an_update_to_the_same_warning_stays_quiet(self):
        self.on(feature(id_="first"))
        self.watcher.check_once()
        self.service.features = [feature(id_="second", message="Update", references=["first"])]
        self.assertEqual(self.watcher.check_once(), [])
        self.service.features = [feature(id_="third", message="Update", references=["second"])]
        self.assertEqual(self.watcher.check_once(), [])

    def test_a_watch_that_becomes_a_warning_is_said(self):
        self.on(feature("Tornado Watch", "Severe", "Future", "watch"))
        self.assertEqual(len(self.watcher.check_once()), 1)
        self.service.features = [feature(id_="warning", references=["watch"])]
        self.assertEqual(len(self.watcher.check_once()), 1)
        self.assertIn("Tornado Warning", self.said[-1])

    def test_urgent_comes_at_night_and_over_a_movie(self):
        self.moment, self.busy = NIGHT, True
        self.on(feature())
        self.assertEqual(len(self.watcher.check_once()), 1)

    def test_serious_waits_for_morning_and_is_not_lost(self):
        self.moment = NIGHT
        self.on(feature("Flood Watch", "Severe", "Future"))
        self.assertEqual(self.watcher.check_once(), [])
        self.moment = NIGHT + timedelta(hours=6)  # 9 AM
        self.assertEqual(len(self.watcher.check_once()), 1)

    def test_serious_waits_for_the_movie(self):
        self.busy = True
        self.on(feature("Flood Watch", "Severe", "Future"))
        self.assertEqual(self.watcher.check_once(), [])
        self.busy = False
        self.assertEqual(len(self.watcher.check_once()), 1)

    def test_a_small_advisory_is_never_said(self):
        self.on(feature("Wind Advisory", "Moderate", "Expected"))
        self.assertEqual(self.watcher.check_once(), [])
        self.assertEqual(self.watcher.handle({"op": "get"})["serious"], [])

    def test_it_says_when_it_cant_reach_the_service(self):
        self.on()
        self.service.error = wa.AlertError("I couldn't reach the National Weather Service")
        self.assertEqual(self.watcher.check_once(), [])
        self.moment += timedelta(minutes=29)
        self.assertEqual(self.watcher.check_once(), [])
        self.moment += timedelta(minutes=2)
        self.assertIn("can't warn you", self.watcher.check_once()[0])
        self.moment += timedelta(minutes=10)
        self.assertEqual(self.watcher.check_once(), [])  # once, not every two minutes
        self.service.error = None
        self.assertIn("back on", self.watcher.check_once()[0])
        self.assertEqual(self.watcher.check_once(), [])

    def test_that_news_waits_for_daytime_but_a_warning_does_not(self):
        self.moment = NIGHT
        self.on()
        self.service.error = wa.AlertError("no")
        self.watcher.check_once()
        self.moment += timedelta(minutes=45)
        self.assertEqual(self.watcher.check_once(), [])
        self.moment = NIGHT + timedelta(hours=6)
        self.assertEqual(len(self.watcher.check_once()), 1)

    def test_a_blip_says_nothing(self):
        self.on()
        self.service.error = wa.AlertError("no")
        self.watcher.check_once()
        self.service.error = None
        self.watcher.check_once()
        self.moment += timedelta(hours=2)
        self.service.error = wa.AlertError("no")
        self.assertEqual(self.watcher.check_once(), [])  # a new failure starts a new half hour

    def test_outside_the_country_it_stays_quiet(self):
        self.on()
        self.service.error = wa.OutsideCoverage("only the United States")
        self.moment += timedelta(hours=3)
        self.assertEqual(self.watcher.check_once(), [])
        self.assertEqual(self.said, [])

    def test_no_saved_town_no_contact(self):
        watcher = wa.AlertWatcher(self.watcher.state_path, lambda: None, say=self.said.append, fetch=self.service)
        watcher.set_enabled(True)
        self.assertEqual(watcher.check_once(), [])
        self.assertEqual(self.service.calls, 0)

    def test_a_voice_that_fails_doesnt_stop_it(self):
        watcher = wa.AlertWatcher(self.watcher.state_path, lambda: SPRINGFIELD, say=mock.Mock(side_effect=OSError("no sound")),
                                  fetch=self.service, clock=lambda: self.moment.timestamp())
        self.on(feature())
        self.assertEqual(len(watcher.check_once()), 1)

    def test_what_bmo_can_ask(self):
        self.on(feature())
        self.watcher.check_once()
        self.assertEqual(self.watcher.handle({"op": "get"}), {"ok": True, "on": True, "serious": ["Tornado Warning"]})
        cards = self.watcher.handle({"op": "alert"})["cards"]
        self.assertEqual(cards[0]["event"], "Tornado Warning")
        self.assertIsNone(self.watcher.handle({"op": "alert"})["cards"])  # shown once
        self.assertEqual(self.watcher.handle({"op": "set", "on": False}), {"ok": True})
        self.assertEqual(self.watcher.handle({"op": "get"}), {"ok": True, "on": False, "serious": []})
        self.assertEqual(self.watcher.handle({"op": "nonsense"}), {"ok": False})
        json.loads(self.watcher.state_path.read_text())


class WhatWasSaid(unittest.TestCase):
    def check(self, want, sentences):
        for said in sentences:
            self.assertEqual(wa.request(said), want, said)

    def test_on(self):
        self.check("alerts on", ["turn on weather alerts", "turn weather alerts on", "turn the weather alerts on", "weather alerts on",
                                 "enable severe weather alerts", "give me weather warnings", "turn on storm alerts",
                                 "warn me if there's severe weather", "let me know if there's a tornado",
                                 "tell me when there's a bad storm", "turn on whether alerts", "turn on weather a lurch"])

    def test_off(self):
        self.check("alerts off", ["turn off weather alerts", "turn weather alerts off", "cancel weather alerts", "no more weather alerts",
                                  "stop telling me about the weather", "mute the weather warnings", "weather alerts off",
                                  "shut off storm alerts", "don't tell me about the weather"])

    def test_asking(self):
        self.check("list", ["any weather alerts?", "any weather warnings", "Weather alerts.", "the weather alerts", "any severe weather",
                            "Severe weather.", "is there severe weather", "is there a tornado warning", "any flood watches",
                            "is there a flash flood warning", "show me the weather alerts", "check for weather alerts",
                            "what are the weather alerts", "any active weather alerts", "got any storm warnings",
                            "any severe whether", "any several weather", "any whether alerts", "is there a winter storm warning",
                            "any weather alerts in Salem, Oregon?"])

    def test_which_alerts(self):
        self.check("which", ["turn off the alerts", "turn off alerts", "turn the alerts off", "cancel my alerts", "turn on all alerts"])

    def test_not_these(self):
        self.check(None, ["how do tornado warnings work", "what is a tornado warning", "what does a flood watch mean",
                          "there's a tornado warning in this movie", "why was there a flood warning", "tell me whether it matters",
                          "close the weather alerts", "hide the alerts", "turn on space alerts", "turn off the lights",
                          "write a program for weather alerts", "watch the weather channel", "what's the weather", "any rain this week",
                          "fire the alert", "the heat is on", "what is heat", "turn off the screen", "a warning about the code",
                          "show me a warning", "wind watch", "what's the weather like", "any stormy weather movies"])

    def test_no_other_command_claims_them(self):
        for said in ("turn on weather alerts", "turn off weather alerts", "any weather alerts?", "is there a tornado warning", "any severe weather"):
            self.assertIsNone(sky.request(said), said)
            self.assertIsNone(library.open_request(said), said)
            self.assertNotIn((library.command(said) or ("play",))[0], ("close", "down", "up"), said)
        for said in ("turn on space alerts", "turn off space alerts", "what's the weather", "show me the radar", "long term forecast",
                     "what's in the sky", "what's the moon phase"):
            self.assertIsNone(wa.request(said), said)

    def test_closing_the_screen(self):
        for said in ("close the alerts", "close the weather alerts", "hide the warnings", "get rid of the alerts", "close", "Clothes."):
            self.assertEqual(library.command(said), ("close", 0), said)
            self.assertNotEqual(live_data_topic(said), "weather", said)


class TheScreen(unittest.TestCase):
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

    def view(self, count):
        cards = [wa.screen_card(alert, SPRINGFIELD, NOON) for alert in wa.read_alerts({"features": [
            feature(id_=str(n), event=("Tornado Warning", "Flood Watch", "Wind Advisory", "Severe Thunderstorm Warning")[n % 4],
                    severity=("Extreme", "Severe", "Moderate", "Severe")[n % 4], urgency=("Immediate", "Future", "Expected", "Immediate")[n % 4])
            for n in range(count)]}, NOON)]
        view = library.LibraryView()
        view.open("weather", [library.Item(c["event"], "alert", str(n), data=c) for n, c in enumerate(cards)], grid=False, per_page=3,
                  extra={"alerts": True, "place": "SPRINGFIELD", "source": "National Weather Service"})
        return view

    def test_it_draws_at_every_size(self):
        for size in ((640, 480), (800, 600), (981, 739), (1024, 768), (1202, 678), (1920, 1080)):
            app, renderer = self.renderer(size)
            for count in (0, 1, 2, 3, 4, 8):
                view = self.view(count)
                for now in (0.1, 1.0):
                    weather_screen.render(renderer, view.snapshot(), now)
                view.scroll(1)
                weather_screen.render(renderer, view.snapshot(), 1.0)

    def test_nothing_reaches_the_edge_of_a_tube(self):
        for size in ((640, 480), (1024, 768)):
            app, renderer = self.renderer(size)
            weather_screen.render(renderer, self.view(3).snapshot(), 1.0)
            back = app._screen.copy()
            w, h = size
            # Outside the margin only the grid is drawn: dim green, never the bright text or the red stripe.
            for x in range(0, w, 3):
                for y in list(range(0, int(h * 0.05))) + list(range(int(h * 0.95), h)):
                    r, g, b, _ = back.get_at((x, y))
                    self.assertLess(r, 90, (x, y, size))

    def test_a_warning_shows_on_the_other_weather_screens(self):
        app, renderer = self.renderer((1024, 768))
        view = library.LibraryView()
        view.open("weather", [], grid=False, per_page=8, extra={"place": "SPRINGFIELD", "all_days": [], "warning": "Tornado Warning"})
        weather_screen.render(renderer, view.snapshot(), 0.0)
        reds = sum(1 for x in range(100, 900, 10) if app._screen.get_at((x, int(768 * 0.93)))[0] > 100)
        self.assertGreater(reds, 50)


class TheSupervisorPassesItOn(unittest.TestCase):
    def test_bmo_reaches_the_watcher_or_hears_no(self):
        import supervisor
        service = supervisor.SupervisorService.__new__(supervisor.SupervisorService)
        with mock.patch.object(service, "weather_alerts", mock.Mock(handle=lambda request: {"ok": True, "op": request["op"]}), create=True):
            self.assertEqual(service.handle({"action": "weather_alerts", "op": "get"}), {"ok": True, "op": "get"})
        self.assertEqual(service.handle({"action": "weather_alerts", "op": "get"}), {"ok": False})  # still starting up


class RestartingWhenHeStopsListening(unittest.TestCase):
    def chat(self, *, closing=False, installing=False):
        chat = mock.Mock(worker_failed=False)
        chat.stop_event.is_set.return_value = closing
        chat.setup_view.active = installing
        return chat

    def test_he_says_so_and_asks_to_be_started_again(self):
        chat = self.chat()
        Chat._restart_after_failure(chat)
        chat.speak.assert_called_once_with("Something went wrong, so I'm restarting.")
        self.assertTrue(chat.worker_failed)

    def test_a_broken_voice_doesnt_stop_the_restart(self):
        chat = self.chat()
        chat.speak.side_effect = OSError("no sound")
        Chat._restart_after_failure(chat)
        self.assertTrue(chat.worker_failed)

    def test_not_while_closing_or_installing(self):
        for chat in (self.chat(closing=True), self.chat(installing=True)):
            Chat._restart_after_failure(chat)
            self.assertFalse(chat.worker_failed)
            chat.speak.assert_not_called()

    def test_it_starts_out_fine(self):
        self.assertIn("self.worker_failed = False", (ROOT / "systems" / "chat.py").read_text())

    def test_the_window_leaves_with_an_error_so_the_supervisor_restarts_him(self):
        source = (ROOT / "app.py").read_text()
        self.assertIn("if self.chat.worker_failed:\n                    raise RuntimeError", source)
        self.assertIn("except BaseException:\n    traceback.print_exc()\n    exit_code = 1", (ROOT / "__init__.py").read_text())
        supervisor = (ROOT / "supervisor.py").read_text()
        self.assertIn('if exit_code == 0:\n                LOG.info("Bemo exited normally', supervisor)
        self.assertIn("restarts = _next_crash_count(", supervisor)


if __name__ == "__main__":
    unittest.main()
