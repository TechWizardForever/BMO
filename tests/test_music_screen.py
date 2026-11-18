"""While music plays (2026-11-17): pixel music notes bounce around a black screen in BMO's window. Going to sleep
stops the music."""
import os
from pathlib import Path
import sys
import unittest

os.environ["SDL_VIDEODRIVER"] = "dummy"
os.environ["SDL_AUDIODRIVER"] = "dummy"
ROOT = Path(os.environ.get("BEMO_TEST_CODE_ROOT", Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(ROOT))
import pygame

from systems.music_screen import NOTES, Notes

pygame.init()


class TheNotes(unittest.TestCase):
    def test_the_art_is_square_rows(self):
        for name, art in NOTES.items():
            self.assertEqual({len(row) for row in art}, {len(art[0])}, name)
            self.assertTrue(set("".join(art)) <= set("#+."), name)

    def test_they_stay_on_screen_and_change_colour_at_the_edges(self):
        for size in ((640, 480), (1920, 1080)):
            notes, surface = Notes(seed=3), pygame.Surface(size)
            notes.draw(surface)
            colours = [n["color"] for n in notes._notes]
            changed = False
            for _ in range(900):  # 30 seconds
                notes.update(1 / 30, *size)
                for note in notes._notes:
                    art = NOTES[note["kind"]]
                    pixel = notes.pixel(size[1], note["big"])
                    self.assertGreaterEqual(note["x"], 0)
                    self.assertGreaterEqual(note["y"], 0)
                    self.assertLessEqual(note["x"] + len(art[0]) * pixel, size[0] + 0.001)
                    self.assertLessEqual(note["y"] + len(art) * pixel, size[1] + 0.001)
                changed = changed or [n["color"] for n in notes._notes] != colours
            notes.draw(surface)
            self.assertTrue(changed)
            self.assertEqual(surface.get_at((0, 0))[:3], (0, 0, 0))  # a black screen behind them

    def test_always_moving(self):
        notes = Notes(seed=1)
        notes.update(0.1, 640, 480)
        before = [(n["x"], n["y"]) for n in notes._notes]
        notes.update(0.1, 640, 480)
        self.assertTrue(all(b != (n["x"], n["y"]) for b, n in zip(before, notes._notes)))


class GoingToSleep(unittest.TestCase):
    def test_sleep_stops_the_music(self):
        # 2026-11-17: the music played on after "go to sleep", with nothing on the screen to stop it.
        import supervisor
        from unittest import mock
        asked = []
        service = type("S", (), {})()
        service.music = type("M", (), {"handle": lambda self, request: asked.append(request)})()
        service.unload_model = lambda *a: None
        with mock.patch.object(supervisor.config, "STANDBY_KEEP_BRAIN_MINUTES", 0):
            supervisor._enter_standby(service)
        self.assertEqual(asked, [{"op": "stop"}])
        self.assertNotIn("ScreenKeeper", (ROOT / "supervisor.py").read_text())


class WhileHesAwake(unittest.TestCase):
    def frame(self, **status):
        sys.path.insert(0, str(ROOT / "tests"))
        import visual_smoke_test as smoke
        from systems.render import FaceRenderer
        from systems.chat import State
        app = smoke.FakeApp()
        app.SCREEN_WIDTH, app.SCREEN_HEIGHT = 640, 480
        app._screen = pygame.Surface((640, 480))
        base = {"idle_seconds": 0.0, "model_switch_active": False, "model_switch_generation": 0, "display_mode": "face",
                "indicator": "ready", "music": True}
        base.update(status)
        app.chat.visual_status = lambda: base
        app.chat.managed_display_active = lambda: False
        app.chat.state = State.READY
        renderer = FaceRenderer(app)
        renderer._render_face = lambda: setattr(renderer, "drew", "face")
        renderer._draw_indicator = lambda *a: None
        renderer.render()
        return getattr(renderer, "drew", "notes" if getattr(renderer, "_music_notes", None) else "other")

    def test_notes_while_music_plays_and_the_face_when_he_listens(self):
        self.assertEqual(self.frame(), "notes")
        self.assertEqual(self.frame(indicator="listening"), "face")
        self.assertEqual(self.frame(indicator="thinking"), "face")
        self.assertEqual(self.frame(music=False), "face")

    def test_the_music_answer_tells_the_screen(self):
        from unittest import mock
        from systems.chat import Chat
        chat = Chat.__new__(Chat)
        chat.supervisor = mock.Mock()
        chat.supervisor.request.return_value = {"ok": True, "playing": True}
        chat._music("play", paths=["/m/a.mp3"])
        self.assertTrue(chat._music_now)
        chat.supervisor.request.return_value = {"ok": True, "playing": False}
        chat._music("stop")
        self.assertFalse(chat._music_now)


if __name__ == "__main__":
    unittest.main()
