"""The 3D parts menu (2026-10-22): pictures, the page, and when it shows."""
import ast
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
import time
import unittest

ROOT = Path(os.environ.get("BEMO_TEST_CODE_ROOT", Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(ROOT))
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
from systems import part_gallery
SOURCE = (ROOT / "systems" / "chat.py").read_text()


def chat_attr(name):
    chat = next(n for n in ast.parse(SOURCE).body if isinstance(n, ast.ClassDef) and n.name == "Chat")
    keep = [n for n in chat.body if (isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") in
                                     {"_PART_WORDS", "_3D_REQUEST", "_PARTS_MENU_QUESTION"})]
    ns = {"re": re}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[ast.ClassDef(
        name="Mini", bases=[], keywords=[], body=keep, decorator_list=[])], type_ignores=[])), "m", "exec"), ns)
    return getattr(ns["Mini"], name)


class WhenTests(unittest.TestCase):
    def test_making_a_part_shows_it(self):
        made = chat_attr("_3D_REQUEST")
        for said in ("make a 20 millimeter cube with a 5 millimeter hole", "make me a phone stand 70 wide",
                     "make a cylinder 12 across with a 5 millimeter hole", "3d print a name tag"):
            self.assertTrue(made.search(said), said)
        for said in ("make a cup of tea", "what is the weather", "switch to engineering model", "tell me about boxes"):
            self.assertFalse(made.search(said), said)

    def test_asking_for_it(self):
        asked = chat_attr("_PARTS_MENU_QUESTION")
        for said in ("show me the parts", "show parts", "what parts can you make", "parts menu",
                     "show me the 3d parts catalog", "what can you 3d print"):
            self.assertTrue(asked.match(said), said)
        self.assertFalse(asked.match("show me the money"))

    def test_the_turn_shows_it_while_bmo_works_and_a_moment_after(self):
        self.assertIn("self._show_parts_menu(3600, busy=True)", SOURCE)
        self.assertIn("self._show_parts_menu(10)", SOURCE)
        self.assertIn('"parts_menu": now < float(getattr(self, "_parts_menu_until", 0.0))', SOURCE)


@unittest.skipUnless(shutil.which("openscad") and os.environ.get("DISPLAY"), "OpenSCAD and a display are needed")
class PictureTests(unittest.TestCase):
    def test_a_part_is_drawn_from_every_side(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(part_gallery.draw(Path(tmp), kinds={"cylinder"}), 1)
            self.assertEqual(len(part_gallery.frames("cylinder", Path(tmp))), part_gallery.FRAMES)
            self.assertTrue(part_gallery.ready(Path(tmp)))
            # 2026-10-23: only some shapes turned; a cylinder turning on its own axis looked still.
            pictures = part_gallery.frames("cylinder", Path(tmp))
            self.assertNotEqual(pictures[0].read_bytes(), pictures[part_gallery.FRAMES // 4].read_bytes())

    def test_the_menu_shows_plain_shapes(self):
        # 2026-10-23: every shape had a hole in it, and the cone was ruined by it.
        from systems import cad_shapes
        for kind, part in cad_shapes.SHAPES.items():
            self.assertEqual(cad_shapes.build(kind, part_gallery.plain(part.example))[2], 0, kind)


class PageTests(unittest.TestCase):
    def render(self, size, gallery, busy=False):
        import pygame
        pygame.init()
        pygame.display.set_mode((1, 1))
        from systems.render import FaceRenderer
        renderer = object.__new__(FaceRenderer)
        app = type("App", (), {})()
        app._screen, (app.SCREEN_WIDTH, app.SCREEN_HEIGHT) = pygame.Surface(size), size
        renderer._app = app
        with unittest.mock.patch.object(part_gallery, "folder", lambda: gallery):
            renderer._render_parts_menu({"parts_menu": True, "parts_menu_busy": busy}, time.monotonic())
        return app._screen

    def test_the_page_draws_with_and_without_pictures(self):
        import unittest.mock  # noqa: F401
        with tempfile.TemporaryDirectory() as tmp:
            for size in ((1920, 1080), (640, 480)):
                screen = self.render(size, Path(tmp))  # no pictures yet: names only
                self.assertEqual(screen.get_at((2, 2))[:3], (2, 10, 11))
            fake = Path(tmp) / "cylinder"
            fake.mkdir()
            import pygame
            for i in range(part_gallery.FRAMES):
                pygame.image.save(pygame.Surface((24, 18)), str(fake / f"{i:02d}.png"))
            (Path(tmp) / "stamp").write_text(part_gallery.stamp())
            self.render((1280, 720), Path(tmp), busy=True)


if __name__ == "__main__":
    unittest.main()
