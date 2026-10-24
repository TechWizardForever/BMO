"""Words Whisper mishears: compare how they sound, not how they're spelled.

Used for app names ("sooner" -> Thunar) and the one- or two-word replies in
preview sessions ("Baker" -> bigger, live 2026-10-18). Only ever picks from a
short, known list, and only with a clear winner.
"""
from __future__ import annotations

import difflib
import re


def sound_key(text: str) -> str:
    """Rough sound of a word: 'Thunar', 'to nar' -> 'tanar'; 'Baker', 'bigger' -> 'bakar', 'bagar'."""
    t = re.sub(r"[^a-z]", "", str(text or "").lower())
    for a, b in (("th", "t"), ("ph", "f"), ("wr", "r"), ("ck", "k"), ("qu", "kw"), ("x", "ks"), ("z", "s")):
        t = t.replace(a, b)
    t = re.sub(r"[aeiouy]+", "a", t)
    return re.sub(r"(.)\1+", r"\1", t)


def closest(heard: str, choices, minimum: float = 0.75, margin: float = 0.15) -> str | None:
    """The choice that `heard` (one or two words) sounds like, or None."""
    words = re.findall(r"[a-z']+", str(heard or "").lower())
    if not words or len(words) > 2:
        return None
    key = sound_key(" ".join(words))
    if len(key) < 3:
        return None
    scored = sorted(((difflib.SequenceMatcher(None, key, sound_key(c)).ratio(), c) for c in choices), reverse=True)
    if not scored or scored[0][0] < minimum or (len(scored) > 1 and scored[0][0] - scored[1][0] < margin):
        return None
    return scored[0][1]
