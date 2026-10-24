"""Simple 3D part requests BMO reads himself (2026-10-23). Every word must be understood, else the brain gets it."""
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

ROOT = Path(os.environ.get("BEMO_TEST_CODE_ROOT", Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
from systems.part_requests import asks_to_make, read, unread_words


class ReadTests(unittest.TestCase):
    def test_what_bmo_fills_in(self):
        cases = {
            # The live test, 2026-10-23.
            "make a 20mm cube with an 8mm hole through the middle.":
                ("block", {"width": 20, "depth": 20, "height": 20, "holes": "center", "hole_diameter": 8}),
            # docs/3D_PARTS.md's examples.
            "make a 20 millimeter cube with an 8 millimeter hole through the middle":
                ("block", {"width": 20, "depth": 20, "height": 20, "holes": "center", "hole_diameter": 8}),
            "make a plate 60 by 40 by 3 with a 4 millimeter hole in each corner":
                ("block", {"width": 60, "depth": 40, "height": 3, "holes": "corners", "hole_diameter": 4}),
            "make a box 50 by 30 by 20 with 2 millimeter walls":
                ("block", {"width": 50, "depth": 30, "height": 20, "wall": 2}),
            "make a spacer 12 across and 10 tall with a 5 millimeter hole":
                ("cylinder", {"diameter": 12, "height": 10, "hole_diameter": 5}),
            "make a hexagon 30 across and 12 tall with a 10 millimeter hole":
                ("prism", {"sides": 6, "diameter": 30, "height": 12, "hole_diameter": 10}),
            # Other ways of saying it.
            "can you make me a cube twenty five millimeters": ("block", {"width": 25, "depth": 25, "height": 25}),
            "3d print a 30 mm ball": ("sphere", {"diameter": 30}),
            "make a sphere 30 millimeters in diameter with a 5 mm hole through it":
                ("sphere", {"diameter": 30, "hole_diameter": 5}),
            "make a cone 30 across at the bottom, 10 across at the top and 25 tall":
                ("cone", {"bottom_diameter": 30, "top_diameter": 10, "height": 25}),
            "make a cone 20 across and 30 tall": ("cone", {"bottom_diameter": 20, "top_diameter": 0, "height": 30}),
            "make a washer 20 across, 2 thick, with a 10 millimeter hole":
                ("cylinder", {"diameter": 20, "height": 2, "hole_diameter": 10}),
            "make a round plate 50 across and 3 thick": ("cylinder", {"diameter": 50, "height": 3}),
            "make a cup 40 across and 30 tall with 2 mm walls": ("cylinder", {"diameter": 40, "height": 30, "wall": 2}),
            "make a block 60x40x10": ("block", {"width": 60, "depth": 40, "height": 10}),
            "make a block 60 long, 40 wide and 10 tall": ("block", {"width": 60, "depth": 40, "height": 10}),
            "make an 8 sided prism 20 across and 5 tall": ("prism", {"sides": 8, "diameter": 20, "height": 5}),
            "make a block 40 by 30 by 20 with rounded corners of 5 mm":
                ("block", {"width": 40, "depth": 30, "height": 20, "radius": 5}),
            "make a 30 mm cube with a 12 mm hexagon hole through it":
                ("block", {"width": 30, "depth": 30, "height": 30, "cutout": "hexagon", "cutout_size": 12}),
        }
        for request, (shape, settings) in cases.items():
            got = read(request)
            self.assertIsNotNone(got, request)
            self.assertEqual(got[:2], (shape, settings), request)

    def test_what_goes_to_the_brain(self):
        for request in (
                "make a cube",                                           # no size
                "make a 20 mm cube with a hole",                         # no hole size
                "make a 20 mm cylinder",                                 # 20 across or tall?
                "make two 20 mm cubes",
                "make a 20 mm cube and a 10 mm sphere",
                "make a 40 by 20 by 5 plate with a 6 mm slot",
                "make a 20 mm cube with 2 holes on one side",
                "make a cube 20 mm with the edges rounded off a bit",
                "make a hollow box 50 by 30 by 20",                      # how thick are the walls?
                "make a cylinder 20 across and 10 tall with a hexagon pocket 9 across the corners and 4 deep",
                "make a lid for a box that is 50 by 30 millimeters",
                "make a name tag that says BMO",
                "make a 20 mm cube with an 8 mm hole and 4 mm corner holes",
                "make an open top box 50 by 30 by 20 with 2 mm walls and a 3 mm floor",  # floor isn't the wall
                "what is a 20 millimeter cube",                          # not asking to make one
                "make it 30 millimeters tall"):
            self.assertIsNone(read(request), request)


class UnreadTests(unittest.TestCase):
    def test_bmo_can_say_which_words_he_couldnt_place(self):
        # Live 2026-10-23: Whisper heard "an 8mm cube hole".
        self.assertEqual(unread_words("make a 20mm cube with an 8mm cube hole through the middle."), ["cube"])
        self.assertEqual(unread_words("make a 40 by 20 by 5 plate with a 6 mm slot"), ["slot"])
        self.assertEqual(unread_words("make a cube"), [])  # a size is missing: no stray word to name
        self.assertTrue(asks_to_make("Make a 20mm cube with an 8mm cube hole"))
        self.assertFalse(asks_to_make("what's a good 3d printer"))

    def test_main_never_claims_a_3d_file(self):
        from systems.grounding import HONEST_REPLIES, fabricated_live_claim
        for claim in ("Got it. I’ll generate the 3D model file for you now.", "Generating the 3D model file now.",
                      "Your STL file is ready."):
            self.assertEqual(fabricated_live_claim(claim), "unmade", claim)
        for talk in ("I will write a Python script that exports an STL.", "Making 3D prints is fun.",
                     "I can make a 3D model if you switch to Engineering."):
            self.assertIsNone(fabricated_live_claim(talk), talk)
        self.assertIn("didn't actually make a 3D file", HONEST_REPLIES["unmade"])


class OfferTests(unittest.TestCase):
    """An unreadable part on a brain that can't make parts: BMO names the words and offers Engineering."""
    def chat(self, switched):
        from systems.chat import Chat

        class FakeChat(Chat):
            active_profile = None
        chat = FakeChat.__new__(FakeChat)
        chat.spoken, chat.answered = [], []
        chat.speak = chat.spoken.append
        chat._say_local = lambda request, line: chat.spoken.append(line) or True
        chat._switch_profile = lambda name: (True, "Switched") if switched else (False, "USER_DECLINED_MODEL_SWITCH")
        chat.respond = chat.answered.append
        return chat

    MISHEARD = "make a 20mm cube with an 8mm cube hole through the middle."

    def test_first_bmo_checks_he_heard_it_right(self):
        # 2026-10-23: confirm it didn't mishear before switching brains.
        chat = self.chat(switched=True)
        chat._part_needs_engineering(self.MISHEARD)
        self.assertEqual(chat.spoken, ["I heard: make a 20mm cube with an 8mm cube hole through the middle. "
                                       "I couldn't place the word cube. Did I hear you right? Say the part again."])
        self.assertEqual(chat.answered, [])

    def test_heard_the_same_again_offers_engineering(self):
        chat = self.chat(switched=True)
        chat._part_needs_engineering(self.MISHEARD)
        chat._part_needs_engineering(self.MISHEARD)
        self.assertIn("I heard it the same way, and I still couldn't place the word cube.", chat.spoken[-1])
        self.assertEqual(chat.answered, [self.MISHEARD])

    def test_no_asks_for_it_another_way(self):
        chat = self.chat(switched=False)
        chat._part_needs_engineering(self.MISHEARD)
        chat._part_needs_engineering(self.MISHEARD)
        self.assertEqual(chat.answered, [])
        self.assertIn("Say the part another way", chat.spoken[-1])

    def test_a_while_later_it_is_a_first_try_again(self):
        chat = self.chat(switched=True)
        chat._part_needs_engineering(self.MISHEARD)
        chat._unread_part_at -= 300
        chat._part_needs_engineering(self.MISHEARD)
        self.assertEqual(chat.answered, [])

    def test_the_reader_runs_before_the_offer(self):
        source = (ROOT / "systems" / "chat.py").read_text()
        self.assertLess(source.index("simple = part_requests.read(request)"),
                        source.index("self._part_needs_engineering(request)"))


@unittest.skipUnless(shutil.which("openscad"), "OpenSCAD is not installed")
class BenchmarkTests(unittest.TestCase):
    def test_the_benchmark_parts_bmo_reads_come_out_exactly_right(self):
        import bench_cad_models as bench
        from systems.engineering import EngineeringToolkit
        kit = EngineeringToolkit(Path(tempfile.mkdtemp()) / "e", cad_output_dir=Path(tempfile.mkdtemp()))
        made_here = []
        for request, size, holes, volume in bench.PARTS:
            got = read(request)
            if got is None:
                continue
            shape, settings, name = got
            made = kit.make_cad_part({"shape": shape, "name": name, "settings": settings})
            self.assertEqual(bench.judge(made, size, holes, volume), [], request)
            made_here.append(request)
        # The simple ones (cubes with a hole, spacer, plate with corner holes, open box, square hole, standoff,
        # round container, rounded block); the bracket, the pocket, the tray, the lid and the clamp go to the brain.
        self.assertEqual(len(made_here), 9, made_here)


if __name__ == "__main__":
    unittest.main()
