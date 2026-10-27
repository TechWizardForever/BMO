"""v1.28.8 regressions built from the real v1.28.7 live-test failures.

Runs offline: no Ollama, Whisper, Piper, microphone or X display needed.
"""
import ast
import os
from pathlib import Path
import random
import re
import sys
import tempfile
import types
import time
import unittest

ROOT = Path(os.environ.get("BEMO_TEST_CODE_ROOT", Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(ROOT))
import config
from voice_commands import VoiceSessionGate
from systems.stt_guard import implausible_speech_rate, is_runaway_repetition, output_is_looping
from systems.grounding import fabricated_live_claim, live_data_topic
from systems.context_policy import coding_work_request, tool_names_for_request
from systems.turn_speech import speakable_units
from systems.computer import ComputerController

# Excerpts of the v1.28.7 terminal log (2026-10-18); the full log isn't published.
LOG = (ROOT / "tests" / "fixtures" / "v1287_log_excerpts.txt").read_text()
_SRC = (ROOT / "systems" / "chat.py").read_text()
# The exact 1.95 s capture Whisper turned into 140 words (10:20:30).
GARBAGE = re.search(r"stt=1\.77s total=3\.72s candidate='([^']*)'", LOG).group(1)
GARBAGE_SECONDS = 1.95
# The tool-call text Coding generated from it before Ctrl+C.
TOOL_TEXT = LOG[LOG.index("<function=computer_control>"):LOG.index("2026-10-18 10:22:10.496")]


def gate():
    return VoiceSessionGate(wake_aliases=config.VOICE_WAKE_ALIASES)


class InputGateTests(unittest.TestCase):
    def test_logged_hallucination_rejected_by_every_layer(self):
        self.assertTrue(is_runaway_repetition(GARBAGE))
        self.assertTrue(implausible_speech_rate(GARBAGE, GARBAGE_SECONDS))
        self.assertEqual(gate().parse(GARBAGE), (True, ""))
        self.assertEqual(gate().apply(GARBAGE, now=0), "")

    def test_any_number_of_repeated_wakes_is_one_wake(self):
        for n in (1, 2, 31, 32, 33, 60, 200, 500):
            self.assertEqual(gate().apply(" ".join(["Hey BMO"] * n), now=0), "", n)

    def test_random_mixed_alias_loops_are_wake_only(self):
        rng = random.Random(1288)
        forms = ["Hey BMO", "Bemo", "Beemo", "Hey Bemo", "BMO", "Hey Beemo"]
        for _ in range(200):
            text = " ".join(rng.choice(forms) for _ in range(rng.randint(2, 120)))
            if rng.random() < 0.5:
                text += " Hey B"  # Whisper truncation at its token limit
            self.assertEqual(gate().apply(text, now=0), "", text[:80])

    def test_real_commands_still_pass(self):
        cases = {
            "Hey BMO, open calculator": "open calculator",
            "Hey BMO Hey BMO Hey BMO open calculator": "open calculator",
            "Beemo can you switch to the coding model please?": "can you switch to the coding model please?",
            "Hey BMO, be more careful with that": "be more careful with that",
            "Hey Bemo, what's 2 plus 2?": "what's 2 plus 2?",
        }
        for heard, expected in cases.items():
            self.assertEqual(gate().apply(heard, now=0), expected)

    def test_long_genuine_request_is_not_filtered(self):
        text = ("Hey BMO, I want you to write a small Python program that reads the temperature log in my "
                "workshop folder, averages each hour, plots the results with matplotlib, saves the chart "
                "next to the log, and then tells me which hour of the day was the warmest and by how much "
                "it beat the coolest hour so I can decide when to run the laser cutter.")
        self.assertFalse(is_runaway_repetition(text))
        self.assertFalse(implausible_speech_rate(text, 22.0))
        self.assertTrue(gate().apply(text, now=0).startswith("I want you to write"))

    def test_short_and_emphatic_speech_survives(self):
        for text, seconds in (("Hey BMO, yes", 0.6), ("No no no, don't do that", 1.5), ("Hey BMO, no", 0.7)):
            self.assertFalse(is_runaway_repetition(text), text)
            self.assertFalse(implausible_speech_rate(text, seconds), text)

    def test_long_followup_counts_from_when_speech_started(self):
        # Live test 12:39:01: bare wake, then a shelf question that started 3.6 s
        # later but finished transcribing 16 s later. It must be accepted.
        g = gate()
        self.assertEqual(g.apply("Hey BMO", now=100.0), "")
        heard = "I'm thinking about building a small shelf for my workshop."
        self.assertEqual(g.apply(heard, now=116.0, started_at=103.6), heard)

    def test_followup_started_too_late_is_still_ignored(self):
        g = gate()
        g.apply("Hey BMO", now=100.0)
        self.assertIsNone(g.apply("some background talk", now=130.0, started_at=125.0))

    def test_rejected_repeated_names_are_recognised_as_a_wake(self):
        # Live test 12:46:04: rejected as a loop while the user was calling BMO.
        self.assertTrue(VoiceSessionGate._is_wake_residue("Hey BMO Bemo Hey BMO Bemo Hey BMO Bemo"))
        self.assertTrue(VoiceSessionGate._is_wake_residue(GARBAGE))
        self.assertFalse(VoiceSessionGate._is_wake_residue("Hey BMO open the door"))

    def test_parse_never_arms_followup(self):
        g = gate()
        self.assertEqual(g.parse("Hey Beemo"), (True, ""))
        self.assertIsNone(g.apply("I like turtles", now=1.0))


class OutputAndGroundingTests(unittest.TestCase):
    def test_logged_tool_loop_detected(self):
        self.assertTrue(output_is_looping(TOOL_TEXT))

    def test_normal_code_is_not_a_loop(self):
        code = (ROOT / "systems" / "turn_speech.py").read_text()
        self.assertFalse(output_is_looping(code))
        self.assertFalse(output_is_looping(LOG[LOG.index("I can handle a wide range"):LOG.index("Let me know what")]))

    def test_logged_fabrications_detected(self):
        self.assertTrue(fabricated_live_claim("Checking the local weather feed... 72°F, partly cloudy, light breeze."))
        self.assertTrue(fabricated_live_claim("Ah, right—local weather for your current location, based on the GPS signal from your wristband."))
        for benign in ("Water boils at 100 °C at sea level.", "The CPU is at 55 °C according to the diagnostics tool.",
                       "Rain forms when water vapour condenses."):
            self.assertFalse(fabricated_live_claim(benign), benign)

    def test_live_data_questions_routed_to_honest_answer(self):
        self.assertEqual(live_data_topic("what's the weather like today?"), "weather")
        self.assertEqual(live_data_topic("is it gonna rain tomorrow"), "weather")
        self.assertEqual(live_data_topic("where am I"), "location")
        for general in ("how does weather work?", "what causes rain?", "write a weather app in python",
                        "how hot is the cpu right now"):
            self.assertIsNone(live_data_topic(general), general)

    def test_list_answers_are_spoken_line_by_line(self):
        listing = LOG[LOG.index("I can handle a wide range"):LOG.index("Let me know what")]
        units, tail = speakable_units(listing)
        self.assertGreater(len(units), 10)
        self.assertTrue(units[0].startswith("I can handle"))
        self.assertFalse(any(u.startswith(("-", "1.", "**")) for u in units))
        # Plain sentences without newlines behave exactly as before.
        self.assertEqual(speakable_units("One. Two is unfinished"), (["One."], "Two is unfinished"))

    def test_pending_action_is_announced_by_bemo_not_the_model(self):
        # Live test: Coding read "Do not claim the action happened yet" aloud.
        body = _SRC[_SRC.index("    def _run_model_agent"):_SRC.index("    def _run_project_agent")]
        self.assertIn("I need your OK first", body)

    def test_prompts_know_the_real_home_folder(self):
        self.assertIn(str(Path.home()), config.HOST_FACTS)
        for profile in config.MODEL_PROFILES.values():
            self.assertIn(config.HOST_FACTS, profile["system_prompt"])

    def test_every_role_prompt_states_its_real_senses(self):
        for name, profile in config.MODEL_PROFILES.items():
            self.assertIn(config.GROUNDING_RULE, profile["system_prompt"], name)
        self.assertNotIn("real-world BMO", config.MODEL_PROFILES["normal"]["system_prompt"])


class ToolSafetyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cc = ComputerController(screenshot_dir=self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_coding_tools_stable_but_chat_is_recognised(self):
        # Same schemas every turn keep Ollama's prompt cache (live test: a
        # tool-set change cost a 23 s re-prefill).
        chat = tool_names_for_request("what kind of things can you code?", "coding", False)
        work = tool_names_for_request("write a python script that prints the time", "coding", False)
        self.assertEqual(chat, work)
        self.assertFalse(coding_work_request("what kind of things can you code?"))
        self.assertTrue(coding_work_request("write a python script that prints the time"))

    def test_input_injection_needs_owner_approval(self):
        for action, payload in (("type_text", {"text": "ls"}), ("key_press", {"key": "Return"}),
                                ("hotkey", {"keys": ["ctrl", "w"]})):
            self.assertTrue(self.cc.assess(action, payload).get("confirmation_required"), action)
        # A click needs a pointed-at target first (v1.28.23); then it still needs approval.
        self.assertTrue(self.cc.assess("mouse_click", {"button": "left"}).get("blocked"))
        self.cc._last_mouse_move = time.monotonic()
        self.assertTrue(self.cc.assess("mouse_click", {"button": "left"}).get("confirmation_required"))
        for action in ("mouse_move", "scroll", "screenshot"):
            self.assertFalse(self.cc.assess(action, {}).get("confirmation_required"), action)

    def test_logged_garbage_cannot_be_typed(self):
        args = TOOL_TEXT[TOOL_TEXT.index('{"text": "') + 10:]
        long_text = (args + " ") * 4
        self.assertTrue(self.cc.assess("type_text", {"text": long_text}).get("blocked"))

    def test_single_file_delete_is_recoverable_or_confirmed(self):
        target = Path(self.tmp.name) / "note.txt"
        target.write_text("x")
        result = self.cc.assess("delete", {"path": str(target)})
        if self.cc._trash_command():
            self.assertFalse(result.get("confirmation_required"))
        else:
            self.assertTrue(result.get("confirmation_required"))


class AutotuneTests(unittest.TestCase):
    def test_benchmark_prompts_cannot_hit_the_prompt_cache(self):
        import hardware_autotune as at
        a, b = at._benchmark_prompt("8-0-1"), at._benchmark_prompt("8-1-2")
        self.assertNotEqual(a[:40], b[:40])  # differ at the very start

    def test_old_v2_record_is_ignored_and_physical_cores_are_used(self):
        import hardware
        record = {"algorithm_version": 2, "cpu_name": "X", "physical_cores": 8,
                  "logical_cores": 16, "chosen_ollama_threads": 16}
        self.assertFalse(hardware.autotune_record_matches(record, cpu_name="X", physical_cores=8, logical_cores=16))
        self.assertEqual(hardware.profile_from_counts(cpu_name="X", physical_cores=8, logical_cores=16, ram_gb=28).ollama_threads, 8)


# --- Approval and pinning, using the real Chat methods ----------------------
_CHAT = next(n for n in ast.parse(_SRC).body if isinstance(n, ast.ClassDef) and n.name == "Chat")
_NAMES = {"_profile_spoken_name", "_confirm_specialist_switch", "_confirm_by_voice", "_switch_profile",
          "_return_to_main_after_specialist", "_normalize_command_text", "_stop_background_warmup"}
_MINI = ast.ClassDef(name="ChatMini", bases=[], keywords=[], decorator_list=[],
                     body=[n for n in _CHAT.body if isinstance(n, ast.FunctionDef) and n.name in _NAMES])


class _Quiet:
    def __getattr__(self, name):
        return lambda *a, **k: None


class _Clock:
    now = 0.0

    def monotonic(self):
        self.now += 0.25
        return self.now


_clock = _Clock()
_State = types.SimpleNamespace(READY="ready", THINKING="thinking", RESPONDING="responding")
_ns = {"config": config, "State": _State, "logger": _Quiet(), "time": _clock, "re": re, "available_ram_gb": lambda: 16}
exec(compile(ast.fix_missing_locations(ast.Module(body=[_MINI], type_ignores=[])), "chat-mini-1288", "exec"), _ns)


class Harness(_ns["ChatMini"]):
    def __init__(self, replies=(), active="normal"):
        self.active_profile_name = active
        self._active_microphone = object()
        self.stop_event = types.SimpleNamespace(is_set=lambda: False)
        self.voice_gate = gate()
        self.replies = iter(replies)
        self.spoken = []
        self.switch_calls = []
        self.state = _State.READY

    @property
    def active_profile(self):
        return config.MODEL_PROFILES[self.active_profile_name]

    @property
    def active_model(self):
        return self.active_profile["model"]

    def listen(self, source):
        try:
            return next(self.replies)
        except StopIteration:
            _clock.now += 25
            return None

    def speak(self, text, *a, **k):
        self.spoken.append(str(text))

    def _is_model_installed(self, model):
        return True

    def _begin_model_switch_visual(self, *a, **k):
        pass

    def _finish_model_switch_visual(self, *a, **k):
        pass

    def _supervisor_switch_model(self, model, **kw):
        self.switch_calls.append(model)
        return True

    def _warm_runtime_prompt(self, **kw):
        self.warmed = True


class ApprovalAndPinTests(unittest.TestCase):
    def setUp(self):
        _clock.now = 0.0

    def test_bare_no_declines(self):
        h = Harness(["No"], active="coding")
        self.assertEqual(h._switch_profile("normal"), (False, "USER_DECLINED_MODEL_SWITCH"))
        self.assertEqual(h.switch_calls, [])

    def test_bare_yes_still_needs_wake_prefix(self):
        h = Harness(["Yes", "Hey BMO, yes"])
        ok, _ = h._switch_profile("coding")
        self.assertTrue(ok)
        self.assertEqual(len(h.switch_calls), 1)

    def test_wake_heard_during_approval_does_not_arm_followup(self):
        h = Harness(["Hey Beemo"], active="coding")
        h._switch_profile("normal")
        self.assertIsNone(h.voice_gate.apply("I like turtles", now=_clock.now + 1))

    def test_manual_specialist_stays_selected(self):
        h = Harness(["Hey BMO, yes"])
        ok, _ = h._switch_profile("coding", pin=True)
        self.assertTrue(ok)
        self.assertTrue(getattr(h, "warmed", False))  # prompt warm-up starts after the switch
        h._return_to_main_after_specialist("coding")
        self.assertEqual(h.active_profile_name, "coding")
        self.assertFalse(any("switch to Main" in s for s in h.spoken[1:]))

    def test_delegated_specialist_still_offers_return(self):
        h = Harness(["Hey BMO, yes", "Hey BMO, yes"])
        h._switch_profile("coding")  # delegated: not pinned
        h._return_to_main_after_specialist("coding")
        self.assertEqual(h.active_profile_name, "normal")

    def test_switching_back_to_main_clears_pin(self):
        h = Harness(["Hey BMO, yes", "Hey BMO, yes"])
        h._switch_profile("coding", pin=True)
        h._switch_profile("normal", pin=True)
        self.assertFalse(h._manual_profile_pin)


if __name__ == "__main__":
    unittest.main()
