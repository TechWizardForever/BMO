"""Deterministic guards against invented live data (weather, location, sensors).

v1.28.7 Main answered "what's the weather like today?" with an invented 72°F
"local weather feed" and later a "GPS signal from your wristband", with no
tools available. Prompts alone did not stop it, so these checks run in Python:
live-data questions are answered honestly before any model runs when no data
source is connected, and fabricated live-data sentences never reach speech.
"""
from __future__ import annotations

import re

from systems.sound_alikes import put_right
from systems.spoken_dates import ALONE_MONTH, FROM_NOW, SPOKEN_DATE

_WEATHER_QUESTION = re.compile(
    r"\b(weather|forecast)\b.*\b(today|tonight|tomorrow|now|right now|outside|this (?:week|weekend|morning|afternoon|evening)|like|going to|gonna)\b"
    r"|\b(?:is it|will it|gonna|going to)\s+(?:rain|snow|storm)\b"
    r"|\b(?:how (?:hot|cold|warm)|what(?:'s| is) the temperature)\b.*\b(outside|out there)\b"
    r"|^\W*(?:(?:the|what(?:'s| is) the|today's|tomorrow's) )?(?:weather|forecast|radar|weather report|weather station)\W*$"
    r"|\bhow(?:'s| is) the weather\b|\bweather (?:report|forecast|update)\b"
    r"|\b(?:what(?:'s| is)|how(?:'s| is)) the (?:weather|forecast) (?:in|for|at) [a-z]"
    r"|^\W*(?:the )?(?:weather|forecast) (?:in|for|at) [a-z]"
    r"|\bwhat(?:'s| is) it like (?:outside|out there)\b"
    r"|\bis it (?:cold|hot|warm|chilly|raining|snowing|sunny|windy|foggy) (?:outside|out)\b"
    # The long-term forecast and the screens (2026-11-02).
    # A date (2026-11-03), and "what will the weather be" (live: it went to the brain).
    rf"|\b(?:weather|forecast)\b.*\b(?:{SPOKEN_DATE}|{FROM_NOW}|(?:in|for) {ALONE_MONTH})"
    r"|\bwhat will the (?:weather|forecast) be\b"
    rf"|\b(?:will it|is it (?:going to|gonna)|it gonna|it going to)\b.*\b(?:rain(?:y|ing)?|snow(?:y|ing)?|storm(?:y|ing)?|cold|hot|warm|chilly|freezing|sunny|cloudy|clear|nice|windy)\b.*\b{SPOKEN_DATE}"
    rf"|\b(?:temperature|how (?:hot|cold|warm))\b.*\b{SPOKEN_DATE}"
    rf"|\b(?:any|will there be|chance of)\b.*\b(?:rain|snow|storms?)\b.*\b{SPOKEN_DATE}"
    rf"|\bwhat(?:'s| is| will be)? the (?:high|low)\b.*\b{SPOKEN_DATE}"
    rf"|\bwhat(?:'s| is)? it (?:going to be |gonna be )?like\b.*\b{SPOKEN_DATE}"
    rf"|\bhow(?:'s| is) {SPOKEN_DATE} look(?:ing)?\b"
    r"|\b(?:weather|forecast)\b.*\b(?:weeks?|weekends?|long[- ]?(?:term|range)|extended|(?:mon|tues|wednes|thurs|fri|satur|sun)day|"
    r"\w+[- ]days?)\b"
    r"|\b(?:long[- ]?(?:term|range)|extended|weekly|\w+[- ]day|week(?:end)?(?:'s)?|tomorrow(?:'s)?|today(?:'s)?)\s+(?:weather|forecast)\b"
    r"|\b(?:show|bring up|pull up|see|display|give me|open|tell me|check|get|read|look at)(?: me)?(?: (?:the|my|our|a|todays?|today's))*\s*"
    r"(?:weather|forecast|radar|rain radar|weather radar|weather station|weather map|radar map)\b(?! (?:app|man|channel)\b)"
    r"|\bweather (?:radar|station|map)s?\b|\brain radar\b|\bradar map\b"
    # Other ways to ask (2026-11-03: every command gets alternates).
    r"|\b(?:how(?:'s| is)|what(?:'s| is)|what does)\s+(?:the |this |next )?(?:coming )?week(?:end)?\s+(?:looking|look)\b"
    r"|\bwhat(?:'s| is) the weather doing\b|\bwhat(?:'s| is) it doing (?:outside|out there)\b"
    r"|\b(?:current|weather) conditions\b|\btwo weeks of (?:weather|forecast)\b"
    r"|^\W*what(?:'s| is) the (?:current )?temperature(?: (?:today|tonight|tomorrow|right now|now|this (?:morning|afternoon|evening)))?\W*$"
    r"|\bhow (?:hot|cold|warm|chilly) (?:is it (?:going to|gonna) (?:get|be)|will it (?:get|be)|does it get)\b"
    r"|\b(?:is|any) (?:the )?(?:rain|snow)\b.*\b(?:coming|today|tonight|tomorrow|week(?:end)?|on the way)\b"
    r"|\b(?:do|should|will) (?:i|we) (?:need|bring|take|grab|want) (?:an? )?(?:umbrella|jacket|coat|raincoat)\b",
    re.IGNORECASE,
)
# "Close the weather radar" closes the screen; it never opens one.
_CLOSING_WEATHER = re.compile(r"\b(?:close|clothes|cloze|hide|exit|quit|dismiss|get rid of|take away|turn off|shut off|put away) "
                              r"(?:the |this |that |my )?(?:\w+ )?(?:weather|forecast|radar|map)\b", re.IGNORECASE)
