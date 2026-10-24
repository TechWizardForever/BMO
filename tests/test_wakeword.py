"""BMO's own "Hey BMO" detector (2026-10-19). Uses the installed model when present; skipped otherwise."""
import glob
import os
from pathlib import Path
import sys
import unittest
import wave

ROOT = Path(os.environ.get("BEMO_TEST_CODE_ROOT", Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(ROOT))
import numpy as np
import config
from systems.wakeword import WakeWordDetector

INSTALLED = Path.home() / ".bemo" / "models" / "wakeword"  # BEMO_DATA_DIR is a temp dir in run_all.sh
OWNER = sorted(glob.glob(str(Path.home() / ".bemo" / "wake-samples" / "*" / "wake" / "*.wav")))


def load(path):
    with wave.open(path) as w:
        return np.frombuffer(w.readframes(w.getnframes()), dtype="<i2")


class WiringTests(unittest.TestCase):
    def test_bmo_and_standby_use_the_detector_beside_whisper(self):
        chat = (ROOT / "systems" / "chat.py").read_text()
        self.assertIn("text = self._wakeword_rescue(audio, text)", chat)
        self.assertIn("rest = raw[max(0, hit.end_sample - int(0.3 * config.STT_SAMPLE_RATE)) * 2:]", chat)
        standby = (ROOT / "standby_wake.py").read_text()
        self.assertIn("if self._wake_decision(text, time.monotonic()) or self._detector_heard(audio, text):", standby)
        self.assertEqual(config.WAKEWORD_THRESHOLD, 0.7)


class DetectorTests(unittest.TestCase):
    def setUp(self):
        self.detector = WakeWordDetector(INSTALLED / "hey_bmo.onnx", INSTALLED / "melspectrogram.onnx",
                                         INSTALLED / "embedding_model.onnx", config.WAKEWORD_THRESHOLD)
        if not self.detector.available():
            self.skipTest("the Hey BMO model isn't installed on this computer")

    def test_owner_recordings_and_silence(self):
        if len(OWNER) < 10:
            self.skipTest("the owner's wake recordings aren't here")
        heard = [self.detector.heard(load(p)) is not None for p in OWNER[1:]]  # the first one is cut off
        self.assertGreaterEqual(sum(heard), 8, heard)
        self.assertIsNone(self.detector.heard(np.zeros(32000, dtype=np.int16)))
        noise = (np.random.default_rng(1).normal(0, 2000, 32000)).astype(np.int16)
        self.assertIsNone(self.detector.heard(noise))

    def test_end_of_the_wake_is_inside_the_clip(self):
        if not OWNER:
            self.skipTest("the owner's wake recordings aren't here")
        audio = load(OWNER[2])
        hit = self.detector.check(np.concatenate([audio, np.zeros(16000, dtype=np.int16)]))
        self.assertGreater(hit.score, config.WAKEWORD_THRESHOLD)
        self.assertLessEqual(hit.end_sample, len(audio) + 8000)

    def test_missing_files_mean_not_available(self):
        missing = WakeWordDetector("/nonexistent/x.onnx", "/nonexistent/m.onnx", "/nonexistent/e.onnx", 0.7)
        self.assertFalse(missing.available())



class NoiseTests(unittest.TestCase):
    def test_a_noise_with_no_words_never_wakes_bmo(self):
        # Live 2026-10-20: the public detector scored a sound 0.83 while Whisper heard nothing.
        from standby_wake import StandbyWakeListener
        self.assertFalse(StandbyWakeListener._detector_heard(object(), ""))
        self.assertFalse(StandbyWakeListener._detector_heard(object(), "   "))


if __name__ == "__main__":
    unittest.main()
