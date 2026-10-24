"""One clean-up step for what the owner says (voice-command plan V2, approved 2026-10-19).

Whisper's text is cleaned once, here, before BMO's own commands are matched:
wake-word leftovers, fillers and politeness, verb forms, and a table of known
mishearings. Only matching uses the clean form; the model and the logs keep the
owner's words. Every table entry names where it was heard; tests/test_voice_intents.py
checks the owner's real sentences (tests/fixtures/voice/heard.jsonl).
"""
from __future__ import annotations

import re

_NAME = r"(?:bemo|beemo|bmo|be mo|bima|beema|beemow)"
# Wake leftovers before the command: "he bemo switched to coding", "hit bemo, switch to main",
# "bemo switched to maine" (live 2026-10-19).
# Fillers count only before the name: "a little quieter" keeps its "a" (V3 bench, 2026-10-19).
_WAKE_RESIDUE = re.compile(rf"^(?:(?:hey|he|hi|hit|a|okay|ok)\s+)*{_NAME}\s+")
# Words that don't change what is asked: "you need to switch to coding brain" (live 2026-10-19).
_FILLERS = re.compile(r"^(?:(?:can|could|would|will) you|please|i need you to|you need to|i want you to|"
                      r"okay so|so|um|uh|now|just)\s+")
_VERB_FORMS = {"switched": "switch", "switching": "switch", "switches": "switch", "swapped": "swap",
               "swapping": "swap", "changed": "change", "closed": "close", "opened": "open"}
# heard -> meant, for matching only. (where it was heard)
MISHEARINGS = (
    ("maine", "main"),          # "switched to Maine." 2026-10-19
    ("mane", "main"),
    ("codeine", "coding"),      # "switch to codeine" 2026-10-19
    ("code aim", "coding"),     # "switch to code aim." 2026-10-19
    ("cody", "coding"),         # "switch to Cody!" 2026-10-19
    ("code in", "coding"),
    ("codey", "coding"),
    ("coating", "coding"),
)


_CONTRACTIONS = (("what's", "what is"), ("where's", "where is"), ("it's", "it is"), ("that's", "that is"),
                 ("who's", "who is"), ("how's", "how is"), ("don't", "do not"), ("can't", "cannot"),
                 ("i'm", "i am"), ("let's", "let us"))


def normalize(text: str) -> str:
    """Chat._normalize_command_text: lower case, letters/digits/.-/space only."""
    text = re.sub(r"[^a-z0-9.\- ]+", " ", str(text or "").lower().strip())
    return re.sub(r"\s+", " ", text).strip()


def canonical(text: str, *, mishearings: bool = True) -> str:
    """The owner's words, cleaned for matching BMO's own commands.

    mishearings=False leaves the brain-name table out ("the weather in Maine" stays Maine).
    """
    # Contractions spelled out first: normalizing drops apostrophes ("what's" -> "what s"), which the
    # sound parser doesn't know (V3 bench, 2026-10-19).
    spelled = str(text or "").replace("\u2019", "'")
    for short, full in _CONTRACTIONS:
        spelled = re.sub(rf"\b{short}\b", full, spelled, flags=re.I)
    words = normalize(spelled).strip(" .!?")
    previous = None
    while words != previous:  # leftovers and fillers in any order
        previous = words
        words = _WAKE_RESIDUE.sub("", words)
        words = _FILLERS.sub("", words)
    words = " ".join(_VERB_FORMS.get(word, word) for word in words.split())
    for heard, meant in (MISHEARINGS if mishearings else ()):
        words = re.sub(rf"\b{re.escape(heard)}\b", meant, words)
    words = re.sub(r"\s+(?:please|now|thanks|thank you)$", "", words)
    return words.strip(" .")


def looks_like_switch(text: str, brain_names) -> bool:
    """A short "switch/swap to …" meant for a brain that nothing understood, so BMO says he didn't catch it
    (2026-10-19) instead of Main playing along ("Got it. What's the next move?").

    Only when it mentions a brain/model or names something close to a brain: "switch to the recording
    tab" still goes to Main, which operates apps.
    """
    import difflib
    words = canonical(text)
    found = re.match(r"^(?:switch|swap)(?:\s+(?:back|over|it|me|us))*(?:\s+(?:to|into))?\s+(?:the\s+)?(.+)$", words)
    if not found or len(words.split()) > 7:
        return False
    target = found.group(1)
    if re.search(r"\b(?:tab|window|app|sound|audio|volume|output|speaker|speakers|headphones|tv|hdmi|screen|"
                 r"page|button|calculator|browser|editor|terminal|files|folder|game)\b", target):
        return False  # apps, tabs and sound outputs belong to their own commands, or to Main's app tool
    if re.search(r"\b(?:brain|model|mode|specialist)\b", target):
        return True
    first = target.split()[0]
    return any(difflib.SequenceMatcher(None, first, name).ratio() >= 0.6 for name in brain_names)


_ASKS = re.compile(r"^(?:what|whats|what's|where|wheres|where's|when|who|whos|who's|how|hows|how's|why|which|is|are|"
                   r"can|could|will|would|do|does|did|tell|give|switch|open|close|turn|set|play|show|read|make|"
                   r"mute|unmute|press|start|stop|launch|calibrate|hide)\b", re.I)


_OPEN_AND = re.compile(r"^\W*(?:please\s+)?(?:open|launch|start|bring up)\s+(?:up\s+)?(?:the\s+)?(?P<app>[\w .'-]+?)"
                       r"\s*,?\s+and\s+(?:then\s+)?(?P<rest>(?:type|write|click|press|close|select|switch|save|paste|"
                       r"make|turn|go to|show|enter|choose)\b.*)$", re.I)


def split_open_and(text: str) -> list[str]:
    """"open the text editor and type hello" -> ["open the text editor", "in the text editor, type hello"]
    (live 2026-10-19: as one request, Main had no app tool because the editor wasn't open yet, and tried
    gedit, mousepad and nano in a row). BMO opens the app himself; then the rest names it."""
    found = _OPEN_AND.match(str(text or "").strip())
    if not found:
        return [str(text or "").strip()]
    app = found.group("app").strip()
    return [f"open the {app}", f"in the {app}, {found.group('rest').strip()}"]


def split_requests(text: str, limit: int = 4) -> list[str]:
    """Several questions or commands said in one breath, one by one (live 2026-10-19: "what time is it?
    What's the weather like? Where am I? ..." got only the time). Split only when every part is itself a
    question or command, so "I'm building a shelf. What wood should I use?" stays one request."""
    parts = [p.strip() for p in re.split(r"(?<=[?.!])\s+", str(text or "").strip()) if p.strip()]
    if not (2 <= len(parts) <= limit and all((part.endswith("?") or _ASKS.match(part)) and len(part.split()) >= 2
                                             for part in parts)):
        parts = [str(text or "").strip()]
    return [piece for part in parts for piece in split_open_and(part)]
