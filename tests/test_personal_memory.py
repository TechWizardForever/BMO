"""General memory (2026-10-24): "remember that ...", "what do you remember about me?", "forget that ..."."""
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(os.environ.get("BEMO_TEST_CODE_ROOT", Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(ROOT))
from systems import personal_memory as pm


class ParseTests(unittest.TestCase):
    def test_what_bmo_handles(self):
        self.assertEqual(pm.parse("Remember that my sister's birthday is May 3rd."),
                         {"action": "add", "note": "my sister's birthday is May 3rd"})
        self.assertEqual(pm.parse("remember I'm allergic to peanuts")["note"], "I'm allergic to peanuts")
        self.assertEqual(pm.parse("what do you remember about me?"), {"action": "list"})
        self.assertEqual(pm.parse("forget that my favorite color is green"),
                         {"action": "forget", "what": "my favorite color is green"})
        self.assertEqual(pm.parse("forget everything about me"), {"action": "forget_all"})

    def test_what_it_leaves_alone(self):
        for said in ("remember for this project the cup is big", "do you remember the weather earlier", "remember me",
                     "remember to buy milk", "forget it", "what is the capital of France"):
            self.assertIsNone(pm.parse(said), said)

    def test_said_back_to_the_owner(self):
        self.assertEqual(pm.second_person("I'm allergic to peanuts and my sister's birthday is May 3rd"),
                         "you're allergic to peanuts and your sister's birthday is May 3rd")


class NotesTests(unittest.TestCase):
    def setUp(self):
        self.notes = pm.PersonalNotes(Path(tempfile.mkdtemp()) / "memory" / "notes.json")

    def test_add_list_forget(self):
        self.notes.add("my sister's birthday is May 3rd")
        self.notes.add("my favorite color is green")
        self.notes.add("my favorite color is green")  # said twice: kept once
        self.assertEqual(pm.spoken_list(self.notes.notes()),
                         "I remember that your sister's birthday is May 3rd; that your favorite color is green.")
        self.assertEqual(self.notes.forget("my favorite color")["text"], "my favorite color is green")
        self.assertIsNone(self.notes.forget("the garage code"))
        self.assertEqual(len(self.notes.notes()), 1)
        self.assertEqual(oct(self.notes.path.stat().st_mode & 0o777), "0o600")

    def test_every_brain_gets_them(self):
        self.assertEqual(self.notes.context(), "")
        self.notes.add("my sister's birthday is May 3rd")
        self.assertIn("- my sister's birthday is May 3rd", self.notes.context())
        source = (ROOT / "systems" / "chat.py").read_text()
        self.assertIn("notes = self._personal_notes().context()", source)


class ChatTests(unittest.TestCase):
    def setUp(self):
        from systems.chat import Chat
        self.path = Path(tempfile.mkdtemp()) / "notes.json"
        self.chat = Chat.__new__(Chat)
        self.said = []
        self.chat._say_local = lambda command, line: self.said.append(line) or True
        self.patch = mock.patch("config.PERSONAL_NOTES_PATH", self.path)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()

    def test_remember_and_forget_everything_asks_first(self):
        self.assertTrue(self.chat._handle_personal_memory("remember that my sister's birthday is May 3rd"))
        self.assertEqual(self.said[-1], "Got it. I'll remember that your sister's birthday is May 3rd.")
        self.chat._confirm_by_voice = lambda *a, **k: False
        self.chat._handle_personal_memory("forget everything about me")
        self.assertEqual(self.said[-1], "Okay, I'll keep them.")
        self.chat._confirm_by_voice = lambda *a, **k: True
        self.chat._handle_personal_memory("forget everything about me")
        self.assertEqual(pm.PersonalNotes(self.path).notes(), [])

    def test_weather_forgetting_comes_first(self):
        source = (ROOT / "systems" / "chat.py").read_text()
        self.assertLess(source.index("if self._handle_weather_memory(command):"),
                        source.index("if self._handle_personal_memory(command):"))


if __name__ == "__main__":
    unittest.main()
