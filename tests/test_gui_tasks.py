"""GUI controller milestone 3: the Main Brain's gui tool (observe / plan / act / close).

Fixtures are real AT-SPI snapshots recorded on the test desktop 2026-10-18:
Thunar with the View menu closed and open, and Text Editor (xed) empty, typed
into, and showing its save prompt after a close request.
"""
import ast
import copy
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import types
import unittest

ROOT = Path(os.environ.get("BEMO_TEST_CODE_ROOT", Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(ROOT))
import config
from systems import gui_control
from systems.apps import AppEntry, match_app, spoken_app_name
from systems.computer import ComputerController, ComputerControlError
from systems.context_policy import tool_names_for_request
from systems.gui_control import numbered_view

FIX = ROOT / "tests" / "fixtures" / "gui"


def load(name):
    return json.loads((FIX / f"{name}.json").read_text())["controls"]


THUNAR_PID, XED_PID = 2281, 94262


class FakeDesktop:
    """Thunar and xed as recorded; actions change them the way the real apps did."""

    def __init__(self):
        self.thunar_menu_open = False
        self.hidden_shown = False
        self.xed_text = ""
        self.xed_prompt = False
        self.xed_open = True
        self.acted, self.points, self.signatures = [], [], []

    def windows(self):
        wins = [{"app": "Thunar", "pid": THUNAR_PID, "title": "bemo - Thunar", "active": False}]
        if self.xed_open:
            wins.append({"app": "xed", "pid": XED_PID, "title": "Unsaved Document 1", "active": True})
        return {"ok": True, "windows": wins}

    def _thunar(self):
        controls = copy.deepcopy(load("thunar-m3-view-menu" if self.thunar_menu_open else "thunar-m3-closed"))
        for c in controls:
            if c["name"] == "Show Hidden Files":
                c["states"] = sorted(set(c["states"]) | {"checked"}) if self.hidden_shown else \
                    [s for s in c["states"] if s != "checked"]
        return controls

    def _xed(self):
        controls = copy.deepcopy(load("xed-m3-save-prompt" if self.xed_prompt else "xed-m3-empty"))
        for c in controls:
            if c["role"] == "text" and "editable" in c["states"]:
                c["text"] = self.xed_text
        return controls

    def snapshot(self, pid, limit=400):
        if pid == XED_PID and not self.xed_open:
            return {"ok": False, "error": "no accessible application"}
        return {"ok": True, "pid": pid, "controls": self._thunar() if pid == THUNAR_PID else self._xed()}

    def act(self, pid, path, action, expect=None, value=None):
        controls = self._thunar() if pid == THUNAR_PID else self._xed()
        node = next(c for c in controls if c["path"] == path)
        self.acted.append((gui_control.display_name(node), action))
        after = copy.deepcopy(node)
        if node["name"] == "View" and node["role"] == "menu":
            self.thunar_menu_open = True
        elif node["name"] == "Show Hidden Files":
            self.hidden_shown = not self.hidden_shown
            self.thunar_menu_open = False  # a menu closes after an item is chosen
            after["states"] = sorted(set(after["states"]) | {"checked"}) if self.hidden_shown else \
                [s for s in after["states"] if s != "checked"]
        elif action == "set_text":
            self.xed_text = after["text"] = str(value)
        elif node["name"] == "Close without Saving":
            self.xed_open, after = False, None
        return {"ok": True, "before": node, "after": after}

    def close(self, pid):
        if pid == XED_PID and self.xed_text:
            self.xed_prompt = True  # unsaved work: the app asks first
            return {"ok": True, "verified": False,
                    "note": "the window is still open; it may be asking about unsaved work (look at it again)"}
        return {"ok": True, "verified": True, "note": "the window closed"}


class FakeSupervisor:
    """assess -> queue -> confirm, over a real ComputerController (tiers, verification)."""

    def __init__(self, desktop):
        self.desktop = desktop
        self.cc = ComputerController(screenshot_dir=tempfile.mkdtemp(), gui_backend=desktop,
                                     protected_pids=lambda: set())
        self.pending, self.confirmed, self.cancelled = {}, [], []

    def _execute(self, action, payload):
        if action == "gui_click_point":
            self.desktop.points.append((payload["x"], payload["y"], payload["target"]))
            self.desktop.signatures.append(payload.get("seen_signature"))
            return {"ok": True, "verified": True, "note": "the screen changed (3.0% of the window, 40% near the click)"}
        if action == "gui_close":
            return self.desktop.close(int(payload["pid"]))
        return self.cc.execute(action, payload)

    def computer_action(self, action, payload, **kw):
        if action == "gui_click_point":  # the real checks need a desktop; see VisionSupervisorTests
            assessment = {"confirmation_required": True, "critical": False, "summary": f"click {payload['target']}"}
        else:
            assessment = self.cc.assess(action, payload)
        if assessment.get("blocked"):
            return {"ok": False, **assessment}
        if assessment.get("confirmation_required"):
            cid = f"c{len(self.pending) + len(self.confirmed) + len(self.cancelled)}"
            self.pending[cid] = (action, payload, bool(assessment.get("critical")))
            return {"ok": False, "confirmation_required": True, "confirmation_id": cid,
                    "summary": assessment["summary"], "critical": bool(assessment.get("critical"))}
        return self._execute(action, payload)

    def confirm_computer_action(self, cid, require_critical=None, timeout=None):
        action, payload, critical = self.pending.pop(cid)
        if critical and require_critical is not True:
            return {"ok": False, "error": "critical"}
        self.confirmed.append((action, critical))
        return self._execute(action, payload)

    def cancel_computer_action(self, cid):
        self.cancelled.append(self.pending.pop(cid))
        return {"ok": True}


def ids_named(tool_output, name):
    return [int(m) for m in re.findall(rf"^#(\d+) [^:]+: {re.escape(name)}\b", tool_output, re.M)]


class GuiToolTests(unittest.TestCase):
    def setUp(self):
        src = (ROOT / "systems" / "chat.py").read_text()
        chat = next(n for n in ast.parse(src).body if isinstance(n, ast.ClassDef) and n.name == "Chat")
        keep = {"_gui_tool", "_gui_observe", "_gui_preobserve", "_gui_request", "_screen_only_windows",
                "_gui_vision", "_gui_click_seen", "_cancel_gui_request", "_gui_act", "_app_action",
                "_remember_gui_app", "_resolve_app_window", "_app_tools", "_mentions_open_app",
                "_handle_gui_command", "_run_gui_command", "_said_list", "_say_local"}
        body = [n for n in chat.body if isinstance(n, ast.FunctionDef) and n.name in keep]
        self.clock = [1000.0]
        ns = {"config": config, "re": re,
              "time": types.SimpleNamespace(monotonic=lambda: self.clock[0], sleep=lambda s: None),
              "State": types.SimpleNamespace(RESPONDING="responding"), "SupervisorError": RuntimeError,
              "logger": types.SimpleNamespace(info=lambda *a, **k: None, exception=lambda *a, **k: None),
              "spoken_app_name": spoken_app_name,
              "_CLIPBOARD_CONTROL": re.compile(r"\b(?:paste|clipboard|copy|cut)\b", re.IGNORECASE),
              **{n: getattr(gui_control, n) for n in (
                  "ROLE_HINTS", "display_name", "find_controls", "numbered_view", "parse_gui_command",
                  "open_dialog_text", "view_changes",
                  "resolve_window", "say_name", "say_text", "spoken_control_names")}}
        exec(compile(ast.fix_missing_locations(ast.Module(body=[ast.ClassDef(
            name="Mini", bases=[], keywords=[], body=body, decorator_list=[])], type_ignores=[])), "mini", "exec"), ns)
        self.Mini = ns["Mini"]

    def chat(self, answers=()):
        c = self.Mini()
        c.desktop = FakeDesktop()
        c.supervisor = FakeSupervisor(c.desktop)
        apps = [AppEntry("thunar", "Thunar File Manager", "File Manager", (), ("thunar",)),
                AppEntry("org.x.editor", "Text Editor", "", (), ("xed",))]
        catalog = types.SimpleNamespace(find=lambda q: match_app(q, apps), entries=lambda: apps)
        c._app_catalog, c._window_probe = catalog, None
        c._gui_task = {"steps": 0}
        c.spoken, c.questions, answers = [], [], list(answers)
        c.speak = c.spoken.append
        c._archive_local_exchange = lambda cmd, resp: None
        c._confirm_by_voice = lambda q, **kw: c.questions.append(q) or answers.pop(0)
        return c

    def test_numbered_view_reads_like_text(self):
        closed, ids = numbered_view(load("thunar-m3-closed"))
        self.assertIn(": View", closed)
        self.assertNotIn("Show Hidden Files", closed)  # hidden until the menu opens
        self.assertEqual(len(ids_named(closed, "Places")), 1)  # a row's repeated cells appear once
        opened, _ = numbered_view(load("thunar-m3-view-menu"))
        self.assertRegex(opened, r"#\d+ check menu item: Show Hidden Files")
        typed, _ = numbered_view(load("xed-m3-typed"))
        self.assertIn("text: (unnamed field) (text='Hello from BMO.')", typed)

    def test_thunar_show_hidden_files_with_one_plan_ok(self):
        c = self.chat(answers=[True])
        view = c._gui_tool({"action": "observe", "app": "Thunar"})
        out = c._gui_tool({"action": "act", "app": "Thunar", "id": ids_named(view, "View")[0], "kind": "click"})
        self.assertTrue(out.startswith("VERIFIED"), out)  # opening a menu is navigation: no question
        self.assertEqual(c.questions, [])
        item = ids_named(out, "Show Hidden Files")[0]
        out = c._gui_tool({"action": "plan", "app": "Thunar",
                           "summary": "Open the View menu and turn on Show Hidden Files"})
        self.assertTrue(out.startswith("APPROVED"), out)
        self.assertEqual(c.questions, ["I'll open the View menu and turn on Show Hidden Files in Thunar. "
                                       "OK? Say Hey BMO, yes, or just say no."])
        out = c._gui_tool({"action": "act", "app": "Thunar", "id": item, "kind": "click"})
        self.assertTrue(out.startswith("VERIFIED"), out)
        self.assertTrue(c.desktop.hidden_shown)
        self.assertEqual([a[0] for a in c.desktop.acted], ["View", "Show Hidden Files"])
        self.assertEqual(len(c.questions), 1)  # the plan's OK covered the change

    def test_text_editor_type_then_close_without_saving_asks_again(self):
        c = self.chat(answers=[True, True])
        view = c._gui_tool({"action": "observe", "app": "text editor"})  # the xed window is found by its menu name
        field = re.search(r"^#(\d+) text: \(unnamed field\)", view, re.M).group(1)
        c._gui_tool({"action": "plan", "app": "text editor",
                     "summary": "type a sentence, then close without saving"})
        out = c._gui_tool({"action": "act", "app": "text editor", "id": int(field), "kind": "set_text",
                           "text": "Hello from BMO."})
        self.assertTrue(out.startswith("VERIFIED"), out)
        out = c._gui_tool({"action": "close", "app": "text editor"})
        self.assertTrue(out.startswith("NOT VERIFIED"), out)  # the save prompt keeps the window open
        button = ids_named(out, "Close without Saving")[0]
        out = c._gui_tool({"action": "act", "app": "text editor", "id": button, "kind": "click"})
        self.assertTrue(out.startswith("VERIFIED"), out)
        self.assertEqual(len(c.questions), 2)  # plan OK, then the critical step asked again
        self.assertIn("may not be undoable", c.questions[1])
        self.assertIn(("gui_act", True), c.supervisor.confirmed)
        self.assertFalse(c.desktop.xed_open)

    def test_a_change_without_a_plan_is_asked_for_itself(self):
        # v1.28.29: Main kept writing "plan: ..." as words after NEEDS APPROVAL (bench);
        # BMO now asks about that step directly, and the OK covers the app for a while.
        c = self.chat(answers=[True])
        view = c._gui_tool({"action": "observe", "app": "text editor"})
        field = int(re.search(r"^#(\d+) text: \(unnamed field\)", view, re.M).group(1))
        out = c._gui_tool({"action": "act", "app": "text editor", "id": field, "kind": "set_text", "text": "BMO was here."})
        self.assertTrue(out.startswith("VERIFIED"), out)
        self.assertEqual(c.questions, ["I'll type 'BMO was here.' into the text field in text editor. "
                                       "Say Hey BMO, yes, or just say no."])
        self.assertEqual(c.desktop.xed_text, "BMO was here.")
        self.assertIn("Done so far: type 'BMO was here.' into", out)
        self.assertTrue(c._gui_tool({"action": "plan", "app": "text editor", "summary": "close it"})
                        .startswith("Already approved"))  # no second question

    def test_save_prompt_is_flagged_not_done(self):
        # Live 2026-10-18: File > Close opened xed's "Save changes?" prompt and
        # Main reported the editor closed.
        c = self.chat(answers=[True])
        view = c._gui_tool({"action": "observe", "app": "text editor"})
        field = int(re.search(r"^#(\d+) text:", view, re.M).group(1))
        c._gui_tool({"action": "plan", "app": "text editor", "summary": "type and close without saving"})
        c._gui_tool({"action": "act", "app": "text editor", "id": field, "kind": "set_text", "text": "hi"})
        out = c._gui_tool({"action": "close", "app": "text editor"})
        self.assertIn("A DIALOG IS OPEN: 'Save changes to document", out)
        self.assertIn("task is not done", out)
        self.assertTrue(ids_named(out, "Close without Saving"))

    def test_numbers_stay_and_results_list_only_changes(self):
        c = self.chat()
        view = c._gui_tool({"action": "observe", "app": "Thunar"})
        menu = ids_named(view, "View")[0]
        out = c._gui_tool({"action": "act", "app": "Thunar", "id": menu, "kind": "click"})
        self.assertNotIn(": Places", out)  # unchanged sidebar not repeated
        self.assertTrue(ids_named(out, "Show Hidden Files"))
        again = c._gui_tool({"action": "observe", "app": "Thunar"})
        self.assertEqual(ids_named(again, "View"), [menu])  # same number after the change
        self.assertLess(len(out.splitlines()), len(again.splitlines()) - 15)  # sidebar etc. not repeated

    def test_main_is_shown_the_app_with_the_request(self):
        c = self.chat()
        c._gui_task = {"steps": 0, "target": "thunar"}
        call, result = c._gui_preobserve([{"function": {"name": "gui"}}])
        self.assertEqual(call["tool_calls"][0]["function"]["arguments"], {"action": "observe", "app": "thunar"})
        self.assertEqual(result["role"], "tool")
        self.assertTrue(ids_named(result["content"], "View"))
        self.assertIsNone(c._gui_preobserve([{"function": {"name": "computer_control"}}]))

    def test_clipboard_only_when_asked(self):
        # Live 2026-10-18: asked to type, Main pressed "Paste the clipboard" and the
        # clipboard (the start command) went into the document.
        c = self.chat(answers=[True])
        c._active_user_text = "in the text editor, type BMO was here, then close it without saving"
        view = c._gui_tool({"action": "observe", "app": "text editor"})
        paste = ids_named(view, "Paste the clipboard")[0]
        out = c._gui_tool({"action": "act", "app": "text editor", "id": paste, "kind": "click"})
        self.assertTrue(out.startswith("REFUSED"), out)
        self.assertEqual(c.desktop.acted, [])
        self.assertNotIn("waiting", c._gui_task)  # nothing to run on approval
        c._active_user_text = "paste my clipboard into the text editor"
        c._gui_tool({"action": "act", "app": "text editor", "id": paste, "kind": "click"})
        self.assertEqual(c.desktop.acted, [("Paste the clipboard", "click")])  # asked for, so allowed
        self.assertEqual(len(c.questions), 1)  # after a question, like any change

    def test_field_contents_stay_out_of_results(self):
        c = self.chat(answers=[True])
        c.desktop.xed_text = "secret clipboard text"
        view = c._gui_tool({"action": "observe", "app": "text editor"})
        field = int(re.search(r"^#(\d+) text:", view, re.M).group(1))
        c._gui_tool({"action": "plan", "app": "text editor", "summary": "type a line"})
        out = c._gui_tool({"action": "act", "app": "text editor", "id": field, "kind": "set_text", "text": "hi"})
        self.assertIn("type 'hi' into the text field", out)
        self.assertNotIn("secret clipboard text", out.split("\n")[0])

    def test_declined_plan_stops_the_task(self):
        c = self.chat(answers=[False])
        view = c._gui_tool({"action": "observe", "app": "Thunar"})
        self.assertTrue(c._gui_tool({"action": "plan", "app": "Thunar", "summary": "show hidden files"})
                        .startswith("STOPPED"))
        self.assertEqual(c._gui_task["final"], "Okay, I won't.")
        out = c._gui_tool({"action": "act", "app": "Thunar", "id": ids_named(view, "View")[0], "kind": "click"})
        self.assertTrue(out.startswith("DECLINED"))
        self.assertEqual(c.desktop.acted, [])

    def test_no_answer_is_not_a_refusal_and_a_late_yes_counts(self):
        # Live 2026-10-18: two clipped "Hey BMO, yes" answers, the question timed
        # out, Main said "I won't close the text editor without saving" and then
        # refused the repeated request.
        c = self.chat(answers=[False])
        c._last_confirm_outcome = "expired"
        c._confirm_by_voice = lambda q, **kw: c.questions.append(q) or False
        c._active_user_text = "make thunar show hidden files"
        c._gui_tool({"action": "observe", "app": "Thunar"})
        out = c._gui_tool({"action": "plan", "app": "Thunar", "summary": "show hidden files"})
        self.assertTrue(out.startswith("STOPPED"))
        self.assertIn("didn't hear a yes", c._gui_task["final"])
        self.assertEqual(c._gui_late_yes["text"], "make thunar show hidden files")
        # The redo of that request: the plan is covered without asking again.
        c._gui_task, c._gui_preapproved = {"steps": 0}, {"until": self.clock[0] + 600}
        c._gui_tool({"action": "observe", "app": "Thunar"})
        self.assertTrue(c._gui_tool({"action": "plan", "app": "Thunar", "summary": "show hidden files"})
                        .startswith("APPROVED"))
        self.assertEqual(len(c.questions), 1)

    def test_step_budget_and_unknown_ids(self):
        c = self.chat()
        self.assertIn("Unknown control id", c._gui_tool({"action": "act", "app": "Thunar", "id": 3}))
        view = c._gui_tool({"action": "observe", "app": "Thunar"})
        back = ids_named(view, "Home")[0]  # navigation: free, so only the budget stops it
        for _ in range(config.GUI_TASK_MAX_STEPS):
            c._gui_tool({"action": "act", "app": "Thunar", "id": back, "kind": "click"})
        out = c._gui_tool({"action": "act", "app": "Thunar", "id": back, "kind": "click"})
        self.assertTrue(out.startswith("STEP BUDGET USED UP"), out)
        self.assertEqual(len(c.desktop.acted), config.GUI_TASK_MAX_STEPS)

    def test_click_by_sight(self):
        from unittest import mock
        c = self.chat(answers=[True])
        c._screen_only_windows = lambda listing: [
            {"app": "firefox-bin", "pid": 105, "title": "BMO vision test — Mozilla Firefox", "screen_only": True}]
        located = []
        c._gui_vision_locator = types.SimpleNamespace(
            available=lambda: (True, ""),
            locate=lambda image, instruction: located.append(instruction) or ((106.0, 270.0), "<answer>"))
        c._app_action = lambda action, payload: (
            {"ok": True} if action == "run" else c.supervisor.computer_action(action, payload))
        from PIL import Image
        with mock.patch("systems.gui_vision.window_regions", return_value=[("0x1", (8, 76, 1280, 942), "x")]), \
                mock.patch("systems.gui_vision.capture", return_value=Image.new("RGB", (1280, 942))), \
                mock.patch("systems.gui_vision.raise_window"):
            self.assertIn("does not share its controls", c._gui_tool({"action": "observe", "app": "Firefox"}))
            out = c._gui_tool({"action": "click_seen", "app": "Firefox", "target": "the Say hello button"})
        self.assertTrue(out.startswith("VERIFIED: click the Say hello button"), out)
        self.assertEqual(c.questions, ["I'll click the Say hello button in Firefox. Say Hey BMO, yes, or just say no."])
        self.assertEqual(located, ["Click the Say hello button."])
        self.assertEqual(c.desktop.points, [(144, 330, "the Say hello button")])
        self.assertEqual(len(c.desktop.signatures[0]), 32 * 18)  # the Supervisor gets what BMO saw
        self.assertEqual(c.spoken, ["Let me look at the screen."])

    def test_one_step_command_for_a_screen_only_app_goes_to_main(self):
        # Live 2026-10-18: "in Firefox, click the say goodbye button" got "Firefox isn't open".
        c = self.chat()
        c._screen_only_windows = lambda listing: [
            {"app": "firefox-bin", "pid": 105, "title": "BMO vision test — Mozilla Firefox", "screen_only": True}]
        for said in ("in Firefox, click the say goodbye button.",
                     "can you click the Hello Bemo button in the Firefox tab?"):
            c._gui_escalation = None
            self.assertFalse(c._handle_gui_command(said), said)
            self.assertEqual(c._gui_escalation, {"app": "firefox"})
        self.assertEqual(c.spoken, [])

    def test_vision_unavailable_is_said_plainly(self):
        c = self.chat()
        c._screen_only_windows = lambda listing: [{"app": "firefox-bin", "pid": 105, "title": "x — Mozilla Firefox",
                                                   "screen_only": True}]
        c._gui_vision_locator = types.SimpleNamespace(available=lambda: (False, "only 3.0 GB of memory is free"))
        out = c._gui_tool({"action": "click_seen", "app": "Firefox", "target": "the Say hello button"})
        self.assertEqual(out, "VISION UNAVAILABLE: only 3.0 GB of memory is free. Tell the owner that, briefly.")
        self.assertEqual(c._gui_task["steps"], 0)

    def test_closed_app_is_not_pretended(self):
        c = self.chat()
        c.desktop.xed_open = False
        self.assertIn("has no open window", c._gui_tool({"action": "observe", "app": "text editor"}))

    def test_one_step_command_hands_over_when_the_control_is_in_a_menu(self):
        c = self.chat()
        self.assertFalse(c._handle_gui_command("turn on show hidden files in thunar"))
        self.assertTrue(c._gui_escalation)
        self.assertEqual(c.spoken, [])
        self.assertEqual(c.desktop.acted, [])

    def test_tool_is_offered_only_for_open_apps(self):
        self.assertIn("gui", tool_names_for_request("make thunar show hidden files", "normal"))
        c = self.chat()
        self.assertTrue(c._mentions_open_app("in text editor type hello then close it without saving", {}))
        self.assertTrue(c._mentions_open_app("make thunar show hidden files", {}))
        self.assertFalse(c._mentions_open_app("what is a tab in python", {}))


class VisionTests(unittest.TestCase):
    def test_points_and_changes(self):
        from PIL import Image, ImageDraw
        from systems.gui_vision import changed_fraction, parse_point, to_screen
        reply = '<grounding_think>the button</grounding_think>\n<answer>\n{"coordinate": [433,841]}\n</answer>'
        self.assertEqual(parse_point(reply), (433.0, 841.0))
        self.assertIsNone(parse_point("I can't see it"))
        self.assertIsNone(parse_point("<answer>[1500, 20]</answer>"))  # outside the 0-1000 frame
        # Bench 2026-10-18: [433, 841] on a 1280x720 frame was the Calculator "5" key at ~(554, 610).
        self.assertEqual(to_screen((433, 841), (0, 0, 1280, 720)), (554, 606))
        self.assertEqual(to_screen((500, 500), (8, 76, 1280, 942)), (648, 547))
        a = Image.new("RGB", (400, 300), (240, 240, 240))
        b = a.copy()
        ImageDraw.Draw(b).text((120, 140), "Hello from BMO!", fill=(170, 50, 50))
        self.assertEqual(changed_fraction(a, a.copy()), 0.0)
        from systems.gui_vision import changed_pixels
        self.assertGreater(changed_pixels(b, a), 120)  # the Supervisor's "near" threshold
        self.assertEqual(changed_pixels(a, a.copy()), 0)

    def test_memory_gate(self):
        from unittest import mock
        from systems import gui_vision
        GiB = 2 ** 30
        with mock.patch("psutil.virtual_memory", return_value=types.SimpleNamespace(total=16 * GiB, available=9 * GiB)):
            ok, why = gui_vision.memory_gate(28, 4.5)
            self.assertFalse(ok)
            self.assertIn("16 GB", why)
        with mock.patch("psutil.virtual_memory", return_value=types.SimpleNamespace(total=28.2 * GiB, available=3 * GiB)):
            self.assertIn("3.0 GB of memory is free", gui_vision.memory_gate(28, 4.5)[1])
        with mock.patch("psutil.virtual_memory", return_value=types.SimpleNamespace(total=28.2 * GiB, available=5.8 * GiB)):
            self.assertEqual(gui_vision.memory_gate(28, 4.5), (True, ""))
        # Live 2026-10-20: refused at 4.1 GB free, saying it needed "4" (4.5 rounded). The bar is now 3.5,
        # and the message shows the real number.
        with mock.patch("psutil.virtual_memory", return_value=types.SimpleNamespace(total=28.2 * GiB, available=4.1 * GiB)):
            self.assertEqual(gui_vision.memory_gate(28, config.GUI_VISION_MIN_AVAILABLE_GIB), (True, ""))
        with mock.patch("psutil.virtual_memory", return_value=types.SimpleNamespace(total=28.2 * GiB, available=3.2 * GiB)):
            self.assertIn("needs 3.5 GB", gui_vision.memory_gate(28, config.GUI_VISION_MIN_AVAILABLE_GIB)[1])


class VisionSupervisorTests(unittest.TestCase):
    def test_point_click_checks(self):
        from unittest import mock
        cc = ComputerController(screenshot_dir=tempfile.mkdtemp(), protected_pids=lambda: {77})
        regions = {105: [("0x1", (8, 76, 1280, 942), "BMO vision test")], 77: [("0x2", (0, 0, 800, 600), "Terminal")]}
        with mock.patch("systems.gui_vision.window_regions", side_effect=lambda pid: regions.get(pid, [])):
            ok = cc.assess("gui_click_point", {"pid": 105, "x": 145, "y": 330, "app": "Firefox", "target": "Say hello"})
            self.assertTrue(ok["confirmation_required"])  # a guessed point is never free
            self.assertFalse(ok["critical"])
            self.assertTrue(cc.assess("gui_click_point", {"pid": 105, "x": 145, "y": 330, "target": "Delete account"})
                            ["critical"])
            self.assertIn("not inside", cc.assess("gui_click_point", {"pid": 105, "x": 1500, "y": 30})["summary"])
            self.assertTrue(cc.assess("gui_click_point", {"pid": 77, "x": 10, "y": 10})["blocked"])


class VisionClickSafetyTests(unittest.TestCase):
    """Bench 2026-10-18: another window came to the front while BMO was looking and got the click."""

    def run_click(self, active, drift_image):
        from unittest import mock
        from PIL import Image
        from systems import adapters
        from systems.gui_vision import signature
        cc = ComputerController(screenshot_dir=tempfile.mkdtemp(), protected_pids=lambda: set())
        seen = signature(Image.new("RGB", (1280, 942), (244, 241, 232)))  # the test page's background
        clicks = []
        fake = types.SimpleNamespace(
            activate=lambda wid: clicks.append(("activate", wid)),
            active_window=lambda: active,
            window_regions=lambda pid: [("0x4c00003", (8, 76, 1280, 942), "t")],
            capture=lambda region: drift_image,
            move=lambda x, y: clicks.append(("move", x, y)),
            click=lambda button=1, repeat=1: clicks.append(("click", button)))
        previous = adapters._DESKTOP
        adapters.set_desktop(fake)
        try:
            cc.gui_click_point({"pid": 105, "x": 145, "y": 330, "target": "Say hello", "seen_signature": seen})
        except ComputerControlError as exc:
            return str(exc), [c for c in clicks if c[0] != "activate"]
        finally:
            adapters.set_desktop(previous)
        return "clicked", [c for c in clicks if c[0] != "activate"]

    def test_refuses_when_another_window_is_in_front(self):
        from PIL import Image
        outcome, clicks = self.run_click(active=0x3800003, drift_image=Image.new("RGB", (1280, 942), (244, 241, 232)))
        self.assertIn("couldn't bring that window to the front", outcome)
        self.assertEqual(clicks, [])

    def test_refuses_when_the_window_looks_different(self):
        from PIL import Image
        youtube_like = Image.new("RGB", (1280, 942), (15, 15, 15))
        outcome, clicks = self.run_click(active=0x4c00003, drift_image=youtube_like)
        self.assertIn("looks different from when I looked", outcome)
        self.assertEqual(clicks, [])

    def test_clicks_when_front_and_unchanged(self):
        from PIL import Image
        outcome, clicks = self.run_click(active=0x4c00003, drift_image=Image.new("RGB", (1280, 942), (244, 241, 232)))
        self.assertEqual(outcome, "clicked")
        self.assertEqual(clicks[:2], [("move", 145, 330), ("click", 1)])


class PseudoToolTextTests(unittest.TestCase):
    def test_steps_written_as_words_are_caught(self):
        src = (ROOT / "systems" / "chat.py").read_text()
        pattern = next(n for n in ast.parse(src).body if isinstance(n, ast.Assign)
                       and getattr(n.targets[0], "id", "") == "_PSEUDO_TOOL_TEXT")
        ns = {"re": re}
        exec(compile(ast.Module(body=[pattern], type_ignores=[]), "p", "exec"), ns)
        caught = ns["_PSEUDO_TOOL_TEXT"]
        said = ('plan Text Editor: type "BMO was here" in the text field.\nact Text Editor, id=18, kind=select\n'
                "VERIFIED: Text editor closed without saving.")
        self.assertTrue(caught.search(said))
        self.assertTrue(caught.search('plan: Click the "Say Hello" button visible on the current Firefox page.'))
        for fine in ("Hidden files are now visible in Thunar.", "I'll act on that plan when you say yes.",
                     "Close the editor without saving? I can do that."):
            self.assertFalse(caught.search(fine), fine)

    def test_promise_without_a_tool_call_is_caught(self):
        src = (ROOT / "systems" / "chat.py").read_text()
        pattern = next(n for n in ast.parse(src).body if isinstance(n, ast.Assign)
                       and getattr(n.targets[0], "id", "") == "_PROMISED_GUI_ACTION")
        ns = {"re": re}
        exec(compile(ast.Module(body=[pattern], type_ignores=[]), "p", "exec"), ns)
        promised = ns["_PROMISED_GUI_ACTION"]
        self.assertTrue(promised.search('Got it. I\'ll find and click the "Say Hello" button on the Firefox page. '
                                        "One moment."))  # live 2026-10-18, then nothing happened
        self.assertFalse(promised.search('Clicked the "Say Hello" button in Firefox. The page changed, verified.'))


class ApprovalListeningTests(unittest.TestCase):
    """_confirm_by_voice with scripted transcripts (the real wake gate)."""

    def setUp(self):
        from voice_commands import VoiceSessionGate
        src = (ROOT / "systems" / "chat.py").read_text()
        chat = next(n for n in ast.parse(src).body if isinstance(n, ast.ClassDef) and n.name == "Chat")
        body = [n for n in chat.body if isinstance(n, ast.FunctionDef)
                and n.name in {"_confirm_by_voice", "_normalize_command_text"}]
        self.now = [0.0]
        ns = {"config": config, "re": re, "logger": types.SimpleNamespace(info=lambda *a, **k: None,
                                                                          warning=lambda *a, **k: None),
              "time": types.SimpleNamespace(monotonic=lambda: self.now[0]),
              "State": types.SimpleNamespace(RESPONDING="r", THINKING="t")}
        src_norm = next((n for n in chat.body if isinstance(n, ast.FunctionDef) and n.name == "_normalize_command_text"), None)
        self.assertIsNotNone(src_norm)
        exec(compile(ast.fix_missing_locations(ast.Module(body=[ast.ClassDef(
            name="Mini", bases=[], keywords=[], body=body, decorator_list=[])], type_ignores=[])), "mini", "exec"), ns)
        self.Mini, self.Gate = ns["Mini"], VoiceSessionGate

    def ask(self, heard):
        c = self.Mini()
        c.voice_gate = self.Gate(wake_aliases=config.VOICE_WAKE_ALIASES)
        c._active_microphone, c.stop_event = object(), types.SimpleNamespace(is_set=lambda: False)
        c.spoken, heard = [], list(heard)

        def listen(source):
            self.now[0] += 5.0
            return heard.pop(0) if heard else None
        c.listen, c.speak = listen, c.spoken.append
        return c, c._confirm_by_voice("OK?", tag="T", target="x", timeout=35.0)

    def test_clipped_wake_yes_counts(self):
        for heard in ("Yemo Yes", "Nemo Yes", "Bemo Yes", "Hey Bemo, yes."):
            c, ok = self.ask([heard])
            self.assertTrue(ok, heard)
            self.assertEqual(c._last_confirm_outcome, "approved")

    def test_bare_yes_gets_one_hint(self):
        c, ok = self.ask(["Yes.", "Yes.", "Hey BMO, yes."])
        self.assertTrue(ok)
        self.assertEqual(c.spoken, ["OK?", "Say: Hey BMO, yes."])

    def test_silence_is_expired_and_no_is_declined(self):
        c, ok = self.ask([])
        self.assertFalse(ok)
        self.assertEqual(c._last_confirm_outcome, "expired")
        c, ok = self.ask(["No."])
        self.assertEqual((ok, c._last_confirm_outcome), (False, "declined"))

    def test_clipped_wake_never_wakes_bmo(self):
        gate = self.Gate(wake_aliases=config.VOICE_WAKE_ALIASES)
        self.assertFalse(gate.parse("Nemo yes")[0])


class LateYesTests(unittest.TestCase):
    def setUp(self):
        src = (ROOT / "systems" / "chat.py").read_text()
        chat = next(n for n in ast.parse(src).body if isinstance(n, ast.ClassDef) and n.name == "Chat")
        body = [n for n in chat.body if isinstance(n, ast.FunctionDef)
                and n.name in {"_handle_local_command", "_handle_local_command_once", "_normalize_command_text", "_say_local"}]
        self.now = [100.0]
        ns = {"config": config, "re": re, "logger": types.SimpleNamespace(info=lambda *a, **k: None),
              "time": types.SimpleNamespace(monotonic=lambda: self.now[0]), "State": types.SimpleNamespace(RESPONDING="r")}
        exec(compile(ast.fix_missing_locations(ast.Module(body=[ast.ClassDef(
            name="Mini", bases=[], keywords=[], body=body, decorator_list=[])], type_ignores=[])), "mini", "exec"), ns)
        self.Mini = ns["Mini"]

    def test_late_yes_redoes_the_request_once(self):
        c = self.Mini()
        c.redone = []
        c.respond = lambda text: c.redone.append((text, bool(c._gui_preapproved)))
        c._gui_late_yes = {"text": "make thunar show hidden files", "until": 160.0}
        self.assertTrue(c._handle_local_command("yes!"))
        self.assertEqual(c.redone, [("make thunar show hidden files", True)])
        self.assertIsNone(c._gui_late_yes)

    def test_too_late_does_nothing(self):
        c = self.Mini()
        c.respond = lambda text: self.fail("should not redo")
        c.spoken = []
        c.speak, c._archive_local_exchange = c.spoken.append, lambda *a: None
        c._gui_late_yes = {"text": "make thunar show hidden files", "until": 90.0}
        self.assertTrue(c._handle_local_command("yes"))
        self.assertTrue(c.spoken[0].startswith("That question timed out"))


if __name__ == "__main__":
    unittest.main()