_LOCATION_QUESTION = re.compile(
    r"\bwhere am i\b|\bwhere are we\b|\bmy (?:current )?location\b|\bmy gps\b|\bwhat (?:city|town) am i in\b",
    re.IGNORECASE,
)

# Phrases that only appear when a model pretends to read live data it was
# never given. Ordinary explanations ("water boils at 100 °C") do not match.
_FABRICATED_LIVE_DATA = re.compile(
    r"\bchecking (?:the |your |my )?(?:local )?(?:weather|forecast|feed|sensors?|gps|cameras?)\b"
    r"|\b(?:local )?weather feed\b"
    r"|\bgps (?:signal|data|reading|says|shows)\b"
    r"|\bwristband\b"
    r"|\b(?:my|the) (?:sensors?|cameras?) (?:show|shows|say|says|read|reads|detect|detects|see)\b"
    r"|\bcurrently\s+-?\d{1,3}\s*(?:°|degrees\b)"
    r"|\b-?\d{1,3}\s*°\s*[FC]\b,?\s+(?:and\s+)?(?:sunny|cloudy|partly|clear|overcast|rain|raining|snow|windy|light breeze)\b",
    re.IGNORECASE,
)

HONEST_REPLIES = {
    "weather": "I don't have a weather source connected yet, so I can't tell you the real weather right now.",
    "location": "I don't have GPS or any location sensor, so I don't know where you are unless you tell me.",
    "fabricated": "Wait, I don't actually have live sensor or weather data, so I shouldn't guess.",
    "unsaved": ("Wait, I haven't actually saved anything. To set the weather location, say "
                "Hey BMO, set my weather location to, and then your town."),
    "undone": "Wait, I didn't actually do that yet. Ask me again, and I'll really do it this time.",
    "untimed": ("Wait, I didn't actually set that. Say it like: Hey BMO, set a timer for 10 minutes, or Hey BMO, "
                "remind me at 5 PM to water the basil."),
    "unmade": ("Wait, I didn't actually make a 3D file. Say the part with its sizes, like: Hey BMO, make a "
               "20 millimeter cube with an 8 millimeter hole through the middle."),
    "unswitched": ("Wait, I didn't actually switch models. Say Hey BMO, switch to, and the model name, "
                   "and I'll ask you to confirm."),
}

# Only Bemo's own code switches models, after a spoken yes. Live 2026-10-18:
# Main heard "switch the coding" and replied "Switched to coding mode."
_MODEL_SWITCH_CLAIM = re.compile(
    r"\b(?:switched|switching|swapped|changed|loaded|i'?m now|now (?:in|using|running))\b[^.!?]{0,30}"
    r"\b(?:coding|coder|engineering|vision|main|emergency)\b[^.!?]{0,20}\b(?:mode|model|brain|specialist|profile)\b",
    re.IGNORECASE,
)

# Live 2026-10-18: "Sutter Creek, CA — confirmed. Weather's sunny with a high of
# 68°F." and "Got it. Weather set for Salem" with no tool behind them.
_WEATHER_WORDS = re.compile(r"\b(?:weather|sunny|cloudy|overcast|fog|foggy|rain|rainy|raining|snow|snowing|"
                            r"forecast|breeze|breezy|windy|humid|drizzle|showers|storms?)\b", re.IGNORECASE)
