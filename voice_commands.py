"""Canonical Bemo voice-command catalog.

This module is intentionally dependency-light so the deterministic voice layer,
the permanent Supervisor, tests, and every model profile can all consume the
same command catalog without maintaining separate copies.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
import time

COMMAND_CATALOG_VERSION = "1.15"
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
        # "Big Bemo Play Jimmy Neutron Season 1 Episode 1" (live 2026-10-22) went to Main, which said "Playing...".
        match = re.match(r"^\s*(?:a|uh|ah|eh|oh|um|okay|ok|he|hi|hay|hit|big)[\s,.]+(?:bemo|beemo|bmo)\b[\s,.:;!\-]*",
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


# These are the canonical user-facing forms. The deterministic parser may accept
# a few natural aliases too, but this list is the authoritative compact catalog
# that Bemo can recite/show and that every model sees in its static system prefix.
VOICE_COMMANDS: tuple[VoiceCommand, ...] = (
    VoiceCommand("Session", "Hey Bemo / Bemo / BMO", "Wake/acknowledge Bemo; the name alone is also a valid wake trigger and arms one immediate follow-up command."),
    VoiceCommand("Session", "Hey Bemo, go to sleep", "Go back to standby; the next Hey Bemo starts him again."),
    VoiceCommand("Session", "Hey Bemo, list commands", "Summarize the built-in deterministic voice commands."),
    VoiceCommand("Session", "Hey Bemo, what hardware am I on?", "CPU, memory (vs the 28 GB minimum), graphics and whether my models use it, free disk, sound, microphone, screen; saved to ~/.bemo/hardware-profile.json."),
    VoiceCommand("Session", "Hey Bemo, what can you do on this computer?", "What works on this computer, what doesn't and why (from what is installed); full list: print_capabilities.py."),

    VoiceCommand("Lifecycle", "Hey Bemo, reboot yourself", "Restart only the Bemo program; Linux stays running."),
    VoiceCommand("Lifecycle", "Hey Bemo, reload yourself", "Alias for restarting only the Bemo program."),
    VoiceCommand("Lifecycle", "Hey Bemo, shutdown yourself", "Stop full Bemo and return to the tiny Hey Bemo standby listener."),

    VoiceCommand("Memory", "Hey Bemo, remember that <something>", "Saved word for word (\"remember that my sister's birthday is May 3rd\"); every brain knows it from then on."),
    VoiceCommand("Memory", "Hey Bemo, what do you remember about me?", "BMO reads back what you asked him to remember."),
    VoiceCommand("Memory", "Hey Bemo, forget that <something>", "Removes the closest note; \"forget everything about me\" asks first."),
    VoiceCommand("Timers", "Hey Bemo, set a timer for <time>", "A timer (\"set a pasta timer for 8 minutes\", \"set a 5 minute timer\"); it rings with a chime and BMO's voice, even while he sleeps."),
    VoiceCommand("Timers", "Hey Bemo, remind me <when> to <something>", "A reminder: \"in 20 minutes\", \"at 5 PM\", \"tomorrow at 9\"; BMO says the time back and the reminder when it comes, even while he sleeps."),
    VoiceCommand("Timers", "Hey Bemo, how much time is left?", "Time left on each timer; \"what reminders do I have?\" lists reminders."),
    VoiceCommand("Timers", "Hey Bemo, cancel the timer", "Cancel the timer (or \"cancel the pasta timer\", \"cancel all timers\", \"cancel the reminder to water the basil\")."),
    VoiceCommand("Models", "Hey Bemo, current model", "Report the active model profile."),
    VoiceCommand("Models", "Hey Bemo, list models", "List configured model profiles."),
    VoiceCommand("Models", "Hey Bemo, switch to <profile> model", "Manually switch model profiles."),
    VoiceCommand("Models", "Hey Bemo, ask <specialist> to <task>", "Run one task on a specialist, then return normally."),
    VoiceCommand("Models", "Hey Bemo, show me the shapes", "The 3D shapes menu: the six basic shapes turning on the screen for a minute (it also shows while BMO makes a part)."),
    VoiceCommand("Models", "Hey Bemo, make a <part>", "A simple 3D-printable part from one of six basic shapes with holes, walls and rounded edges (docs/3D_PARTS.md): in seconds when BMO understands every word, else by the Engineering model; saves .scad and .stl in ~/BMO-3D (or the open project's models folder) after checking its size, holes and that it is one piece."),
    VoiceCommand("Models", "Hey Bemo, make it <number> millimeters tall", "Change one size of the last part (tall, wide, deep, thick, across, the hole, the walls) in seconds; the earlier part is kept."),

    VoiceCommand("Voice", "Hey Bemo, voice status", "Say which voice Bemo is using."),
    VoiceCommand("Voice", "Hey Bemo, test voice", "Speak a short test phrase."),
    VoiceCommand("Voice", "Hey Bemo, learn my voice", "Say Hey Bemo six times and talk for ten seconds, so he wakes for your voice."),

    VoiceCommand("Projects", "Hey Bemo, list projects", "List known projects."),
    VoiceCommand("Projects", "Hey Bemo, current project", "Report the active project."),
    VoiceCommand("Projects", "Hey Bemo, create project <name>", "Create and open a project."),
    VoiceCommand("Projects", "Hey Bemo, import project from USB", "Discover, copy, verify, and open a project from mounted removable media."),
    VoiceCommand("Projects", "Hey Bemo, import files into current project", "Copy and verify removable-drive files into the active project without overwriting existing files."),
    VoiceCommand("Projects", "Hey Bemo, open project <name>", "Open an existing project."),
    VoiceCommand("Projects", "Hey Bemo, close project", "Close the active project."),
    VoiceCommand("Projects", "Hey Bemo, reindex project", "Refresh the active project's file index."),
    VoiceCommand("Projects", "Hey Bemo, save project snapshot", "Create a manual Git safety snapshot."),
    VoiceCommand("Projects", "Hey Bemo, undo last Bemo change", "Revert the most recent Bemo project change when safely identifiable."),
    VoiceCommand("Projects", "Hey Bemo, export <file-or-folder> to USB", "Copy and verify selected active-project files or folders to removable storage without moving or overwriting originals."),
    VoiceCommand("Projects", "Hey Bemo, export project", "Export and verify the active project to removable storage."),
    VoiceCommand("Projects", "Hey Bemo, remember for this project <note>", "Append a durable project note."),
    VoiceCommand("Projects", "Hey Bemo, what do you remember about this project?", "Read the open project's saved notes aloud, word for word (also: project notes, project memory)."),

    VoiceCommand("Interface", "Hey Bemo, text mode", "Switch the normal display from the animated face to the text-only conversation view."),
    VoiceCommand("Interface", "Hey Bemo, face mode", "Switch back to the animated Bemo face and ensure TTS is on."),
    VoiceCommand("Interface", "Hey Bemo, TTS off", "Disable spoken replies only while Text mode is active; Face mode always keeps TTS on."),
    VoiceCommand("Interface", "Hey Bemo, TTS on", "Enable spoken replies; the voice loads on demand if it was skipped at startup."),
    VoiceCommand("Interface", "Hey Bemo, interface status", "Report Face/Text mode and whether TTS is enabled."),

    VoiceCommand("System", "Hey Bemo, system status", "Report Supervisor/model/managed-app status."),
    VoiceCommand("System", "Hey Bemo, check for updates", "Look for a newer signed release; installing needs Hey Bemo, yes."),
    VoiceCommand("System", "Hey Bemo, how are the downloads?", "Progress of the brain downloads after installing."),
    VoiceCommand("System", "Hey Bemo, display status", "Report the live resolution, safe-area crop, and whether a display test is pending."),
    VoiceCommand("System", "Hey Bemo, list screen resolutions", "List display modes advertised by the current X11 monitor/output."),
    VoiceCommand("System", "Hey Bemo, calibrate the screen", "Guided whole-desktop fit for CRTs/TVs: a calibration pattern replaces the face; say smaller, bigger, move left/right/up/down, the left side is cut off, more, too far, lower resolution, 60 hertz, keep it, or cancel (no Hey Bemo needed inside the session). Silence puts it back."),
    VoiceCommand("System", "Hey Bemo, test screen resolution <width> by <height>", "Temporarily test a resolution; it is only saved after approval and otherwise auto-reverts."),
    VoiceCommand("System", "Hey Bemo, test screen crop <pixels> pixels", "Temporarily inset all screen edges to compensate for CRT/adapter overscan."),
    VoiceCommand("System", "Hey Bemo, test <edge> crop <pixels> pixels", "Temporarily adjust left, right, top, bottom, horizontal, or vertical safe-area crop."),
    VoiceCommand("System", "Hey Bemo, reset screen crop", "Temporarily test zero safe-area crop on every edge."),
    VoiceCommand("System", "Hey Bemo, approve display changes", "Save the currently tested resolution/crop as the startup display settings."),
    VoiceCommand("System", "Hey Bemo, reject display changes", "Revert the current resolution/crop test to the previous settings."),
    VoiceCommand("System", "Hey Bemo, list apps", "List allowlisted apps Bemo can launch directly."),
    VoiceCommand("System", "Hey Bemo, launch <app>", "Launch an allowlisted app such as games or the media player."),
    VoiceCommand("System", "Hey Bemo, close <app>", "Close an allowlisted managed app."),
    VoiceCommand("Desktop apps", "Hey Bemo, open <app>", "Open any installed desktop app by name (e.g. Calculator, Thunar); verified by its window. Misheard names get 'Did you mean…?'."),
    VoiceCommand("Desktop apps", "Hey Bemo, switch to <app>", "Bring an open app's window to the front."),
    VoiceCommand("Desktop apps", "Hey Bemo, close <app>", "Close an app's window; reports if it is asking about unsaved work."),
    VoiceCommand("App controls", "Hey Bemo, press <button> in <app>", "Press a named button; 'press 6, then equals' does several. Ordinary changes ask once per app (3 minutes)."),
    VoiceCommand("App controls", "Hey Bemo, switch to the <name> tab in <app>", "Switch tabs (no question: navigation only)."),
    VoiceCommand("App controls", "Hey Bemo, turn <setting> on|off in <app>", "Flip a named switch or checkbox."),
    VoiceCommand("App controls", "Hey Bemo, what can I press in <app>?", "List the app's visible controls."),
    VoiceCommand("App controls", "Hey Bemo, <task> in <app>", "Multi-step tasks (e.g. make Thunar show hidden files): one OK for the plan, every step verified; buttons not shared by the app (web pages) are found by looking. Critical steps (Delete, Don't Save) always ask."),
    VoiceCommand("Sound", "Hey Bemo, volume up|down / louder / quieter", "Change the volume by 10 points ('a little' 5, 'a lot' 20); read back after."),
    VoiceCommand("Sound", "Hey Bemo, set the volume to <percent>", "Set an exact level; jumps over 30 points or above 85% ask first."),
    VoiceCommand("Sound", "Hey Bemo, mute / unmute", "Mute or unmute the computer's sound (BMO's voice too)."),
    VoiceCommand("Sound", "Hey Bemo, what's the volume?", "Report the level and which output is playing."),
    VoiceCommand("Sound", "Hey Bemo, switch sound to <output>", "Play sound through the TV/HDMI, speakers, or a named device."),
    VoiceCommand("Readability", "Hey Bemo, make the text bigger|smaller", "Text size in steps (100-200%); a preview: say keep it, bigger, smaller, or cancel; silence puts it back after 30 s."),
    VoiceCommand("Readability", "Hey Bemo, make the mouse pointer bigger|smaller", "Pointer size 24-96 pixels, previewed the same way."),
    VoiceCommand("Readability", "Hey Bemo, hide|show the panel", "Auto-hide the desktop panel (Xfce), previewed the same way."),
    VoiceCommand("Readability", "Hey Bemo, normal text size / normal pointer size", "Back to the desktop's defaults."),
    VoiceCommand("Weather", "Hey Bemo, set my weather location to <town>", "Opt-in weather (Open-Meteo); the town is confirmed before saving."),
    VoiceCommand("Weather", "Hey Bemo, what's the weather?", "Current weather or forecast for the saved location."),
    VoiceCommand("Weather", "Hey Bemo, do you remember the weather earlier?", "The last weather report he gave, and when."),
    VoiceCommand("Weather", "Hey Bemo, forget my weather location", "Remove the saved location."),
    VoiceCommand("System", "Hey Bemo, disable computer control", "Disable model-requested full computer control."),
    VoiceCommand("System", "Hey Bemo, enable computer control", "Re-enable model-requested full computer control."),
    VoiceCommand("System", "Hey Bemo, computer control status", "Report full-computer-control state and pending actions."),
    VoiceCommand("System", "Hey Bemo, confirm computer action", "Approve a pending non-critical computer action."),
    VoiceCommand("System", "Hey Bemo, confirm critical computer action", "Approve a pending critical computer action."),
    VoiceCommand("System", "Hey Bemo, cancel computer action", "Cancel the pending computer action."),

    VoiceCommand("Movies", "Hey Bemo, play <movie or show>", "Play from the Videos folder, full screen, English audio first; a show carries on where it left off."),
    VoiceCommand("Movies", "Hey Bemo, play <show> season <n> episode <n>", "Also: the first episode of <show>, episode 3 of <show>, season 2 of <show>. The rest of the season follows."),
    VoiceCommand("Movies", "Hey Bemo, pause / resume", "While a video plays; plain pause and play work without Hey Bemo too."),
    VoiceCommand("Movies", "Hey Bemo, next episode / previous episode", "While a show plays."),
    VoiceCommand("Movies", "Hey Bemo, skip / rewind / skip ahead 30 seconds / go back 2 minutes", "While a video plays; skip alone is 30 seconds, rewind 10."),
    VoiceCommand("Movies", "Hey Bemo, stop / stop the show / close <title> / turn it off", "Close the player; BMO's face comes back."),
    VoiceCommand("Games", "Hey Bemo, boot games", "Open the GameCube library frontend."),
    VoiceCommand("Games", "Hey Bemo, list games", "Report discovered GameCube games."),
    VoiceCommand("Games", "Hey Bemo, import games from USB", "Copy and verify supported GameCube ROMs from mounted removable media into Bemo's permanent library."),
    VoiceCommand("Games", "Hey Bemo, rescan games", "Rescan the GameCube library and refresh ES-DE if it is currently open."),
    VoiceCommand("Games", "Hey Bemo, play <game>", "Launch a uniquely matched GameCube title in Dolphin. Gamepads plugged in become players 1-4; with none, the keyboard is player 1."),
    VoiceCommand("Games", "Hey Bemo, close current game", "Close the active Dolphin game session."),

    VoiceCommand("Maintenance", "Hey Bemo, backup Bemo to USB", "Create and verify a full lightweight Bemo backup on removable storage."),
    VoiceCommand("Maintenance", "Hey Bemo, enable Bemo autostart", "Enable Bemo standby automatically when the Xfce desktop logs in."),
    VoiceCommand("Maintenance", "Hey Bemo, disable Bemo autostart", "Disable future Xfce-login standby autostart without stopping the current session."),
    VoiceCommand("Maintenance", "Hey Bemo, Bemo autostart status", "Report autostart status."),
    VoiceCommand("Maintenance", "Hey Bemo, screensaver status", "Report Wine/SCR screensaver readiness or current saver."),
    VoiceCommand("Maintenance", "Hey Bemo, active task", "Report the current unfinished task."),
    VoiceCommand("Maintenance", "Hey Bemo, resume task", "Resume the most recent incomplete task/checkpoint."),
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
    """Short spoken response; the full catalog remains available to local docs/UI."""
    return (
        "I have session, lifecycle, model, voice, interface, project, system, app, sound, readability, weather, game, and maintenance commands. "
        "Use Hey Bemo, Bemo, or BMO to wake me. You can reload or shut me down, ask for current model or project, switch models, "
        "switch between face and text mode, turn TTS off in text mode or back on, manage projects, open, switch, close and use apps, change the volume, change text and pointer size, check the weather, set up or test my permanent voice, calibrate the screen, test and approve display resolution or crop, import projects or project files from USB, "
        "import or play games, control computer access, back me up, check the screensaver, or resume a task. "
        "The full command list is in Bemo's command catalog."
    )


def model_command_catalog() -> str:
    """Render the deterministic command catalog for local docs/UI only."""
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


def markdown_catalog() -> str:
    lines = [
        "# Bemo voice-command catalog",
        "",
        f"Canonical wake phrase: **{CANONICAL_WAKE_PHRASE}**. **Bemo**, **Beemo**, and **BMO** are also accepted wake aliases; no trailing **over** is required.",
        "A wake-only name acknowledges Bemo and arms exactly one immediate unprefixed follow-up.",
        "",
    ]
    for category in categories():
        lines += [f"## {category}", ""]
        for item in commands_for_category(category):
            lines.append(f"- `{item.syntax}` — {item.description}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
