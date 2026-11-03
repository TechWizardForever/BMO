"""The help center on BMO's screen (owner 2026-11-03), with no sound and no network."""
import os
from pathlib import Path
import sys
import unittest
from unittest import mock

os.environ["SDL_VIDEODRIVER"] = "dummy"
os.environ["SDL_AUDIODRIVER"] = "dummy"
ROOT = Path(os.environ.get("BEMO_TEST_CODE_ROOT", Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
import pygame
import visual_smoke_test as smoke
import voice_commands
from systems import help_center as hc, help_screen, library, reminders, sky, weather_alerts, weather_screen
from systems.chat import Chat
from systems.grounding import live_data_topic
from systems.render import FaceRenderer

HOME_PHRASES = ("help", "Help.", "Hey BMO, help", "help me", "i need help", "help center", "help centre", "the help menu", "show help",
                "open the help center", "show me the commands", "list commands", "list voice commands", "voice commands", "Voice comments.",
                "commands", "the command list", "what commands do you know", "what can i say", "what can I ask you", "what do I say",
                "how do I use you", "show me the list of commands", "can you show me your commands", "list comments", "BMO commands",
                "what can you do", "What can you do?", "Hey BMO, what can you do?", "What can you do, BMO?", "so what can you do", "what can you do for me",
                "what else can you do", "what all can you do", "what things can you do", "what kind of things can you do", "what could you do",
                "what can ya do", "what can BMO do", "what can Bemo do", "tell me what you can do", "show me what you can do", "what you can do",
                "can you tell me what you can do", "what are you able to do", "what are you capable of", "what are your abilities", "what are your skills",
                "what do you do", "what do you know how to do", "what can you help me with", "what can you help with", "how can you help me")
SUBJECT_PHRASES = {
    "Weather": ("weather commands", "show me the weather commands", "help with the weather", "weather help", "commands for the weather",
                "what can I say about the weather", "radar commands", "the forecast commands", "whether commands", "weather comments",
                "Can you show me the weather commands?"),
    "The sky": ("sky commands", "commands for the sky", "moon commands", "help with the moon", "space commands", "what commands are there for space"),
    "Movies and shows": ("movie commands", "show me the movie commands", "help with movies", "tv commands", "what are the movie commands",
                         "list the movie commands", "show commands for movies"),
    "Timers and reminders": ("timer commands", "reminder commands", "help with timers", "alarm commands"),
    "Games": ("game commands", "help with games"),
    "Projects": ("project commands", "what can i say about my projects", "help with my projects", "commands for my projects"),
    "3D parts": ("3D commands", "3d parts commands", "help with 3D printing", "part commands"),
    "Sound": ("sound commands", "volume commands"),
    "BMO himself": ("commands for yourself", "help with updates"),
    "Brains": ("brain commands",),
    "Apps": ("app commands",),
    "Computer control": ("computer commands",),
    "Screen and text": ("screen commands",),
    "Downloads and USB sticks": ("USB commands",),
    "Remembering things": ("memory commands",),
    "Talking to BMO": ("help with talking to you",),
}
NOT_HELP = ("help me with my project", "you're gonna help me build a better self-destruct sequence", "help me build a box",
            "can you help me with this", "what's the weather", "show me the weather", "show me my projects", "show my movies", "what is help",
            "who helped you", "the help", "helpful", "helping", "command the army", "what are your commands for the crew", "what can you do on this computer",
            "what can you do here", "what can you do about the leak", "what can you do with this", "what can you do for me today", "what do you do for fun",
            "what do you do when I'm gone", "what are your favorite movies", "what can you tell me", "how can you help me with this", "what can you not do",
            "what can't you do", "what canyon do", "what are your capabilities",
            "what can you tell me about gold", "what can I watch", "comments on my code", "show me the comments on this page", "weather", "movies",
            "the moon", "help the weather", "a project help desk", "weather commander", "timer", "set a timer for ten minutes", "close the commands")


class WhatWasSaid(unittest.TestCase):
    def test_the_whole_center(self):
        for said in HOME_PHRASES:
            self.assertEqual(hc.request(said), hc.HOME, said)

    def test_one_subject(self):
        for subject, phrases in SUBJECT_PHRASES.items():
            for said in phrases:
                self.assertEqual(hc.request(said), subject, said)

    def test_not_help(self):
        for said in NOT_HELP:
            self.assertIsNone(hc.request(said), said)

    def test_every_subject_can_be_asked_for(self):
        self.assertEqual(set(hc.SUBJECT_WORDS), set(voice_commands.categories()))  # a new subject in the table needs its words
        self.assertEqual(set(SUBJECT_PHRASES), set(voice_commands.categories()))
        for subject in hc.subjects():
            for word in hc.SUBJECT_WORDS[subject]:
                # "BMO commands" is Hey BMO, commands, and "show" or "display commands" means show them: all the whole center.
                self.assertEqual(hc.request(f"{word} commands") if word not in ("bmo", "show", "shows", "display") else subject, subject, word)
                self.assertEqual(hc.subject_named(word) if word != "bmo" else subject, subject, word)

    def test_a_name_on_the_screen_works_when_said(self):
        for number, subject in enumerate(hc.subjects(), 1):
            self.assertEqual(hc.subject_named(subject), subject, subject)
            self.assertEqual(hc.subject_named(subject + "."), subject, subject)
            self.assertEqual(hc.request(f"help with {subject}"), subject, subject)

    def test_a_word_belongs_to_one_subject(self):
        seen = {}
        for subject, words in hc.SUBJECT_WORDS.items():
            for word in words:
                self.assertNotIn(word, seen, f"{word}: {subject} and {seen.get(word)}")
                seen[word] = subject

    def test_whisper_in_silence_never_picks_a_subject(self):
        for said in ("you", "You.", "thank you", "Thanks for watching!", "okay", "bye", "the", "yeah", "hmm", "show me", "Thank you."):
            self.assertIsNone(hc.subject_named(said), said)
            self.assertFalse(hc.asks_back(said), said)

    def test_names_on_the_screen(self):
        for said, subject in (("weather", "Weather"), ("The sky.", "The sky"), ("the moon", "The sky"), ("movies", "Movies and shows"),
                              ("Sound.", "Sound"), ("3D parts", "3D parts"), ("projects", "Projects"), ("weather commands", "Weather")):
            self.assertEqual(hc.subject_named(said), subject, said)

    def test_back(self):
        for said in ("back", "Back.", "go back", "go back to the subjects", "the menu", "subjects", "help", "start over"):
            self.assertTrue(hc.asks_back(said), said)


class NoOtherCommandClaimsThem(unittest.TestCase):
    def phrases(self):
        yield from HOME_PHRASES
        for phrases in SUBJECT_PHRASES.values():
            yield from phrases

    def test_help_comes_first_where_it_has_to(self):
        """A phrase another command also claims needs the help center checked before that command."""
        source = (ROOT / "systems" / "chat.py").read_text()
        body = source[source.index("def _handle_local_command_once"):]
        self.assertLess(body.index("self._handle_help(command)"), body.index("self._handle_reminder_command(command)"))
        self.assertLess(body.index("self._handle_help(command)"), body.index("self._handle_sky_command(command)"))
        self.assertLess(body.index("self._handle_help(command)"), body.index("live_data_topic(command)"))

    def test_nothing_of_theirs_is_taken(self):
        for said in ("what's the weather", "show me the radar", "long term forecast", "any weather alerts?", "turn on weather alerts",
                     "what's in the sky", "what's the moon phase", "when does the space station pass", "show me my projects", "show my movies",
                     "show my games", "set a timer for ten minutes", "remind me at 5 PM to water the basil", "what are my reminders",
                     "volume up", "turn it up", "close the game", "next page", "scroll down", "close", "play number 3", "current project",
                     "open project garden", "list projects", "what time is it", "what's the date"):
            self.assertIsNone(hc.request(said), said)
            self.assertIsNone(hc.subject_named(said), said)

    def test_listed_commands_keep_their_meaning(self):
        help_phrases = {"help", "what can you do?", "list commands"}
        checked = 0
        for command in voice_commands.VOICE_COMMANDS:
            for said in command.syntax.split(" / "):
                said = voice_commands.re.sub(r"^Hey Bemo,\s*", "", said).strip()
                said = voice_commands.re.sub(r"<[^>]+>", "garden", said)
                if said in help_phrases or "commands" in said or "help with" in said:
                    continue
                checked += 1
                self.assertIsNone(hc.request(said), said)
        self.assertGreater(checked, 100)

    def test_closing_it(self):
        for said in ("close", "close help", "close the help center", "close the commands", "close the list", "Clothes.", "hide the help menu"):
            self.assertEqual(library.command(said), ("close", 0), said)
            self.assertIsNone(hc.request(said), said)


class TheScreens(unittest.TestCase):
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

    def view(self, subject):
        cards, _, per_page = hc.items(subject)
        view = library.LibraryView()
        view.open("help", [library.Item(c.get("subject") or c["say"], "help", c.get("subject", ""), data=c) for c in cards], grid=False,
                  per_page=per_page, extra={"page": "home"} if subject == hc.HOME else {"page": "subject", "subject": subject, "per_page": per_page})
        return view

    def test_every_page_draws_and_no_words_are_cut_off(self):
        real, cut = weather_screen._wrapped, []

        def watching(font, text, room, lines):
            out = real(font, text, room, lines)
            if lines < 99 and out and out[-1].endswith("..."):
                cut.append(text[:60])
            return out
        with mock.patch.object(help_screen, "_wrapped", watching):
            for size in ((640, 480), (800, 600), (981, 739), (1024, 768), (1202, 678), (1920, 1080)):
                app, renderer = self.renderer(size)
                for subject in (hc.HOME,) + hc.subjects():
                    view = self.view(subject)
                    for _ in range(12):
                        help_screen.render(renderer, view.snapshot(), 1.0)
                        if not view.scroll(1):
                            break
                self.assertEqual(cut, [], size)

    def test_every_command_is_on_a_page(self):
        total = 0
        for subject in hc.subjects():
            cards, _, _ = hc.items(subject)
            total += len(cards)
            for card in cards:
                self.assertTrue(card["say"] and card["does"])
                self.assertNotIn("<", card["say"] + card["does"])  # [your words], as printed
        self.assertEqual(total, len(voice_commands.VOICE_COMMANDS))

    def test_a_new_command_shows_up_by_itself(self):
        added = voice_commands.VOICE_COMMANDS + (voice_commands.VoiceCommand("Sound", "Hey Bemo, sing louder", "Sings louder."),)
        with mock.patch.object(voice_commands, "VOICE_COMMANDS", added):
            self.assertIn("sing louder", [card["say"] for card in hc.items("Sound")[0]])

    def test_nothing_reaches_the_edge_of_a_tube(self):
        for size in ((640, 480), (1024, 768)):
            for subject in (hc.HOME, "Weather"):
                app, renderer = self.renderer(size)
                help_screen.render(renderer, self.view(subject).snapshot(), 1.0)
                w, h = size
                for x in range(0, w, 3):
                    for y in list(range(0, int(h * 0.05))) + list(range(int(h * 0.95), h)):
                        self.assertLess(app._screen.get_at((x, y))[0], 90, (x, y, size, subject))

    def test_it_is_drawn_for_a_help_view(self):
        self.assertIn('kind") == "help"', (ROOT / "systems" / "render.py").read_text())


class UsingIt(unittest.TestCase):
    def chat(self):
        chat = mock.Mock()
        chat.library_view = library.LibraryView()
        chat.managed_display_active.return_value = False
        chat.said = []
        chat._say_local.side_effect = lambda command, line: chat.said.append(line) or True
        for name in ("_open_help", "_help_screen_command", "_handle_help", "_library_action"):
            setattr(chat, name, getattr(Chat, name).__get__(chat))
        chat._screen_takes = Chat._screen_takes
        chat.supervisor = None
        return chat

    def test_help_opens_every_subject(self):
        chat = self.chat()
        self.assertTrue(chat._handle_help("Hey BMO, help"))
        view = chat.library_view
        self.assertEqual((view.kind, view.extra["page"], len(view.items)), ("help", "home", len(hc.subjects())))
        self.assertEqual(view.per_page, len(hc.subjects()))  # all on one page
        self.assertIn("by subject", chat.said[0])

    def test_straight_to_a_subject(self):
        chat = self.chat()
        self.assertTrue(chat._handle_help("show me the weather commands"))
        self.assertEqual(chat.library_view.extra["subject"], "Weather")
        self.assertEqual(chat.said[0], "Here are the commands for weather.")
        chat._handle_help("3d commands")
        self.assertEqual(chat.said[-1], "Here are the commands for 3D parts.")
        chat._handle_help("usb commands")
        self.assertEqual(chat.said[-1], "Here are the commands for downloads and USB sticks.")
        chat._handle_help("help with talking to you")
        self.assertEqual(chat.said[-1], "Here are the commands for talking to BMO.")

    def test_a_number_picks_a_subject(self):
        chat = self.chat()
        chat._handle_help("help")
        action = library.command("number 7")
        self.assertEqual(action, ("play", 7))
        self.assertTrue(chat._screen_takes(chat.library_view, action))
        chat._library_action("number 7", *action)
        self.assertEqual(chat.library_view.extra["subject"], hc.subjects()[6])

    def test_a_subject_page_has_no_numbers_to_pick(self):
        chat = self.chat()
        chat._handle_help("weather commands")
        self.assertFalse(chat._screen_takes(chat.library_view, ("play", 3)))
        self.assertTrue(chat._screen_takes(chat.library_view, ("down", 0)))
        self.assertTrue(chat._screen_takes(chat.library_view, ("close", 0)))

    def test_a_name_works_while_it_is_up_and_not_after(self):
        chat = self.chat()
        chat._handle_help("help")
        self.assertTrue(chat._handle_help("weather"))
        self.assertEqual(chat.library_view.extra["subject"], "Weather")
        chat.library_view.close()
        self.assertFalse(chat._handle_help("weather"))

    def test_back_goes_to_the_subjects_then_closes(self):
        chat = self.chat()
        chat._handle_help("weather commands")
        self.assertTrue(chat._handle_help("back"))
        self.assertEqual(chat.library_view.extra["page"], "home")
        self.assertTrue(chat._handle_help("back"))
        self.assertFalse(chat.library_view.active)

    def test_scrolling_a_subject(self):
        chat = self.chat()
        chat._handle_help("weather commands")
        chat._library_action("scroll down", "down")
        self.assertEqual(chat.library_view.top, 3)

    def test_a_movie_on_the_screen_leaves_the_spoken_summary(self):
        chat = self.chat()
        chat.managed_display_active.return_value = True
        self.assertFalse(chat._handle_help("help"))
        self.assertFalse(chat.library_view.active)
        self.assertIn('"what commands do you know", "what can i say", "help"', (ROOT / "systems" / "chat.py").read_text())

    def test_other_sentences_pass_through(self):
        chat = self.chat()
        for said in ("what's the weather", "help me with my project", "show my movies"):
            self.assertFalse(chat._handle_help(said), said)
        self.assertFalse(chat.library_view.active)

    def test_without_hey_bmo_while_it_is_up(self):
        chat = self.chat()
        chat._handle_help("help")
        chat._bare = Chat._bare_library_command.__get__(chat)
        self.assertTrue(chat._bare("weather"))
        self.assertEqual(chat.library_view.extra["subject"], "Weather")
        self.assertTrue(chat._bare("back"))
        self.assertEqual(chat.library_view.extra["page"], "home")
        self.assertFalse(chat._bare("this is a sentence somebody said nearby in the room"))
        self.assertFalse(chat._bare("you"))
        self.assertEqual(chat.library_view.extra["page"], "home")


if __name__ == "__main__":
    unittest.main()
