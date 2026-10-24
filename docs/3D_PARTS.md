# Simple 3D-printable parts by voice

Describe a simple part with its sizes in millimeters, like "Hey BMO, make a 20 millimeter cube with an
8 millimeter hole through the middle." If he understands every word, he fills in the shape himself and the part
is ready in a few seconds. If he doesn't, he tells you what he heard so you can say it again, and after that he
can hand it to the Engineering brain, which takes a few minutes.

Each part is saved as two files in the BMO-3D folder in your home folder (or in the open project's `models`
folder): the `.stl` for your slicer and the `.scad` design. Nothing gets overwritten.

You can change one size at a time afterwards: "make it 30 millimeters tall" (or wide, deep, thick, across),
"make the hole 5 millimeters", "make the walls 3 millimeters". The earlier version is kept.

## The six shapes

| Shape | Sizes | Built-in extras |
|---|---|---|
| block (also cube, box, plate) | width, depth, height | rounded upright edges; hollow with walls (open top); a hole through the middle or a hole in each corner |
| cylinder (also tube, spacer, washer, cup) | diameter, height | a hole through it; or hollow with walls (a cup) |
| cone | bottom diameter, top diameter (0 for a point), height | a hole through it |
| sphere | diameter | a hole through it (a bead) |
| prism (hexagon or other polygon) | sides, diameter across the corners, height | a hole through it |
| text | the words, letter size, how far they stand up, the plate they stand on | a keyring hole |

Some things to ask for:
- "a 20 millimeter cube with an 8 millimeter hole through the middle"
- "a plate 60 by 40 by 3 with a 4 millimeter hole in each corner"
- "a box 50 by 30 by 20 with 2 millimeter walls"
- "a spacer 12 across and 10 tall with a 5 millimeter hole"
- "a hexagon 30 across and 12 tall with a 10 millimeter hole"
- "a name tag that says BMO, 60 by 20, with a keyring hole"

"Hey BMO, show me the shapes" shows them turning on the screen. The same menu comes up while he makes a part.

## What BMO checks

Before saving, BMO measures the finished part: its outside size, how many holes go all the way through, and
whether it's one solid piece. If it doesn't match what you asked for, it isn't saved.
