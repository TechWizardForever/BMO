"""The Earth under the space station (2026-11-17): the real continents, drawn from simplified coastlines, on a globe
that slowly turns while the station flies over."""
import os
from pathlib import Path
import sys
import unittest

os.environ["SDL_VIDEODRIVER"] = "dummy"
os.environ["SDL_AUDIODRIVER"] = "dummy"
ROOT = Path(os.environ.get("BEMO_TEST_CODE_ROOT", Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(ROOT))
import pygame
from systems import earth, sky_screen


class TheMap(unittest.TestCase):
    def test_land_and_sea(self):
        for name, (lat, lon, land) in {"Sahara": (23, 10, True), "Kansas": (39, -98, True), "Brazil": (-10, -50, True),
                                       "Siberia": (60, 100, True), "Australia": (-25, 133, True), "India": (22, 78, True),
                                       "France": (46, 2, True), "Antarctica": (-85, 0, True), "Greenland": (72, -40, True),
                                       "Japan": (36, 138, True), "Britain": (53, -1.5, True), "Madagascar": (-19, 46.5, True),
                                       "mid Atlantic": (30, -40, False), "Pacific": (0, -140, False), "Indian Ocean": (-20, 80, False),
                                       "Mediterranean": (35, 18, False), "Black Sea": (43, 34, False), "Caspian": (42, 51, False),
                                       "Hudson Bay": (60, -85, False), "Gulf of Mexico": (25, -90, False), "Baltic": (58, 20, False),
                                       "Red Sea": (20, 38.5, False), "Persian Gulf": (27, 51, False)}.items():
            self.assertEqual(earth.is_land(lat, lon), land, name)

    def test_no_stray_lakes(self):
        lonely = [(lat, lon) for lat in range(-55, 70) for lon in range(-170, 179)
                  if not earth.is_land(lat + 0.5, lon + 0.5)
                  and sum(earth.is_land(lat + 0.5 + a, lon + 0.5 + b) for a in (-1, 0, 1) for b in (-1, 0, 1) if a or b) == 8]
        self.assertLessEqual(len(lonely), 2, lonely)


class TheGlobe(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        pygame.init()

    def test_it_turns(self):
        self.assertNotEqual(pygame.image.tobytes(earth.globe(64, 0), "RGBA"), pygame.image.tobytes(earth.globe(64, 90), "RGBA"))
        self.assertEqual(pygame.image.tobytes(earth.globe(64, 0), "RGBA"), pygame.image.tobytes(earth.globe(64, 360), "RGBA"))

    def test_land_moves_left_to_right(self):
        # East is to the right on the picture, and turning reads the map further west at each pixel, so the land
        # drifts east, left to right, the way the Earth turns seen from space.
        inside, rows, cols, light = earth._view(64)
        row = 40
        across = [int(cols[row, i]) for i in range(64) if inside[row, i]]
        self.assertEqual(across, sorted(across))
        self.assertIn("(cols - int(turn)) % 360", (ROOT / "systems" / "earth.py").read_text())

    def test_outside_the_disc_is_see_through(self):
        picture = earth.globe(64, 0)
        self.assertEqual(picture.get_at((0, 0)).a, 0)
        self.assertEqual(picture.get_at((32, 32)).a, 255)

    def test_the_station_screen_draws_at_every_moment(self):
        rect = pygame.Rect(10, 10, 600, 280)
        screen = pygame.Surface((620, 300))
        for now in (0.0, 7.3, 30.0, 59.9, 3600.0):
            sky_screen._flight(screen, rect, now)

    def test_no_location_is_used(self):
        self.assertNotIn("continent_at", (ROOT / "systems" / "earth.py").read_text())
        self.assertIn("def _flight(screen, rect, now):", (ROOT / "systems" / "sky_screen.py").read_text())


if __name__ == "__main__":
    unittest.main()
