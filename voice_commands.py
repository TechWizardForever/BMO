"""Canonical Bemo voice-command catalog.

This module is intentionally dependency-light so the deterministic voice layer,
the permanent Supervisor, tests, and every model profile can all consume the
same command catalog without maintaining separate copies.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
import time

COMMAND_CATALOG_VERSION = "1.16"
CANONICAL_WAKE_PHRASE = "Hey Bemo"
CANONICAL_SLEEP_PHRASE = "go to sleep"


class VoiceSessionGate:
    """Pure wake-prefix gate used by Bemo's deterministic voice layer.

    The microphone/STT layer continues local transcription so the wake trigger
    can be detected. Normal commands begin with a configured wake alias: either
    "Hey Bemo" or Bemo/BMO by itself. A bare wake may authorize exactly one
    immediate unprefixed follow-up; unrelated background speech still never
    reaches the LLM or persistence.
    """

    def __init__(self, *, wake_aliases=("hey bemo",), timeout_seconds=90.0, followup_seconds=12.0):
        self.wake_aliases = tuple(str(x).strip() for x in wake_aliases if str(x).strip())
        self.timeout_seconds = max(1.0, float(timeout_seconds))
        self.followup_seconds = max(1.0, float(followup_seconds))
        self.awake = False
        self.last_activity_monotonic = 0.0
        self.followup_armed_until = 0.0

    @staticmethod
    def _normalize_spaces(text: str) -> str:
        return re.sub(r"\s+", " ", str(text).strip())

    @staticmethod
    def _phonetic_wake_match(cleaned: str):
        """Match common Whisper spellings of the invented name Bemo.

        The extra fuzzy spellings remain restricted to forms beginning with
        ``Hey``. Bare-name wake support comes only from the explicit alias list
        (Bemo/Beemo/BMO), avoiding broad phrase matching.
        """
        return re.match(
            r"^\s*hey[\s,.:;!\-]+(?:"
            r"bemo|beemo|bmo|"
            r"be{2,}\s*[- ]?\s*mo|"
            r"b\s*[- ]?\s*mo|"
            r"p\s*[- ]?\s*mo|"
            # Whisper's spellings of the owner's "Hey BMO" said straight into a command (bench on the
            # owner's own recordings, 2026-10-19: 8 of 45 phrases lost to these). Only after "Hey".
            r"bima|beema|beemow|bemow|b\s*-?\s*mount|d\s*-?\s*mo|b\.\s*m\.\s*o\.?"
            r")\b[\s,.:;!\-]*",
            cleaned,
            flags=re.IGNORECASE,
        )

    def _strip_wake(self, cleaned: str) -> tuple[bool, str]:
        # First accept the canonical configured forms exactly.
        for phrase in self.wake_aliases:
            match = re.match(
                rf"^\s*{re.escape(phrase)}\b[\s,.:;!\-]*",
                cleaned,
                flags=re.IGNORECASE,
            )
            if match:
                return True, cleaned[match.end():].strip()

        # Then tolerate the small set of phonetic spellings that Whisper
        # commonly emits for the invented name Bemo.
        match = self._phonetic_wake_match(cleaned)
        if match:
            return True, cleaned[match.end():].strip()
        # Whisper turns a quick "Hey BMO" into "A Bemo" / "Uh, BMO" (live
        # 2026-10-18: "A Bemo close volume control." reached the Main Brain).
        # "He Bemo switched to coding." (live 2026-10-19) kept the name in the command.
        # "Big Bemo Play Jimmy Neutron Season 1 Episode 1" (live 2026-10-22) went to Main, which said "Playing...";
        # "Heed Bemo Play Arietti" (live 2026-10-28) did the same. Any short word starting with h is Whisper's "Hey"
        # (he, hi, hay, hit, heed, head, he'd, hello).
        match = re.match(r"^\s*(?:a|uh|ah|eh|oh|um|okay|ok|big|h[a-z']{0,4})[\s,.]+(?:bemo|beemo|bmo)\b[\s,.:;!\-]*",
                         cleaned, flags=re.IGNORECASE)
        if match:
            return True, cleaned[match.end():].strip()
        return False, cleaned

    # Words that only ever appear in Bemo's name, its wake phrase, or Whisper's
    # truncated tail of it ("Hey B"). A remainder made solely of these is a wake,
    # never a command. Ordinary words such as "be" or "more" are deliberately
    # absent so "Hey BMO, be more careful" still reaches the model.
    _WAKE_RESIDUE = re.compile(r"^(?:hey|hay|bemo|beemo|bmo|b|mo|bee+mo|be+-?mo)$", re.IGNORECASE)

    @classmethod
    def _is_wake_residue(cls, text: str) -> bool:
        tokens = re.findall(r"[a-z'\-]+", str(text or "").lower())
        return bool(tokens) and all(cls._WAKE_RESIDUE.match(token) for token in tokens)

    def parse(self, text: str) -> tuple[bool, str]:
        """Return (woke, command) without changing session state.

        Every adjacent wake form is consumed, however many Whisper produced.
        A remainder that is only wake-name residue counts as wake-only ('').
        """
        cleaned = self._normalize_spaces(text)
        woke, remainder = self._strip_wake(cleaned)
        if not woke:
            return False, cleaned
        while remainder:
            another, following = self._strip_wake(remainder)
            # Each strip consumes characters, so this always terminates.
            if not another or len(following) >= len(remainder):
                break
            remainder = following
        if self._is_wake_residue(remainder):
            remainder = ""
        return True, remainder

    # Only for answering a question BMO just asked: the start of "Hey BMO" is
    # often clipped when the owner answers right as BMO stops talking (live
    # 2026-10-18: "Yemo Yes", "Nemo Yes"). Never used to wake BMO.
    _CLIPPED_WAKE = re.compile(r"^\s*(?:[a-z]{0,2}[ei]+e*mo|b+e*mo|p+mo|k?bmo|e?mo)\b[\s,.:;!\-]*", re.IGNORECASE)

    def clipped_wake(self, text: str) -> tuple[bool, str]:
        """(True, rest) for a wake whose first sound was cut off; else parse()."""
        woke, remainder = self.parse(text)
        if woke:
            return True, remainder
        match = self._CLIPPED_WAKE.match(self._normalize_spaces(text))
        if match and match.end() < len(self._normalize_spaces(text)):
            return True, self._normalize_spaces(text)[match.end():].strip()
        return False, self._normalize_spaces(text)

    def has_wake_prefix(self, text: str) -> bool:
        cleaned = self._normalize_spaces(text)
        woke, _ = self._strip_wake(cleaned)
        return woke

    def is_active(self, *, now=None) -> bool:
        if not self.awake:
            return False
        now = time.monotonic() if now is None else float(now)
        if now - self.last_activity_monotonic > self.timeout_seconds:
            self.sleep()
            return False
        return True

    def wake(self, *, now=None) -> None:
        self.awake = True
        self.last_activity_monotonic = time.monotonic() if now is None else float(now)

    def sleep(self) -> None:
        self.awake = False
        self.last_activity_monotonic = 0.0
        self.followup_armed_until = 0.0

    def apply(self, text: str, *, now=None, started_at=None) -> str | None:
        """Return an accepted command, '' for wake-only, or None otherwise.

        A normal command needs an explicit wake alias such as "Hey Bemo" or
        "Bemo"/"BMO". A wake with no remainder arms exactly one immediate
        unprefixed follow-up, which helps when Whisper hears the name separately
        from the rest of a long sentence.
        """
        cleaned = self._normalize_spaces(text)
        if not cleaned:
            return None
        now = time.monotonic() if now is None else float(now)
        # A prolonged capture can contain many calls of BMO's name. Treat ALL
        # adjacent recognized wake forms as one wake, never as words for the
        # Main Brain to argue about or learn from. (v1.28.7 stopped after 32
        # and passed the rest of a hallucinated loop to Coding.)
        woke, remainder = self.parse(cleaned)
        if woke:
            self.wake(now=now)
            if remainder:
                self.followup_armed_until = 0.0
                return remainder
            self.followup_armed_until = now + self.followup_seconds
            return ""

        # Only a preceding *bare* wake can authorize an unprefixed follow-up,
        # and only once within a short deterministic window. The window is
        # judged by when the user STARTED speaking: a 15 s follow-up that began
        # in time used to expire before Whisper finished (v1.28.8 live test).
        started = now if started_at is None else float(started_at)
        if self.is_active(now=now) and self.followup_armed_until >= started:
            self.followup_armed_until = 0.0
            self.last_activity_monotonic = now
            return cleaned
        return None


@dataclass(frozen=True)
class VoiceCommand:
    category: str
    syntax: str
    description: str


# The set phrases BMO always handles himself, before any brain sees them. VOICE_COMMANDS.md is made from this list
# (markdown_catalog) and is the printable command list on GitHub (owner 2026-10-28: plain words, one line each,
# grouped by what they're for). Placeholders are <like this>; "a|b" and "a / b" mean either one.
VOICE_COMMANDS: tuple[VoiceCommand, ...] = (
    VoiceCommand("Talking to BMO", "Hey Bemo / Bemo / BMO", "Wakes BMO. You can say just his name, wait for him, and then say the command."),
    VoiceCommand("Talking to BMO", "Hey Bemo, yes / no", "Answers BMO when he asks before doing something. No works on its own; yes needs Hey BMO."),
    VoiceCommand("Talking to BMO", "Hey Bemo, go to sleep / shutdown yourself", "BMO closes and lets go of his brain. He still hears Hey BMO, which starts him again."),
    VoiceCommand("Talking to BMO", "Hey Bemo, list commands", "BMO names the kinds of things he can do. This page has the whole list."),
    VoiceCommand("Talking to BMO", "Hey Bemo, what time is it? / what's the date?", "From this computer's clock."),
    VoiceCommand("Talking to BMO", "Hey Bemo, what hardware am I on?", "What this computer has: processor, memory, graphics, free disk space, sound and screen."),
    VoiceCommand("Talking to BMO", "Hey Bemo, what can you do on this computer?", "What works on this computer, what doesn't, and why."),

    VoiceCommand("Movies and shows", "Hey Bemo, show my movies", "Your movies and shows on BMO's screen, each with a picture and its name. A show has one tile."),
    VoiceCommand("Movies and shows", "Hey Bemo, scroll down / scroll up", "Next or previous page of the movies or games on the screen; \"next\", \"more\" and \"back\" work too. No need for Hey BMO while they're showing."),
    VoiceCommand("Movies and shows", "Hey Bemo, play number <n>", "Plays that number from the movies or games on the screen; \"the third one\" or just \"three\" works too. No need for Hey BMO while they're showing."),
    VoiceCommand("Movies and shows", "Hey Bemo, close the library", "Puts BMO's face back. Plain \"close\" works too, and it closes by itself after 3 minutes."),
    VoiceCommand("Movies and shows", "Hey Bemo, play <movie or show>", "Plays it full screen, in English when there's a choice. A movie picks up where you stopped it; a show carries on from the last episode you watched. \"Watch\", \"put on\" and \"I want to watch\" work too."),
    VoiceCommand("Movies and shows", "Hey Bemo, continue watching", "The last thing you were watching, from where you stopped. \"Pick up where I left off\" works too."),
    VoiceCommand("Movies and shows", "Hey Bemo, play <show> season <n> episode <n>", "That episode, then the rest of the season. \"The first episode of <show>\" and \"season 2 of <show>\" work too."),
    VoiceCommand("Movies and shows", "Hey Bemo, restart / play <movie> from the beginning", "Starts over. Say restart while it's playing, or ask for it from the beginning to start fresh."),
    VoiceCommand("Movies and shows", "Hey Bemo, pause / resume", "While something is playing; \"hold on\" or \"wait a second\" pauses too, and \"keep going\" carries on. Plain \"pause\" and \"play\" work without Hey BMO."),
    VoiceCommand("Movies and shows", "Hey Bemo, next episode / previous episode", "While a show is playing."),
    VoiceCommand("Movies and shows", "Hey Bemo, skip / rewind / skip ahead 30 seconds / go back 2 minutes", "Skip goes 30 seconds ahead and rewind 10 seconds back, or say how far. \"What did he say?\" goes back 10 seconds."),
    VoiceCommand("Movies and shows", "Hey Bemo, stop / stop the show / close <title> / turn it off", "Closes the player and brings BMO's face back; \"that's enough\" and \"I'm done watching\" work too. He remembers where you stopped."),

    VoiceCommand("Games", "Hey Bemo, show my games", "Your games on BMO's screen, with their covers if there are any, otherwise as a list. Scroll and pick by number like the movies."),
    VoiceCommand("Games", "Hey Bemo, play <game>", "Starts the game; \"let's play\", \"fire up\" and \"start\" work too. Controllers that are plugged in are players 1 to 4; with none, the keyboard is player 1."),
    VoiceCommand("Games", "Hey Bemo, close the game", "Closes the game. While a game is on, a plain \"close\" or \"quit\" does too."),
    VoiceCommand("Games", "Hey Bemo, boot games", "Opens the full game menu (ES-DE), which works with a controller."),
    VoiceCommand("Games", "Hey Bemo, list games", "How many games BMO has."),
    VoiceCommand("Games", "Hey Bemo, import games from USB", "Copies the GameCube games from a USB stick into BMO's games. The stick stays as it was."),
    VoiceCommand("Games", "Hey Bemo, rescan games", "Looks for new games in BMO's games folder."),

    VoiceCommand("Downloads", "Hey Bemo, sort my downloads", "Finds the games, movies and shows in your Downloads folder and says what he found. Say Hey BMO, yes, and he moves them where they go. Anything still downloading is left alone."),

    VoiceCommand("Timers and reminders", "Hey Bemo, set a timer for <time>", "Like \"set a pasta timer for 8 minutes\". It rings with a chime and BMO's voice, even while he's asleep."),
    VoiceCommand("Timers and reminders", "Hey Bemo, remind me <when> to <something>", "Like \"remind me in 20 minutes to check the oven\" or \"remind me tomorrow at 9 to call Sam\". He says it back so you know he got it."),
    VoiceCommand("Timers and reminders", "Hey Bemo, how much time is left?", "Time left on each timer. \"What reminders do I have?\" lists your reminders."),
    VoiceCommand("Timers and reminders", "Hey Bemo, cancel the timer", "Or \"cancel the pasta timer\", \"cancel all timers\", \"cancel the reminder to water the basil\"."),

    VoiceCommand("Remembering things", "Hey Bemo, remember that <something>", "Saved word for word, like \"remember that my sister's birthday is May 3rd\". Every brain knows it from then on."),
    VoiceCommand("Remembering things", "Hey Bemo, what do you remember about me?", "BMO reads back what you asked him to remember."),
    VoiceCommand("Remembering things", "Hey Bemo, forget that <something>", "Removes that note. \"Forget everything about me\" asks first."),

    VoiceCommand("Weather", "Hey Bemo, set my weather location to <town>", "Turns the weather on for your town. He checks the town with you before saving it."),
    VoiceCommand("Weather", "Hey Bemo, what's the weather?", "The weather now, or the forecast, for your town."),
    VoiceCommand("Weather", "Hey Bemo, do you remember the weather earlier?", "The last weather report he gave you, and when."),
    VoiceCommand("Weather", "Hey Bemo, forget my weather location", "Removes your town."),

    VoiceCommand("Sound", "Hey Bemo, volume up|down / louder / quieter", "Changes the volume by 10. Add \"a little\" for 5 or \"a lot\" for 20."),
    VoiceCommand("Sound", "Hey Bemo, set the volume to <percent>", "An exact level. A big jump, or going over 85, asks first."),
    VoiceCommand("Sound", "Hey Bemo, mute / unmute", "All of the computer's sound off or back on, BMO's voice too."),
    VoiceCommand("Sound", "Hey Bemo, what's the volume?", "The level, and where the sound is going."),
    VoiceCommand("Sound", "Hey Bemo, switch sound to <output>", "Sound through the TV, the speakers, or another device by its name."),

    VoiceCommand("Screen and text", "Hey Bemo, make the text bigger|smaller", "Text size one step at a time. Then say keep it, bigger, smaller or cancel; if you say nothing it goes back after 30 seconds."),
    VoiceCommand("Screen and text", "Hey Bemo, make the mouse pointer bigger|smaller", "The same, for the mouse pointer."),
    VoiceCommand("Screen and text", "Hey Bemo, hide|show the panel", "Hides the taskbar until the mouse goes there, or shows it again."),
    VoiceCommand("Screen and text", "Hey Bemo, normal text size / normal pointer size", "Back to the usual size."),
    VoiceCommand("Screen and text", "Hey Bemo, calibrate the screen", "Fits the desktop on an old TV or monitor. Say smaller, bigger, move left, the left side is cut off, keep it or cancel; no Hey BMO needed until it's done."),
    VoiceCommand("Screen and text", "Hey Bemo, display status", "The screen's resolution and how much is trimmed off each edge."),
    VoiceCommand("Screen and text", "Hey Bemo, list screen resolutions", "The resolutions this screen can do."),
    VoiceCommand("Screen and text", "Hey Bemo, test screen resolution <width> by <height>", "Tries a resolution. It goes back by itself unless you approve it."),
    VoiceCommand("Screen and text", "Hey Bemo, test screen crop <pixels> pixels", "Trims every edge, for a TV that cuts off the picture."),
    VoiceCommand("Screen and text", "Hey Bemo, test <edge> crop <pixels> pixels", "Trims one edge: left, right, top or bottom."),
    VoiceCommand("Screen and text", "Hey Bemo, reset screen crop", "Tries the screen with nothing trimmed."),
    VoiceCommand("Screen and text", "Hey Bemo, approve display changes", "Keeps the resolution or trim you're trying."),
    VoiceCommand("Screen and text", "Hey Bemo, reject display changes", "Goes back to how it was."),

    VoiceCommand("Apps", "Hey Bemo, open <app>", "Opens an app by its name, like Calculator or Firefox. If he isn't sure which one you mean, he asks."),
    VoiceCommand("Apps", "Hey Bemo, switch to <app>", "Brings that app to the front."),
    VoiceCommand("Apps", "Hey Bemo, close <app>", "Closes the app. If it has unsaved work, he tells you instead."),
    VoiceCommand("Apps", "Hey Bemo, press <button> in <app>", "Presses a button by its name; \"press 6, then equals\" does more than one. He asks once before changing anything in an app."),
    VoiceCommand("Apps", "Hey Bemo, switch to the <name> tab in <app>", "Opens that tab."),
    VoiceCommand("Apps", "Hey Bemo, turn <setting> on|off in <app>", "Flips a switch or tick box."),
    VoiceCommand("Apps", "Hey Bemo, what can I press in <app>?", "The buttons and settings he can see in it."),
    VoiceCommand("Apps", "Hey Bemo, <task> in <app>", "A job with several steps, like \"show hidden files in Thunar\". He tells you the plan first, and always asks before deleting or closing without saving."),
    VoiceCommand("Apps", "Hey Bemo, list apps", "The apps BMO runs himself: the games and the movie player."),

    VoiceCommand("Computer control", "Hey Bemo, disable computer control / enable computer control", "Stops, or lets again, BMO's brains using the computer on their own."),
    VoiceCommand("Computer control", "Hey Bemo, computer control status", "Whether it's on, and if anything is waiting for your OK."),
    VoiceCommand("Computer control", "Hey Bemo, confirm computer action", "Lets BMO go ahead with what he asked to do."),
    VoiceCommand("Computer control", "Hey Bemo, confirm critical computer action", "The same, for something that can't be undone."),
    VoiceCommand("Computer control", "Hey Bemo, cancel computer action", "Stops what he asked to do."),

    VoiceCommand("Brains", "Hey Bemo, current model", "Which brain BMO is using."),
    VoiceCommand("Brains", "Hey Bemo, list models", "All of BMO's brains."),
    VoiceCommand("Brains", "Hey Bemo, switch to <profile> model", "Changes brain, like \"switch to coding model\". It takes about a minute, with a game to play while you wait."),
    VoiceCommand("Brains", "Hey Bemo, ask <specialist> to <task>", "Gives one job to another brain, then comes back."),

    VoiceCommand("3D parts", "Hey Bemo, show me the shapes", "The six basic shapes BMO builds parts from, turning on his screen for a minute."),
    VoiceCommand("3D parts", "Hey Bemo, make a <part>", "A simple part to 3D print, like \"make a 20 millimeter cube with an 8 millimeter hole through the middle\". Saved in the BMO-3D folder; docs/3D_PARTS.md has more."),
    VoiceCommand("3D parts", "Hey Bemo, make it <number> millimeters tall", "Changes one size of the last part: tall, wide, deep, thick, across, the hole or the walls. The earlier one is kept."),

    VoiceCommand("Projects", "Hey Bemo, list projects", "Your projects."),
    VoiceCommand("Projects", "Hey Bemo, current project", "The project that's open."),
    VoiceCommand("Projects", "Hey Bemo, create project <name>", "Makes a new project and opens it."),
    VoiceCommand("Projects", "Hey Bemo, open project <name>", "Opens a project."),
    VoiceCommand("Projects", "Hey Bemo, close project", "Closes the project."),
    VoiceCommand("Projects", "Hey Bemo, import project from USB", "Copies a project from a USB stick, checks it, and opens it."),
    VoiceCommand("Projects", "Hey Bemo, import files into current project", "Copies files from a USB stick into the open project. Nothing already there is replaced."),
    VoiceCommand("Projects", "Hey Bemo, export <file-or-folder> to USB", "Copies files from the open project to a USB stick and checks them."),
    VoiceCommand("Projects", "Hey Bemo, export project", "Copies the whole open project to a USB stick and checks it."),
    VoiceCommand("Projects", "Hey Bemo, reindex project", "Looks through the project's files again."),
    VoiceCommand("Projects", "Hey Bemo, save project snapshot", "Saves the project as it is now, so you can go back to it."),
    VoiceCommand("Projects", "Hey Bemo, undo last Bemo change", "Undoes the last change BMO made to the project, when he can tell exactly what it was."),
    VoiceCommand("Projects", "Hey Bemo, remember for this project <note>", "Adds a note to the project."),
    VoiceCommand("Projects", "Hey Bemo, what do you remember about this project?", "Reads the project's notes back, word for word."),

    VoiceCommand("BMO himself", "Hey Bemo, reboot yourself / reload yourself", "Restarts BMO. The computer stays on."),
    VoiceCommand("BMO himself", "Hey Bemo, check for updates", "Looks for a new version of BMO. To install it, say Hey BMO, yes."),
    VoiceCommand("BMO himself", "Hey Bemo, how are the downloads?", "How far along BMO's brain downloads are, after a new install."),
    VoiceCommand("BMO himself", "Hey Bemo, learn my voice", "Say Hey BMO six times and talk for ten seconds, so he wakes up for your voice."),
    VoiceCommand("BMO himself", "Hey Bemo, voice status", "Which voice BMO is using."),
    VoiceCommand("BMO himself", "Hey Bemo, test voice", "BMO says a short test sentence."),
    VoiceCommand("BMO himself", "Hey Bemo, text mode", "The conversation as text, in place of BMO's face."),
    VoiceCommand("BMO himself", "Hey Bemo, face mode", "BMO's face again, with his voice on."),
    VoiceCommand("BMO himself", "Hey Bemo, TTS off / TTS on", "In text mode, turns his spoken answers off or back on."),
    VoiceCommand("BMO himself", "Hey Bemo, interface status", "Whether he's showing his face or text, and if his voice is on."),
    VoiceCommand("BMO himself", "Hey Bemo, system status", "What's running and which brain is on."),
    VoiceCommand("BMO himself", "Hey Bemo, backup Bemo to USB", "Saves a backup of BMO on a USB stick, and checks it."),
    VoiceCommand("BMO himself", "Hey Bemo, enable Bemo autostart / disable Bemo autostart", "Whether BMO starts when you log in to the computer."),
    VoiceCommand("BMO himself", "Hey Bemo, Bemo autostart status", "Whether BMO starts when you log in."),
    VoiceCommand("BMO himself", "Hey Bemo, screensaver status", "Which screensaver is on, or whether they're ready."),
    VoiceCommand("BMO himself", "Hey Bemo, active task", "What BMO was in the middle of."),
    VoiceCommand("BMO himself", "Hey Bemo, resume task", "Carries on with it."),
)


def categories() -> tuple[str, ...]:
    seen: list[str] = []
    for item in VOICE_COMMANDS:
        if item.category not in seen:
            seen.append(item.category)
    return tuple(seen)


def commands_for_category(category: str) -> tuple[VoiceCommand, ...]:
    return tuple(item for item in VOICE_COMMANDS if item.category == category)


def compact_spoken_summary() -> str:
    """What "Hey BMO, list commands" says; the printable list is VOICE_COMMANDS.md."""
    return (
        "Say Hey BMO first, then what you want. I can show your movies and games and play them, sort your "
        "downloads, set timers and reminders, remember things for you, check the weather, change the volume, the "
        "text size and the screen, open and use apps, switch brains, make 3D parts, and look after your projects. "
        "The full list is VOICE_COMMANDS, on my GitHub page."
    )


def model_command_catalog() -> str:
    """The catalog as plain lines, for local docs and the UI."""
    lines = [
        "BEMO VOICE COMMAND CATALOG",
        "These fixed phrases are handled by Bemo's deterministic local voice layer before an LLM turn. Do not invent additional fixed command phrases.",
        f"Canonical wake phrase: {CANONICAL_WAKE_PHRASE}. The names Bemo, Beemo, and BMO are also accepted wake aliases. No end word such as 'over' is required.",
        "A wake alias may prefix a command directly; a wake-only name arms exactly one immediate unprefixed follow-up.",
        "Lifecycle commands that reload or shut down Bemo require an explicit Hey Bemo on that turn.",
    ]
    for category in categories():
        forms = "; ".join(item.syntax for item in commands_for_category(category))
        lines.append(f"{category}: {forms}")
    return "\n".join(lines)


def printed_phrase(syntax: str) -> str:
    """How a phrase is printed: without "Hey Bemo," (the page says it once), BMO spelled BMO, [your words]."""
    text = re.sub(r"^Hey Bemo, ", "", syntax)
    text = "Hey BMO / BMO" if text == "Hey Bemo / Bemo / BMO" else text.replace("Bemo", "BMO")
    text = re.sub(r"<([^>]+)>", lambda m: "[" + m.group(1).replace("-", " ") + "]", text)
    return re.sub(r"\s*\|\s*", " / ", text)


def markdown_catalog() -> str:
    """VOICE_COMMANDS.md: the command list to print, a table per section."""
    lines = [
        "# BMO voice commands",
        "",
        "Start every command with **Hey BMO**, like \"Hey BMO, what's the weather?\" You can also say \"Hey BMO\" on "
        "its own, wait for him, and then say the command.",
        "",
        "Words in [square brackets] are yours to fill in. A slash means you can say either one.",
        "",
        "These are the set phrases BMO always handles the same way. The movie, game and library ones also work "
        "said other ways, like \"put on Ponyo\", \"hold on\" or \"the third one\". For anything else, just ask him "
        "in your own words.",
        "",
    ]
    for category in categories():
        lines += [f"## {category}", "", "| Say | What it does |", "|---|---|"]
        for item in commands_for_category(category):
            description = re.sub(r"<([^>]+)>", r"[\1]", item.description.replace("|", "/"))
            lines.append(f"| **{printed_phrase(item.syntax)}** | {description} |")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
