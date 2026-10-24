"""Turn-safe, complete-sentence speech assembly (no arbitrary word slicing)."""
from __future__ import annotations
import re

# Do not mistake abbreviations for speech sentence boundaries.
_ABBREVIATIONS = frozenset({
    "mr.", "mrs.", "ms.", "dr.", "prof.", "sr.", "jr.",
    "vs.", "etc.", "e.g.", "i.e.", "approx.", "fig.", "no.",
    "st.", "mt.", "u.s.", "u.k.",
})
_END = re.compile(r"[.!?]+(?:[\"'”’)]*)?(?=\s|$)")
_COMPLETE = re.compile(r"[.!?]+(?:[\"'”’)]*)?\s*$")


def complete_sentences(text: str) -> tuple[list[str], str]:
    """Release only entire sentences; retain every unfinished character."""
    remaining = str(text or "")
    output: list[str] = []
    while remaining:
        match = None
        for candidate in _END.finditer(remaining):
            prefix = remaining[:candidate.end()].strip()
            last = prefix.split()[-1].strip("\"“”’)]*") if prefix.split() else ""
            if candidate.group().startswith(".") and last.lower() in _ABBREVIATIONS:
                continue
            # U.S.A. and single initials within a name are not sentences.
            if candidate.group().startswith(".") and candidate.end() < len(remaining) and re.fullmatch(r"(?:[A-Za-z]\.){1,5}", last):
                continue
            match = candidate
            break
        if match is None:
            break
        output.append(remaining[:match.end()].strip())
        remaining = remaining[match.end():].lstrip()
    return output, remaining


def ends_in_sentence(text: str) -> bool:
    # Ignore trailing Markdown emphasis markers but not arbitrary stage actions.
    return bool(_COMPLETE.search(str(text or "").rstrip(' *\n\t')))


def completed_prefix(text: str) -> str:
    parts, _ = complete_sentences(text)
    return " ".join(parts)


_LIST_MARKER = re.compile(r"^\s*(?:[-*•>#]+|\d+[.)])(?=\s)\s*")


def speakable_units(text: str, *, final: bool = False) -> tuple[list[str], str]:
    """Like complete_sentences, but a finished line is also a spoken unit.

    Models often answer in lists with no full stops (v1.28.8 Coding read a
    120-word list only after generation ended). Each completed line is spoken
    as soon as its newline arrives, without its bullet or number.

    While streaming (final=False) the last word is held until the next
    character arrives, so "Node." is not spoken before ".js" streams in, and
    a leading "1." list number is never treated as a sentence.
    """
    text = str(text or "")
    hold = ""
    if not final and text and not text[-1].isspace():
        cut = max(text.rfind(" "), text.rfind("\n"), text.rfind("\t"))
        text, hold = text[: cut + 1], text[cut + 1 :]
    units: list[str] = []
    if "\n" in text:
        done, text = text.rsplit("\n", 1)
        for line in done.split("\n"):
            line = _LIST_MARKER.sub("", line).replace("**", "").strip()
            if not line or line.startswith("```"):
                continue
            sentences, rest = complete_sentences(line)
            units.extend(sentences)
            if rest.strip():
                units.append(rest.strip())
    marker = _LIST_MARKER.match(text)
    prefix = text[: marker.end()] if marker else ""
    sentences, tail = complete_sentences(text[len(prefix):])
    if final and tail.strip():
        tail = _LIST_MARKER.sub("", prefix + tail) if not sentences else tail
    elif not sentences:
        tail = prefix + tail
    return units + sentences, tail + hold
