"""Chatterbox dropped (2026-10-19): Bemo speaks with Piper only."""
import ast
import os
from pathlib import Path
import re
import subprocess
import sys
import unittest

ROOT = Path(os.environ.get("BEMO_TEST_CODE_ROOT", Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(ROOT))
import config


class InstallTests(unittest.TestCase):
    def test_installer_has_no_chatterbox_or_pytorch(self):
        for name in ("install.sh", "requirements.txt", "constraints.txt", "prefetch_runtime_models.py"):
            text = (ROOT / name).read_text().lower()
            self.assertNotIn("chatterbox @", text, name)
            self.assertNotIn("resemble-ai", text, name)
            self.assertNotRegex(text, r"\btorch(audio)?==", name)
        self.assertIn("piper-tts==1.8.0", (ROOT / "installer" / "requirements.lock").read_text())

    def test_bemo_starts_without_chatterbox_or_pytorch(self):
        # A fresh install has neither; the modules BMO runs must import without them.
        code = ("import sys\n"
                "for name in ('chatterbox', 'torch', 'torchaudio'): sys.modules[name] = None\n"
                "import systems.chat, systems.piper_streaming, standby_wake, supervisor, hardware_autotune, "
                "prefetch_runtime_models\n"
                "from systems.piper_streaming import PCMChunk\n")
        done = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=120)
        self.assertEqual(done.returncode, 0, done.stderr[-2000:])


class VoiceTests(unittest.TestCase):
    def test_piper_is_the_only_voice(self):
        self.assertEqual(config.TTS_BACKEND, "piper")
        self.assertEqual(config.TTS_MODEL, str(config.PIPER_MODEL_PATH))
        from systems.voice import voice_profile_signature
        self.assertTrue(voice_profile_signature().startswith("piper:" + config.PIPER_MODEL_PATH.name))

    def handler(self):
        src = (ROOT / "systems" / "chat.py").read_text()
        chat = next(n for n in ast.parse(src).body if isinstance(n, ast.ClassDef) and n.name == "Chat")
        keep = [n for n in chat.body if isinstance(n, ast.FunctionDef)
                and n.name in ("_handle_voice_command", "_normalize_command_text")]
        ns = {"config": config, "re": re, "State": type("State", (), {"RESPONDING": 3, "THINKING": 2})}
        exec(compile(ast.fix_missing_locations(ast.Module(body=[ast.ClassDef(
            name="Mini", bases=[], keywords=[], body=keep, decorator_list=[])], type_ignores=[])), "m", "exec"), ns)
        mini = ns["Mini"]()
        mini.said = []
        mini.speak = mini.said.append
        mini._archive_local_exchange = lambda *a: None
        return mini

    def test_voice_import_explains_instead_of_going_to_main(self):
        for heard in ("import voice from USB", "Clone my voice from the USB", "make this my voice"):
            mini = self.handler()
            self.assertTrue(mini._handle_voice_command(heard), heard)
            self.assertIn("Piper voice", mini.said[0])
        self.assertFalse(self.handler()._handle_voice_command("import project from USB"))

    def test_voice_status_names_piper(self):
        mini = self.handler()
        self.assertTrue(mini._handle_voice_command("voice status"))
        self.assertIn("Piper", mini.said[0])


if __name__ == "__main__":
    unittest.main()
