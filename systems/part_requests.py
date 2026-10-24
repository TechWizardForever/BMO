"""Simple 3D part requests BMO reads himself (2026-10-23); only the ones he can't read go to Engineering.

Live the same day Engineering took three and a half minutes of thinking to fill in "a 20 millimeter cube with an
8 millimeter hole through the middle". These requests name one of the six shapes and give its sizes in a few common
ways, so BMO fills in the numbers himself in a moment, and the part gets the same size, hole and one-piece checks.

A request is read only when every word of it is understood: anything left over (a word for a shape BMO doesn't
know, "slot", "two holes on one side", "rounded off a bit") means it goes to the Engineering brain as before, never
a part made from half the request.
"""
from __future__ import annotations

import re

_UNIT = r"(?: ?(?:mm|millimeters?|millimetres?))?"
_N = r"(\d+(?:\.\d+)?)" + _UNIT

_ONES = {w: i for i, w in enumerate("zero one two three four five six seven eight nine ten eleven twelve thirteen "
                                    "fourteen fifteen sixteen seventeen eighteen nineteen".split())}
_TENS = {w: 10 * i for i, w in enumerate("twenty thirty forty fifty sixty seventy eighty ninety".split(), 2)}

_ASK = re.compile(r"^(?:(?:please|can you|could you|would you|now|ok|okay|and|go ahead and|i want you to|i need you to) )*"
                  r"(?:make|create|design|build|model|print|3d print)(?: me| us)?(?: a| an)? ")

# The words for each shape; a prism's word may give its sides.
_NOUNS = {"cube": "block", "block": "block", "box": "block", "plate": "block", "brick": "block", "slab": "block",
          "cylinder": "cylinder", "tube": "cylinder", "spacer": "cylinder", "standoff": "cylinder",
          "washer": "cylinder", "disc": "cylinder", "disk": "cylinder", "puck": "cylinder", "rod": "cylinder",
          "peg": "cylinder", "cup": "cylinder", "container": "container", "cone": "cone", "sphere": "sphere",
          "ball": "sphere", "bead": "sphere", "prism": "prism", "hexagon": "prism", "hex": "prism",
          "pentagon": "prism", "octagon": "prism", "triangle": "prism"}
_SIDES = {"hexagon": 6, "hex": 6, "pentagon": 5, "octagon": 8, "triangle": 3}
_CUTOUTS = {"square": "square", "hexagon": "hexagon", "hex": "hexagon", "hexagonal": "hexagon", "triangle": "triangle",
            "triangular": "triangle", "octagon": "octagon", "octagonal": "octagon"}

# Words that may be left over once everything else is read.
_FILLER = set("a an the and with it that is its of please me for solid simple 3d printable part shape model stl through "
              "on outside overall straight in total size round circular flat square cylindrical hexagonal pointed "
              "open top hollow mm millimeter millimeters millimetre millimetres".split())


class _Unreadable(Exception):
    def __init__(self, why: str, words=()):
        super().__init__(why)
        self.words = list(words)


def _number(text: str) -> float | int:
    value = float(text)
    return int(value) if value.is_integer() else value


def _words_to_numbers(text: str) -> str:
    out, value = [], None
    for word in text.split():
        if word in _ONES or word in _TENS or (word == "hundred" and (value is not None or (out and out[-1] == "a"))):
            if word == "hundred":
                if value is None:
                    out.pop()  # "a hundred"
                value = (value or 1) * 100
            else:
                value = (value or 0) + (_ONES.get(word) if word in _ONES else _TENS[word])
            continue
        if value is not None:
            out.append(str(value))
            value = None
        out.append(word)
    if value is not None:
        out.append(str(value))
    return " ".join(out)


def _plain(request: str) -> str:
    text = str(request or "").lower().replace("-", " ")
    text = re.sub(r"(\d)\s*[x×]\s*(?=\d)", r"\1 by ", text)          # 60x40x3
    text = re.sub(r"(\d)(mm)\b", r"\1 mm", text)                       # 20mm
    text = re.sub(r"\.(?!\d)", " ", re.sub(r"[^a-z0-9. ]+", " ", text))
    return _words_to_numbers(re.sub(r"\s+", " ", text).strip())


class _Reader:
    def __init__(self, text: str):
        self.text, self.found = f" {text} ", {}

    def take(self, pattern: str, keys) -> None:
        """Every match of `pattern` (whole words) sets `keys` from its groups and is removed from the text."""
        def store(match):
            for key, value in zip(keys, match.groups()):
                if key is None or value is None:
                    continue
                value = _number(value) if re.fullmatch(r"\d+(?:\.\d+)?", value) else value
                if key in self.found and self.found[key] != value:
                    raise _Unreadable(f"two different {key}")
                self.found[key] = value
            return " "
        self.text = re.sub(rf"(?<= ){pattern}(?= )", store, self.text)


