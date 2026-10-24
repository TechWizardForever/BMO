"""Sound by voice (settings plan S1): parser, pactl backend, and the voice handler."""
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
from systems import sound
from systems.sound import PactlSound, needs_ok, parse_sound_command, plan_change

HDMI = "alsa_output.pci-0000_05_00.1.hdmi-stereo"
SCARLETT = "alsa_output.usb-Focusrite_Scarlett_Solo_USB_Y73XNDD1CDBC29-00.analog-stereo"


class FakePactl:
    """pactl as recorded on the test PC (PipeWire 1.0.5), with state."""

    def __init__(self, volume=40, muted=False, default=SCARLETT):
        self.state = {HDMI: [40, False], SCARLETT: [volume, muted]}
        self.default, self.calls = default, []
        self.streams = [{"index": 12}]

    def __call__(self, argv, **kw):
        self.calls.append(argv[1:])
        args, out = argv[1:], ""
        if args == ["get-default-sink"]:
            out = self.default + "\n"
        elif args == ["-f", "json", "list", "sinks"]:
            out = json.dumps([
                {"index": 79, "name": HDMI, "description": "Renoir Radeon High Definition Audio Controller Digital Stereo (HDMI)",
                 "mute": self.state[HDMI][1], "volume": {"front-left": {"value_percent": f"{self.state[HDMI][0]}%"},
                                                         "front-right": {"value_percent": f"{self.state[HDMI][0]}%"}}},
                {"index": 80, "name": SCARLETT, "description": "Scarlett Solo (3rd Gen.) Analog Stereo",
                 "mute": self.state[SCARLETT][1], "volume": {"front-left": {"value_percent": f"{self.state[SCARLETT][0]}%"},
                                                             "front-right": {"value_percent": f"{self.state[SCARLETT][0]}%"}}}])
        elif args == ["-f", "json", "list", "sink-inputs"]:
            out = json.dumps(self.streams)
        elif args[0] == "set-sink-volume":
            self.state[self.default][0] = int(args[2].rstrip("%"))
        elif args[0] == "set-sink-mute":
            self.state[self.default][1] = args[2] == "1"
        elif args[0] == "set-default-sink":
            self.default = args[1]
        return types.SimpleNamespace(returncode=0, stdout=out, stderr="")


class ParseTests(unittest.TestCase):
    def test_phrasings(self):
        p = parse_sound_command
        self.assertEqual(p("volume up"), {"kind": "change", "delta": 10})
        self.assertEqual(p("turn it up"), {"kind": "change", "delta": 10})
        self.assertEqual(p("a little louder"), {"kind": "change", "delta": 5})
        self.assertEqual(p("turn the volume up a lot"), {"kind": "change", "delta": 20})
        self.assertEqual(p("could you make it quieter please"), {"kind": "change", "delta": -10})
        self.assertEqual(p("volume down by 20"), {"kind": "change", "delta": -20})
        self.assertEqual(p("set the volume to 40 percent"), {"kind": "set", "level": 40})
        self.assertEqual(p("volume to max"), {"kind": "set", "level": 100})
        self.assertEqual(p("mute"), {"kind": "mute"})
        self.assertEqual(p("unmute the sound"), {"kind": "unmute"})
        self.assertEqual(p("what's the volume?"), {"kind": "query"})
        self.assertEqual(p("switch sound to the TV"), {"kind": "output", "target": "the tv", "explicit": True})
        self.assertEqual(p("use the speakers for sound"), {"kind": "output", "target": "the speakers", "explicit": True})
        # Live 2026-10-18: "Switch Sound to the Scarlet." / "switch sound back to the Scarlet" / "...Back to Solo."
        self.assertEqual(p("switch sound back to the Scarlet")["target"], "the scarlet")
        self.assertEqual(p("Switch Sound Back to Solo.")["target"], "solo")
        self.assertFalse(p("switch back to the speakers")["explicit"])

    def test_leaves_other_commands_alone(self):
        for other in ("mute yourself", "unmute voice", "turn on computer control", "what time is it",
                      "turn off the lights", "open volume control", "tell me a joke"):
            self.assertIsNone(parse_sound_command(other), other)

    def test_owner_approved_safety(self):
        self.assertFalse(needs_ok(40, 50))
        self.assertTrue(needs_ok(40, 80))   # a jump of more than 30 points
        self.assertTrue(needs_ok(80, 90))   # above 85%
        self.assertFalse(needs_ok(95, 90))  # turning it down never asks
        self.assertEqual(plan_change(95, {"kind": "change", "delta": 10}), 100)
        self.assertEqual(plan_change(3, {"kind": "change", "delta": -10}), 0)


class BackendTests(unittest.TestCase):
    def test_outputs_names_and_matching(self):
        backend = PactlSound(runner=FakePactl())
        hdmi, scarlett = backend.outputs()
        self.assertEqual((scarlett.volume, scarlett.default, hdmi.default), (40, True, False))
        self.assertEqual(scarlett.spoken, "Scarlett Solo")
        self.assertEqual(hdmi.spoken, "the HDMI output (TV or monitor)")
        self.assertEqual(hdmi.matches("the tv"), 1.0)
        self.assertEqual(scarlett.matches("speakers"), 1.0)

    def test_other_pcs_ports_decide_speakers_or_headphones(self):
        from systems.sound import Output
        laptop_phones = Output("a", "Built-in Audio Analog Stereo", 50, False, True, "analog-output-headphones")
        laptop_speaker = Output("b", "Built-in Audio Analog Stereo", 50, False, False, "analog-output-speaker")
        self.assertEqual((laptop_phones.matches("headphones"), laptop_phones.matches("speakers")), (1.0, 0.0))
        self.assertEqual((laptop_speaker.matches("speakers"), laptop_speaker.matches("headphones")), (1.0, 0.0))
        dp = Output("c", "Some GPU Digital Stereo", 50, False, False, "hdmi-output-1")
        self.assertEqual(dp.matches("the tv"), 1.0)

    def test_switching_output_moves_what_is_playing(self):
        fake = FakePactl()
        after = PactlSound(runner=fake).use_output(PactlSound(runner=fake).outputs()[0])
        self.assertEqual(after.name, HDMI)
        self.assertIn(["move-sink-input", "12", HDMI], fake.calls)


