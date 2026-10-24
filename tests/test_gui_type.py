"""'Type <text> in <app>' as one quick step that adds text, never replaces it (live 2026-10-19)."""
import ast
import os
from pathlib import Path
import re
import sys
import types
import unittest

ROOT = Path(os.environ.get("BEMO_TEST_CODE_ROOT", Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(ROOT))
import config
from systems.gui_control import classify, CHANGE, parse_gui_command, verify


class ParseTests(unittest.TestCase):
    def test_type_forms(self):
        self.assertEqual(parse_gui_command("in the text editor, type, I love pancakes.")["kind"], "type")
        self.assertEqual(parse_gui_command("type hello there in the text editor")["control"], "hello there")
        self.assertEqual(parse_gui_command("in the text editor, type I am in love")["control"], "i am in love")
        self.assertIsNone(parse_gui_command("type hello"))  # no app named: not this command
        self.assertIsNone(parse_gui_command("in the text editor, type BMO was here, then close it without saving."))
        self.assertEqual(parse_gui_command("press 7 in calculator")["kind"], "press")

    def test_adding_text_is_an_ordinary_change_and_is_verified(self):
        self.assertEqual(classify({"role": "text", "name": ""}, "append_text"), CHANGE)
        self.assertEqual(verify("append_text", {}, {"appended": True})[0], True)
        self.assertEqual(verify("append_text", {}, {"appended": False})[0], False)
        helper = (ROOT / "systems" / "adapters" / "a11y_helper.py").read_text()
        self.assertIn("node.queryEditableText().insertText(end, str(value), len(str(value)))", helper)


class TypeCommandTests(unittest.TestCase):
    def mini(self):
        src = (ROOT / "systems" / "chat.py").read_text()
        chat = next(n for n in ast.parse(src).body if isinstance(n, ast.ClassDef) and n.name == "Chat")
        keep = [n for n in chat.body if isinstance(n, ast.FunctionDef) and n.name == "_gui_type"]
        ns = {"config": config, "re": re}
        exec(compile(ast.fix_missing_locations(ast.Module(body=[ast.ClassDef(
            name="Mini", bases=[], keywords=[], body=keep, decorator_list=[])], type_ignores=[])), "m", "exec"), ns)
        c = ns["Mini"]()
        c.acts, c.said = [], []
        c._remember_gui_app = lambda pid: None
        c._say_local = lambda command, text: c.said.append(text) or True
        c._gui_act = lambda *a, **k: c.acts.append((a[2]["path"], k["action"], k["value"])) or {"ok": True, "verified": True}
        return c

    def test_adds_to_the_document_with_the_owners_capitals(self):
        c = self.mini()
        controls = [{"path": "0.1", "role": "entry", "states": ["editable", "single line"]},
                    {"path": "0.5", "role": "text", "states": ["editable", "multi line"]}]
        command = "in the text editor, type, I love pancakes."
        c._gui_type(command, parse_gui_command(command), {"pid": 1}, "Mousepad", controls)
        self.assertEqual(c.acts, [("0.5", "append_text", "I love pancakes")])
        self.assertEqual(c.said, ["I typed it."])

    def test_a_document_without_multi_line_is_still_added_to(self):
        # Live 2026-10-19: xed's document area isn't marked "multi line"; it was replaced, not added to.
        c = self.mini()
        controls = [{"path": "doc", "role": "text", "states": ["editable", "focused"]}]
        command = "type hello there in the text editor"
        c._gui_type(command, parse_gui_command(command), {"pid": 1}, "xed", controls)
        self.assertEqual(c.acts, [("doc", "append_text", "hello there")])
        c = self.mini()
        c._gui_type(command, parse_gui_command(command), {"pid": 1}, "Firefox",
                    [{"path": "search", "role": "entry", "states": ["editable", "single line"]}])
        self.assertEqual(c.acts[0][1], "set_text")  # explicitly single-line fields are replaced

    def test_the_gui_tool_uses_the_same_rule(self):
        src = (ROOT / "systems" / "chat.py").read_text()
        self.assertIn('kind = "set_text" if "single line" in control.get("states", []) else "append_text"', src)
        self.assertNotIn('"append_text" if "multi line" in', src)

    def test_focused_document_wins_and_nothing_to_type_into_is_said(self):
        c = self.mini()
        controls = [{"path": "a", "role": "text", "states": ["editable", "multi line"]},
                    {"path": "b", "role": "text", "states": ["editable", "multi line", "focused"]}]
        command = "type hi in the text editor"
        c._gui_type(command, parse_gui_command(command), {"pid": 1}, "Mousepad", controls)
        self.assertEqual(c.acts[0][0], "b")
        c = self.mini()
        c._gui_type(command, parse_gui_command(command), {"pid": 1}, "Calculator", [{"path": "x", "states": []}])
        self.assertEqual(c.said, ["I can't find a place to type in Calculator."])


if __name__ == "__main__":
    unittest.main()