def read(request: str):
    """(shape, settings, name) for a simple part BMO can fill in himself, or None to ask the Engineering brain."""
    return _attempt(request)[0]


def asks_to_make(request: str) -> bool:
    """The request starts like "make a ..." (so a question about 3D printers is not one)."""
    return _ASK.match(_plain(request) + " ") is not None


def unread_words(request: str) -> list[str]:
    """The words BMO couldn't place in a part request, so he can say which (live 2026-10-23 Whisper heard
    "an 8mm cube hole"); empty when something else was the trouble, e.g. a size missing."""
    return _attempt(request)[1]


def _attempt(request: str):
    text = _plain(request)
    ask = _ASK.match(text + " ")
    if not ask:
        return None, []
    try:
        return _read(text[ask.end():]), []
    except _Unreadable as exc:
        return None, exc.words


def _read(text: str):
    r = _Reader(text)
    # Holes and cutouts first: "hexagon" in "a 9 mm hexagon hole" is the hole's shape, not the part's.
    r.take(rf"(?:with )?(?:a |an )?{_N} ({'|'.join(_CUTOUTS)}) (?:shaped )?hole(?: (?:straight |right )?through"
           rf"(?: the)?(?: middle| center| centre| it)?)?", ("cutout_size", "cutout"))
    r.take(rf"(?:with )?(?:a |an |4 )?{_N} (?:screw |mounting )?holes? (?:in|near|at|through) (?:each|every|all|the|"
           rf"all the|all 4|the 4) corners?", ("corner_hole",))
    r.take(rf"(?:the )?(?:holes? )?(?:cent(?:er|re)s? )?{_N} (?:in )?from (?:both|each|all|the|all the) "
           rf"(?:edges?|sides?)", ("hole_inset",))
    r.take(rf"(?:with )?(?:a |an |1 )?{_N} (?:round )?(?:hole|bore)(?: (?:straight |right )?(?:through|down|in)"
           rf"(?: the)? (?:middle|center|centre|it)| (?:straight |right )?through(?: it)?)?", ("hole",))
    r.take(rf"(?:with )?(?:a |an )?hole {_N} (?:across|wide|in diameter)(?: (?:straight )?through(?: the)?"
           rf"(?: middle| center| centre| it)?)?", ("hole",))
    r.take(rf"(?:with )?{_N} (?:thick )?walls?(?: and (?:a )?{_N} (?:thick )?(?:floor|bottom))?", ("wall", "floor"))
    r.take(rf"(?:with )?{_N} (?:thick )?walls? and (?:a )?(?:floor|bottom)", ("wall",))
    r.take(rf"(?:with )?(?:its )?(?:4 )?(?:vertical |upright )?(?:edges|corners) rounded(?: off)?(?: to| by| with)?"
           rf"(?: a)? {_N}(?: radius)?", ("radius",))
    r.take(rf"(?:with )?(?:a )?{_N} (?:radius )?(?:rounded |round )?(?:corners|edges)", ("radius",))
    r.take(rf"(?:with )?rounded (?:corners|edges) (?:of )?{_N}", ("radius",))
    r.take(rf"{_N} (?:across |wide |in diameter )?at the (?:bottom|base)", ("bottom",))
    r.take(rf"{_N} (?:across |wide |in diameter )?at the top", ("top",))
    r.take(r"(?:coming )?to a point|with a point", ())
    r.take(rf"{_N} by {_N} by {_N}", ("width", "depth", "height"))
    r.take(rf"{_N} by {_N}", ("width", "depth"))
    r.take(rf"(?:a )?diameter of {_N}|{_N} (?:across(?: the corners)?|in diameter|diameter)", ("across", "across"))
    r.take(rf"{_N} wide", ("wide",))
    r.take(rf"(?:a )?height of {_N}|{_N} (?:tall|high|in height|height|thick)", ("tall", "tall"))
    r.take(rf"{_N} long", ("long",))
    r.take(rf"{_N} deep", ("deep",))
    r.take(rf"(\d+) sided", ("sides",))

    # The shape's own word, and a size said right before or after it ("a 20 mm cube", "a cube 20 mm").
    nouns = [w for w in r.text.split() if w in _NOUNS]
    if len(nouns) > 1:
        # "a 20 mm cube with an 8 mm cube hole": the second shape word is the one that couldn't be placed.
        raise _Unreadable("more than one shape", nouns[1:2])
    if not nouns:
        raise _Unreadable("no shape")
    noun = nouns[0]
    kind = _NOUNS[noun]
    words = r.text.split()
    if kind == "container" or (noun in ("plate", "box") and re.search(rf"(?:round|circular) {noun}\b", r.text)):
        kind = "cylinder" if re.search(rf"(?:round|circular) {noun}\b", r.text) else "block"
    if noun in _SIDES:
        r.found.setdefault("sides", _SIDES[noun])
    r.take(rf"{_N} (?:(?:round|square|solid|flat|hollow|open top|simple) )*{noun}|{noun} (?:of )?{_N}", ("size", "size"))
    r.text = re.sub(rf" {noun} ", " ", r.text, count=1)
    words = r.text.split()
    if any(w not in _FILLER for w in words):
        raise _Unreadable("words left over", [w for w in words if w not in _FILLER and not w[0].isdigit()])
    if ("open" in words or "hollow" in words) and "wall" not in r.found:
        raise _Unreadable("hollow, but no wall size")
    if "top" in words and "open" not in words:
        raise _Unreadable("top of what")
    return kind if kind != "container" else "block", _settings(kind, r.found), noun


