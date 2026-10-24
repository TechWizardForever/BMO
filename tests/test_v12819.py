"""v1.28.19 regressions from the short-reply live check (2026-10-18)."""
import ast
import os
from pathlib import Path
import re
import sys
import types
import unittest

ROOT = Path(os.environ.get("BEMO_TEST_CODE_ROOT", Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(ROOT))
import config
from voice_commands import VoiceSessionGate
from systems.grounding import HONEST_REPLIES, fabricated_live_claim

SRC = (ROOT / "systems" / "chat.py").read_text()
CHAT = next(n for n in ast.parse(SRC).body if isinstance(n, ast.ClassDef) and n.name == "Chat")


def mini(names, **extra):
    body = [n for n in CHAT.body if isinstance(n, ast.FunctionDef) and n.name in names]
    ns = {"config": config, "re": re, "logger": types.SimpleNamespace(info=lambda *a, **k: None,
                                                                       warning=lambda *a, **k: None), **extra}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[ast.ClassDef(
        name="Mini", bases=[], keywords=[], body=body, decorator_list=[])], type_ignores=[])), "mini", "exec"), ns)
    return ns["Mini"]()


class SwitchPhraseTests(unittest.TestCase):
    def setUp(self):
        self.c = mini({"_extract_switch_target", "_match_profile_name", "_normalize_command_text"})

    def test_whisper_variants_reach_the_real_switch(self):
        # Live: "switch the coding" went to Main, which answered "Switched to coding mode."
        for heard in ("switch the coding", "switch too coding brain", "swap to coding", "switch to coding"):
            self.assertEqual(self.c._extract_switch_target(heard), "coding", heard)

    def test_loose_phrases_without_a_model_are_not_switches(self):
        for heard in ("switch the lights off", "change the channel", "swap the batteries"):
            self.assertIsNone(self.c._extract_switch_target(heard), heard)


class FakeSwitchClaimTests(unittest.TestCase):
    def test_models_cannot_claim_a_switch(self):
        claim = "Switched to coding mode. What do you want to build or fix?"  # exact live reply
        self.assertEqual(fabricated_live_claim(claim), "unswitched")
        self.assertIn("didn't actually switch models", HONEST_REPLIES["unswitched"])
        for fine in ("When you switch to coding mode, I can edit files.", "The coding model is good at Python."):
            self.assertIsNone(fabricated_live_claim(fine), fine)


class ShortNoTests(unittest.TestCase):
    def test_nope_and_friends_decline(self):
        clock = types.SimpleNamespace(t=0.0)

        def monotonic():
            clock.t += 0.25
            return clock.t

        for reply in ("Nope", "Nah.", "Ma.", "Naw", "No thank you", "Don't", "Not now"):  # "Ma." = Whisper's "nah"
            c = mini({"_confirm_by_voice", "_normalize_command_text"},
                     time=types.SimpleNamespace(monotonic=monotonic),
                     State=types.SimpleNamespace(RESPONDING="r", THINKING="t"))
            c._active_microphone = object()
            c.stop_event = types.SimpleNamespace(is_set=lambda: False)
            c.voice_gate = VoiceSessionGate(wake_aliases=config.VOICE_WAKE_ALIASES)
            c.speak = lambda text: None
            replies = iter([reply])
            c.listen = lambda source: next(replies, None)
            self.assertFalse(c._confirm_by_voice("Switch?", tag="T", target="coding"), reply)


class ModelStatusTests(unittest.TestCase):
    def pattern(self):
        i = SRC.index("        status = re.search(") + len("        status = re.search(")
        j = SRC.index('normalized.strip(". "),', i)
        return eval("(" + SRC[i:j].strip().rstrip(",") + ")")

    def test_which_model_questions_are_answered_from_state(self):
        # Live: "are you on the coding model right now?" -> Main: "Yes, I'm on the coding model."
        pat = self.pattern()
        m = re.search(pat, "are you on the coding model right now")
        self.assertEqual(m.group("asked"), "coding")
        for q in ("which model are you on", "what brain is this", "current model", "what mode are you in"):
            self.assertTrue(re.search(pat, q), q)
        for q in ("what model should i use for python", "tell me about the coding model"):
            self.assertIsNone(re.search(pat, q), q)
        handler = SRC[SRC.index("        if status:"):SRC.index("        list_phrases = {")]
        self.assertIn("not {self._profile_spoken_name(asked)}", handler)

    def test_short_words_can_start_a_recording(self):
        self.assertLess(0.14, config.STT_VAD_STRONG_START_THRESHOLD)  # loudest noise measured: 0.14
        self.assertLessEqual(config.STT_VAD_STRONG_START_THRESHOLD, 0.46)  # the missed "nah"


if __name__ == "__main__":
    unittest.main()
