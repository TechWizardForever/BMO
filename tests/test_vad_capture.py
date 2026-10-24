"""Voice-activity endpointing: real speech and fan-like noise, no microphone.

Noise is synthetic. Speech comes from captures Bemo recorded of the owner on
this PC (kept in ~/.bemo, not in the repo); those tests skip elsewhere. The
bundled BMO voice sample is cartoon-pitched and Silero does not score it as
speech (max 0.41), so it cannot be used here.
"""
import os
from pathlib import Path
import sys
import types
import unittest
import wave

import numpy as np
import speech_recognition as sr

ROOT = Path(os.environ.get("BEMO_TEST_CODE_ROOT", Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(ROOT))
from faster_whisper.vad import get_vad_model
from systems.vad_capture import VoiceEndpointer

MIC_RATE = 44100
CHUNK = 1024
# Measured in Bemo's own captures on 2026-10-18: room noise RMS ~240-350,
# just under the old 420 energy cap that let recordings run 10-30 s.
ROOM_RMS = 350.0
LOUD_FAN_RMS = 1600.0
# (file, first voiced second, last voiced second), from Silero on the full file.
LOCAL_SPEECH = [
    ("20261018-123853-repetition-loop.wav", 1.02, 5.41),
    ("20261018-132236-repetition-loop.wav", 0.03, 5.15),
]
REAL_SPEECH_DIR = Path.home() / ".bemo" / "runtime" / "stt-rejects"


def _local_speech(name):
    path = REAL_SPEECH_DIR / name
    if not path.is_file():
        return None
    with wave.open(str(path)) as w:
        raw, rate = w.readframes(w.getnframes()), w.getframerate()
    pcm = sr.AudioData(raw, rate, 2).get_raw_data(convert_rate=MIC_RATE, convert_width=2)
    return np.frombuffer(pcm, dtype=np.int16).astype(np.float32)


def _room_noise(seconds, rms=ROOM_RMS, seed=7):
    """Fan-like noise: low-passed hum + broadband hiss + mains tone."""
    rng = np.random.default_rng(seed)
    n = int(MIC_RATE * seconds)
    hiss = rng.normal(0, 1, n)
    hum = np.convolve(rng.normal(0, 1, n), np.ones(40) / 40, mode="same")
    t = np.arange(n) / MIC_RATE
    noise = hiss * 0.4 + hum * 4 + np.sin(2 * np.pi * 120 * t)
    return noise / np.sqrt(np.mean(noise ** 2)) * rms


class FakeMic:
    SAMPLE_RATE = MIC_RATE
    SAMPLE_WIDTH = 2
    CHUNK = CHUNK

    def __init__(self, samples):
        data = np.clip(samples, -32768, 32767).astype("<i2").tobytes()
        pos = [0]

        def read(frames):
            start = pos[0]
            pos[0] += frames * 2
            return data[start:pos[0]]

        self.stream = types.SimpleNamespace(read=read)


class VoiceEndpointerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.vad = get_vad_model()

    def endpointer(self, **kw):
        # The thresholds BMO actually runs with.
        import config
        kw.setdefault("start_timeout_seconds", 30.0)
        return VoiceEndpointer(self.vad, end_silence_seconds=1.1, start_threshold=config.STT_VAD_START_THRESHOLD,
                               end_threshold=config.STT_VAD_END_THRESHOLD,
                               strong_start_threshold=config.STT_VAD_STRONG_START_THRESHOLD, **kw)

    def test_real_phrases_end_about_a_second_after_the_voice(self):
        found = 0
        for name, first, last in LOCAL_SPEECH:
            speech = _local_speech(name)
            if speech is None:
                continue
            found += 1
            ep = self.endpointer()
            audio = ep.capture(FakeMic(np.concatenate([speech, _room_noise(10.0)])))
            self.assertIsNotNone(audio, name)
            self.assertEqual(ep.last_stats["end_reason"], "voice ended", name)
            seconds = len(audio.frame_data) / 2 / MIC_RATE
            # Ends ~1.1 s after the recording's last (possibly faint) voice; the
            # old energy endpointer ran on to 10-30 s in this room.
            recorded = len(speech) / MIC_RATE
            self.assertLess(seconds, recorded + 1.1 + 0.4, (name, ep.last_stats))
            self.assertGreater(seconds, last - first, (name, ep.last_stats))
        if not found:
            self.skipTest("no local speech recordings on this machine")

    def test_noise_alone_never_starts_a_phrase(self):
        # As BMO listens: back-to-back 1 s idle checks on one continuous stream.
        for rms, seed in ((ROOM_RMS, 7), (LOUD_FAN_RMS, 3)):
            ep = self.endpointer(start_timeout_seconds=1.0)
            mic = FakeMic(_room_noise(12.0, rms=rms, seed=seed))
            peaks = []
            for _ in range(9):
                self.assertIsNone(ep.capture(mic), rms)
                peaks.append(ep.last_stats["peak_voice_prob"])
            self.assertLess(max(peaks), 0.2, (rms, peaks))  # well under the 0.30 start

    def test_the_start_of_the_voice_is_kept(self):
        name, first, last = LOCAL_SPEECH[1]
        speech = _local_speech(name)
        if speech is None:
            self.skipTest("no local speech recordings on this machine")
        audio = self.endpointer().capture(FakeMic(np.concatenate([_room_noise(0.8), speech, _room_noise(8.0, seed=9)])))
        self.assertIsNotNone(audio)
        # Voice begins 0.83 s in; the capture must hold all of it plus pre-roll.
        self.assertGreaterEqual(len(audio.frame_data) / 2 / MIC_RATE, (last - first) + 0.3)

    def test_noise_only_captures_from_the_live_test_never_reach_whisper(self):
        # Whisper turned these noise-only captures into "Hey Bemo" loops.
        found = 0
        for name in ("20261018-124604-repetition-loop.wav", "20261018-132256-repetition-loop.wav",
                     "20261018-132619-repetition-loop.wav"):
            noise = _local_speech(name)
            if noise is None:
                continue
            found += 1
            ep = self.endpointer(start_timeout_seconds=1.0)
            self.assertIsNone(ep.capture(FakeMic(noise)), name)
        if not found:
            self.skipTest("no local recordings on this machine")


class SpeakPausesMicTests(unittest.TestCase):
    def test_speak_pauses_an_open_microphone_first(self):
        import ast
        src = (ROOT / "systems" / "chat.py").read_text()
        chat = next(n for n in ast.parse(src).body if isinstance(n, ast.ClassDef) and n.name == "Chat")
        methods = [n for n in chat.body if isinstance(n, ast.FunctionDef)
                   and n.name in {"speak", "_suspend_microphone_capture", "_microphone_stream"}]
        order = []

        class Pipeline:
            def __init__(self, chat):
                pass

            def enqueue(self, text):
                order.append(("speak", raw.active))
                return True

            def finish(self):
                pass

        ns = {"_StreamingSpeechPipeline": Pipeline,
              "logger": types.SimpleNamespace(debug=lambda *a, **k: None, exception=lambda *a, **k: None)}
        exec(compile(ast.fix_missing_locations(ast.Module(body=[ast.ClassDef(
            name="Mini", bases=[], keywords=[], body=methods, decorator_list=[])], type_ignores=[])), "mini", "exec"), ns)
        raw = types.SimpleNamespace(active=True)
        raw.stop_stream = lambda: setattr(raw, "active", False)
        obj = ns["Mini"]()
        obj._active_microphone = types.SimpleNamespace(stream=types.SimpleNamespace(pyaudio_stream=raw))
        obj._mic_capture_suspended = False
        obj.stop_event = types.SimpleNamespace(is_set=lambda: False)
        obj.interface_settings = types.SimpleNamespace(tts_enabled=True)
        obj._set_live_assistant_text = lambda text: None
        obj.speak("Okay, I will stay on this model.")
        self.assertEqual(order, [("speak", False)])


if __name__ == "__main__":
    unittest.main()
