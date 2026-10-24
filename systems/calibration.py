"""Screen calibration by voice (settings plan S2): what a phrase means, and what it does.

Pure logic, no display: the chat session applies the result through
DisplayManager (whole-desktop fit with preview and auto-revert). Borders are
pixels at the current mode, one per edge; "smaller" adds to all four.
"""
from __future__ import annotations

import re

EDGES = ("left", "right", "top", "bottom")
_EDGE_WORDS = {"left": "left", "right": "right", "top": "top", "bottom": "bottom", "upper": "top", "lower": "bottom"}

START = re.compile(
    r"\b(?:calibrat\w*|fit|adjust|fix|centre|center|line up)\b.*\b(?:screen|display|picture|tv|monitor|crt)\b"
    r"|\b(?:screen|display|picture|tv|monitor|crt)\b.*\b(?:calibration|is cut off|are cut off|gets cut off|cut off)\b"
    r"|^(?:the\s+)?(?:left|right|top|bottom|sides?|edges?)\b.*\bcut off\b", re.I)


def should_start(text: str) -> bool:
    """A request to calibrate, not a question about screens ("how do I fix a cracked screen?")."""
    t = str(text or "").strip().lower()
    if re.match(r"^(?:how|what|why|when|where|who|which|is|are|does|do|should|did|was|were)\b", t):
        return False
    if re.search(r"\b(?:phone|laptop|cracked|broken|tablet|iphone|android)\b", t):
        return False
    return bool(START.search(t))


def size_of(text: str) -> float:
    if re.search(r"\b(?:a lot|lots|way (?:more|bigger|smaller))\b|\bmuch (?:more|bigger|smaller|larger)\b", text):
        return 3.0
    if re.search(r"\b(?:a little|a bit|a tad|slightly|tiny|just a)\b", text):
        return 0.5
    return 1.0


def parse_calibration(text: str) -> dict | None:
    """'move it left a little' -> {'do': 'move', 'edge': 'left', 'size': 0.5}."""
    t = re.sub(r"[^a-z0-9' ]+", " ", str(text or "").lower())
    t = re.sub(r"\s+", " ", t).strip()
    t = re.sub(r"^(?:okay|ok|um|uh|now|please|can you|could you|bemo|bmo)\s+", "", t)
    if not t:
        return None
    size = size_of(t)
    if re.fullmatch(r"(?:keep it|keep that|keep|save it|save|looks good|that's good|that looks good|good|perfect|"
                    r"done|that's it|yes|approve|approved|lock it in)(?: now)?", t):
        return {"do": "keep"}
    if re.fullmatch(r"(?:cancel|stop|never mind|nevermind|put it back|change it back|no|revert|quit|exit|forget it)", t):
        return {"do": "cancel"}
    if re.search(r"\b(?:undo|too far|go back|back one|that's too much|overshot)\b", t):
        return {"do": "undo"}
    if re.fullmatch(r"(?:reset|start over|from scratch|no borders|full size)(?: please)?", t):
        return {"do": "reset"}
    if re.fullmatch(r"(?:(?:a little |a bit |a lot |much )?more|again|keep going|one more|more please|"
                    r"(?:a little|a bit|a lot) more)", t):
        return {"do": "again", "size": size}
    if re.search(r"\b(?:numbers|settings|how big|what are the borders)\b", t):
        return {"do": "numbers"}
    if re.search(r"\b(?:help|what can i say|what do i say)\b", t):
        return {"do": "help"}
    mode = re.search(r"\b(\d{3,4})\s*(?:x|by)\s*(\d{3,4})\b", t)
    if mode:
        return {"do": "mode", "width": int(mode.group(1)), "height": int(mode.group(2))}
    rate = re.search(r"\b(\d{2,3})\s*(?:hertz|hz|herts)\b", t)
    if rate:
        return {"do": "rate", "rate": int(rate.group(1))}
    if re.search(r"\b(?:list|what|which)\b.*\bresolutions?\b", t):
        return {"do": "list_modes"}
    if re.search(r"\bresolution\b", t):
        if re.search(r"\b(?:lower|smaller|less|down|reduce)\b", t):
            return {"do": "resolution", "direction": -1}
        if re.search(r"\b(?:higher|bigger|more|up|increase|raise)\b", t):
            return {"do": "resolution", "direction": 1}
        if re.search(r"\b(?:next|another|different|other)\b", t):
            return {"do": "resolution", "direction": -1}
    # "the left side is cut off" / "I can't see the top": bring that edge in
    edge = next((_EDGE_WORDS[w] for w in re.findall(r"\b(left|right|top|bottom|upper|lower)\b", t)), None)
    if edge and re.search(r"\b(?:cut off|cutoff|missing|can't see|cannot see|hidden|off (?:the )?screen|bring in|"
                          r"too far out)\b", t):
        return {"do": "edge", "edge": edge, "sign": +1, "size": size}
    if edge and re.search(r"\b(?:gap|space|black bar|border|too far in|push out|empty)\b", t):
        return {"do": "edge", "edge": edge, "sign": -1, "size": size}
    move = re.search(r"\b(?:move|shift|push|slide|nudge)\b.*\b(left|right|up|down)\b", t) or \
        re.fullmatch(r"(?:(?:a little|a bit|a lot) )?(left|right|up|down)(?: (?:a little|a bit|a lot))?", t)
    if move:
        return {"do": "move", "edge": {"up": "top", "down": "bottom"}.get(move.group(1), move.group(1)), "size": size}
    if re.search(r"\b(?:smaller|shrink|zoom out|make it fit|fit it)\b", t):
        return {"do": "all", "sign": +1, "size": size}
    if re.search(r"\b(?:bigger|larger|grow|zoom in|enlarge)\b", t):
        return {"do": "all", "sign": -1, "size": size}
    if re.search(r"\bnarrower\b|\bthinner\b", t):
        return {"do": "pair", "edges": ("left", "right"), "sign": +1, "size": size}
    if re.search(r"\bwider\b", t):
        return {"do": "pair", "edges": ("left", "right"), "sign": -1, "size": size}
    if re.search(r"\bshorter\b|\bsquash\b", t):
        return {"do": "pair", "edges": ("top", "bottom"), "sign": +1, "size": size}
    if re.search(r"\btaller\b|\bstretch\b", t):
        return {"do": "pair", "edges": ("top", "bottom"), "sign": -1, "size": size}
    return None