class HandlerTests(unittest.TestCase):
    def setUp(self):
        src = (ROOT / "systems" / "chat.py").read_text()
        chat = next(n for n in ast.parse(src).body if isinstance(n, ast.ClassDef) and n.name == "Chat")
        body = [n for n in chat.body if isinstance(n, ast.FunctionDef) and n.name in {"_handle_sound_command", "_say_local"}]
        ns = {"config": config, "logger": types.SimpleNamespace(info=lambda *a, **k: None, warning=lambda *a, **k: None),
              "State": types.SimpleNamespace(RESPONDING="r"), "host_sound": lambda: None,
              "needs_ok": needs_ok, "parse_sound_command": parse_sound_command, "plan_change": plan_change}
        exec(compile(ast.fix_missing_locations(ast.Module(body=[ast.ClassDef(
            name="Mini", bases=[], keywords=[], body=body, decorator_list=[])], type_ignores=[])), "mini", "exec"), ns)
        self.Mini = ns["Mini"]

    def chat(self, fake, answers=()):
        c = self.Mini()
        c._sound, c.fake = PactlSound(runner=fake), fake
        c.spoken, c.questions, answers = [], [], list(answers)
        c.speak, c._archive_local_exchange = c.spoken.append, lambda *a: None
        c._confirm_by_voice = lambda q, **kw: c.questions.append(q) or (answers.pop(0) if answers else False)
        return c

    def test_up_down_and_read_back(self):
        c = self.chat(FakePactl(volume=40))
        self.assertTrue(c._handle_sound_command("volume up"))
        self.assertEqual(c.spoken[-1], "Volume is 50 percent.")
        c._handle_sound_command("a little quieter")
        self.assertEqual(c.spoken[-1], "Volume is 45 percent.")
        c._handle_sound_command("what's the volume")
        self.assertEqual(c.spoken[-1], "The volume is 45 percent, on Scarlett Solo.")
        self.assertEqual(c.questions, [])

    def test_big_jump_asks_first(self):
        c = self.chat(FakePactl(volume=40), answers=[False, True])
        c._handle_sound_command("set the volume to 90")
        self.assertEqual(c.spoken[-1], "Okay, I'll leave it at 40 percent.")
        self.assertEqual(c.fake.state[SCARLETT][0], 40)
        c._handle_sound_command("set the volume to 90")
        self.assertEqual(c.spoken[-1], "Volume is 90 percent.")
        self.assertEqual(len(c.questions), 2)
        self.assertTrue(c.questions[0].startswith("That's a big jump, from 40 to 90 percent."))
        c = self.chat(FakePactl(volume=85), answers=[True])
        c._handle_sound_command("set the volume to 100")
        self.assertTrue(c.questions[0].startswith("That's loud: 100 percent."))

    def test_mute_speaks_first_and_turning_up_unmutes(self):
        c = self.chat(FakePactl(volume=40))
        c._handle_sound_command("mute")
        self.assertEqual(c.spoken, ["Muting. Say Hey BMO, unmute, to hear me again."])
        self.assertTrue(c.fake.state[SCARLETT][1])
        c._handle_sound_command("volume up")
        self.assertFalse(c.fake.state[SCARLETT][1])
        self.assertEqual(c.spoken[-1], "Volume is 50 percent.")

    def test_output_switch(self):
        c = self.chat(FakePactl())
        c._handle_sound_command("switch sound to the TV")
        self.assertEqual(c.spoken[-1], "Sound now plays through the HDMI output (TV or monitor).")
        c._handle_sound_command("play the sound through the bathtub")
        self.assertIn("I don't have a sound output called the bathtub", c.spoken[-1])

    def test_switching_back_with_whispers_spelling(self):
        c = self.chat(FakePactl(default=HDMI))
        for said in ("Switch Sound to the Scarlet.", "switch sound back to the Scarlet", "Switch Sound Back to Solo."):
            c.fake.default = HDMI
            self.assertTrue(c._handle_sound_command(said), said)
            self.assertEqual(c.spoken[-1], "Sound now plays through Scarlett Solo.", said)

    def test_switch_back_to_a_model_or_app_is_not_sound(self):
        c = self.chat(FakePactl())
        for said in ("switch back to coding", "switch back to firefox", "go back to main"):
            self.assertFalse(c._handle_sound_command(said), said)
        self.assertEqual(c.spoken, [])
        self.assertTrue(c._handle_sound_command("switch back to the TV"))  # an output's name

    def test_not_a_sound_command(self):
        c = self.chat(FakePactl())
        self.assertFalse(c._handle_sound_command("tell me a joke"))
        self.assertEqual(c.spoken, [])


if __name__ == "__main__":
    unittest.main()
