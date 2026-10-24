"""Whisper loads from this computer first (starting without internet, 2026-10-19)."""
import os
from pathlib import Path
import sys
import types
import unittest
from unittest import mock

ROOT = Path(os.environ.get("BEMO_TEST_CODE_ROOT", Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(ROOT))
from systems.whisper_local import load_whisper


class LoadTests(unittest.TestCase):
    def test_local_first_then_download(self):
        calls = []

        def fake(name, **kw):
            calls.append(kw.get("local_files_only", False))
            if kw.get("local_files_only"):
                raise FileNotFoundError("not cached")
            return "model"
        with mock.patch.dict(sys.modules, {"faster_whisper": types.SimpleNamespace(WhisperModel=fake)}):
            self.assertEqual(load_whisper("base.en", device="cpu"), "model")
        self.assertEqual(calls, [True, False])

    def test_cached_model_never_goes_online(self):
        calls = []
        with mock.patch.dict(sys.modules, {"faster_whisper": types.SimpleNamespace(
                WhisperModel=lambda name, **kw: calls.append(kw.get("local_files_only")) or "model")}):
            load_whisper("base.en")
        self.assertEqual(calls, [True])

    def test_bmo_and_standby_use_it(self):
        self.assertIn("self.stt = load_whisper(", (ROOT / "systems" / "chat.py").read_text())
        self.assertIn("model = load_whisper(", (ROOT / "standby_wake.py").read_text())


if __name__ == "__main__":
    unittest.main()
