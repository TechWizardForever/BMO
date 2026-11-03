"""What Whisper may write for the weather, sky and project commands -> what was said (owner 2026-11-03: every command
gets alternates, because Whisper mishears). For matching only; the logs and the brain keep the words as heard.

Each entry needs the words around it to be sure: "the whether", not any "whether" ("tell me whether it matters"), and
"next the clips", not any "clip". None has been heard live yet: they're what these words sound like. Add what the logs
show, and name where it was heard, as systems/voice_intents.py does.
"""
from __future__ import annotations

import re

_FAKE_RADAR = r"(?:raider|radder|rader|ratter|reader)"
_SHOW = r"(?:show|bring up|pull up|see|display|give me|open|check)(?: me)?(?: the| my| our)?"

_TABLE = tuple((re.compile(heard, re.IGNORECASE), meant) for heard, meant in (
    # The weather
    (r"\b(?:for|four|fore|foe)[ -]?cast(?:s)?\b(?! (?:members?|list|and)\b)", "forecast"),
    (r"\bthe (?:whether|wether)\b(?! or\b)", "the weather"),
    (r"\b(what(?:'?s| is)|how(?:'?s| is)|today'?s|tomorrow'?s|long[- ]?term|long[- ]range|extended|weekend) "
     r"(?:whether|wether)\b(?! or\b)", r"\1 weather"),
    (r"\b(?:whether|wether)(?= (?:forecast|report|radar|station|map|outlook|update)\b)", "weather"),
    (r"^\W*(?:whether|wether)\W*$", "weather"),
    (r"\blong[- ]?(?:turn|tern|terms?|time)(?= (?:forecast|weather|outlook)\b)", "long term"),
    (r"\blonger (?:forecast|outlook)\b", "long term forecast"),
    (r"\b(weather|extended|long term|long range|two[- ]week|2[- ]week|weekly|week'?s|\w+[- ]day) outlook\b", r"\1 forecast"),
    # The radar
    (rf"\b({_SHOW}) {_FAKE_RADAR}(?=\W*$)", r"\1 radar"),
    (rf"\b(weather|rain|storm) {_FAKE_RADAR}\b", r"\1 radar"),
    (r"^\W*(?:the )?(?:raider|radder|rader)\W*$", "radar"),
    (r"\bdoppler(?: radar)?(?=\W*$)", "radar"),
    (r"\bstorm radar\b", "rain radar"),
    (r"\b(?:rain(?:fall)?|storm|precipitation|weather) map\b", "rain radar"),
    (r"\bwhere(?:'?s| is) the (?:rain|storm)(?=(?: right now| now)?\W*$)", "show the rain radar"),
    (r"\bshow(?: me)? the (?:rain|storm)(?=\W*$)", "show the rain radar"),
    (r"^\W*what(?:'?s| is) (?:moving|coming) in\W*$", "show the rain radar"),
    # The sky
    (r"\b(?:spice|face|pace|ice) station\b", "space station"),
    (r"\bspace (?:nation|stations)\b", "space station"),
    (r"\bbase station\b(?=.*\b(?:pass|passes|passing|over|overhead)\b)", "space station"),
    (r"\bmoon(?:'?s)? (?:face|faze|fase|phrase|phases|phase)\b", "moon phase"),
    (r"\b(full|new) noon\b", r"\1 moon"),
    (r"\bmoon (rise|set)\b", r"moon\1"),
    (r"\bthe (?:ski|skies|sky'?s)\b", "the sky"),
    (r"\bnight (?:ski|skies)\b", "night sky"),
    (r"\bstar gazing\b", "stargazing"),
    (r"\bwhat plants\b(?=.*\b(?:tonight|visible|in the sky)\b)", "what planets"),
    (r"\b(next|upcoming|any|another|solar|lunar) (?:the|a|e|ee|i) clips?e?s?\b", r"\1 eclipse"),
    (r"\b(?:e|ee) clips?e?s?\b", "eclipse"),
    (r"\b(?:media|meter|metro|meaty|meteorite|meteoric|metier|meet your|meat your)s? showers?\b", "meteor shower"),
    (r"\bnorther(?:n)? (?:lights?|lites?|lines?|flights?)\b", "northern lights"),
    (r"\b(?:a roar a|auroras|aurora's)\b", "aurora"),
    (r"\b(space|sky) (?:a lurch(?:es)?|a lerts?|allerts?|allergies)\b", r"\1 alerts"),
    (r"\bskype (alerts?)\b", r"sky \1"),
    # The project list
    (r"\b(my|our) (?:prospects|products|objects|projex)\b", r"\1 projects"),
))


def put_right(text: str) -> str:
    said = str(text or "")
    for heard, meant in _TABLE:
        said = heard.sub(meant, said)
    return said
