"""'Do you remember the weather earlier?' repeats BMO's last report, with when (2026-10-20: after a restart
Main said he had no live weather data although BMO had reported it minutes before)."""
import ast
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
import re
import sys
import tempfile
import unittest

ROOT = Path(os.environ.get("BEMO_TEST_CODE_ROOT", Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(ROOT))
from systems.persistence import MemoryStore

SOURCE = (ROOT / "systems" / "chat.py").read_text()
REPORT = ("Right now in Salem it's 63 degrees and overcast, wind 6 miles per hour. Today: drizzle, high 65, low 48, "
          "21 percent chance of rain. That's from Open-Meteo, updated at 12:45 PM.")


def mini(store):
    chat = next(n for n in ast.parse(SOURCE).body if isinstance(n, ast.ClassDef) and n.name == "Chat")
    keep = [n for n in chat.body if (isinstance(n, ast.FunctionDef) and n.name in ("_handle_weather_memory", "_spoken_when"))
            or (isinstance(n, ast.Assign) and any(getattr(t, "id", "") == "_WEATHER_MEMORY" for t in n.targets))]
    ns = {"re": re, "logger": type("L", (), {"exception": staticmethod(lambda *a, **k: None)})}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[ast.ClassDef(
        name="Mini", bases=[], keywords=[], body=keep, decorator_list=[])], type_ignores=[])), "m", "exec"), ns)
    m = ns["Mini"]()
    m.memory_store, m.said = store, []
    m._say_local = lambda command, response: m.said.append(response) or True
    return m


class WeatherMemoryTests(unittest.TestCase):
    def setUp(self):
        self.store = MemoryStore(Path(tempfile.mkdtemp()) / "conversation.sqlite3")
        self.session = self.store.start_session()

    def tearDown(self):
        self.store.close()

    def add(self, role, content):
        self.store.add_message(session_id=self.session, role=role, content=content, project_slug=None,
                               model_profile="normal", model_name="x")

    def test_repeats_the_last_report_in_the_past_tense(self):
        self.add("user", "how's the weather?")
        self.add("assistant", REPORT)
        self.add("assistant", "Something else entirely.")
        m = mini(self.store)
        self.assertTrue(m._handle_weather_memory("do you remember the weather earlier?"))
        self.assertTrue(m.said[0].startswith("When I checked at "))
        self.assertIn("in Salem it was 63 degrees and overcast", m.said[0])
        self.assertNotIn("Open-Meteo", m.said[0])

    def test_no_report_yet_offers_to_check(self):
        m = mini(self.store)
        self.assertTrue(m._handle_weather_memory("what was the weather this morning?"))
        self.assertIn("how's the weather", m.said[0])

    def test_only_questions_about_earlier_weather(self):
        m = mini(self.store)
        self.assertFalse(m._handle_weather_memory("how's the weather?"))
        self.assertFalse(m._handle_weather_memory("do you remember my name?"))

    def test_when_in_words(self):
        m = mini(self.store)
        now = datetime.now(timezone.utc)
        self.assertTrue(m._spoken_when(now.isoformat()).startswith("at "))
        self.assertTrue(m._spoken_when((now - timedelta(days=1)).isoformat()).startswith("yesterday at "))
        self.assertTrue(m._spoken_when((now - timedelta(days=9)).isoformat()).startswith("on "))

    def test_asked_before_the_live_weather_question(self):
        self.assertLess(SOURCE.index("if self._handle_weather_memory(command):"),
                        SOURCE.index("topic = live_data_topic(command)"))


if __name__ == "__main__":
    unittest.main()
