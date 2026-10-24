"""Starting BMO from standby (decided 2026-10-19), replayed on the day's real transcripts."""
import os
from pathlib import Path
import sys
import unittest

ROOT = Path(os.environ.get("BEMO_TEST_CODE_ROOT", Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
import config
from standby_wake import StandbyWakeListener, is_standby_wake_phrase


def replay(lines):
    """(seconds, transcript) -> the second at which BMO would start, or None."""
    listener = StandbyWakeListener(should_listen=lambda: False, on_wake=lambda: None)
    for at, text in lines:
        if listener._wake_decision(text, float(at)):
            return at
    return None


class StandbyWakeTests(unittest.TestCase):
    def test_hey_and_filler_forms(self):
        for said in ("Hey Bemo", "Hey BMO.", "Hey, P-mo.", "A Bemo", "8 Bemo", "Uh, BMO"):
            self.assertTrue(is_standby_wake_phrase(said), said)
        for said in ("Bemo", "They Bemo", "Hey Siri", "A BMO toy is on the shelf and I like it a lot"):
            self.assertFalse(is_standby_wake_phrase(said), said)

    def test_owner_attempts_at_1308(self):
        # Standby log 13:08:29-13:13:08 (someone trying to wake him during a phone call).
        lines = [(0, "Bemo"), (2.4, "Un..."), (51, "Bemo"), (54, "Bemo"), (218, "Bemo"),
                 (265, "8 Bemo"), (273, "A Bemo"), (279, "Hey Bemo")]
        self.assertEqual(replay(lines), 265)  # "8 Bemo"; bare names never count

    def test_owner_attempts_at_1321(self):
        # Standby log 13:21:52-13:22:49 (after v1.28.49): woke only on the spelled "B M O".
        lines = [(0, "Bemo"), (2, "No, I need that."), (5, "What do we got?"), (9, "Bemo"),
                 (35, "Bemo Bemo"), (52, "Bemo"), (57, "BMO")]
        self.assertIsNone(replay(lines))  # bare names (even "Bemo Bemo") never count

    def test_room_speech_does_not_start_him(self):
        # 10:59 a single "Bemo" started BMO unasked; and the phone call around 13:08-13:12.
        lines = [(0, "Bemo"), (20, "Alright, love you."), (23, "Let's go to the tank."),
                 (28, "Okay, what just to get cheese and eggs?"), (31, "H, help."), (40, "Hello?"),
                 (45, "So, this newer album..."), (52, "Yes"), (60, "Love you!")]
        self.assertIsNone(replay(lines))

    def test_background_bemo_bemo_does_not_start_him(self):
        # Standby log 13:30:37/13:30:41 and 13:33:20/13:33:24: background sound heard as "Bemo" twice,
        # 4 s apart, started BMO (the removed "name twice" rule).
        self.assertIsNone(replay([(0, "I'm gonna go to the side."), (4, "Bemo"), (7.5, "Bemo")]))
        self.assertIsNone(replay([(0, "Thank you for coming."), (4.6, "Bemo"), (8.3, "Bemo")]))

    def test_coding_mishearings_switch(self):
        aliases = config.MODEL_PROFILES["coding"]["aliases"]
        for heard in ("codeine", "code aim", "cody"):
            self.assertIn(heard, aliases)



class OwnerRecordingTests(unittest.TestCase):
    """Transcripts (text only) of the owner's 10 recorded "Hey BMO"s, 2026-10-19, per model."""

    def setUp(self):
        import json
        self.heard = json.loads((ROOT / "tests" / "fixtures" / "wake" / "owner-hey-bmo-2026-10-19.json").read_text())

    def test_chosen_model_wakes_on_nine_of_ten(self):
        self.assertEqual(config.STANDBY_WAKE_STT_MODEL, "base.en")
        self.assertEqual(config.STANDBY_WAKE_HOTWORDS, "Bemo")
        woke = [t for t in self.heard["base.en hint=Bemo"] if is_standby_wake_phrase(t)]
        self.assertEqual(len(woke), 9, self.heard["base.en hint=Bemo"])  # the miss was an empty clip

    def test_room_sound_heard_as_bemo_does_not_wake(self):
        for text in self.heard["room_heard_as_bemo_tiny_hint"]:
            self.assertFalse(is_standby_wake_phrase(text), text)


class StandbyNoiseTests(unittest.TestCase):
    """Real tiny.en through the listener's own transcription: background noise must not start BMO.

    2026-10-19: with hotwords="Bemo", 4 of 6 noise bursts came back as "Bemo", and background sound
    started BMO three times via the "name twice" rule.
    """

    @classmethod
    def setUpClass(cls):
        try:
            from faster_whisper import WhisperModel
            cls.model = WhisperModel(config.STANDBY_WAKE_STT_MODEL, device="cpu",
                                     compute_type=config.STANDBY_WAKE_STT_COMPUTE_TYPE, cpu_threads=2,
                                     download_root=None, local_files_only=True)
        except Exception as exc:  # not downloaded on this machine
            raise unittest.SkipTest(f"tiny.en not available offline: {exc}")

    def audio(self, samples):
        import numpy as np
        import speech_recognition as sr
        pad = np.zeros(4800, dtype=np.float32)
        x = np.clip(np.concatenate([pad, samples, pad]), -1, 1)
        return sr.AudioData((x * 32767).astype("<i2").tobytes(), 16000, 2)

    def test_noise_never_starts_him(self):
        import numpy as np
        rng = np.random.default_rng(7)
        listener = StandbyWakeListener(should_listen=lambda: False, on_wake=lambda: None)
        heard = []
        for i in range(12):
            n = int(16000 * rng.uniform(0.6, 1.6))
            x = rng.normal(0, rng.uniform(0.02, 0.15), n).astype(np.float32)
            if i % 2:
                x *= np.hanning(n).astype(np.float32) * 3
            text = listener._transcribe(self.model, self.audio(x))
            heard.append(text)
            self.assertFalse(listener._wake_decision(text, float(i * 3)), heard)
        self.assertFalse(any("bemo" in t.lower() for t in heard), heard)


if __name__ == "__main__":
    unittest.main()
