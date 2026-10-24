"""3D models from the Engineering brain (2026-10-20): OpenSCAD source -> .scad + .stl, measured.
A fake OpenSCAD writes a 20 mm cube; the last test uses the real one when it is installed."""
import os
from pathlib import Path
import shutil
import sys
import tempfile
import time
import unittest

ROOT = Path(os.environ.get("BEMO_TEST_CODE_ROOT", Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(ROOT))
from systems.engineering import ENGINEERING_TOOL_NAMES, EngineeringToolError, EngineeringToolkit, engineering_tool_schemas

FAKE_OPENSCAD = f"""#!{sys.executable}
import sys
import trimesh
out, source = sys.argv[sys.argv.index("-o") + 1], sys.argv[-1]
if "oops" in open(source).read():
    sys.stderr.write("ERROR: Parser error in file model.scad, line 1: syntax error\\n")
    sys.exit(1)
trimesh.creation.box(extents=[20, 20, 20]).export(out)
"""
CUBE_WITH_HOLE = "difference() { cube(20, center=true); cylinder(h=30, d=5, center=true, $fn=64); }"


class MakeModelTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        fake = self.tmp / "openscad"
        fake.write_text(FAKE_OPENSCAD)
        fake.chmod(0o755)
        self.out = self.tmp / "BMO-3D"
        self.kit = EngineeringToolkit(self.tmp / "engineering", cad_output_dir=self.out, openscad_binary=str(fake))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_saves_the_design_and_the_stl_and_measures_it(self):
        made = self.kit.make_cad_model({"source": "cube(20);", "name": "test cube"})
        self.assertEqual(made["stl_path"], str(self.out / "test_cube.stl"))
        self.assertTrue((self.out / "test_cube.scad").read_text().endswith("cube(20);\n"))
        self.assertIn("$fn = 64;", (self.out / "test_cube.scad").read_text())  # smooth curves by default
        self.assertEqual(made["through_holes"], 0)
        self.assertEqual(made["solid_fraction"], 1.0)
        self.assertEqual(made["size_mm"], [20.0, 20.0, 20.0])
        self.assertAlmostEqual(made["volume_mm3"], 8000.0, places=3)
        self.assertTrue(made["watertight"])

    def test_never_overwrites_an_earlier_model(self):
        self.kit.make_cad_model({"source": "cube(20);", "name": "bracket"})
        again = self.kit.make_cad_model({"source": "cube(20);", "name": "bracket"})
        self.assertEqual(Path(again["stl_path"]).name, "bracket-2.stl")
        self.assertTrue((self.out / "bracket.scad").read_text().endswith("cube(20);\n"))

    def test_a_part_without_its_holes_is_refused(self):
        with self.assertRaisesRegex(EngineeringToolError, "has 0 hole.*through_holes=1.*difference.*start below"):
            self.kit.make_cad_model({"source": "cube(20);", "name": "holed", "through_holes": 1})
        self.assertEqual(list(self.out.iterdir()), [])

    def test_a_part_of_the_wrong_size_is_refused(self):
        with self.assertRaisesRegex(EngineeringToolError, "measures 20 x 20 x 20 mm, but size_mm 40 x 20 x 40"):
            self.kit.make_cad_model({"source": "cube(20);", "name": "big", "size_mm": [40, 20, 40]})
        self.assertEqual(list(self.out.iterdir()), [])
        made = self.kit.make_cad_model({"source": "cube(20);", "name": "ok", "size_mm": [20, 20, 20.3]})
        self.assertEqual(made["size_mm"], [20.0, 20.0, 20.0])

    def test_a_designers_own_curve_setting_is_kept(self):
        self.kit.make_cad_model({"source": "$fn = 12;\ncube(20);", "name": "rough"})
        self.assertNotIn("$fn = 64", (self.out / "rough.scad").read_text())

    def test_designs_cannot_read_other_files(self):
        for source in ('import("/home/someone/secret.stl");', "include <other.scad>", "use <lib.scad>",
                       'surface(file="map.dat");', 'x = "//"; import("a.stl");'):
            with self.assertRaisesRegex(EngineeringToolError, "may not read other files"):
                self.kit.make_cad_model({"source": source, "name": "sneaky"})
        self.assertFalse(self.out.exists() and any(self.out.iterdir()))

    def test_an_openscad_error_leaves_no_files_and_says_why(self):
        with self.assertRaisesRegex(EngineeringToolError, "No STL was made: ERROR: Parser error"):
            self.kit.make_cad_model({"source": "cube(20) oops", "name": "broken"})
        self.assertEqual(list(self.out.iterdir()), [])

    def test_without_openscad_it_says_so(self):
        kit = EngineeringToolkit(self.tmp / "engineering", cad_output_dir=self.out, openscad_binary="no-such-openscad")
        self.assertIn("OpenSCAD is not installed", kit.execute("make_cad_model", {"source": "cube(1);", "name": "x"}))

    def test_an_open_project_gets_the_files(self):
        project = self.tmp / "garden" / "models"
        made = self.kit.make_cad_model({"source": "cube(20);", "name": "planter", "_output_dir": str(project)})
        self.assertEqual(made["stl_path"], str(project / "planter.stl"))

    def test_engineering_can_read_a_design_back(self):
        self.kit.make_cad_model({"source": CUBE_WITH_HOLE, "name": "block"})
        read = self.kit.inspect_file({"path": str(self.out / "block.scad")})
        self.assertIn("cylinder(h=30", read["text"])


class WiringTests(unittest.TestCase):
    def test_engineering_is_offered_the_tool(self):
        self.assertIn("make_cad_model", ENGINEERING_TOOL_NAMES)
        self.assertIn("make_cad_model", [t["function"]["name"] for t in engineering_tool_schemas()])

    def test_asking_for_a_part_offers_the_tool(self):
        from systems.context_policy import tool_names_for_request
        # Live 2026-10-20: "CONTEXT tools profile=engineering selected=3/22 names=['calculate_python', 'solve_math',
        # 'convert_units']" for this request, so no STL could be made.
        for heard in ("make a 20mm cube with a 5mm hole through the middle.",
                      "make the same cube but with an 8 millimeter hole", "make me a 3D printable phone stand"):
            self.assertIn("make_cad_model", tool_names_for_request(heard, "engineering"), heard)
        self.assertNotIn("make_cad_model", tool_names_for_request("what is the torque on a 2 inch shaft", "engineering"))
        self.assertNotIn("make_cad_model", tool_names_for_request("make a 20mm cube", "normal"))

    def test_bmo_picks_the_folder_not_the_model(self):
        chat = (ROOT / "systems" / "chat.py").read_text()
        self.assertIn('if k != "_output_dir"}', chat)
        self.assertIn('self.project_manager.active_workspace / "models"', chat)

    def test_the_installer_installs_openscad(self):
        packages = (ROOT / "installer" / "apt-packages.txt").read_text().split()
        self.assertIn("openscad", packages)
        self.assertIn("installer/apt-packages.txt", (ROOT / "install.sh").read_text())


@unittest.skipUnless(shutil.which("openscad"), "OpenSCAD is not installed")
class RealOpenSCADTests(unittest.TestCase):
    def test_a_20_mm_cube_with_a_5_mm_hole(self):
        with tempfile.TemporaryDirectory() as tmp:
            kit = EngineeringToolkit(Path(tmp) / "engineering", cad_output_dir=Path(tmp) / "out")
            made = kit.make_cad_model({"source": CUBE_WITH_HOLE, "name": "cube_with_hole"})
            self.assertEqual([round(v, 1) for v in made["size_mm"]], [20.0, 20.0, 20.0])
            # 8000 mm³ less the hole, about π·2.5²·20 ≈ 393 mm³.
            self.assertAlmostEqual(made["volume_mm3"], 8000 - 392.7, delta=5)
            self.assertTrue(made["watertight"])
            self.assertEqual(made["through_holes"], 1)

    def test_the_bracket_from_the_benchmark_is_caught(self):
        # Benchmark 2026-10-22: the upright stood beside the base, touching it only along an edge.
        with tempfile.TemporaryDirectory() as tmp:
            kit = EngineeringToolkit(Path(tmp) / "engineering", cad_output_dir=Path(tmp) / "out")
            with self.assertRaisesRegex(EngineeringToolError, "not one solid part"):
                kit.make_cad_model({"name": "bracket", "through_holes": 0, "size_mm": [40, 20, 40], "source":
                                    "union() {\n  cube([40, 20, 3]);\n  translate([40, 0, 3]) cube([3, 20, 40]);\n}"})
            with self.assertRaisesRegex(EngineeringToolError, "2 separate piece"):
                kit.make_cad_model({"name": "two", "source": "cube(10); translate([20, 0, 0]) cube(10);"})
            made = kit.make_cad_model({"name": "bracket", "through_holes": 0, "size_mm": [40, 20, 40],
                                       "source": "cube([40, 20, 3]); cube([3, 20, 40]);"})
            self.assertAlmostEqual(made["volume_mm3"], 4620, delta=1)

    def test_the_8_mm_cube_from_the_live_test_is_caught(self):
        # Live 2026-10-21, Engineering's 20mm_cube_8mm_hole.scad: the cylinder was added, not cut out.
        with tempfile.TemporaryDirectory() as tmp:
            kit = EngineeringToolkit(Path(tmp) / "engineering", cad_output_dir=Path(tmp) / "out")
            with self.assertRaisesRegex(EngineeringToolError, "has 0 hole"):
                kit.make_cad_model({"name": "20mm_cube_8mm_hole", "through_holes": 1, "source":
                                    "cube([20, 20, 20]);\ntranslate([10, 10, 0]) cylinder(h=20, r=4, center=false);"})



class ShapeCheckTests(unittest.TestCase):
    """Six basic shapes filled in by number (2026-10-22: simple shapes, simple changes, fast, accurate).
    Numbers that can't make the shape are refused before anything is drawn."""

    def kit(self):
        return EngineeringToolkit(Path(tempfile.mkdtemp()) / "e", cad_output_dir=Path(tempfile.mkdtemp()))

    def test_impossible_numbers_are_refused(self):
        for shape, settings, why in (("gear", {}, "no 'gear' shape"),
                                     ("block", {"width": 10, "depth": 10}, "block needs height"),
                                     ("cylinder", {"diameter": 5, "height": 3, "hole_diameter": 6}, "leave a wall"),
                                     ("cylinder", {"diameter": 9, "height": 3, "hole_diameter": 2, "wall": 1}, "not both"),
                                     ("block", {"size": 10, "holes": "corners", "hole_diameter": 4, "hole_inset": 1},
                                      "inside the block"),
                                     ("prism", {"sides": 20, "diameter": 5, "height": 2}, "sides must be from 3 to 12"),
                                     ("text", {"text": "BMO", "size": 5}, "text needs plate_width")):
            with self.assertRaisesRegex(EngineeringToolError, why):
                self.kit().make_cad_part({"shape": shape, "name": "x", "settings": settings})

    def test_offered_for_3d_requests(self):
        from systems.context_policy import tool_names_for_request
        names = tool_names_for_request("make a 20 millimeter cube with a 5 millimeter hole", "engineering")
        self.assertTrue({"make_cad_part", "make_cad_model"} <= names)
        self.assertIn("make_cad_part", ENGINEERING_TOOL_NAMES)


@unittest.skipUnless(shutil.which("openscad"), "OpenSCAD is not installed")
class ShapeTests(unittest.TestCase):
    def test_every_shape_and_feature(self):
        import math
        from systems.cad_shapes import SHAPES
        kit = EngineeringToolkit(Path(tempfile.mkdtemp()) / "e", cad_output_dir=Path(tempfile.mkdtemp()))
        cases = [  # shape, settings, size, holes through, volume (None: font-dependent); 64-sided circles, 1 %
            ("block", {"width": 40, "depth": 30, "height": 20}, [40, 30, 20], 0, 24000),
            ("cube", {"size": 20, "holes": "center", "hole_diameter": 8}, [20, 20, 20], 1, 8000 - math.pi * 16 * 20),
            ("plate", {"width": 60, "depth": 40, "height": 3, "holes": "corners", "hole_diameter": 4}, [60, 40, 3], 4,
             7200 - 4 * math.pi * 4 * 3),
            ("box", {"width": 50, "depth": 30, "height": 20, "wall": 2}, [50, 30, 20], 0, 30000 - 46 * 26 * 18),
            ("block", {"width": 40, "depth": 30, "height": 20, "radius": 5}, [40, 30, 20], 0,
             24000 - 4 * (25 - math.pi * 25 / 4) * 20),
            ("cylinder", {"diameter": 20, "height": 30}, [20, 20, 30], 0, math.pi * 100 * 30),
            ("spacer", {"diameter": 12, "height": 10, "hole_diameter": 5}, [12, 12, 10], 1, math.pi * (36 - 6.25) * 10),
            ("cup", {"diameter": 40, "height": 30, "wall": 2}, [40, 40, 30], 0, math.pi * (400 * 30 - 324 * 28)),
            ("cone", {"bottom_diameter": 30, "top_diameter": 0, "height": 30}, [30, 30, 30], 0, math.pi * 225 * 30 / 3),
            ("sphere", {"diameter": 30}, [30, 30, 30], 0, 4 / 3 * math.pi * 15 ** 3),
            ("prism", {"sides": 6, "diameter": 30, "height": 15}, [30, 30 * math.sqrt(3) / 2, 15], 0,
             3 * math.sqrt(3) / 2 * 15 ** 2 * 15),
            ("text", {"text": "BMO", "size": 12, "height": 2, "plate_width": 60, "plate_depth": 20, "hole_diameter": 4},
             [60, 20, 4], 1, None),
        ]
        self.assertEqual({__import__("systems.cad_shapes", fromlist=["x"]).kind_for(c[0]) for c in cases}, set(SHAPES))
        for shape, settings, size, holes, volume in cases:
            made = kit.make_cad_part({"shape": shape, "name": shape, "settings": settings})
            self.assertEqual([round(v, 1) for v in made["size_mm"]], [round(v, 1) for v in size], shape)
            self.assertEqual(made["through_holes"], holes, shape)
            if volume is not None:
                self.assertAlmostEqual(made["volume_mm3"], volume, delta=volume * 0.01, msg=shape)

    def test_cutouts_of_other_shapes(self):
        # 2026-10-22.
        import math
        kit = EngineeringToolkit(Path(tempfile.mkdtemp()) / "e", cad_output_dir=Path(tempfile.mkdtemp()))
        cases = (("block", {"width": 30, "depth": 30, "height": 10, "cutout": "square", "cutout_size": 10}, 1, 8000),
                 ("block", {"width": 40, "depth": 20, "height": 10, "cutout": "rectangle", "cutout_width": 20,
                            "cutout_length": 4, "cutout_depth": 5}, 0, 8000 - 20 * 4 * 5),
                 ("cylinder", {"diameter": 20, "height": 10, "cutout": "hexagon", "cutout_size": 9, "cutout_depth": 4}, 0,
                  math.pi * 100 * 10 - 3 * math.sqrt(3) / 2 * 4.5 ** 2 * 4),
                 ("block", {"width": 30, "depth": 30, "height": 10, "cutout": "rectangle", "cutout_width": 10,
                            "cutout_length": 10, "cutout_y": 12}, 0, 9000 - 10 * 8 * 10))  # a notch at the edge
        for shape, settings, holes, volume in cases:
            made = kit.make_cad_part({"shape": shape, "name": shape, "settings": settings})
            self.assertEqual(made["through_holes"], holes, settings)
            self.assertAlmostEqual(made["volume_mm3"], volume, delta=volume * 0.01, msg=str(settings))
        with self.assertRaisesRegex(EngineeringToolError, "2 separate piece"):  # a slot right across cuts it in two
            kit.make_cad_part({"shape": "block", "name": "cut_in_two", "settings": {
                "width": 30, "depth": 30, "height": 10, "cutout": "rectangle", "cutout_width": 10, "cutout_length": 40}})

    def test_the_last_part_is_remembered_for_simple_changes(self):
        # 2026-10-22.
        kit = EngineeringToolkit(Path(tempfile.mkdtemp()) / "e", cad_output_dir=Path(tempfile.mkdtemp()))
        kit.make_cad_part({"shape": "cube", "name": "cube_hole", "settings": {"size": 20, "holes": "center",
                                                                              "hole_diameter": 8}})
        self.assertEqual((kit.last_part["shape"], kit.last_part["settings"]["hole_diameter"]), ("block", 8))
        bigger = kit.make_cad_part({"shape": "block", "name": "cube_hole",
                                    "settings": dict(kit.last_part["settings"], hole_diameter=10)})
        self.assertTrue(bigger["stl_path"].endswith("cube_hole-2.stl"))  # the first one is kept
        chat = (ROOT / "systems" / "chat.py").read_text()
        self.assertIn('last_part = self._recent_3d_part() if hasattr(self, "_recent_3d_part") else None', chat)
        self.assertIn('allowed.update({"make_cad_part", "make_cad_model"})  # "make it taller" names no part', chat)

    def test_text_that_doesnt_fit_says_so(self):
        kit = EngineeringToolkit(Path(tempfile.mkdtemp()) / "e", cad_output_dir=Path(tempfile.mkdtemp()))
        with self.assertRaisesRegex(EngineeringToolError, "doesn't fit on its plate"):
            kit.make_cad_part({"shape": "text", "name": "tag", "settings": {
                "text": "SUPERCALIFRAGILISTIC", "size": 10, "plate_width": 30, "plate_depth": 12}})

@unittest.skipUnless(shutil.which("openscad"), "OpenSCAD is not installed")
class QuickChangeTests(unittest.TestCase):
    """2026-10-23: took Engineering three and a half minutes; BMO changes the
    one number himself."""
    def setUp(self):
        from systems.chat import Chat

        class FakeChat(Chat):
            active_profile = None  # a plain value here, not the brain lookup
        self.folder = Path(tempfile.mkdtemp())
        self.kit = EngineeringToolkit(Path(tempfile.mkdtemp()) / "e", cad_output_dir=self.folder)
        self.chat = FakeChat.__new__(FakeChat)
        self.chat.engineering_toolkit = self.kit
        self.chat.active_profile = {"engineering_tools": True}
        self.chat.project_manager = type("NoProject", (), {"active_workspace": None})()
        self.said = []
        self.chat._say_local = lambda request, line: self.said.append(line) or True
        self.kit.make_cad_part({"shape": "block", "name": "block", "settings": {
            "width": 20, "depth": 20, "height": 20, "holes": "center", "hole_diameter": 8}})

    def test_make_it_taller(self):
        self.assertTrue(self.chat._quick_part_change("Make it 30 millimeters tall."))
        self.assertEqual(self.kit.last_part["settings"]["height"], 30)
        self.assertEqual(self.kit.last_part["settings"]["hole_diameter"], 8)  # everything else stays
        self.assertTrue(self.kit.last_part["stl_path"].endswith("block-2.stl"))
        self.assertIn("Block 2 measures 20 by 20 by 30 millimeters with one hole through it", self.said[-1])

    def test_other_changes(self):
        for request, key, value in (("make the hole 5mm", "hole_diameter", 5), ("make it 2.5 mm thick", "height", 2.5),
                                    ("now make it 40 millimeters wide", "width", 40),
                                    ("change the block to 35 millimeters deep", "depth", 35)):
            self.assertTrue(self.chat._quick_part_change(request), request)
            self.assertEqual(self.kit.last_part["settings"][key], value, request)

    def test_unclear_requests_go_to_the_brain(self):
        for request in ("make it bigger", "make it 30 millimeters", "make a 20 millimeter cube",
                        "make the hole a bit bigger", "what is 30 millimeters in inches"):
            self.assertFalse(self.chat._quick_part_change(request), request)
        self.assertEqual(self.said, [])
        self.kit.last_part["at"] -= 3600  # an hour later it is forgotten, saved design included
        old = time.time() - 3700
        for design in self.folder.glob("*.scad"):
            os.utime(design, (old, old))
        self.assertTrue(self.chat._quick_part_change("make it 30 millimeters tall"))
        self.assertTrue(self.said[-1].startswith("There's no part to change yet."))
        self.assertEqual(self.kit.last_part["settings"]["height"], 20)

    def test_an_impossible_change_says_why(self):
        self.assertTrue(self.chat._quick_part_change("make the hole 25 millimeters"))
        self.assertTrue(self.said[-1].startswith("I couldn't make that change:"), self.said[-1])
        self.assertEqual(self.kit.last_part["settings"]["hole_diameter"], 8)

    def test_where_it_is_saved(self):
        from systems.chat import Chat
        import config
        made = {"stl_path": str(Path(config.CAD_OUTPUT_DIR).expanduser() / "cup.stl"), "size_mm": [60, 60, 50],
                "through_holes": 0}
        import json
        self.assertEqual(Chat._part_saved_line(json.dumps(made)),
                         "Done! Cup measures 60 by 60 by 50 millimeters, checked and saved in your BMO-3D folder.")
        made["stl_path"] = "/home/x/projects/garden/models/cup.stl"
        self.assertIn("the garden project's models folder", Chat._part_saved_line(json.dumps(made)))
        self.assertIsNone(Chat._part_saved_line("Tool error: EngineeringToolError: No STL kept"))

    def test_after_a_restart_the_newest_saved_part_is_changed(self):
        # Live 2026-10-24: BMO slept between making the cup and changing it; only memory knew its settings.
        self.kit.last_part = None
        self.assertTrue(self.chat._quick_part_change("make it 30 millimeters tall"))
        self.assertEqual(self.kit.last_part["settings"],
                         {"width": 20, "depth": 20, "height": 30, "holes": "center", "hole_diameter": 8})
        self.assertTrue(self.kit.last_part["stl_path"].endswith("block-2.stl"))

    def test_each_part_keeps_its_settings_in_its_design(self):
        design = (self.folder / "block.scad").read_text()
        self.assertTrue(design.startswith('// BMO part: {"settings": {"depth": 20, "height": 20, "hole_diameter": 8, '
                                          '"holes": "center", "width": 20}, "shape": "block"}\n'), design)
        (self.folder / "cup.scad").write_text("$fn = 64;\ndifference() {\n  cylinder(h=50, d=60);\n"
                                              "  translate([0, 0, 2]) cylinder(h=50, d=56);\n}\n")
        saved = {p["name"]: p for p in self.kit.saved_parts(self.folder)}
        self.assertEqual(saved["block"]["shape"], "block")
        self.assertEqual(saved["cup"]["source"], "$fn = 64; difference() { cylinder(h=50, d=60); "
                                                 "translate([0, 0, 2]) cylinder(h=50, d=56); }")
        chat = (ROOT / "systems" / "chat.py").read_text()
        self.assertIn("3D parts already saved here, newest first.", chat)

    def test_no_second_brain_round_after_a_saved_part(self):
        chat = (ROOT / "systems" / "chat.py").read_text()
        self.assertIn("part_saved = self._part_saved_line(output_text) or part_saved", chat)
        self.assertIn("final_response = part_saved", chat)
        self.assertIn('hasattr(self, "engineering_toolkit") and self._quick_part_change(request)', chat)
        self.assertIn("simple = part_requests.read(request)", chat)


if __name__ == "__main__":
    unittest.main()
