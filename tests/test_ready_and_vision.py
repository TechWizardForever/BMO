"""'Ready' means ready; standby holds no model; vision brains get a screenshot (live 2026-10-19)."""
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


class ReadyTests(unittest.TestCase):
    def test_warm_up_runs_before_ready(self):
        # "ready" came 12 s before the warm-up finished; the first question waited 17 s.
        self.assertTrue(config.RUNTIME_WARM_ACTIVE_PROMPT_ON_START)
        src = (ROOT / "systems" / "chat.py").read_text()
        self.assertLess(src.index("                self._warm_runtime_prompt()\n"),
                        src.index("self.speak(config.VOICE_STARTUP_READY_ANNOUNCEMENT)"))

    def test_standby_unload_waits_for_a_switch_in_progress(self):
        src = (ROOT / "supervisor.py").read_text()
        fn = src[src.index("def _unload_active_model_for_standby"):src.index("def _hand_over_to_running_supervisor")]
        self.assertIn("service.unload_model()", fn)
        self.assertNotIn("service.unload_model(model)", fn)


class VisionScreenshotTests(unittest.TestCase):
    def mini(self, profile, shot=None):
        src = (ROOT / "systems" / "chat.py").read_text()
        chat = next(n for n in ast.parse(src).body if isinstance(n, ast.ClassDef) and n.name == "Chat")
        keep = [n for n in chat.body if isinstance(n, ast.FunctionDef)
                and n.name in ("_attach_screenshot_for_vision", "_prune_screenshots", "_scan_kind", "_scan_copy")
                or isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") in (
                    "_SCREEN_REQUEST", "_DETAILED_SCAN", "_QUICK_SCAN")]
        calls = []

        class Sup:
            def computer_action(self, action, payload, **kw):
                calls.append(action)
                return shot or {"ok": True, "path": "/tmp/bemo-screen-1.png"}
        ns = {"re": re, "Path": Path,
              "config": types.SimpleNamespace(ENABLE_TASK_LEDGER=False, VISION_QUICK_SCAN_MAX_WIDTH=640,
                                             VISION_DETAILED_SCAN_MAX_WIDTH=1280),
              "logger": types.SimpleNamespace(info=lambda *a, **k: None, warning=lambda *a, **k: None,
                                              exception=lambda *a, **k: (_ for _ in ()).throw(AssertionError(a))),
              "SupervisorError": RuntimeError}
        exec(compile(ast.fix_missing_locations(ast.Module(body=[ast.ClassDef(
            name="Mini", bases=[], keywords=[], body=keep, decorator_list=[])], type_ignores=[])), "mini", "exec"), ns)
        c = ns["Mini"]()
        c.active_profile = config.MODEL_PROFILES[profile]
        c.active_profile_name = profile
        c._selected_image_paths = []
        c._active_task_id = "t1"
        c.supervisor = Sup()
        return c, calls

    def screen(self):
        from PIL import Image
        import tempfile
        path = Path(tempfile.mkdtemp()) / "bemo-screen-1.png"
        Image.new("RGB", (1920, 1080), "white").save(path)
        return {"ok": True, "path": str(path)}

    def test_deep_vision_asked_about_the_screen_gets_a_screenshot(self):
        shot = self.screen()
        c, calls = self.mini("deep-vision", shot)
        c._attach_screenshot_for_vision("take a screenshot and tell me what's on the screen.")
        self.assertEqual(calls, ["screenshot"])
        self.assertEqual(len(c._selected_image_paths), 1)

    def test_quick_by_default_detailed_when_reading(self):
        from PIL import Image
        shot = self.screen()
        c, _ = self.mini("deep-vision", shot)
        c._attach_screenshot_for_vision("what's on the screen?")
        quick = c._selected_image_paths[0]
        self.assertTrue(quick.endswith("-quick.png"))
        self.assertEqual(Image.open(quick).size, (640, 360))
        self.assertTrue(c._offer_detailed_scan)
        for asked in ("do a detailed scan of the screen", "read the error on the screen",
                      "what does the screen say", "look closely at my desktop"):
            c, _ = self.mini("deep-vision", self.screen())
            c._attach_screenshot_for_vision(asked)
            self.assertTrue(c._selected_image_paths[0].endswith("-detailed.png"), asked)
            self.assertEqual(Image.open(c._selected_image_paths[0]).size, (1280, 720))
        c, _ = self.mini("deep-vision", self.screen())
        c._attach_screenshot_for_vision("give the screen a quick read")  # "quick" wins
        self.assertTrue(c._selected_image_paths[0].endswith("-quick.png"))

    def test_not_for_other_brains_or_other_questions(self):
        c, calls = self.mini(config.MAIN_MODEL_PROFILE)
        c._attach_screenshot_for_vision("what's on the screen")
        c2, calls2 = self.mini("deep-vision")
        c2._attach_screenshot_for_vision("what is a resistor")
        self.assertEqual((calls, calls2), ([], []))

    def test_an_image_already_chosen_is_kept(self):
        c, calls = self.mini("deep-vision")
        c._selected_image_paths = ["/home/u/board.jpg"]
        c._attach_screenshot_for_vision("what's on the screen")
        self.assertEqual((calls, c._selected_image_paths), ([], ["/home/u/board.jpg"]))


if __name__ == "__main__":
    unittest.main()
