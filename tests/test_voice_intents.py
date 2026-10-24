"""The owner's real sentences through BMO's command matching (voice-command plan V1/V2/V4, 2026-10-19)."""
import ast
import json
import os
from pathlib import Path
import re
import sys
import types
import unittest

ROOT = Path(os.environ.get("BEMO_TEST_CODE_ROOT", Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(ROOT))
import config
from systems.voice_intents import canonical, looks_like_switch, split_requests
from voice_commands import VoiceSessionGate

FIXTURES = ROOT / "tests" / "fixtures" / "voice"


def chat_parts():
    src = (ROOT / "systems" / "chat.py").read_text()
    chat = next(n for n in ast.parse(src).body if isinstance(n, ast.ClassDef) and n.name == "Chat")
    keep = [n for n in chat.body if isinstance(n, ast.FunctionDef)
            and n.name in ("_extract_switch_target", "_match_profile_name", "_normalize_command_text")
            or isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") == "_SAVED_FILE_QUESTION"]
    ns = {"config": config, "re": re, "logger": types.SimpleNamespace(info=lambda *a, **k: None)}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[ast.ClassDef(
        name="Mini", bases=[], keywords=[], body=keep, decorator_list=[])], type_ignores=[])), "mini", "exec"), ns)
    return ns["Mini"]()


BRAINS = {re.sub(r"[^a-z0-9.\- ]+", " ", str(alias).lower()).strip()
          for name, profile in config.MODEL_PROFILES.items() for alias in (name, *profile.get("aliases", ()))}


class RealSentenceTests(unittest.TestCase):
    def setUp(self):
        lines = (FIXTURES / "heard.jsonl").read_text().splitlines()
        self.rows = [json.loads(line) for line in lines[1:]]
        self.c = chat_parts()

    def test_every_sentence_does_what_it_should(self):
        wrong = []
        for row in self.rows:
            said, expect = row["said"], row["expect"]
            phrase = self.c._extract_switch_target(said)
            brain = self.c._match_profile_name(phrase) if phrase else None
            norm = self.c._normalize_command_text(said)
            if expect.startswith("switch:"):
                ok = brain == expect.split(":", 1)[1]
            elif expect == "file_question":
                ok = bool(self.c._SAVED_FILE_QUESTION.search(norm)) and len(norm.split()) <= 10
            else:  # app / other: never a brain switch, never "didn't catch"
                ok = brain is None and not looks_like_switch(said, BRAINS)
            if not ok:
                wrong.append((said, expect, brain))
        self.assertEqual(wrong, [], f"{len(wrong)} of {len(self.rows)} sentences go wrong")
        self.assertGreaterEqual(len(self.rows), 150)

    def test_didnt_catch_only_for_brain_like_switches(self):
        for said in ("switch to the mane brain please", "swap to coder thing", "switch to deep visions mode"):
            self.assertTrue(looks_like_switch(said, BRAINS) or self.c._match_profile_name(
                self.c._extract_switch_target(said) or "") is not None, said)
        for said in ("switch to the recording tab", "switch to the calculator", "switch sound to the TV",
                     "I switched to decaf coffee"):
            self.assertFalse(looks_like_switch(said, BRAINS), said)

    def test_canonical_cleanup(self):
        self.assertEqual(canonical("Hey BMO, you need to switch to coding brain."), "switch to coding brain")
        self.assertEqual(canonical("Bemo switched to Maine."), "switch to main")
        self.assertEqual(canonical("He Bemo, can you switch to codeine please"), "switch to coding")


class AlreadyThereTests(unittest.TestCase):
    def test_asking_for_the_current_brain_gets_an_answer(self):
        src = (ROOT / "systems" / "chat.py").read_text()
        body = src[src.index("        target_phrase = self._extract_switch_target(command)"):][:1500]
        self.assertIn("if profile_name == self.active_profile_name:", body)
        self.assertIn('response = f"I\'m already using {self._profile_spoken_name(profile_name)}."', body)
        self.assertLess(body.index("if profile_name == self.active_profile_name:"), body.index("self._switch_profile("))