def steps(width: int, height: int, size: float = 1.0) -> tuple[int, int]:
    """About 1% of the picture per nudge, at least 2 px."""
    return max(2, round(width * 0.01 * size)), max(2, round(height * 0.01 * size))


def nudge(borders: dict, action: dict, width: int, height: int) -> tuple[dict, str]:
    """New borders for a nudge, and a short spoken echo ('' = say nothing special)."""
    b = dict(borders)
    sx, sy = steps(width, height, float(action.get("size", 1.0)))
    step = {"left": sx, "right": sx, "top": sy, "bottom": sy}
    do = action["do"]
    if do == "all":
        for edge in EDGES:
            b[edge] = max(0, b[edge] + action["sign"] * step[edge])
        return b, "Smaller." if action["sign"] > 0 else "Bigger."
    if do == "pair":
        for edge in action["edges"]:
            b[edge] = max(0, b[edge] + action["sign"] * step[edge])
        word = {("left", 1): "Narrower.", ("left", -1): "Wider.", ("top", 1): "Shorter.", ("top", -1): "Taller."}
        return b, word[(action["edges"][0], action["sign"])]
    if do == "edge":
        edge = action["edge"]
        b[edge] = max(0, b[edge] + action["sign"] * step[edge])
        return b, f"{edge.capitalize()} edge {'in' if action['sign'] > 0 else 'out'}."
    if do == "move":
        toward = action["edge"]  # move left: the left border shrinks, the right one grows
        away = {"left": "right", "right": "left", "top": "bottom", "bottom": "top"}[toward]
        if b[toward] <= 0:
            return b, f"It's already against the {toward} edge."
        shift = min(step[toward], b[toward])
        b[toward] -= shift
        b[away] += shift
        return b, {"left": "Left.", "right": "Right.", "top": "Up.", "bottom": "Down."}[toward]
    raise ValueError(do)


def next_mode(modes, current: tuple[int, int], direction: int) -> tuple[int, int] | None:
    """The next smaller (-1) or larger (+1) mode by area, skipping same-area duplicates."""
    area = current[0] * current[1]
    ordered = sorted(set(tuple(m) for m in modes), key=lambda m: (m[0] * m[1], m[0]))
    if direction < 0:
        smaller = [m for m in ordered if m[0] * m[1] < area]
        return smaller[-1] if smaller else None
    larger = [m for m in ordered if m[0] * m[1] > area]
    return larger[0] if larger else None


def pick_rate(rates, preferred: float) -> float:
    """Keep the current refresh if the mode has it, else 60 Hz (CRT converters), else the mode's first."""
    rates = list(rates or ())
    if not rates:
        return 0.0
    for want in (preferred, 60.0, 59.94, 50.0):
        if want:
            close = min(rates, key=lambda r: abs(r - want))
            if abs(close - want) < 0.6:
                return close
    return rates[0]
