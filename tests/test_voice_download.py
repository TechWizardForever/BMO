"""BMO's voice from the repository's release, checked against its SHA-256 (decided 2026-10-20).
A small fake download in a temporary folder: no network."""
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(os.environ.get("BEMO_TEST_CODE_ROOT", Path(__file__).resolve().parents[1]))
SCRIPT = ROOT / "installer" / "voice.sh"
NAME = "en_US-bemo-best-v3.onnx"


class VoiceDownloadTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.release = self.dir / "release"
        self.release.mkdir()
        (self.release / NAME).write_bytes(b"onnx" * 1000)
        (self.release / f"{NAME}.json").write_text('{"audio": {"sample_rate": 22050}}')
        self.data = self.dir / "data"

    def tearDown(self):
        shutil.rmtree(self.dir)

    def sha(self, name):
        return hashlib.sha256((self.release / name).read_bytes()).hexdigest()

    def run_script(self, *, onnx_sha=None):
        env = dict(os.environ, BEMO_VOICE_BASE_URL=self.release.as_uri(), BEMO_DATA_DIR=str(self.data),
                   BEMO_VOICE_SHA256_ONNX=onnx_sha or self.sha(NAME), BEMO_VOICE_SHA256_JSON=self.sha(f"{NAME}.json"))
        return subprocess.run(["bash", str(SCRIPT), sys.executable], env=env, capture_output=True, text=True,
                              timeout=120)

    def test_downloads_checks_and_installs_then_skips(self):
        done = self.run_script()
        self.assertEqual(done.returncode, 0, done.stderr)
        installed = self.data / "voice" / "piper" / NAME
        self.assertEqual(installed.read_bytes(), (self.release / NAME).read_bytes())
        self.assertTrue(Path(str(installed) + ".json").is_file())
        self.assertEqual(list((self.data / "install-cache" / "voice").iterdir()), [])
        self.assertIn("already installed", self.run_script().stdout)

    def test_a_file_that_does_not_match_is_refused(self):
        done = self.run_script(onnx_sha="0" * 64)
        self.assertEqual(done.returncode, 1)
        self.assertIn("does not match its published checksum", done.stderr)
        self.assertFalse((self.data / "voice" / "piper" / NAME).exists())

    def test_pinned_to_the_release(self):
        text = SCRIPT.read_text()
        self.assertIn("https://github.com/TechWizardForever/BMO/releases/download/voice-1", text)
        self.assertIn("897c58bff4fdd726cd1d465bbc22caa3acca9e82b7b666bedb9d38bd2da5921f", text)
        self.assertIn('bash "$SCRIPT_DIR/installer/voice.sh" python', (ROOT / "install.sh").read_text())


if __name__ == "__main__":
    unittest.main()
