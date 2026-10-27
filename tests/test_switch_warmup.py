"""A brain swap stops the old brain's background warm-up (live 2026-10-27: going back to Main waited behind
Coding's 51 s warm-up). A local server stands in for Ollama: it takes the request and never answers."""
import json
import os
from pathlib import Path
import socket
import sys
import threading
import time
import unittest
from unittest import mock

ROOT = Path(os.environ.get("BEMO_TEST_CODE_ROOT", Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(ROOT))


class SlowOllama:
    """Accepts one connection, reads the request, then answers only if told to."""
    def __init__(self, answer=None):
        self.server = socket.socket()
        self.server.bind(("127.0.0.1", 0))
        self.server.listen(1)
        self.port = self.server.getsockname()[1]
        self.answer, self.request, self.body, self.closed = answer, b"", b"", threading.Event()
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        conn, _ = self.server.accept()
        conn.settimeout(10)
        try:
            while b"\r\n\r\n" not in self.request:
                self.request += conn.recv(65536)
            head, _, body = self.request.partition(b"\r\n\r\n")
            length = int(next((line.split(b":")[1] for line in head.split(b"\r\n")
                               if line.lower().startswith(b"content-length:")), b"0"))
            while len(body) < length:
                body += conn.recv(65536)
            self.request, self.body = head, body
            if self.answer is not None:
                body = json.dumps(self.answer).encode()
                conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: %d\r\n\r\n%s"
                             % (len(body), body))
            while conn.recv(65536):  # until BMO's side goes away
                pass
        except OSError:
            pass
        finally:
            self.closed.set()
            conn.close()


def bare_chat(model="qwen3-coder:30b"):
    from systems.chat import Chat
    chat = Chat.__new__(Chat)
    chat.chat_history = []
    chat.active_profile_name = "coding"
    chat._tool_schemas_for_active_profile = lambda text: []
    chat._ollama_messages = lambda text, tool_schema_chars=0: [{"role": "user", "content": text}]
    chat._ollama_options = lambda: {"num_ctx": 8192}
    chat._keep_alive = lambda: "10m"
    return chat


def wait_for(check, seconds=5.0):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if check():
            return True
        time.sleep(0.02)
    return False


class SwapStopsWarmupTests(unittest.TestCase):
    def test_a_swap_to_another_brain_stops_the_warmup_at_once(self):
        ollama = SlowOllama()
        chat = bare_chat()
        with mock.patch.dict(os.environ, {"OLLAMA_HOST": f"127.0.0.1:{ollama.port}"}):
            chat._warm_runtime_prompt(background=True)
            warmup = chat._background_warmup
            self.assertTrue(wait_for(lambda: getattr(warmup["conn"], "sock", None) is not None and ollama.body))
        self.assertIn(b"POST /api/chat", ollama.request)
        self.assertTrue(chat._stop_background_warmup(keep_model="qwen3-main:30b"))
        self.assertTrue(ollama.closed.wait(2), "Ollama must see the connection go, so it drops the work")
        self.assertIsNone(chat._background_warmup)

    def test_the_same_brain_keeps_its_warmup_and_nothing_running_is_fine(self):
        chat = bare_chat()
        chat._background_warmup = {"model": "qwen3-coder:30b", "conn": None, "stopped": False}
        self.assertFalse(chat._stop_background_warmup(keep_model="qwen3-coder:30b"))
        self.assertIsNotNone(chat._background_warmup)
        chat._background_warmup = None
        self.assertFalse(chat._stop_background_warmup(keep_model="qwen3-main:30b"))

    def test_a_finished_warmup_clears_itself(self):
        ollama = SlowOllama(answer={"prompt_eval_duration": 2e9, "prompt_eval_count": 700, "done": True})
        chat = bare_chat()
        with mock.patch.dict(os.environ, {"OLLAMA_HOST": f"127.0.0.1:{ollama.port}"}):
            chat._warm_runtime_prompt(background=True)
            self.assertTrue(wait_for(lambda: chat._background_warmup is None))
        body = json.loads(ollama.body)
        self.assertEqual((body["model"], body["stream"], body["options"]["num_predict"]), ("qwen3-coder:30b", False, 2))

    def test_switching_stops_it_before_the_supervisor_loads_the_new_brain(self):
        src = (ROOT / "systems" / "chat.py").read_text()
        switch = src[src.index("    def _switch_profile("):]
        self.assertLess(switch.index("self._stop_background_warmup(keep_model=target_model)"),
                        switch.index("supervised = self._supervisor_switch_model("))


if __name__ == "__main__":
    unittest.main()