class SecondTryTests(unittest.TestCase):
    """V3: BMO's own commands get a second try with the cleaned wording, only if the first failed."""

    def handler(self, accepts):
        src = (ROOT / "systems" / "chat.py").read_text()
        chat = next(n for n in ast.parse(src).body if isinstance(n, ast.ClassDef) and n.name == "Chat")
        keep = [n for n in chat.body if isinstance(n, ast.FunctionDef)
                and n.name in ("_handle_local_command", "_normalize_command_text")]
        ns = {"re": re, "logger": types.SimpleNamespace(info=lambda *a, **k: None)}
        exec(compile(ast.fix_missing_locations(ast.Module(body=[ast.ClassDef(
            name="Mini", bases=[], keywords=[], body=keep, decorator_list=[])], type_ignores=[])), "mini", "exec"), ns)
        c = ns["Mini"]()
        c.tried = []
        c._handle_local_command_once = lambda text: c.tried.append(text) or text in accepts
        return c

    def test_original_first_cleaned_second(self):
        c = self.handler({"mute", "press 7 in calculator"})
        self.assertTrue(c._handle_local_command("mute"))
        self.assertEqual(c.tried, ["mute"])
        c.tried.clear()
        self.assertTrue(c._handle_local_command("Can you mute, please?"))
        self.assertEqual(c.tried, ["Can you mute, please?", "mute"])
        c.tried.clear()
        self.assertFalse(c._handle_local_command("tell me a joke"))
        self.assertEqual(c.tried, ["tell me a joke"])  # nothing to clean: no second try

    def test_filler_variants_of_working_commands_get_through(self):
        from systems.sound import parse_sound_command
        from systems.gui_control import parse_gui_command
        wrappers = ["can you {}", "could you {} please", "please {}", "you need to {}", "now {}",
                    "i want you to {}", "{} please", "just {}", "okay so {}"]
        for parse, commands in ((parse_sound_command, ["mute", "volume down", "what's the volume",
                                                       "a little quieter", "switch sound to the TV"]),
                                (parse_gui_command, ["press 7 in calculator", "press times in calculator"])):
            for command in commands:
                for wrapper in wrappers:
                    said = wrapper.format(command)
                    self.assertTrue(parse(said) or parse(canonical(said, mishearings=False)), said)

    def test_real_chat_sentences_do_not_become_commands(self):
        from systems.sound import parse_sound_command
        from systems import desktop_settings
        rows = [json.loads(line) for line in (FIXTURES / "heard.jsonl").read_text().splitlines()[1:]]
        for row in rows:
            if row["expect"] != "other":
                continue
            clean = canonical(row["said"], mishearings=False)
            for parse in (parse_sound_command, desktop_settings.parse_setting):
                if not parse(row["said"]):
                    self.assertFalse(parse(clean), row["said"])

    def test_a_little_keeps_its_a(self):
        self.assertEqual(canonical("a little quieter"), "a little quieter")
        self.assertEqual(canonical("A Bemo, a little quieter"), "a little quieter")


class AnswerTests(unittest.TestCase):
    def test_answers_are_tidied_and_switches_take_everyday_yeses(self):
        src = (ROOT / "systems" / "chat.py").read_text()
        body = src[src.index("    def _confirm_by_voice"):src.index("    def _switch_profile")]
        self.assertIn("tidy = re.sub(r\"^(?:(?:oh|ah|um|uh|well|okay|ok)\\s+)+\", \"\", answer)", body)
        self.assertIn('if tag == "MODEL_APPROVAL" and answer in {"yeah", "yep", "yup", "sure", "okay", "ok", "go ahead",',
                      body)
        # Everything else still needs "Hey BMO, yes".
        self.assertIn('if not woke and tag != "MODEL_APPROVAL":', body)


