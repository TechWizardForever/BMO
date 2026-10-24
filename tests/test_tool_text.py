"""Tool calls written as text (Qwen3-Coder XML form) are decoded and run (live 2026-10-19 snake game)."""
import ast
import os
from pathlib import Path
import sys
import unittest

ROOT = Path(os.environ.get("BEMO_TEST_CODE_ROOT", Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(ROOT))
from systems.tool_text import recover_tool_calls

# What Coding actually answered on 2026-10-19 (content shortened).
LIVE = ("I'll create a complete Snake game in Python using Pygame. Let me implement this step by step.\n\n"
        "<function=computer_control>\n<parameter=action>\nwrite_text\n</parameter>\n<parameter=args>\n"
        '{"path": "/home/bemo/snake_game.py", "content": "# Snake Game in Python using Pygame\\nimport pygame\\n"}\n'
        "</parameter>\n</function>\n</tool_call>")


class RecoverTests(unittest.TestCase):
    def test_the_live_answer(self):
        calls, rest = recover_tool_calls(LIVE, {"computer_control", "find_files"})
        self.assertEqual(len(calls), 1)
        args = calls[0]["function"]["arguments"]
        self.assertEqual((calls[0]["function"]["name"], args["action"]), ("computer_control", "write_text"))
        self.assertEqual(args["args"]["path"], "/home/bemo/snake_game.py")
        self.assertTrue(args["args"]["content"].startswith("# Snake Game"))
        self.assertEqual(rest, "I'll create a complete Snake game in Python using Pygame. Let me implement this "
                               "step by step.")

    def test_only_offered_tools(self):
        self.assertEqual(recover_tool_calls(LIVE, {"find_files"}), ([], LIVE))
        self.assertEqual(recover_tool_calls("Plain answer, no tools.", {"computer_control"})[0], [])

    def test_plain_parameters(self):
        calls, _ = recover_tool_calls("<function=find_files>\n<parameter=query>\nsnake\n</parameter>\n</function>",
                                      {"find_files"})
        self.assertEqual(calls[0]["function"]["arguments"], {"query": "snake"})

    def test_the_agent_loop_uses_it(self):
        src = (ROOT / "systems" / "chat.py").read_text()
        chat = next(n for n in ast.parse(src).body if isinstance(n, ast.ClassDef) and n.name == "Chat")
        loop = next(n for n in chat.body if isinstance(n, ast.FunctionDef) and n.name == "_run_model_agent")
        self.assertIn("recover_tool_calls", ast.get_source_segment(src, loop))


if __name__ == "__main__":
    unittest.main()