def _settings(kind: str, f: dict) -> dict:
    def need(*keys):
        if any(k not in f for k in keys):
            raise _Unreadable("a size is missing")

    def only(*keys):
        if set(f) - set(keys):
            raise _Unreadable("a feature this shape doesn't have")

    s = {}
    if f.get("floor") is not None and f.get("floor") != f.get("wall"):
        raise _Unreadable("a floor thicker or thinner than the walls")
    f.pop("floor", None)
    if kind == "block":
        only("width", "depth", "height", "size", "wide", "long", "deep", "tall", "hole", "corner_hole", "hole_inset",
             "wall", "radius", "cutout", "cutout_size")
        if "size" in f:
            if set(f) & {"width", "depth", "height", "wide", "long", "deep", "tall"}:
                raise _Unreadable("a cube size and other sizes")
            s.update(width=f["size"], depth=f["size"], height=f["size"])
        elif "width" in f:
            if "height" not in f:
                need("tall")
                f["height"] = f.pop("tall")
            if set(f) & {"wide", "long", "deep", "tall"}:
                raise _Unreadable("sizes given twice")
            s.update(width=f["width"], depth=f["depth"], height=f["height"])
        else:
            need("tall")
            across = [f[k] for k in ("long", "wide") if k in f]
            if "deep" in f:
                across.append(f["deep"])
            if len(across) != 2:
                raise _Unreadable("not two sizes across")
            s.update(width=across[0], depth=across[1], height=f["tall"])
        for mine, theirs in (("wall", "wall"), ("radius", "radius")):
            if mine in f:
                s[theirs] = f[mine]
        if "hole" in f and "corner_hole" in f:
            raise _Unreadable("a middle hole and corner holes")
        if "hole" in f:
            s.update(holes="center", hole_diameter=f["hole"])
        if "corner_hole" in f:
            s.update(holes="corners", hole_diameter=f["corner_hole"])
            if "hole_inset" in f:
                s["hole_inset"] = f["hole_inset"]
        elif "hole_inset" in f:
            raise _Unreadable("an inset with no corner holes")
    elif kind == "cylinder":
        only("across", "wide", "tall", "long", "hole", "wall", "cutout", "cutout_size")
        diameter = [f[k] for k in ("across", "wide") if k in f]
        height = [f[k] for k in ("tall", "long") if k in f]
        if len(diameter) != 1 or len(height) != 1:
            raise _Unreadable("one diameter and one height are needed")
        s.update(diameter=diameter[0], height=height[0])
        if "hole" in f:
            s["hole_diameter"] = f["hole"]
        if "wall" in f:
            s["wall"] = f["wall"]
    elif kind == "cone":
        only("across", "bottom", "top", "tall", "hole")
        bottom = [f[k] for k in ("across", "bottom") if k in f]
        if len(bottom) != 1:
            raise _Unreadable("one bottom diameter is needed")
        need("tall")
        s.update(bottom_diameter=bottom[0], top_diameter=f.get("top", 0), height=f["tall"])
        if "hole" in f:
            s["hole_diameter"] = f["hole"]
    elif kind == "sphere":
        only("across", "size", "hole")
        diameter = [f[k] for k in ("across", "size") if k in f]
        if len(diameter) != 1:
            raise _Unreadable("one diameter is needed")
        s["diameter"] = diameter[0]
        if "hole" in f:
            s["hole_diameter"] = f["hole"]
    elif kind == "prism":
        only("sides", "across", "tall", "hole", "cutout", "cutout_size")
        need("sides", "across", "tall")
        s.update(sides=f["sides"], diameter=f["across"], height=f["tall"])
        if "hole" in f:
            s["hole_diameter"] = f["hole"]
    else:
        raise _Unreadable("not a shape BMO fills in")
    if "cutout" in f:
        if "hole" in f or "corner_hole" in f:
            raise _Unreadable("a cutout and holes")
        s.update(cutout=_CUTOUTS[f["cutout"]], cutout_size=f["cutout_size"])
    return s
