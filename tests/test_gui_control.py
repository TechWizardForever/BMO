"""GUI controller: tiers, lookup, verification, Supervisor wiring (M1) and voice commands (M2).

Fixtures are real AT-SPI snapshots recorded on the test desktop 2026-10-18.
"""
import ast
import copy
import json
import os
import re
import types
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(os.environ.get("BEMO_TEST_CODE_ROOT", Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(ROOT))
import config
from systems import gui_control
from systems.apps import AppEntry, match_app, spoken_app_name
from systems.computer import ComputerController, ComputerControlError
from systems.gui_control import (
    CHANGE, CRITICAL, LOOK, NAVIGATE, classify, compact_view, find_controls, parse_gui_command, resolve_window,
    snapshot_changed, spoken_control_names, verify,
)

FIX = ROOT / "tests" / "fixtures" / "gui"
CALC = json.loads((FIX / "gnome-calculator.json").read_text())["controls"]
PAVU = json.loads((FIX / "pavucontrol.json").read_text())["controls"]
THUNAR = json.loads((FIX / "Thunar.json").read_text())["controls"]


def one(controls, name):
    found = find_controls(controls, name)
    assert len(found) == 1, (name, found)
    return found[0]


class TierTests(unittest.TestCase):
    def test_real_controls(self):
        self.assertEqual(classify(one(CALC, "7"), "click"), CHANGE)
        self.assertEqual(classify(one(PAVU, "Recording"), "click"), NAVIGATE)
        self.assertEqual(classify(one(THUNAR, "Back"), "click"), NAVIGATE)
        self.assertEqual(classify(find_controls(THUNAR, "File", role="menu")[0], "click"), NAVIGATE)
        self.assertEqual(classify(find_controls(PAVU, "Mute audio")[0], "click"), CHANGE)
        self.assertEqual(classify(one(CALC, "7"), "look"), LOOK)

    def test_irreversible_and_password(self):
        for name in ("Delete", "Don't Save", "Don\u2019t Save", "Move to Trash", "Log Out", "Send", "Empty Trash",
                     "Install Updates"):
            self.assertEqual(classify({"role": "push button", "name": name}, "click"), CRITICAL, name)
        self.assertIsNone(classify({"role": "password text", "name": "Password"}, "set_text"))
        self.assertEqual(classify({"role": "text", "name": "Search"}, "set_text"), CHANGE)


class FindTests(unittest.TestCase):
    def test_names_labels_and_symbols(self):
        self.assertEqual(one(CALC, "×")["role"], "push button")
        self.assertEqual(one(CALC, "clear display")["label"], "Clear Display [Escape]")  # unnamed, tooltip label
        self.assertEqual(one(PAVU, "playback")["role"], "page tab")

    def test_duplicates_come_with_context(self):
        mutes = find_controls(PAVU, "mute audio")
        self.assertGreater(len(mutes), 1)  # several streams: BMO must ask which one
        self.assertEqual([m["context"] for m in mutes], ["System Sounds", "speech-dispatcher-dummy", "Firefox"])

    def test_display_contents_are_not_a_key_name(self):
        shown = [dict(c, text="7") if c["role"] == "editbar" else c for c in CALC]
        self.assertEqual(len(find_controls(shown, "7")), 1)

    def test_compact_view_is_model_readable(self):
        text = compact_view(CALC)
        self.assertIn("push button: 7", text)
        self.assertTrue(text.splitlines()[0].startswith("["))


class VerifyTests(unittest.TestCase):
    def test_control_level_checks(self):
        tog = {"role": "toggle button", "states": []}
        self.assertEqual(verify("click", tog, dict(tog, states=["checked"]))[0], True)
        self.assertEqual(verify("click", tog, tog)[0], False)
        self.assertEqual(verify("press", tog, dict(tog, states=["checked"]))[0], True)  # LibreOffice toolbars
        tab = {"role": "page tab", "states": []}
        self.assertTrue(verify("click", tab, dict(tab, states=["selected"]))[0])
        self.assertTrue(verify("set_text", {}, {"text": "hello"}, "hello")[0])
        self.assertTrue(verify("set_value", {}, {"value": {"current": 50.2}}, 50)[0])
        self.assertIsNone(verify("click", {"role": "push button"}, {"role": "push button"})[0])

    def test_snapshot_diff(self):
        a = {"controls": [{"path": "0/1", "role": "text", "name": "", "text": "7", "states": []}]}
        b = {"controls": [{"path": "0/1", "role": "text", "name": "", "text": "42", "states": []}]}
        self.assertTrue(snapshot_changed(a, b))
        self.assertFalse(snapshot_changed(a, json.loads(json.dumps(a))))


class FakeBackend:
    def __init__(self, before, after, act_ok=True, after_snap_ok=True):
        self.snaps = [before, after]
        self.act_ok, self.after_snap_ok = act_ok, after_snap_ok

    def snapshot(self, pid, limit=400):
        snap = self.snaps.pop(0) if self.snaps else {"ok": False}
        return snap

    def act(self, pid, path, action, expect=None, value=None):
        if not self.act_ok:
            return {"ok": False, "error": "the control changed since I looked"}
        c = {"path": path, "role": "push button", "name": "7", "states": []}
        return {"ok": True, "before": c, "after": c}

    def windows(self):
        return {"ok": True, "windows": []}


class SupervisorWiringTests(unittest.TestCase):
    def cc(self, backend=None, protected=frozenset()):
        return ComputerController(screenshot_dir=tempfile.mkdtemp(), gui_backend=backend,
                                  protected_pids=lambda: set(protected))

    def payload(self, control, action="click", pid=4242):
        return {"pid": pid, "path": control.get("path", "0"), "action": action, "app": "Calculator", "expect": control}

    def test_tiers_map_to_confirmations(self):
        cc = self.cc()
        self.assertFalse(cc.assess("gui_snapshot", {"pid": 1}).get("confirmation_required"))
        self.assertFalse(cc.assess("gui_act", self.payload(one(PAVU, "Recording"))).get("confirmation_required"))
        change = cc.assess("gui_act", self.payload(one(CALC, "7")))
        self.assertTrue(change["confirmation_required"])
        self.assertFalse(change["critical"])
        self.assertIn("'7'", change["summary"])
        crit = cc.assess("gui_act", self.payload({"role": "push button", "name": "Delete", "path": "0/9"}))
        self.assertTrue(crit["critical"])
        self.assertTrue(cc.assess("gui_act", self.payload({"role": "password text", "name": "Password"},
                                                          "set_text")).get("blocked"))

    def test_never_operates_bemos_own_windows(self):
        cc = self.cc(protected={4242})
        self.assertTrue(cc.assess("gui_act", self.payload(one(CALC, "7"))).get("blocked"))

    def test_execute_verifies_by_observing(self):
        before = {"ok": True, "controls": [{"path": "0/1", "role": "text", "name": "", "text": "", "states": []}]}
        after = {"ok": True, "controls": [{"path": "0/1", "role": "text", "name": "", "text": "7", "states": []}]}
        result = self.cc(FakeBackend(before, after)).execute("gui_act", self.payload(one(CALC, "7")))
        self.assertEqual((result["verified"], result["note"]), (True, "something changed"))
        result = self.cc(FakeBackend(before, before)).execute("gui_act", self.payload(one(CALC, "7")))
        self.assertEqual((result["verified"], result["note"]), (False, "nothing visible changed"))

    def test_stale_control_is_an_error_not_a_click(self):
        with self.assertRaises(ComputerControlError):
            self.cc(FakeBackend({"ok": True, "controls": []}, {}, act_ok=False)).execute(
                "gui_act", self.payload(one(CALC, "7")))


class ParseTests(unittest.TestCase):
    def test_commands(self):
        p = parse_gui_command
        self.assertEqual(p("press 7 in calculator"), {"kind": "press", "control": "7", "app": "calculator", "role": None})
        self.assertEqual(p("switch to the Recording tab in Volume Control"),
                         {"kind": "select", "control": "recording", "app": "volume control", "role": "tab"})
        self.assertEqual(p("can you turn the mute audio button off in volume control")["kind"], "off")
        self.assertEqual(p("what can I press in calculator?")["kind"], "list")
        for chat in ("switch to coding", "tell me a joke", "switch to firefox", "what time is it"):
            self.assertIsNone(p(chat), chat)

    def test_spoken_names_and_windows(self):
        self.assertIn("7", spoken_control_names("seven"))
        self.assertIn("×", spoken_control_names("times"))
        wins = [{"app": "gnome-calculator", "pid": 9, "title": "Calculator"},
                {"app": "pavucontrol", "pid": 8, "title": "Volume Control"}]
        self.assertEqual(resolve_window(wins, "the calculator")["pid"], 9)
        self.assertEqual(resolve_window(wins, "volume control")["pid"], 8)
        self.assertIsNone(resolve_window(wins, "parliament"))
        self.assertEqual(gui_control.say_name("Clear Display [Escape]"), "Clear Display")
        self.assertEqual(gui_control.say_text("7×6−1"), "7 times 6 minus 1")


class ReplayBackend:
    """Recorded snapshots; actions change them the way the real apps did."""

    def __init__(self):
        self.apps = {9: copy.deepcopy(CALC), 8: copy.deepcopy(PAVU)}
        self.acted = []

    active = 8

    def windows(self):
        return {"ok": True, "windows": [{"app": "gnome-calculator", "pid": 9, "title": "Calculator",
                                         "active": self.active == 9},
                                        {"app": "pavucontrol", "pid": 8, "title": "Volume Control",
                                         "active": self.active == 8}]}

    def snapshot(self, pid, limit=400):
        return {"ok": True, "pid": pid, "controls": copy.deepcopy(self.apps[pid])}

    def act(self, pid, path, action, expect=None, value=None):
        node = next(c for c in self.apps[pid] if c["path"] == path)
        before = copy.deepcopy(node)
        self.acted.append((pid, display_name(node), action))
        if node["role"] == "page tab":
            for c in self.apps[pid]:
                if c["role"] == "page tab":
                    c["states"] = [s for s in c["states"] if s != "selected"] + (["selected"] if c is node else [])
        elif pid == 9:
            display = next(c for c in self.apps[9] if c["role"] == "editbar")
            key = {"×": "×", "=": "="}.get(node["name"], node["name"])
            display["text"] = "42" if key == "=" else display["text"] + key
        return {"ok": True, "before": before, "after": copy.deepcopy(node)}


display_name = gui_control.display_name


class FakeSupervisor:
    """The Supervisor's assess → queue → confirm flow over a real ComputerController."""

    def __init__(self, backend):
        self.cc = ComputerController(screenshot_dir=tempfile.mkdtemp(), gui_backend=backend,
                                     protected_pids=lambda: set())
        self.pending, self.confirmed, self.cancelled = {}, [], []

    def computer_action(self, action, payload, **kw):
        assessment = self.cc.assess(action, payload)
        if assessment.get("blocked"):
            return {"ok": False, **assessment}
        if assessment.get("confirmation_required"):
            cid = f"c{len(self.pending)}"
            self.pending[cid] = (action, payload, bool(assessment.get("critical")))
            return {"ok": False, "confirmation_required": True, "confirmation_id": cid,
                    "summary": assessment["summary"], "critical": bool(assessment.get("critical"))}
        return self.cc.execute(action, payload)

    def confirm_computer_action(self, cid, require_critical=None, timeout=None):
        action, payload, critical = self.pending.pop(cid)
        if critical and require_critical is not True:
            return {"ok": False, "error": "critical"}
        self.confirmed.append(cid)
        return self.cc.execute(action, payload)

    def cancel_computer_action(self, cid):
        self.cancelled.append(self.pending.pop(cid))
        return {"ok": True}


class VoiceCommandTests(unittest.TestCase):
    def setUp(self):
        src = (ROOT / "systems" / "chat.py").read_text()
        chat = next(n for n in ast.parse(src).body if isinstance(n, ast.ClassDef) and n.name == "Chat")
        keep = {"_handle_gui_command", "_app_action", "_say_local", "_app_tools", "_said_list",
                "_remember_gui_app", "_gui_act", "_run_gui_command", "_gui_request", "_cancel_gui_request",
                "_resolve_app_window", "_screen_only_windows"}
        body = [n for n in chat.body if isinstance(n, ast.FunctionDef) and n.name in keep]
        self.clock = [1000.0]
        ns = {"config": config, "re": re, "time": types.SimpleNamespace(monotonic=lambda: self.clock[0]),
              "State": types.SimpleNamespace(RESPONDING="responding"), "SupervisorError": RuntimeError,
              "logger": types.SimpleNamespace(info=lambda *a, **k: None), "spoken_app_name": spoken_app_name,
              **{n: getattr(gui_control, n) for n in ("ROLE_HINTS", "display_name", "find_controls",
                                                      "parse_gui_command", "resolve_window", "say_name",
                                                      "say_text", "spoken_control_names")}}
        exec(compile(ast.fix_missing_locations(ast.Module(body=[ast.ClassDef(
            name="Mini", bases=[], keywords=[], body=body, decorator_list=[])], type_ignores=[])), "mini", "exec"), ns)
        self.Mini = ns["Mini"]

    def chat(self, answers=()):
        c = self.Mini()
        c.backend = ReplayBackend()
        c.supervisor = FakeSupervisor(c.backend)
        apps = [AppEntry("org.gnome.Calculator", "Calculator", "", (), ("gnome-calculator",)),
                AppEntry("pavucontrol", "PulseAudio Volume Control", "Volume Control", (), ("pavucontrol",)),
                AppEntry("org.gnome.TextEditor", "Text Editor", "", (), ("gnome-text-editor",))]
        c._app_catalog = types.SimpleNamespace(find=lambda q: match_app(q, apps))
        c._window_probe = None
        c.spoken, c.questions, answers = [], [], list(answers)
        c.speak = c.spoken.append
        c._archive_local_exchange = lambda cmd, resp: None
        c._confirm_by_voice = lambda q, **kw: c.questions.append(q) or answers.pop(0)
        return c

    def test_tab_switch_is_free_and_verified(self):
        c = self.chat()
        self.assertTrue(c._handle_gui_command("switch to the Recording tab in Volume Control"))
        self.assertEqual(c.questions, [])
        self.assertEqual(c.spoken, ["Switched to Recording."])

    def test_calculator_one_ok_covers_the_task(self):
        c = self.chat(answers=[True])
        c._handle_gui_command("press 7 in calculator")
        self.assertEqual(len(c.questions), 1)
        self.assertEqual(c.questions[0], "I'll press 7 in Calculator. Say Hey BMO, yes, or just say no.")
        for text in ("press times", "press six", "press equals"):
            self.clock[0] += 10
            self.assertTrue(c._handle_gui_command(text), text)  # app remembered, no new question
        self.assertEqual(len(c.questions), 1)
        self.assertEqual(c.spoken[0], "Pressed 7. It shows 7.")
        self.assertEqual(c.spoken[1], "Pressed times. It shows 7 times.")
        self.assertEqual(c.spoken[-1], "Pressed equals. It shows 42.")
        self.assertFalse(c._handle_gui_command("turn on computer control"))  # BMO's own command, not Calculator's
        self.assertEqual([a[1] for a in c.backend.acted], ["7", "×", "6", "="])

    def test_ok_expires_and_no_cancels(self):
        c = self.chat(answers=[True, False])
        c._handle_gui_command("press 7 in calculator")
        self.clock[0] += config.GUI_TASK_OK_SECONDS + 1
        c._handle_gui_command("press 8 in calculator")
        self.assertEqual(len(c.questions), 2)
        self.assertEqual(c.spoken[-1], "Okay, I won't.")
        self.assertEqual(len(c.supervisor.cancelled), 1)
        self.assertEqual([a[1] for a in c.backend.acted], ["7"])

    def test_ambiguous_missing_and_not_open(self):
        c = self.chat(answers=[False])
        c._handle_gui_command("click mute audio in volume control")
        self.assertIn("Which one?", c.spoken[-1])
        self.assertTrue(c.spoken[-1].endswith("Say, for example, mute audio for Firefox."))
        c._handle_gui_command("click mute audio for firefox in volume control")
        self.assertEqual(c.questions, ["I'll press Mute audio in Volume Control. Say Hey BMO, yes, or just say no."])
        spoken = len(c.spoken)
        # Not on screen: the Main Brain's gui tool looks further (menus) and answers (v1.28.24).
        self.assertFalse(c._handle_gui_command("press the banana button in calculator"))
        self.assertTrue(c._gui_escalation)
        self.assertEqual(len(c.spoken), spoken)
        c._handle_gui_command("press save in text editor")
        self.assertEqual(c.spoken[-1], "Text Editor isn't open. Say Hey BMO, open Text Editor, first.")
        self.assertEqual(c.backend.acted, [])  # the Firefox mute was declined

    def test_unrelated_speech_goes_elsewhere(self):
        c = self.chat()
        for text in ("press the issue in parliament", "press equals", "switch to coding", "tell me a joke"):
            self.assertFalse(c._handle_gui_command(text), text)
        self.assertEqual(c.spoken, [])

    def test_live_2026_10_18_phrasings(self):
        c = self.chat(answers=[True])
        c.backend.active = 8
        c._handle_gui_command("press 7 in calculator")
        c._handle_gui_command("press times")
        self.assertTrue(c._handle_gui_command("press 6, then press equals."))
        self.assertEqual(c.spoken[-1], "Pressed 6, then equals. It shows 42.")
        self.assertEqual([a[1] for a in c.backend.acted], ["7", "×", "6", "="])
        self.assertTrue(c._handle_gui_command("on the calculator, press 7."))
        self.assertTrue(c._handle_gui_command("I'm giving you access to the GUI. Can you please click recording tab?"))
        self.assertEqual(c.spoken[-1], "Switched to Recording.")  # Calculator was recent; Volume Control in front
        self.assertEqual(len(c.questions), 1)

    def test_not_open_uses_the_owners_words(self):
        c = self.chat()
        c.backend.windows = lambda: {"ok": True, "windows": []}
        c._handle_gui_command("switch to the recording tab in volume control")
        self.assertEqual(c.spoken, ["Volume Control isn't open. Say Hey BMO, open Volume Control, first."])

    def test_app_in_front_is_used_only_if_it_has_the_control(self):
        c = self.chat()
        c.backend.active = 8
        self.assertTrue(c._handle_gui_command("switched to the recording tab"))
        self.assertEqual(c.spoken, ["Switched to Recording."])
        self.assertFalse(c._handle_gui_command("turn on computer control"))

    def test_stops_at_a_step_it_cannot_find(self):
        c = self.chat(answers=[True])
        c._handle_gui_command("press 7 and banana in calculator")
        self.assertEqual(c.spoken[-1], "I pressed 7, then stopped: I can't find banana in Calculator.")

    def test_list_controls(self):
        c = self.chat()
        c._handle_gui_command("what can I press in calculator")
        self.assertTrue(c.spoken[0].startswith("In Calculator I can press "))


class LiveTestRegressions(unittest.TestCase):
    """v1.28.23: what went wrong in the 2026-10-18 voice run of v1.28.22."""

    def test_main_cannot_claim_app_actions(self):
        from systems.grounding import fabricated_live_claim
        for said in ("Pressed 6, then equals. Result: 42.", "Closed Volume Control.", "PulseAudio is closed.",
                     "PulseAudio is already closed.", "I clicked the Recording tab for you.",
                     "Sound is back on Scarlett Solo.", "Volume is set to 50 percent.",
                     "Playing Jimmy Neutron, Season 1, Episode 1.", "Launching Super Smash Bros Melee."):
            self.assertEqual(fabricated_live_claim(said), "undone", said)
        for fine in ("Press 7, then equals.", "Hit equals to see the answer.", "Closed captions are on most videos.",
                     "Sound waves travel at 343 meters per second.", "Turn the volume down if it is too loud.",
                     "The knight opened the door.", "Sure, the answer is 42.",
                     "Playing with clay is fun for kids.", "Starting a garden takes patience."):
            self.assertIsNone(fabricated_live_claim(fine), fine)

    def test_misheard_wake(self):
        from voice_commands import VoiceSessionGate
        gate = VoiceSessionGate(wake_aliases=config.VOICE_WAKE_ALIASES)
        self.assertEqual(gate.parse("A Bemo close volume control."), (True, "close volume control."))
        # Live 2026-10-22, right after "close the game." (inside the follow-up window).
        self.assertEqual(gate.parse("Big Bemo Play Jimmy Neutron Season 1 Episode 1"),
                         (True, "Play Jimmy Neutron Season 1 Episode 1"))
        self.assertEqual(gate.parse("Hey Bemo, A Bemo switched to the recording tab."),
                         (True, "switched to the recording tab."))
        self.assertFalse(gate.parse("I want a bemo plush")[0])
        # Live 2026-10-28: the brain answered this one without playing anything.
        for heard in ("Heed Bemo Play Arietti", "Head Bemo, play Arietti", "He'd Bemo play Arietti",
                      "Hello BMO, play Arietti"):
            self.assertEqual(gate.parse(heard), (True, heard.split(" ", 2)[2].lstrip(", ")), heard)
        self.assertFalse(gate.parse("I'd like Bemo to play")[0])

    def test_pulse_audio_finds_volume_control(self):
        vol = AppEntry("pavucontrol", "PulseAudio Volume Control", "Volume Control", (), ("pavucontrol",))
        self.assertIs(match_app("pulse audio", [vol]).entry, vol)
        self.assertIs(match_app("volume control", [vol]).entry, vol)


if __name__ == "__main__":
    unittest.main()
