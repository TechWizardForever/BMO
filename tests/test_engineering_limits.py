"""Talking to Engineering (2026-11-15: no limits). Live 2026-11-10 it reasoned for 8 minutes, used up its 4,096 and said
nothing; its 4,800-character history dropped details given a few turns before; and the mouth stopped moving partway
through a long answer."""
import os
from pathlib import Path
import sys
import types
import unittest
from unittest import mock

os.environ["SDL_VIDEODRIVER"] = "dummy"
os.environ["SDL_AUDIODRIVER"] = "dummy"
ROOT = Path(os.environ.get("BEMO_TEST_CODE_ROOT", Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(ROOT))
import config
import supervisor
from systems.chat import Chat, State
from systems.render import FaceRenderer


def chat_on(profile):
    chat = Chat.__new__(Chat)
    chat.active_profile_name = profile

    return chat


class TheLimits(unittest.TestCase):
    def test_engineering_has_room(self):
        options = chat_on("engineering")._ollama_options()
        self.assertEqual(options["num_ctx"], 16384)
        self.assertGreaterEqual(options["num_predict"], 12288)
        self.assertGreater(options["num_ctx"], options["num_predict"])  # the answer still fits beside the question

    def test_a_chatty_question_to_engineering_isnt_cut_short(self):
        chat = chat_on("engineering")
        chat._specialist_chat_turn = True
        self.assertGreaterEqual(chat._ollama_options()["num_predict"], 12288)

    def test_the_other_specialists_keep_theirs(self):
        chat = chat_on("coding")
        chat._specialist_chat_turn = True
        self.assertEqual(chat._ollama_options()["num_predict"], config.SPECIALIST_CHAT_NUM_PREDICT)
        self.assertEqual(chat_on("normal")._ollama_options()["num_ctx"], config.OLLAMA_NUM_CTX)

    def test_engineering_remembers_more_of_the_conversation(self):
        self.assertGreaterEqual(Chat.history_char_budget(config.MODEL_PROFILES["engineering"]), 36000)
        self.assertEqual(Chat.history_char_budget(config.MODEL_PROFILES["normal"]), config.HISTORY_CONTEXT_CHAR_BUDGET)

    def test_the_supervisor_loads_it_at_that_size(self):
        self.assertEqual(supervisor._context_for(config.MODEL_PROFILES["engineering"]["model"]), 16384)
        self.assertEqual(supervisor._context_for(config.MODEL_PROFILES["normal"]["model"]), int(config.OLLAMA_NUM_CTX))
        self.assertEqual(supervisor._context_for("something-else:1b"), int(config.OLLAMA_NUM_CTX))

    def test_it_fits_in_memory(self):
        # Qwen3-30B-A3B: 48 layers x 4 KV heads x 128 x 2 (K and V) x 2 bytes per token.
        kv_gb = 16384 * 48 * 4 * 128 * 2 * 2 / 1024 ** 3
        self.assertLess(kv_gb, 2.0)


class StillThinking(unittest.TestCase):
    def chat(self):
        chat = chat_on("engineering")
        chat.said = []
        chat.speak = chat.said.append
        chat.stop_event = mock.Mock(is_set=lambda: False)
        chat.interface_settings = types.SimpleNamespace(tts_enabled=True)
        chat._thinking_notices_said = 0
        chat.state = State.THINKING
        return chat

    def test_a_long_quiet_think_says_so_now_and_then(self):
        chat = self.chat()
        for waited in (1, 10, 19.9):
            chat._say_still_thinking(waited)
        self.assertEqual(chat.said, [])
        chat._say_still_thinking(20.5)
        chat._say_still_thinking(60)
        self.assertEqual(chat.said, [config.THINKING_NOTICES[0]])
        chat._say_still_thinking(171)
        chat._say_still_thinking(321)
        self.assertEqual(chat.said, [config.THINKING_NOTICES[0], "Still thinking.", "Still thinking."])
        self.assertIs(chat.state, State.THINKING)  # the brain symbol stays up between them

    def test_quiet_when_his_voice_is_off(self):
        chat = self.chat()
        chat.interface_settings.tts_enabled = False
        chat._say_still_thinking(500)
        self.assertEqual(chat.said, [])

    def test_only_before_the_answer_starts(self):
        src = (ROOT / "systems" / "chat.py").read_text()
        self.assertIn("if thinking_piece and not content_parts:\n                    self._say_still_thinking(", src)
        self.assertIn("self._thinking_notices_said = 0", src[src.index("def _run_model_agent"):])


class TheMouth(unittest.TestCase):
    def renderer(self):
        renderer = FaceRenderer.__new__(FaceRenderer)
        renderer.mouth_frame_change_timer = 0.0
        renderer.current_mouth_frame = 1
        renderer._choose_mouth_frame = lambda xf, yf, rms: 3 if rms > 0 else 1
        renderer.visualizer = FaceRenderer.visualizer.__get__(renderer)
        chat = types.SimpleNamespace(count=0.0, xf_list=[], yf_list=[], rms_list=[])
        renderer._app = types.SimpleNamespace(chat=chat)
        return renderer, chat

    def frames(self, renderer, seconds, moved=None):
        """`seconds` of drawing at the screen's rate, on a clock of its own."""
        from systems import render
        clock = getattr(self, "_clock", 1000.0)
        fake = types.SimpleNamespace(monotonic=lambda: self._clock, time=lambda: self._clock)
        with mock.patch.object(render, "time", fake):
            for _ in range(int(config.FPS * seconds)):
                self._clock = clock
                renderer.mouth_frame_change_timer = 0.0
                renderer._advance_mouth()
                if moved is not None:
                    moved.append(renderer.current_mouth_frame)
                clock += 1.0 / config.FPS
        self._clock = clock

    def sound(self, chat, seconds):
        frames = int(seconds * config.VALUES_PER_SECOND)
        chat.xf_list += [[1.0]] * frames
        chat.yf_list += [[1.0]] * frames
        chat.rms_list += [0.5] * frames

    def test_a_gap_doesnt_leave_the_clock_ahead_of_the_sound(self):
        renderer, chat = self.renderer()
        self.sound(chat, 1.0)
        self.frames(renderer, 9)  # nine seconds of drawing, one second of sound: a brain falling behind
        self.assertLessEqual(chat.count, len(chat.xf_list))
        self.assertEqual(renderer.current_mouth_frame, 1)  # resting while it waits
        self.sound(chat, 2.0)
        moved = []
        self.frames(renderer, 1.5, moved)
        self.assertIn(3, moved)  # talking again with the new sound

    def test_a_whole_sentence_at_once_keeps_it_moving_to_the_end(self):
        # 2026-11-17: the face didn't move while he talked. A sentence's sound arrives all at once, before it
        # plays, and the mouth jumped to its end and sat shut.
        renderer, chat = self.renderer()
        self.sound(chat, 4.0)
        moved = []
        self.frames(renderer, 3.8, moved)
        self.assertEqual(moved.count(1), 0, moved)  # talking the whole way through
        self.assertLess(chat.count, len(chat.xf_list))

    def test_slow_frames_keep_pace_with_the_sound(self):
        renderer, chat = self.renderer()
        self.sound(chat, 5.0)
        from systems import render
        times = iter([100.0, 100.1, 100.2, 100.3])  # ten frames a second
        fake = types.SimpleNamespace(monotonic=lambda: next(times), time=lambda: 0.0)
        with mock.patch.object(render, "time", fake):
            for _ in range(4):
                renderer._advance_mouth()
        self.assertAlmostEqual(chat.count, config.VALUES_PER_SECOND * 0.3, places=3)


if __name__ == "__main__":
    unittest.main()
