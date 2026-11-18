"""His user's name, and who made him (2026-11-15). The name is said back so a mishearing is caught, kept in ~/.bemo with
the notes, and every brain gets it with who made him."""
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
import config
from systems import personal_memory as pm
from systems.chat import Chat


class Words(unittest.TestCase):
    def test_saying_a_name(self):
        for said, name in (("my name is Sam", "Sam"), ("My name is Sam.", "Sam"), ("my name's Sam", "Sam"), ("call me Sam", "Sam"),
                           ("you can call me Sam", "Sam"), ("just call me Sam", "Sam"), ("I go by Sam", "Sam"), ("I'm called Sam", "Sam"),
                           ("remember that my name is Sam", "Sam"), ("my name is sam lee", "Sam Lee"), ("my name is Mary Anne Smith", "Mary Anne Smith"),
                           ("my name is o'brien", "O'Brien"), ("Okay, my name is Sam!", "Sam"), ("call me Tech Wizard", "Tech Wizard")):
            self.assertEqual(pm.parse(said), {"action": "name", "name": name}, said)

    def test_not_a_name(self):
        for said in ("call me back", "call me later", "call me tomorrow", "call me a taxi", "call me when you're done", "call me maybe",
                     "my name is not important", "what is my name", "I'm tired", "I'm Sam", "my name is on the list somewhere",
                     "call me if anything happens", "they call me every day", "my name is", "what's in a name"):
            self.assertNotEqual((pm.parse(said) or {}).get("action"), "name", said)

    def test_asking(self):
        for said in ("what's my name?", "What is my name?", "whats my name", "do you know my name", "do you remember my name", "who am I?",
                     "what do you call me", "say my name", "what's my name again"):
            self.assertEqual(pm.parse(said), {"action": "ask_name"}, said)
        self.assertEqual(pm.parse("forget my name"), {"action": "forget_name"})

    def test_who_made_him(self):
        for said in ("who made you?", "Who created you?", "who built you", "who programmed you", "who's your creator", "who is your maker",
                     "who made BMO", "who invented you", "where did you come from"):
            self.assertEqual(pm.parse(said), {"action": "creator"}, said)
        for said in ("who made this cake", "who made the first computer", "who is your favorite character"):
            self.assertNotEqual((pm.parse(said) or {}).get("action"), "creator", said)

    def test_the_old_notes_still_work(self):
        self.assertEqual(pm.parse("remember that my sister's birthday is May 3rd")["action"], "add")
        self.assertEqual(pm.parse("forget that my favorite color is green")["action"], "forget")


class Kept(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.notes = pm.PersonalNotes(Path(self.folder.name) / "memory" / "notes.json", creator="Tech Wizard")

    def test_every_brain_is_told(self):
        self.assertEqual(self.notes.context(), "Tech Wizard made you.")
        self.notes.set_name("Sam")
        self.assertIn("The person talking to you is Sam", self.notes.context())
        self.notes.add("my sister's birthday is May 3rd")
        context = self.notes.context()
        self.assertTrue(context.startswith("Tech Wizard made you."))
        self.assertIn("- my sister's birthday is May 3rd", context)

    def test_private_and_beside_the_notes(self):
        self.notes.set_name("Sam")
        path = Path(self.folder.name) / "memory" / "user.json"
        self.assertEqual(oct(path.stat().st_mode & 0o777), "0o600")
        self.assertEqual(pm.PersonalNotes(Path(self.folder.name) / "memory" / "notes.json").name(), "Sam")

    def test_forgetting_all_notes_keeps_the_name_and_maker(self):
        self.notes.set_name("Sam")
        self.notes.add("my sister's birthday is May 3rd")
        self.notes.forget_all()
        self.assertEqual(self.notes.name(), "Sam")
        self.assertIn("Tech Wizard made you.", self.notes.context())


class SaidByBMO(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.chat = Chat.__new__(Chat)
        self.said = []
        self.chat._say_local = lambda command, text: self.said.append(text) or True
        self.patch = mock.patch.object(config, "PERSONAL_NOTES_PATH", Path(self.folder.name) / "memory" / "notes.json")
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def ask(self, said):
        self.assertTrue(self.chat._handle_personal_memory(said), said)
        return self.said[-1]

    def test_a_conversation(self):
        self.assertIn("I don't know your name yet", self.ask("what's my name?"))
        self.assertEqual(self.ask("my name is Sam"), "Nice to meet you, Sam! I'll remember that.")
        self.assertEqual(self.ask("what's my name?"), "You're Sam!")
        self.assertEqual(self.ask("call me Sam"), "I know, Sam! I won't forget.")
        self.assertEqual(self.ask("call me Sammy"), "Nice to meet you, Sammy! I'll remember that.")
        self.assertEqual(self.ask("forget my name"), "Okay, I forgot your name.")
        self.assertIn("I don't know your name yet", self.ask("who am I"))

    def test_who_made_him(self):
        self.assertEqual(self.ask("who made you?"), "Tech Wizard made me, right here on this computer.")

    def test_every_brain_hears_it(self):
        self.ask("my name is Sam")
        context = self.chat._personal_notes().context()
        self.assertIn("Tech Wizard made you.", context)
        self.assertIn("Sam", context)
        self.assertIn("made by Tech Wizard", config.MODEL_PROFILES["normal"]["system_prompt"])
        self.assertIn("made by Tech Wizard", config.MODEL_PROFILES["engineering"]["system_prompt"])


if __name__ == "__main__":
    unittest.main()
