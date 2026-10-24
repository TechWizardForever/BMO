"""BMO's spoken first boot, microphone and screen steps (installer plan I2), with fake audio and screens."""
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest

import numpy as np

ROOT = Path(os.environ.get("BEMO_TEST_CODE_ROOT", Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(ROOT))
from systems import first_boot as fb
from systems.first_boot import DONE, FAILED, PENDING, SKIPPED


def audio(voice=0.0, noise=200.0, seconds=5.0, seed=1):
    """Room noise, with a 1.5 s 'voice' in the middle when voice > 0 (16 kHz mono int16)."""
    rng = np.random.default_rng(seed)
    n = int(fb.RATE * seconds)
    signal = rng.normal(0, noise, n)
    if voice:
        start, length = int(fb.RATE * 1.5), int(fb.RATE * 1.5)
        t = np.arange(length) / fb.RATE
        signal[start:start + length] += voice * np.sin(2 * np.pi * 220 * t)
    return np.clip(signal, -32768, 32767).astype("<i2").tobytes()


class FakeIO:
    def __init__(self, inputs=(), recordings=(), heard=None, answers=()):
        self.inputs = list(inputs)
        self.recordings = list(recordings)   # one {name: pcm} per prompt
        self.heard = heard or {}             # pcm -> transcript
        self.answers = list(answers)
        self.said, self.default_set, self.calibrated = [], [], 0

    def say(self, text):
        self.said.append(text)

    def list_inputs(self):
        return self.inputs

    def record_inputs(self, names, seconds):
        return self.recordings.pop(0) if self.recordings else {n: audio() for n in names}

    def transcribe(self, pcm):
        return self.heard.get(pcm, "")

    def heard_wake(self, pcm):
        return False

    def set_default_input(self, name):
        self.default_set.append(name)

    def ask(self, question):
        self.said.append(question)
        return self.answers.pop(0) if self.answers else None

    def calibrate_screen(self):
        self.calibrated += 1

    def online(self):
        return True


def mic(name, desc, default=False):
    return types.SimpleNamespace(name=name, description=desc, default=default)


SOLO = mic("alsa_input.usb-Focusrite_Scarlett_Solo", "Scarlett Solo (3rd Gen.) Analog Stereo")
CLIP = mic("alsa_input.usb-Clip-on_ATR4650", "ATR4650-USB Mono", default=True)


class StateTests(unittest.TestCase):
    def setUp(self):
        self.path = Path(tempfile.mkdtemp()) / "install" / "first-boot.json"

    def test_saved_after_each_step_and_resumed(self):
        state = fb.FirstBootState(self.path)
        self.assertFalse(state.active())  # no file: an install from before first boot existed
        state.begin(("microphone", "screen"))
        state.mark("microphone", DONE, {"input": "x"})
        again = fb.FirstBootState(self.path)  # e.g. after a power cut
        self.assertTrue(again.active())
        self.assertEqual([s["status"] for s in again.steps()], [DONE, PENDING])
        again.mark("screen", DONE)
        self.assertFalse(fb.FirstBootState(self.path).active())
        self.assertTrue(json.loads(self.path.read_text())["completed_at"])

    def test_a_broken_file_is_no_first_boot(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text("{not json")
        self.assertFalse(fb.FirstBootState(self.path).active())


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.state = fb.FirstBootState(Path(tempfile.mkdtemp()) / "first-boot.json")
        self.state.begin(("microphone", "screen", "internet"))  # downloads: tests/test_downloads.py

    def test_each_phase_runs_its_steps_in_order(self):
        clip_voice = audio(voice=6000)
        io = FakeIO([CLIP], [{CLIP.name: clip_voice}], {clip_voice: "Hey BMO, hello."}, answers=["Yes."])
        boot = fb.FirstBoot(self.state, io)
        self.assertTrue(boot.run("before_listening"))
        self.assertEqual(io.said[0], fb.GREETING)
        self.assertEqual([s["status"] for s in self.state.steps()], [DONE, PENDING, PENDING])
        self.assertTrue(boot.run("listening"))
        self.assertIn("I'm connected to the internet.", io.said)
        self.assertEqual(io.said.count(fb.GREETING), 1)
        self.assertEqual(io.said[-1], fb.FINISHED)
        self.assertFalse(self.state.active())

    def test_a_failed_step_is_tried_again_at_the_next_start(self):
        io = FakeIO([])  # no microphone at all
        self.assertFalse(fb.FirstBoot(self.state, io).run("before_listening"))
        self.assertIn("can't find a microphone", io.said[-1])
        self.assertEqual(self.state.steps()[0]["status"], FAILED)
        voice = audio(voice=6000)
        later = FakeIO([CLIP], [{CLIP.name: voice}], {voice: "hello"})
        self.assertTrue(fb.FirstBoot(self.state, later).run("before_listening"))
        self.assertEqual(self.state.steps()[0]["attempts"], 2)
        self.assertNotIn(fb.GREETING, later.said)  # already greeted the first time

    def test_steps_not_built_yet_are_skipped(self):
        self.state.begin(("microphone", "first_start"))
        voice = audio(voice=6000)
        fb.FirstBoot(self.state, FakeIO([CLIP], [{CLIP.name: voice}], {voice: "hello"})).run("before_listening")
        self.assertEqual([s["status"] for s in self.state.steps()], [DONE, SKIPPED])


class MicrophoneTests(unittest.TestCase):
    def test_level_tells_voice_from_room(self):
        self.assertGreater(fb.level(audio(voice=6000)).snr_db, 20)
        self.assertLess(fb.level(audio()).snr_db, 3)

    def test_a_burst_of_noise_as_an_input_opens_is_not_a_voice(self):
        # Live 2026-10-19: the motherboard input gives ~0.26 s of full-scale noise, then zeros.
        burst = np.zeros(fb.RATE * 5, dtype="<i2")
        burst[1:int(fb.RATE * 0.45):2] = 32767
        self.assertEqual(fb.level(burst.tobytes()).snr_db, 0.0)
        late = burst.copy()
        late[fb.RATE * 2:fb.RATE * 2 + 800] = 20000  # digital silence with a click: still no microphone
        self.assertEqual(fb.level(late.tobytes()).snr_db, 0.0)
        clipped = np.full(fb.RATE * 5, 32767, dtype="<i2")
        clipped[::2] = -32768
        self.assertEqual(fb.level(clipped.tobytes()).snr_db, 0.0)

    def test_picks_the_microphone_that_heard_the_phrase_best_and_makes_it_the_default(self):
        near, far = audio(voice=8000, seed=2), audio(voice=1500, seed=3)
        io = FakeIO([CLIP, SOLO], [{CLIP.name: far, SOLO.name: near}], {near: "Hey BMO, hello.", far: "hello"})
        outcome = fb.MicrophoneStep().run(io)
        self.assertEqual(outcome.status, DONE)
        self.assertEqual(outcome.result["input"], SOLO.name)
        self.assertEqual(io.default_set, [SOLO.name])
        self.assertIn("I can hear you on the Scarlett Solo.", io.said[-1])
        self.assertNotIn("noisy", io.said[-1])

    def test_room_noise_and_other_words_are_not_the_phrase(self):
        talk = audio(voice=6000, seed=4)
        io = FakeIO([CLIP], [{CLIP.name: talk}, {CLIP.name: audio()}], {talk: "what's on TV tonight"})
        outcome = fb.MicrophoneStep().run(io)
        self.assertEqual(outcome.status, FAILED)
        self.assertEqual([s for s in io.said if "Hey BMO, hello" in s], [fb.MicrophoneStep.FIRST, fb.MicrophoneStep.AGAIN])
        self.assertIn("plugged in", io.said[-1])
        self.assertNotIn("what's on TV", json.dumps(outcome.result))  # nothing heard is kept

    def test_a_quiet_voice_gets_a_tip_and_the_default_stays(self):
        quiet = audio(voice=900, noise=200)
        io = FakeIO([CLIP], [{CLIP.name: quiet}], {quiet: "hello"})
        outcome = fb.MicrophoneStep().run(io)
        self.assertEqual(outcome.status, DONE)
        self.assertEqual(io.default_set, [])
        self.assertIn("move it closer", io.said[-1])

    def test_phrase_and_names(self):
        for text in ("Hey BMO, hello.", "Hello.", "Hey Bemo.", "hi there"):
            self.assertTrue(fb.heard_phrase(text), text)
        for text in ("", "What's the weather?", "Bemo"):
            self.assertFalse(fb.heard_phrase(text), text)
        self.assertEqual(fb.spoken_device("Family 17h/19h HD Audio Controller Analog Stereo"),
                         "computer's own microphone socket")
        self.assertEqual(fb.spoken_device("ATR4650-USB Mono"), "ATR4650 USB")

    def test_recording_every_input_at_once(self):
        calls = []

        class FakeProc:
            def __init__(self, argv, **kwargs):
                calls.append(argv)
                self.stdout = io.BytesIO(b"\x01\x00" * (fb.RATE * 2))

            def terminate(self):
                pass

            def wait(self, timeout=None):
                return 0

        got = fb.record_sources(["a", "b"], 1.0, popen=FakeProc)
        self.assertEqual(sorted(got), ["a", "b"])
        self.assertEqual(len(got["a"]), fb.RATE * 2)
        self.assertIn("--device=a", calls[0] + calls[1])
        self.assertIn("--rate=16000", calls[0])


class ScreenTests(unittest.TestCase):
    def test_yes_or_no(self):
        for text in ("Yes.", "Yeah, it fits.", "I can see all of it", "Looks good", "Hey BMO, yes"):
            self.assertIs(fb.yes_or_no(text), True, text)
        for text in ("No.", "Um, no.", "The left side is cut off", "It doesn't fit", "nope"):
            self.assertIs(fb.yes_or_no(text), False, text)
        for text in ("", None, "banana bread"):
            self.assertIsNone(fb.yes_or_no(text), text)

    def test_yes_keeps_the_screen_and_no_starts_calibration(self):
        io = FakeIO(answers=["yes"])
        self.assertEqual(fb.ScreenStep().run(io).result, {"fits": True})
        self.assertEqual(io.calibrated, 0)
        io = FakeIO(answers=["no, the top is cut off"])
        self.assertEqual(fb.ScreenStep().run(io).result, {"fits": False, "calibration_offered": True})
        self.assertEqual(io.calibrated, 1)

    def test_no_answer_leaves_the_screen_and_says_how_to_fix_it_later(self):
        io = FakeIO(answers=[None, "banana"])
        outcome = fb.ScreenStep().run(io)
        self.assertEqual((outcome.status, outcome.result), (DONE, {"fits": None}))
        self.assertIn("calibrate the screen, any time", io.said[-1])


class WiringTests(unittest.TestCase):
    def test_bmo_runs_the_phases_where_they_belong(self):
        src = (ROOT / "systems" / "chat.py").read_text()
        self.assertLess(src.index('self._first_boot("before_listening")'),
                        src.index("if config.SUPERVISOR_PRELOAD_ACTIVE_MODEL_ON_START:"))
        self.assertLess(src.index("self._calibrate_microphone(source)\n"), src.index('self._first_boot("listening")'))
        self.assertLess(src.index('self._first_boot("listening")'), src.index("config.VOICE_STARTUP_READY_ANNOUNCEMENT)"))

    def test_supervisor_starts_bmo_without_the_wake_phrase_only_during_first_boot(self):
        import supervisor
        path = Path(tempfile.mkdtemp()) / "first-boot.json"
        original = fb.FirstBootState.default
        fb.FirstBootState.default = classmethod(lambda cls: cls(path))
        try:
            self.assertFalse(supervisor._first_boot_open())
            fb.FirstBootState(path).begin()
            self.assertTrue(supervisor._first_boot_open())
        finally:
            fb.FirstBootState.default = original
        self.assertIn('service.request_start_bemo("first_boot")', (ROOT / "supervisor.py").read_text())


if __name__ == "__main__":
    unittest.main()
