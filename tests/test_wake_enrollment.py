"""Learning each person's "Hey BMO" at first boot (installer plan I7), with fake audio."""
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
from systems import wake_enrollment as we
from systems import wakeword as ww


def voice(seed=1, loud=6000.0, seconds=2.5):
    rng = np.random.default_rng(seed)
    n = int(fb.RATE * seconds)
    x = rng.normal(0, 200, n)
    x[int(n * 0.4):int(n * 0.8)] += loud * np.sin(np.arange(int(n * 0.8) - int(n * 0.4)) / 7)
    return np.clip(x, -32768, 32767).astype("<i2").tobytes()


class FakeIO:
    def __init__(self, clips, result=None):
        self.clips, self.said, self.enrolled = list(clips), [], None
        self.result = result or we.Enrollment(True, 0.5, True, 6, [1.0] * 6)

    def say(self, text):
        self.said.append(text)

    def record_voice(self, seconds):
        return self.clips.pop(0) if self.clips else voice(loud=0)

    def enroll(self, wakes, talk):
        self.enrolled = (len(wakes), len(talk))
        return self.result


class SettingsTests(unittest.TestCase):
    def test_a_check_when_the_detector_knows_the_voice(self):
        self.assertEqual(we.settings_from([0.95, 1.0, 0.9, 0.2, 0.99, 1.0], 0.7), (we.VERIFIER_THRESHOLD, True))

    def test_a_lower_threshold_for_a_voice_it_hears_weakly(self):
        threshold, check = we.settings_from([0.45, 0.4, 0.35, 0.5, 0.1, 0.38], 0.7)
        self.assertFalse(check)
        self.assertGreaterEqual(threshold, we.MIN_THRESHOLD)
        self.assertLess(threshold, 0.7)

    def test_never_below_the_floor_and_unchanged_when_it_hardly_reacts(self):
        self.assertEqual(we.settings_from([0.31, 0.32, 0.33, 0.0, 0.0, 0.0], 0.7)[0], we.MIN_THRESHOLD)
        self.assertEqual(we.settings_from([0.05, 0.1, 0.0, 0.2, 0.0, 0.1], 0.7), (0.7, False))


class StepTests(unittest.TestCase):
    def test_six_wakes_then_talk_then_enroll(self):
        io = FakeIO([voice(i) for i in range(6)] + [voice(9, seconds=10)])
        outcome = fb.VoiceStep().run(io)
        self.assertEqual(outcome.status, fb.DONE)
        self.assertEqual(io.enrolled, (6, 1))
        self.assertEqual(outcome.result, {"enrolled": True, "personal_check": True, "threshold": 0.5, "heard": 6})
        self.assertEqual(io.said.count("Again."), 5)
        self.assertIn("learnt how you say my name", io.said[-1])

    def test_silence_is_asked_again_and_too_little_is_a_problem_to_come_back_to(self):
        silent = voice(loud=0)
        io = FakeIO([voice(1), silent, voice(2), silent, silent, silent])
        outcome = fb.VoiceStep().run(io)
        self.assertEqual((outcome.status, outcome.result["enrolled"]), (fb.FAILED, False))
        self.assertIsNone(io.enrolled)
        self.assertEqual(io.said[-1], "I couldn't hear enough.")
        self.assertEqual(io.said.count("I didn't hear that."), 4)
        self.assertEqual(fb.problem_lines("voice", outcome.result), ["Learn your voice: I couldn't hear enough."])

    def test_a_voice_the_detector_hardly_knows_still_finishes_the_setup(self):
        io = FakeIO([voice(i) for i in range(7)], result=we.Enrollment(False, 0.7, False, 6, [0.1] * 6, "hardly"))
        outcome = fb.VoiceStep().run(io)
        self.assertEqual((outcome.status, outcome.result["enrolled"]), (fb.DONE, False))
        self.assertIn("usual settings", io.said[-1])

    def test_it_comes_right_after_the_microphone(self):
        self.assertEqual(fb.STEP_ORDER.index("voice"), fb.STEP_ORDER.index("microphone") + 1)
        self.assertEqual(fb.VoiceStep.phase, "before_listening")


class DetectorTests(unittest.TestCase):
    def test_personal_settings_are_picked_up_when_they_change(self):
        directory = Path(tempfile.mkdtemp())
        for name in ("hey_bmo.onnx", "melspectrogram.onnx", "embedding_model.onnx"):
            (directory / name).write_bytes(b"x")
        config = types.SimpleNamespace(ENABLE_WAKEWORD_MODEL=True, WAKEWORD_MODEL_PATH=directory / "hey_bmo.onnx",
                                       WAKEWORD_MELSPEC_PATH=directory / "melspectrogram.onnx",
                                       WAKEWORD_EMBEDDING_PATH=directory / "embedding_model.onnx",
                                       WAKEWORD_THRESHOLD=0.7)
        ww._DETECTOR, ww._PERSONAL_STAMP = None, None
        try:
            self.assertEqual(ww.detector(config).threshold, 0.7)
            self.assertIsNone(ww.detector(config).verifier_path)
            (directory / "verifier.pkl").write_bytes(b"x")
            (directory / "personal.json").write_text(json.dumps({"threshold": 0.5, "verifier": "verifier.pkl"}))
            found = ww.detector(config)
            self.assertEqual((found.threshold, found.verifier_path.name), (0.5, "verifier.pkl"))
        finally:
            ww._DETECTOR, ww._PERSONAL_STAMP = None, None

    def test_nothing_heard_is_written_only_the_small_settings(self):
        src = (ROOT / "systems" / "wake_enrollment.py").read_text()
        self.assertNotIn(".wav", src)
        self.assertNotIn("np.save", src)

    def test_the_public_detector_ships_and_installs_only_where_none_is(self):
        folder = ROOT / "assets" / "wakeword"
        for name in ("hey_bmo.onnx", "melspectrogram.onnx", "embedding_model.onnx"):
            self.assertGreater((folder / name).stat().st_size, 100_000, name)
        install = (ROOT / "install.sh").read_text()
        self.assertIn('cp -n "$SCRIPT_DIR/assets/wakeword/$f" "$WAKEWORD_DIR/$f"', install)

    def test_the_public_detector_stays_quiet_on_silence(self):
        folder = ROOT / "assets" / "wakeword"
        detector = ww.WakeWordDetector(folder / "hey_bmo.onnx", folder / "melspectrogram.onnx",
                                       folder / "embedding_model.onnx", 0.7)
        if not detector.available():
            self.skipTest("openwakeword not installed")
        self.assertIsNone(detector.heard(np.zeros(fb.RATE * 2, dtype=np.int16).tobytes()))

    def test_the_command_to_redo_it(self):
        chat = (ROOT / "systems" / "chat.py").read_text()
        self.assertIn("if self._handle_learn_voice(command):", chat)


if __name__ == "__main__":
    unittest.main()
