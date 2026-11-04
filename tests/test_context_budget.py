"""Long requests keep their end, the answer keeps its room, and tool rounds stay inside the context (2026-10-31: a 12,065-character request kept only its first 4,729 characters and lost its final constraint;
ten tool rounds could each add 10,000 characters unchecked)."""
import json
import os
from pathlib import Path
import sys
import types
import unittest

ROOT = Path(os.environ.get("BEMO_TEST_CODE_ROOT", Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(ROOT))
import config


def chat(history=()):
    from systems.chat import Chat
    c = Chat.__new__(Chat)
    c.active_profile_name = config.MAIN_MODEL_PROFILE
    c.chat_history = list(history)
    c.conversation_summary = ""
    c._effective_context = lambda: 8192
    c._older_memory_context = lambda q: ""
    c._selected_image_paths = []
    c.project_manager = types.SimpleNamespace(active_slug=None)
    c.task_ledger = types.SimpleNamespace(prompt_context=lambda **kw: "")
    return c


def size(messages):
    return sum(len(str(m.get("content", ""))) + len(json.dumps(m.get("tool_calls") or [])) for m in messages)


class RequestTests(unittest.TestCase):
    def long_request(self):
        middle = " ".join(f"Step {n}: measure the bracket and note the hole positions." for n in range(500))
        return "Here is my plan for the shelf. " + middle + " FINAL: make every hole 5 millimeters, not 4."

    def test_a_long_request_keeps_its_end_and_says_the_middle_was_left_out(self):
        request = self.long_request()
        self.assertGreater(len(request), 25000)  # more than fits (about 14,800 characters do now, 4,700 before)
        history = [{"role": "user", "content": "earlier question " * 50},
                   {"role": "assistant", "content": "earlier answer " * 50},
                   {"role": "user", "content": request[:config.RECENT_MESSAGE_CHAR_LIMIT]}]  # the RAM copy is cut
        messages = chat(history)._ollama_messages(request)
        current = messages[-1]["content"]
        self.assertEqual(messages[-1]["role"], "user")
        self.assertTrue(current.startswith("Here is my plan"))
        self.assertTrue(current.endswith("FINAL: make every hole 5 millimeters, not 4."))
        self.assertIn("left out to fit", current)
        room = int(8192 * config.PROMPT_CHAR_PER_TOKEN_BUDGET
                   - config.ANSWER_RESERVE_TOKENS * config.PROMPT_CHAR_PER_TOKEN_BUDGET)
        self.assertLessEqual(size(messages), room)  # and the answer keeps its room

    def test_a_request_that_fits_is_whole_even_past_the_ram_copy_limit(self):
        request = "Please check this list. " + "item, " * 900 + "and END HERE."  # ~5,500 characters
        history = [{"role": "user", "content": request[:config.RECENT_MESSAGE_CHAR_LIMIT]}]
        messages = chat(history)._ollama_messages(request)
        self.assertEqual(messages[-1]["content"], request)

    def test_a_short_voice_request_is_untouched_and_history_stays(self):
        history = [{"role": "user", "content": "what's a good name for a cat"},
                   {"role": "assistant", "content": "How about Biscuit?"},
                   {"role": "user", "content": "and a dog"}]
        messages = chat(history)._ollama_messages("and a dog")
        self.assertEqual(messages[-1]["content"], "and a dog")
        self.assertTrue(any(m.get("content") == "How about Biscuit?" for m in messages))


class ToolRoundTests(unittest.TestCase):
    def test_ten_rounds_of_big_results_stay_inside_the_context(self):
        c = chat()
        messages = [{"role": "system", "content": "policy " * 300}, {"role": "user", "content": "fix the project"}]
        for n in range(10):
            messages.append({"role": "assistant", "content": "",
                             "tool_calls": [{"function": {"name": "write_project_file",
                                                          "arguments": {"path": f"f{n}.py", "content": "x = 1\n" * 900}}}]})
            messages.append({"role": "tool", "content": f"result {n} " + "line\n" * 2000, "tool_name": "write_project_file"})
            c._fit_tool_rounds(messages, tools=[])
        room = int(8192 * config.PROMPT_CHAR_PER_TOKEN_BUDGET
                   - config.ANSWER_RESERVE_TOKENS * config.PROMPT_CHAR_PER_TOKEN_BUDGET)
        self.assertLessEqual(size(messages), room)
        self.assertEqual(messages[1], {"role": "user", "content": "fix the project"})   # the request untouched
        self.assertTrue(messages[-1]["content"].startswith("result 9 line"))            # the newest result whole
        self.assertNotIn("shortened to fit", messages[-1]["content"])
        roles = [m["role"] for m in messages[2:]]
        self.assertEqual(roles, ["assistant", "tool"] * 10)                             # calls and results in pairs

    def test_nothing_changes_while_it_fits(self):
        messages = [{"role": "user", "content": "hi"}, {"role": "tool", "content": "small", "tool_name": "x"}]
        self.assertEqual(chat()._fit_tool_rounds(messages, tools=[]), 0)
        self.assertEqual(messages[1]["content"], "small")

    def test_wired_in(self):
        src = (ROOT / "systems" / "chat.py").read_text()
        self.assertLess(src.index("self._fit_tool_rounds(messages, tools)"),
                        src.index("assistant_message, content, tool_calls = self._stream_chat_round("))


if __name__ == "__main__":
    unittest.main()