_TEMPERATURE = re.compile(r"-?\d{1,3}\s*(?:°\s*[FC]?|degrees\b)|\b(?:high|low)s? (?:of|around|near) -?\d{1,3}\b", re.IGNORECASE)
_SETTING_CLAIM = re.compile(r"\b(?:weather|location|zip)\b[^.!?]{0,50}\b(?:is |are |has been |been )?"
                            r"(?:set|saved|updated|confirmed)\b|\b(?:set|saved|updated) (?:the |your |my )?weather\b",
                            re.IGNORECASE)
_SENTENCE = re.compile(r"(?<=[.!?])\s+")
# Only BMO's own timer command sets timers and reminders (2026-10-24); a brain saying so set nothing.
_TIMER_CLAIM = re.compile(
    r"\b(?:timer (?:is |has been )?(?:set|started)|reminder (?:is |has been )?set|alarm (?:is |has been )?set|"
    r"(?:i['’]?ll|i will) remind you|(?:i['’]?ve|i have) set (?:a|an|the|your) (?:\w+ )?(?:timer|reminder|alarm))\b",
    re.IGNORECASE,
)
# Only BMO's make_cad tools make 3D files, and they say so themselves. Live 2026-10-23 Main answered "make a 20mm
# cube ..." with "I'll generate the 3D model file for you now." twice, and nothing was made.
_MODEL_FILE_CLAIM = re.compile(
    r"\b(?:i['’]?ll|i will|i['’]?m going to|i am going to|let me|i['’]?m|i am|i['’]?ve|i have)\s+(?:now\s+|just\s+)?"
    r"(?:generat|creat|mak|export|sav|build|buil|render|print)\w*\s+(?:the|your|a|an|this|that|it)?\s*"
    r"(?:3d|stl|scad|openscad)\b"
    r"|^(?:now\s+)?(?:generating|creating|exporting|saving|rendering|building|making)\s+(?:the|your|a|an|this)\s+"
    r"(?:3d model|stl|scad)\b"
    r"|\b(?:3d model|stl|scad) (?:file )?(?:is|has been) (?:ready|saved|created|generated|made)\b",
    re.IGNORECASE,
)
# App/button actions happen only through BMO's own commands, which speak for
# themselves. Live 2026-10-18 the Main Brain said "Pressed 6, then equals.
# Result: 42." and "PulseAudio is closed." with nothing behind them.
_ACTION_CLAIM = re.compile(
    r"^(?:done[.!,]?\s*|okay[.!,]?\s*|ok[.!,]?\s*)?(?:i(?:'ve| have)?\s+)?(?:just\s+)?"
    r"(?:pressed|clicked|tapped|closed(?! captions?)|opened|launched|muted|unmuted|switched to the|turned (?:on|off)"
    # Live 2026-10-20: "Deleted `/home/bemo/BMO-code/trash-test.txt`. No more trash." with no delete run.
    # ("saved" stays out: BMO saves Coding's files himself, after the reply.)
    r"|deleted|removed|trashed|moved|copied|renamed|typed)\s+\S"
    r"|\b(?:is|are|was) (?:now |already )?(?:closed|muted|unmuted|switched|deleted|gone)\b"
    # Live 2026-10-18 Main said "Sound is back on Scarlett Solo." twice with nothing done.
    r"|\b(?:sound|audio|volume)\s+(?:is\s+)?(?:now\s+|back\s+)*(?:on|playing|going|coming|set to|at)\s+\S"
    r"|\bresult:\s*-?\d",
    re.IGNORECASE,
)


# Live 2026-10-22: Main answered "Big Bemo Play Jimmy Neutron..." with "Playing ..." and nothing played. A title
# (capital letter) or a movie/show/game must follow, so "Playing with clay is fun" is just talk.
# Live 2026-11-01: "USB ejected." with nothing behind it. Eject is BMO's own command, which speaks for itself.
_EJECT_CLAIM = re.compile(r"\b(?:usb|stick|thumb ?drive|flash ?drive|drive)\b[^.!?]{0,30}\b(?:ejected|unmounted|safe to (?:remove|"
                          r"unplug|pull))\b|\b(?:ejected|unmounted)\b[^.!?]{0,20}\b(?:usb|stick|drive)\b", re.I)
_PLAY_CLAIM = re.compile(r"^(?i:(?:okay|ok|sure|done)[.!,]?\s*)?(?i:now\s+)?(?i:playing|launching|starting|opening|loading)\s+"
                         r"(?:[A-Z0-9]|(?i:(?:the|your|that|this)\s+)?(?i:movie|show|film|video|episode|game|song)\b)")