class SplitTests(unittest.TestCase):
    def test_several_questions_in_one_breath_are_answered_one_by_one(self):
        # Live 2026-10-19: only the time was answered.
        self.assertEqual(split_requests("what time is it? What's the weather like? Where am I? "
                                        "What's the weather like in Springfeld, Oregon?"),
                         ["what time is it?", "What's the weather like?", "Where am I?",
                          "What's the weather like in Springfeld, Oregon?"])

    def test_open_an_app_and_do_something(self):
        # Live 2026-10-19: Main had no app tool (the editor wasn't open yet) and tried gedit, mousepad, nano.
        self.assertEqual(split_requests("open the text editor and type, I love pancakes."),
                         ["open the text editor", "in the text editor, type, I love pancakes."])
        self.assertEqual(split_requests("open the calculator and press 7 times 6"),
                         ["open the calculator", "in the calculator, press 7 times 6"])
        for said in ("open the text editor", "I opened the door and it was cold", "open the pod bay doors"):
            self.assertEqual(split_requests(said), [said])

    def test_context_and_corrections_stay_together(self):
        for said in ("I'm thinking about building a small shelf for my workshop. What wood should I use?",
                     "write a Python game for a snake program. Never mind, cancel that.",
                     "I'm giving you access to the GUI. Can you please click recording tab?"):
            self.assertEqual(split_requests(said), [said])

    def test_no_earlier_real_sentence_is_split(self):
        rows = [json.loads(line) for line in (FIXTURES / "heard.jsonl").read_text().splitlines()[1:]]
        split = [r["said"] for r in rows if len(split_requests(r["said"])) > 1]
        self.assertEqual(split, ["what time is it? What's the weather like? Where am I? "
                                 "What's the weather like in Springfeld, Oregon?"])

    def test_the_main_loop_handles_each_part(self):
        src = (ROOT / "systems" / "chat.py").read_text()
        self.assertIn("requests = split_requests(gated_prompt)", src)
        self.assertIn("self._handle_one_request(request)", src)


class AnswerFollowupTests(unittest.TestCase):
    """decided 2026-10-19: after an answer, one follow-up within 8 s needs no "Hey BMO"."""

    def test_one_short_follow_up_then_hey_bmo_again(self):
        gate = VoiceSessionGate(wake_aliases=config.VOICE_WAKE_ALIASES)
        self.assertEqual(config.VOICE_ANSWER_FOLLOWUP_SECONDS, 8.0)
        gate.wake(now=100.0)
        gate.followup_armed_until = 100.0 + config.VOICE_ANSWER_FOLLOWUP_SECONDS  # what the answer opens
        self.assertEqual(gate.apply("What's the weather like?", now=103.0, started_at=102.0),
                         "What's the weather like?")
        self.assertIsNone(gate.apply("Where am I?", now=104.0, started_at=103.5))  # one only
        gate.followup_armed_until = 110.0
        self.assertIsNone(gate.apply("too late", now=120.0, started_at=119.0))
        self.assertEqual(gate.apply("Hey BMO, where am I?", now=121.0), "where am I?")

    def test_the_main_loop_opens_it_after_answering_and_limits_words(self):
        src = (ROOT / "systems" / "chat.py").read_text()
        loop = src[src.index("requests = split_requests(gated_prompt)"):][:700]
        self.assertIn("self._open_answer_followup()", loop)
        self.assertIn("len(gated_prompt.split()) > int(config.VOICE_FOLLOWUP_MAX_WORDS)", src)
        self.assertEqual(config.VOICE_FOLLOWUP_MAX_WORDS, 20)


class WakeTests(unittest.TestCase):
    def test_wake_cases(self):
        gate = VoiceSessionGate(wake_aliases=config.VOICE_WAKE_ALIASES)
        for line in (FIXTURES / "wake.jsonl").read_text().splitlines():
            row = json.loads(line)
            woke, command = gate.parse(row["heard"])
            self.assertEqual(woke, row["wake"], row)
            if row["wake"]:
                self.assertEqual(command, row["command"], row)


if __name__ == "__main__":
    unittest.main()
