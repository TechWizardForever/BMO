"""v1.28.10 regressions from the second 2026-10-18 live test (docs/live-tests)."""
import ast
import os
from pathlib import Path
import re
import socket
import sys
import threading
import time
import types
import unittest

ROOT = Path(os.environ.get("BEMO_TEST_CODE_ROOT", Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(ROOT))
import config
import ollama
from voice_commands import VoiceSessionGate
from systems.turn_speech import speakable_units

SRC = (ROOT / "systems" / "chat.py").read_text()
TREE = ast.parse(SRC)
CHAT = next(n for n in TREE.body if isinstance(n, ast.ClassDef) and n.name == "Chat")


def _stream(text):
    """Feed text one character at a time, as Ollama streams it."""
    tail, out = "", []
    for ch in text:
        tail += ch
        units, tail = speakable_units(tail)
        out += units
    units, tail = speakable_units(tail, final=True)
    return out + units, tail


class SpeechSplitTests(unittest.TestCase):
    def test_numbered_list_streams_cleanly(self):
        units, tail = _stream("I can help with:\n1. Web apps (Node.js, React)\n2. Scripts, etc.\n3. Games\n")
        self.assertEqual(units, ["I can help with:", "Web apps (Node.js, React)", "Scripts, etc.", "Games"])
        self.assertEqual(tail, "")

    def test_decimals_and_version_numbers_survive(self):
        units, tail = _stream("Use 2.5 inch boards. Python 3.12 is fine. Done")
        self.assertEqual(units, ["Use 2.5 inch boards.", "Python 3.12 is fine."])
        self.assertEqual(tail, "Done")

    def test_plain_sentences_unchanged(self):
        self.assertEqual(speakable_units("One. Two is unfinished", final=True), (["One."], "Two is unfinished"))


class CancelTests(unittest.TestCase):
    def test_cancel_unblocks_a_silent_stream(self):
        """Ollama sends nothing while it builds a tool call; cancel must still work."""
        abort = None
        for node in TREE.body:
            if isinstance(node, ast.FunctionDef) and node.name == "_abort_ollama_request":
                ns = {}
                exec(compile(ast.Module(body=[node], type_ignores=[]), "abort", "exec"), ns)
                abort = ns["_abort_ollama_request"]
        server = socket.socket()
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        port = server.getsockname()[1]

        def silent_ollama():
            conn, _ = server.accept()
            conn.recv(65536)
            conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: application/x-ndjson\r\nTransfer-Encoding: chunked\r\n\r\n")
            time.sleep(20)  # "building a tool call": no chunks
            conn.close()

        threading.Thread(target=silent_ollama, daemon=True).start()
        client = ollama.Client(host=f"http://127.0.0.1:{port}")
        finished = threading.Event()

        def consume():
            try:
                for _ in client.chat(model="x", messages=[{"role": "user", "content": "hi"}], stream=True):
                    pass
            except Exception:
                pass
            finished.set()

        threading.Thread(target=consume, daemon=True).start()
        time.sleep(0.5)
        self.assertGreaterEqual(abort(client), 1)
        self.assertTrue(finished.wait(3.0), "chat thread still blocked after cancel")
        server.close()

    def test_cancel_flag_is_cleared_after_every_turn_and_before_listening(self):
        respond = SRC[SRC.index("    def respond("):SRC.index("    def _pop_speech_chunks")]
        finally_block = respond[respond.rindex("        finally:"):]
        self.assertIn("self.cancel_turn_event.clear()", finally_block)
        listen = SRC[SRC.index("    def listen("):SRC.index("    def _recover_dropped_wake")]
        self.assertIn("self.cancel_turn_event.clear()", listen)

    def test_failures_and_cancels_are_spoken(self):
        respond = SRC[SRC.index("    def respond("):SRC.index("    def _pop_speech_chunks")]
        self.assertIn("Okay, I stopped.", respond)
        self.assertIn("something went wrong on my end", respond)


class WakeRecoveryTests(unittest.TestCase):
    def _harness(self, head_text):
        method = next(n for n in CHAT.body if isinstance(n, ast.FunctionDef) and n.name == "_recover_dropped_wake")
        quiet = types.SimpleNamespace(info=lambda *a, **k: None)
        ns = {"config": config, "logger": quiet}
        exec(compile(ast.fix_missing_locations(ast.Module(body=[ast.ClassDef(
            name="Mini", bases=[], keywords=[], body=[method], decorator_list=[])], type_ignores=[])), "mini", "exec"), ns)
        obj = ns["Mini"]()
        obj.voice_gate = VoiceSessionGate(wake_aliases=config.VOICE_WAKE_ALIASES)
        obj._transcribe_local = lambda audio, head_seconds=None: head_text
        return obj

    def _audio(self, seconds):
        return types.SimpleNamespace(get_raw_data=lambda **k: b"\0\0" * int(seconds * config.STT_SAMPLE_RATE))

    SHELF = "I'm thinking about building a small shelf for my workshop. What wood should I use?"

    def test_dropped_leading_wake_is_recovered(self):
        obj = self._harness("Hey BMO, I'm thinking")
        text = obj._recover_dropped_wake(self._audio(14.2), self.SHELF, started=0.0)
        self.assertEqual(obj.voice_gate.apply(text, now=1.0), self.SHELF)

    def test_background_speech_stays_ignored(self):
        obj = self._harness("so anyway I told him")
        text = obj._recover_dropped_wake(self._audio(14.2), "so anyway I told him the shelf was fine", started=0.0)
        self.assertIsNone(obj.voice_gate.apply(text, now=1.0))


class ConfigTests(unittest.TestCase):
    def test_whisper_hint_has_no_repeated_names(self):
        words = config.STT_HOTWORDS.lower().split()
        self.assertEqual(len(words), len(set(words)))

    def test_chat_instruction_rides_on_the_question(self):
        agent = SRC[SRC.index("    def _run_model_agent"):SRC.index("    def _run_project_agent")]
        self.assertIn('messages[-1]["content"] = f"{messages[-1][\'content\']}', agent)
        self.assertLessEqual(config.SPECIALIST_CHAT_MAX_WORDS, 40)


if __name__ == "__main__":
    unittest.main()
