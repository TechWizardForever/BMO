"""The help center on BMO's screen (owner 2026-11-03): "Hey BMO, help" shows every subject, and "weather commands" goes
straight to one. It all comes from voice_commands.VOICE_COMMANDS, the same table as the printed list, so a new command
shows up here by itself. Only whole sentences match: "help me with my project" is for the brain.
"""
from __future__ import annotations

import re

import voice_commands
from systems.media_phrases import first
from systems.sound_alikes import put_right

HOME = "home"

# What each subject can be called. Short and whole words only, and nothing Whisper invents in silence, such as "you".
SUBJECT_WORDS = {
    "Talking to BMO": ("talking to bmo", "talking to", "talking to you", "talking", "the basics", "basics", "getting started", "waking you up"),
    "Movies and shows": ("movies and shows", "movies", "movie", "shows", "show", "tv", "tv shows", "films", "film", "videos", "video", "watching"),
    "Games": ("games", "game", "gaming", "video games"),
    "Downloads and USB sticks": ("downloads and usb sticks", "downloads", "download", "usb sticks", "usb stick", "usb"),
    "Timers and reminders": ("timers and reminders", "timers", "timer", "reminders", "reminder", "alarms", "alarm"),
    "Remembering things": ("remembering things", "remembering", "remember", "memory", "notes"),
    "Weather": ("weather", "whether", "forecast", "radar", "weather alerts", "weather warnings"),
    "The sky": ("the sky", "sky", "space", "moon", "stars", "planets", "astronomy", "space station", "iss", "space alerts"),
    "Sound": ("sound", "volume", "audio", "speakers", "speaker"),
    "Screen and text": ("screen and text", "screen", "display", "text", "text size", "crt", "calibration"),
    "Apps": ("apps", "app", "applications", "application", "programs"),
    "Computer control": ("computer control", "computer", "the computer", "pc"),
    "Brains": ("brains", "brain", "models", "model"),
    "3D parts": ("3d parts", "3d part", "3d printing", "3d", "parts", "part", "cad"),
    "Projects": ("projects", "project", "project folders"),
    "BMO himself": ("bmo himself", "bmo", "yourself", "himself", "updates", "settings"),
}
_BY_WORD = {word: subject for subject, words in SUBJECT_WORDS.items() for word in words}
_SUBJECT = "(?P<subject>" + "|".join(sorted(map(re.escape, _BY_WORD), key=len, reverse=True)) + ")"
_COMMANDS = r"(?:commands?|comments?)"  # Whisper writes "voice comments"
_THE = r"(?: the| your| my| all the| all)?"
_SHOW = r"(?:show|open|bring up|pull up|give me|display|see|list|read|go to|what are)"
# "What can you do?" alone (owner 2026-11-03). With more on it, "on this computer" or "about the leak", it isn't this.
_WHAT_YOU_DO = (r"what(?: all| else| things| (?:kind|kinds|sort|sorts) of things)? (?:can|could) (?:you|ya|bmo|bemo|beemo) do|"
                r"(?:(?:show|tell) me )?what you can do|what are you (?:able to do|capable of)|"
                r"what are your (?:abilities|skills|features)|what do you (?:do|know how to do)|"
                r"what can you help(?: me)? with|how can you help(?: me)?")

_HOME = re.compile(
    r"^(?:(?:i need )?help(?: me)?|help (?:center|centre|menu|screen|page)|voice " + _COMMANDS + r"|commands|command list|"
    rf"(?:the )?(?:command list|help (?:center|centre|menu|screen|page))|list of commands|"
    rf"{_SHOW}(?: me)?{_THE} (?:help|help (?:center|centre|menu|screen|page)|voice {_COMMANDS}|{_COMMANDS}|command list|list of commands)|"
    rf"what (?:voice )?commands (?:do you (?:know|have)|can i (?:say|use)|are there)|"
    rf"what can i (?:say|ask)(?: you)?|what do i say|how do i use you|{_WHAT_YOU_DO})$")
_ASKED_SUBJECT = tuple(re.compile(form) for form in (
    rf"^(?:{_SHOW}(?: me)?{_THE} )?(?:the )?{_SUBJECT} (?:voice )?{_COMMANDS}$",
    rf"^(?:{_SHOW}(?: me)?{_THE} )?(?:voice )?commands (?:for|about|on)(?: the| my)? {_SUBJECT}$",
    rf"^help (?:with|for|on)(?: the| my)? {_SUBJECT}$",
    rf"^(?:the )?{_SUBJECT} help$",
    rf"^what can i (?:say|ask)(?: you)? (?:about|for)(?: the| my)? {_SUBJECT}$",
    rf"^what (?:voice )?commands (?:are there|do you have|can i use|do you know) (?:for|about)(?: the| my)? {_SUBJECT}$",
))
_ON_SCREEN_NAME = re.compile(rf"^(?:the |my )?{_SUBJECT}(?: {_COMMANDS})?$")
_BACK = re.compile(r"^(?:go )?back(?: to (?:the )?(?:subjects|start|help|help center|menu|main menu|list))?$|"
                   r"^(?:the )?(?:main )?menu$|^(?:all )?(?:the )?subjects$|^help$|^start over$")


def _parse(said: str) -> str | None:
    if _HOME.match(said):
        return HOME
    for form in _ASKED_SUBJECT:
        found = form.match(said)
        if found:
            return _BY_WORD[found.group("subject")]
    return None


def _spoken(text: str) -> str:
    return put_right(str(text or ""))


def request(text: str) -> str | None:
    """HOME for "help", a subject for "weather commands", else None."""
    return first(_spoken(text), _parse)


def subject_named(text: str) -> str | None:
    """With the help center up, the subject's name alone is enough: "weather", "the sky"."""
    def named(said):
        found = _ON_SCREEN_NAME.match(said)
        return _BY_WORD[found.group("subject")] if found else None
    return first(_spoken(text), named)


def asks_back(text: str) -> bool:
    return bool(first(_spoken(text), _BACK.match))


def subjects() -> tuple[str, ...]:
    return voice_commands.categories()


def items(subject: str = HOME):
    """(cards for the screen, what he says, how many fit on a page)."""
    if subject == HOME:
        names = subjects()
        cards = [{"subject": name} for name in names]
        return cards, "Here's what I can do, by subject. Say a subject or a number.", len(names)
    cards = [{"say": voice_commands.printed_phrase(item.syntax), "does": re.sub(r"<([^>]+)>", r"[\1]", item.description)}
             for item in voice_commands.commands_for_category(subject)]
    spoken = re.sub(r"\bbmo\b", "BMO", subject.lower())
    spoken = re.sub(r"\busb\b", "USB", re.sub(r"\b3d\b", "3D", spoken))
    return cards, f"Here are the commands for {spoken}.", 3
