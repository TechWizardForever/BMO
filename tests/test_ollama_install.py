"""Ollama at the tested version, checked against its published SHA-256 (installer plan I1).

Runs installer/ollama.sh against a small fake download in a temporary folder: no network, no sudo,
nothing outside the folder is touched.
"""
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import unittest

ROOT = Path(os.environ.get("BEMO_TEST_CODE_ROOT", Path(__file__).resolve().parents[1]))
SCRIPT = ROOT / "installer" / "ollama.sh"


def fake_ollama(version):
    return f"#!/bin/sh\necho 'ollama version is {version}'\n"


@unittest.skipUnless(shutil.which("zstd"), "zstd is needed")
class OllamaInstallTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.prefix = self.dir / "prefix"
        build = self.dir / "build"
        (build / "bin").mkdir(parents=True)
        (build / "lib" / "ollama").mkdir(parents=True)
        (build / "bin" / "ollama").write_text(fake_ollama("0.40.5"))
        (build / "bin" / "ollama").chmod(0o755)
        (build / "lib" / "ollama" / "libggml-base.so").write_text("x")
        for gpu in ("cuda_v12", "cuda_v13", "rocm_v7_2", "vulkan"):
            (build / "lib" / "ollama" / gpu).mkdir()
            (build / "lib" / "ollama" / gpu / "libggml-gpu.so").write_text("x")
        tar = self.dir / "ollama.tar"
        with tarfile.open(tar, "w") as archive:
            archive.add(build / "bin", "bin")
            archive.add(build / "lib", "lib")
        subprocess.run(["zstd", "-q", str(tar), "-o", str(self.dir / "ollama.tar.zst")], check=True)
        self.archive = self.dir / "ollama.tar.zst"
        self.sha = hashlib.sha256(self.archive.read_bytes()).hexdigest()

    def tearDown(self):
        shutil.rmtree(self.dir)

    def run_script(self, sha=None, **extra):
        env = dict(os.environ, BEMO_OLLAMA_URL=self.archive.as_uri(), BEMO_OLLAMA_SHA256=sha or self.sha,
                   BEMO_OLLAMA_PREFIX=str(self.prefix), BEMO_SUDO="", BEMO_OLLAMA_SKIP_SERVICE="1",
                   BEMO_INSTALL_CACHE=str(self.dir / "cache"), **extra)
        return subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True, text=True, timeout=60)

    def test_installs_the_checked_download_then_skips_next_time(self):
        done = self.run_script()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertTrue((self.prefix / "bin" / "ollama").is_file())
        self.assertTrue((self.prefix / "lib" / "ollama" / "libggml-base.so").is_file())
        self.assertIn("SHA-256 matches", done.stdout)
        self.assertEqual(list((self.dir / "cache").iterdir()), [])  # the download is removed afterwards
        again = self.run_script()
        self.assertIn("already installed", again.stdout)

    def test_the_graphics_card_libraries_are_left_out(self):
        # ~4.7 GB that BMO never uses: it runs its brains on the processor (installer plan I8).
        self.assertEqual(self.run_script().returncode, 0)
        self.assertEqual(sorted(p.name for p in (self.prefix / "lib" / "ollama").iterdir()),
                         ["libggml-base.so", "vulkan"])
        shutil.rmtree(self.prefix)
        self.run_script(BEMO_OLLAMA_KEEP_GPU="1")
        self.assertTrue((self.prefix / "lib" / "ollama" / "cuda_v12").is_dir())

    def test_a_download_that_does_not_match_is_refused(self):
        done = self.run_script(sha="0" * 64)
        self.assertEqual(done.returncode, 1)
        self.assertIn("does not match its published checksum", done.stderr)
        self.assertFalse(self.prefix.exists())
        self.assertEqual(list((self.dir / "cache").iterdir()), [])

    def test_another_installed_version_is_kept_unless_asked(self):
        (self.prefix / "bin").mkdir(parents=True)
        (self.prefix / "lib" / "ollama").mkdir(parents=True)
        (self.prefix / "lib" / "ollama" / "old.so").write_text("old")
        (self.prefix / "bin" / "ollama").write_text(fake_ollama("0.39.0"))
        (self.prefix / "bin" / "ollama").chmod(0o755)
        kept = self.run_script()
        self.assertEqual(kept.returncode, 0)
        self.assertIn("Keeping 0.39.0", kept.stdout)
        self.assertTrue((self.prefix / "lib" / "ollama" / "old.so").exists())
        replaced = self.run_script(BEMO_OLLAMA_REPLACE="1")
        self.assertEqual(replaced.returncode, 0, replaced.stderr)
        self.assertFalse((self.prefix / "lib" / "ollama" / "old.so").exists())
        self.assertIn("0.40.5", (self.prefix / "bin" / "ollama").read_text())


class PinTests(unittest.TestCase):
    def test_pinned_to_the_github_release_with_its_checksum(self):
        text = SCRIPT.read_text()
        self.assertIn('OLLAMA_VERSION="0.40.5"', text)
        self.assertIn("dc5eabc292f9949546c90a97c820f924b035e05243b0de13c7f366182562169c", text)
        self.assertIn("https://github.com/ollama/ollama/releases/download/v${OLLAMA_VERSION}/", text)
        self.assertFalse("ollama.com/install.sh" in (ROOT / "install.sh").read_text(), "install.sh still runs the online script")


if __name__ == "__main__":
    unittest.main()
