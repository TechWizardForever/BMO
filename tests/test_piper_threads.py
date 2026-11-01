"""The voice has its own quiet threads: while BMO talks, ONNX's default spinning threads took the CPU from the brain
(2026-11-01 on the 5700U: 9.4-12 tok/s beside them, 14.5-15.8 beside 4 quiet ones)."""
import os
from pathlib import Path
import sys
import types
import unittest
from unittest import mock

ROOT = Path(os.environ.get("BEMO_TEST_CODE_ROOT", Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(ROOT))
import config


class VoiceThreadTests(unittest.TestCase):
    def fake_onnx(self, fail=False):
        calls = {}

        class Options:
            def add_session_config_entry(self, key, value):
                calls[key] = value

        def session(path, sess_options, providers):
            if fail:
                raise RuntimeError("no")
            calls["options"] = sess_options
            return "quiet"
        return types.SimpleNamespace(SessionOptions=Options, InferenceSession=session), calls

    def test_few_threads_that_do_not_spin(self):
        from systems.piper_streaming import quiet_session
        onnx, calls = self.fake_onnx()
        voice = types.SimpleNamespace(session="default")
        with mock.patch.dict(sys.modules, {"onnxruntime": onnx}):
            quiet_session(voice, "/x.onnx", 4)
        self.assertEqual(voice.session, "quiet")
        self.assertEqual(calls["options"].intra_op_num_threads, 4)
        self.assertEqual(calls["session.intra_op.allow_spinning"], "0")

    def test_a_failure_keeps_the_voice(self):
        from systems.piper_streaming import quiet_session
        onnx, _ = self.fake_onnx(fail=True)
        voice = types.SimpleNamespace(session="default")
        with mock.patch.dict(sys.modules, {"onnxruntime": onnx}):
            quiet_session(voice, "/x.onnx", 4)
        self.assertEqual(voice.session, "default")

    def test_half_the_cores_at_most_four(self):
        if not os.environ.get("BEMO_PIPER_THREADS"):
            self.assertEqual(config.PIPER_THREADS, max(1, min(4, config.HARDWARE.physical_cores // 2)))

    def test_the_engine_uses_it(self):
        src = (ROOT / "systems" / "piper_streaming.py").read_text()
        self.assertIn("quiet_session(self.voice, path, config.PIPER_THREADS)", src)


if __name__ == "__main__":
    unittest.main()