_CLOCK_QUESTIONS = (
    ("timezone", re.compile(r"\btime ?zone (?:are|am|is) (?:you|we|i|it|this)\b|\b(?:your|my|our|the computer's|this computer's) time ?zone\b|\bwhat time ?zone\b(?!.*\b(?:code|script|program|convert)\b)", re.IGNORECASE)),
    ("date", re.compile(r"\bwhat(?:'s| is) (?:the |today's )?date\b|\bwhat day is (?:it|today)\b|\bwhat(?:'s| is) today\b", re.IGNORECASE)),
    ("time", re.compile(r"\bwhat time is it\b|\bwhat(?:'s| is) the time\b|\bwhat time do you have\b", re.IGNORECASE)),
)
CLOCK_TOPICS = {topic for topic, _ in _CLOCK_QUESTIONS}


def clock_reply(topic: str, now=None) -> str:
    """Answer time/date/time-zone questions from this PC's clock, never a model.

    v1.28.11 live test: Main claimed "my clock is set to UTC" with no evidence.
    """
    from datetime import datetime
    now = now or datetime.now().astimezone()
    if topic == "timezone":
        offset = now.utcoffset()
        hours = offset.total_seconds() / 3600 if offset is not None else 0.0
        sign = "plus" if hours >= 0 else "minus"
        amount = f"{abs(hours):g}"
        return f"This computer's clock is set to {now.tzname()}, which is UTC {sign} {amount}."
    if topic == "date":
        return f"Today is {now.strftime('%A')}, {now.strftime('%B')} {now.day}, {now.year}."
    hour = now.hour % 12 or 12
    return f"It's {hour}:{now.minute:02d} {'AM' if now.hour < 12 else 'PM'}."


def live_data_topic(user_text: str) -> str | None:
    """Return 'weather', 'location', or a clock topic for live real-world questions."""
    text = str(user_text or "")
    for topic, pattern in _CLOCK_QUESTIONS:
        if pattern.search(text):
            return topic
    said = put_right(text)
    if _WEATHER_QUESTION.search(said) and not _CLOSING_WEATHER.search(said):
        return "weather"
    if _LOCATION_QUESTION.search(text):
        return "location"
    return None


# Which tools can back each kind of claim (2026-10-31). Others (file searches, calculators, references)
# back none: the guard stays on for them.
CLAIM_EVIDENCE = {
    "fabricated": {"instrument_telemetry", "computer_control", "gui", "ocr_image", "crop_zoom_image", "select_image",
                   "select_project_image"},
    "unsaved": {"computer_control", "gui", "write_project_file", "replace_project_text", "set_project_fact",
                "supersede_project_fact"},
    "unswitched": {"request_capability_handoff"},
    "unmade": {"make_cad_part", "make_cad_model", "write_project_file", "computer_control"},
    "untimed": set(),  # timers are BMO's own commands, never a tool
    "undone": {"computer_control", "gui", "write_project_file", "replace_project_text", "run_project_check",
               "checkpoint_task", "make_cad_part", "make_cad_model"},
}


def unbacked_claim(text: str, tools_used) -> str | None:
    """The kind of claim in `text` that none of this turn's tools can back, else None."""
    kind = fabricated_live_claim(text)
    if kind and not (set(tools_used or ()) & CLAIM_EVIDENCE.get(kind, set())):
        return kind
    return None


def fabricated_live_claim(text: str) -> str | None:
    """Kind of claim ('fabricated' or 'unsaved') no tool backed, else None.

    Only called when no tool ran this turn; HONEST_REPLIES[kind] replaces it.
    """
    text = str(text or "")
    if _FABRICATED_LIVE_DATA.search(text):
        return "fabricated"
    for sentence in _SENTENCE.split(text):
        if _WEATHER_WORDS.search(sentence) and _TEMPERATURE.search(sentence):
            return "fabricated"
        if _SETTING_CLAIM.search(sentence):
            return "unsaved"
        if _MODEL_SWITCH_CLAIM.search(sentence):
            return "unswitched"
        if _MODEL_FILE_CLAIM.search(sentence):
            return "unmade"
        if _TIMER_CLAIM.search(sentence):
            return "untimed"
        if _EJECT_CLAIM.search(sentence):
            return "undone"
        if len(sentence.split()) <= 10 and (_ACTION_CLAIM.search(sentence.strip()) or _PLAY_CLAIM.search(sentence.strip())):
            return "undone"
    return None
