"""GameCube games by voice (2026-10-20: five No-Intro .rvz games), with an empty fake library folder."""
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(os.environ.get("BEMO_TEST_CODE_ROOT", Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(ROOT))
from systems import games as g

FILES = ["Legend of Zelda, The - Collector's Edition (USA).rvz",
         "Legend of Zelda, The - Ocarina of Time & Master Quest (USA).rvz",
         "Legend of Zelda, The - The Wind Waker (USA).rvz",
         "Legend of Zelda, The - Twilight Princess (USA).rvz",
         "Super Smash Bros. Melee (USA) (En,Ja).rvz"]


class GameLibraryTests(unittest.TestCase):
    def setUp(self):
        pads = mock.patch("systems.gamepads.connected", return_value=[])  # no real SDL check here
        pads.start()
        self.addCleanup(pads.stop)
        self.dir = Path(tempfile.mkdtemp())
        self.lib = g.GameLibrary(self.dir, ROOT)
        self.lib.ensure_layout()
        for name in FILES:
            (self.lib.gamecube_dir / name).write_bytes(b"RVZ\x01")

    def tearDown(self):
        shutil.rmtree(self.dir)

    def test_titles_read_naturally(self):
        self.assertEqual([x.title for x in self.lib.scan()],
                         ["Super Smash Bros Melee", "The Legend of Zelda - Collector's Edition",
                          "The Legend of Zelda - Ocarina of Time & Master Quest",
                          "The Legend of Zelda - The Wind Waker", "The Legend of Zelda - Twilight Princess"])

    def test_spoken_names_find_the_game(self):
        for said, title in (("wind waker", "The Wind Waker"), ("zelda twilight princess", "Twilight Princess"),
                            ("ocarina of time", "Ocarina of Time"), ("master quest", "Ocarina of Time"),
                            ("collectors edition", "Collector's Edition"), ("smash bros", "Melee"),
                            ("melee", "Melee"), ("super smash brothers melee", "Melee")):
            game, _ = self.lib.find(said)
            self.assertIsNotNone(game, said)
            self.assertIn(title, game.title, said)
        game, candidates = self.lib.find("zelda")
        self.assertIsNone(game)  # four Zeldas: BMO asks which
        self.assertEqual(len(candidates), 4)

    def test_dolphin_opens_full_screen_and_closes_without_asking(self):
        # Live 2026-10-20: "Main." isn't a Dolphin settings file, so it opened windowed and asked before closing.
        self.lib.dolphin_prefix = lambda: ["dolphin-emu"]
        command = self.lib.command_for(self.lib.find("wind waker")[0])
        self.assertIn("Dolphin.Display.Fullscreen=True", command)
        self.assertIn("Dolphin.Interface.ConfirmStop=False", command)
        self.assertNotIn("Main.Display.Fullscreen=True", command)


class WiringTests(unittest.TestCase):
    def test_ways_to_close_a_game(self):
        from systems.media_phrases import close_game
        for said in ("close the game", "quit game", "stop the game", "exit current game", "close current game",
                     "post the game", "clothes the game", "closed the game"):  # misheard over Melee
            self.assertTrue(close_game(said), said)
        self.assertFalse(close_game("close the movie"))
        self.assertIn("media_phrases.close_game(command)", (ROOT / "systems" / "chat.py").read_text())

    def test_flatpak_dolphin_may_write_its_save_folder(self):
        # The Flatpak sees home read-only, so saves in ~/.bemo/games/dolphin-user were lost without this.
        self.assertIn('flatpak override --user --filesystem="$BEMO_DATA_DIR/games/dolphin-user" org.DolphinEmu.dolphin-emu',
                      (ROOT / "install.sh").read_text())


if __name__ == "__main__":
    unittest.main()
