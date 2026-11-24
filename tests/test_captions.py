"""Captions (2026-11-24): what BMO says shows at the bottom of his screen while he says it, by voice, saved across
restarts. Also the lettering on BMO's screens: bold, bigger and outlined, because thin letters were hard to read on
the CRT."""
import ast
import os
from pathlib import Path
import re
import sys
import tempfile
import time
import unittest

os.environ["SDL_VIDEODRIVER"] = "dummy"
os.environ["SDL_AUDIODRIVER"] = "dummy"
ROOT = Path(os.environ.get("BEMO_TEST_CODE_ROOT", Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
import pygame
import config
import visual_smoke_test as smoke
from systems.interface import InterfaceSettings
from systems.render import FaceRenderer, OutlinedFont, ui_font

SOURCE = (ROOT / "systems" / "chat.py").read_text()


def command_handler():
    chat = next(n for n in ast.parse(SOURCE).body if isinstance(n, ast.ClassDef) and n.name == "Chat")
    keep = [n for n in chat.body if (isinstance(n, ast.FunctionDef) and n.name == "_handle_captions_command")
            or (isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "").startswith("_CAPTION"))]
    ns = {"re": re}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[ast.ClassDef(
        name="Mini", bases=[], keywords=[], body=keep, decorator_list=[])], type_ignores=[])), "m", "exec"), ns)
    mini = ns["Mini"]()
    mini.interface_settings = InterfaceSettings(Path(tempfile.mkdtemp()) / "interface.json")
    return mini


def normalized(said):
    text = re.sub(r"[^a-z0-9.\- ]+", " ", said.lower().strip())
    return re.sub(r"\s+", " ", text).strip()


class CaptionCommandTests(unittest.TestCase):
    def test_ways_to_turn_them_on_and_off(self):
        chat = command_handler()
        for said in ("Captions on.", "turn on captions", "Turn captions on", "turn on the captions", "show captions",
                     "captions mode", "closed captions on", "turn on closed captions", "show what you're saying",
                     "show me what you are saying on the screen", "caption what you say",
                     "Captains on.", "turn on the captains", "caption's on", "cap shuns on"):  # Whisper's spellings
            chat.interface_settings.set_captions(False)
            self.assertTrue(chat._handle_captions_command(normalized(said)).startswith("Captions on"), said)
            self.assertTrue(chat.interface_settings.captions, said)
        for said in ("Captions off.", "turn off captions", "turn the captions off", "hide the captions", "no captions",
                     "stop captions", "close captions", "Captains off", "stop showing what you're saying"):
            chat.interface_settings.set_captions(True)
            self.assertEqual(chat._handle_captions_command(normalized(said)), "Captions off.", said)
            self.assertFalse(chat.interface_settings.captions, said)

    def test_other_sentences_are_left_alone(self):
        chat = command_handler()
        for said in ("what's the weather", "the captain is on the ship", "turn on the TV", "show my music",
                     "pause", "what does caption mean", "close the game", "play Captain Toad"):
            self.assertIsNone(chat._handle_captions_command(normalized(said)), said)

    def test_saved_across_restarts_while_the_rest_starts_fresh(self):
        path = Path(tempfile.mkdtemp()) / "interface.json"
        settings = InterfaceSettings(path)
        self.assertFalse(settings.captions)  # off until asked for
        settings.set_display_mode("text")
        settings.set_captions(True)
        again = InterfaceSettings(path)
        self.assertTrue(again.captions)
        self.assertEqual(again.display_mode, "face")
        self.assertTrue(again.status()["captions"])

    def test_wired_into_speech(self):
        tts = SOURCE[SOURCE.index("    def _tts_worker(self):"):SOURCE.index("    def _audio_worker(self):")]
        self.assertLess(tts.index("self._put_audio(_Caption(str(item)))"), tts.index("self._put_audio(pcm_chunk)"))
        audio = SOURCE[SOURCE.index("    def _audio_worker(self):"):SOURCE.index("class Chat:")]
        self.assertIn("self.chat._set_caption(item.text)", audio)
        self.assertIn('self.chat._set_caption("")', audio[audio.index("finally:"):])  # cleared when he stops
        self.assertIn('status["caption"] = (getattr(self, "_caption_text", "") if self.interface_settings.captions else "")',
                      SOURCE)
        self.assertLess(SOURCE.index("response = self._handle_captions_command(normalized)"),
                        SOURCE.index('"text mode", "text only mode", "switch to text mode"'))


class ScreenTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        pygame.init()

    @classmethod
    def tearDownClass(cls):
        pygame.quit()

    def renderer(self, size):
        app = smoke.FakeApp()
        app.SCREEN_WIDTH, app.SCREEN_HEIGHT = size
        app._screen = pygame.Surface(size)
        renderer = FaceRenderer(app)
        renderer._saver_guard = type("Quiet", (), {"hold_off": lambda self: None})()
        return app, renderer

    def test_bold_outlined_lettering(self):
        self.assertTrue(config.UI_FONT.endswith("Silkscreen-Bold.ttf"))
        self.assertTrue((ROOT / config.UI_FONT).is_file())
        self.assertGreater(config.UI_FONT_SCALE, 1.0)
        font = ui_font(32)
        self.assertIsInstance(font, OutlinedFont)
        plain = pygame.font.Font(str(ROOT / config.UI_FONT), int(round(32 * config.UI_FONT_SCALE)))
        o = font.outline_width
        self.assertGreaterEqual(o, 2)
        self.assertEqual(font.size("HI 60"), (plain.size("HI 60")[0] + 2 * o, plain.size("HI 60")[1] + 2 * o))
        surface = font.render("HI 60", True, (255, 255, 255))
        self.assertEqual(surface.get_size(), font.size("HI 60"))
        colours = {surface.get_at((x, y))[:3] for x in range(surface.get_width()) for y in range(surface.get_height())
                   if surface.get_at((x, y))[3] == 255}
        self.assertIn(tuple(config.UI_TEXT_OUTLINE), colours)  # the dark edge is there
        self.assertIs(font.render("HI 60", True, (255, 255, 255)), surface)  # kept, not redrawn every frame

    def test_the_caption_stays_below_the_open_mouth_at_every_size(self):
        for size in ((640, 480), (1024, 768), (1280, 720), (1920, 1080)):
            app, renderer = self.renderer(size)
            app.chat.status.update(caption="Tomorrow looks cloudy with a high of sixty one and a low of forty one.",
                                   caption_started=time.monotonic())
            renderer.render()
            mouth = renderer._mouth_bottom()
            column = [app._screen.get_at((size[0] // 2, y))[:3] for y in range(size[1])]
            band_top = next(y for y in range(mouth, size[1]) if column[y] != tuple(config.FACE_BACKGROUND_COLOR))
            self.assertGreater(band_top, mouth, size)

    def test_long_sentences_turn_pages_instead_of_being_cut(self):
        app, renderer = self.renderer((1024, 768))
        text = ("Tomorrow looks cloudy with a high of sixty one and a low of forty one, and there's a small chance of rain "
                "in the afternoon, so maybe bring a light jacket just in case.")
        start = time.monotonic()
        pages = []
        for later in (0.0, 6.0, 12.0, 60.0):
            app._screen.fill((0, 0, 0))
            renderer._draw_caption(text, start, start + later)
            pages.append(pygame.image.tostring(app._screen, "RGB"))
        self.assertNotEqual(pages[0], pages[1])
        self.assertNotEqual(pages[1], pages[2])
        self.assertEqual(pages[2], pages[3])  # the last page stays until he's done

    def test_no_caption_no_change(self):
        app, renderer = self.renderer((1024, 768))
        renderer.render()
        face = pygame.image.tostring(app._screen, "RGB")
        app.chat.status.update(caption="", caption_started=None)
        renderer.render()
        self.assertEqual(pygame.image.tostring(app._screen, "RGB"), face)


if __name__ == "__main__":
    unittest.main()
