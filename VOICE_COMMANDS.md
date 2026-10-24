# Bemo voice-command catalog

Canonical wake phrase: **Hey Bemo**. **Bemo**, **Beemo**, and **BMO** are also accepted wake aliases; no trailing **over** is required.
A wake-only name acknowledges Bemo and arms exactly one immediate unprefixed follow-up.

## Session

- `Hey Bemo / Bemo / BMO` — Wake/acknowledge Bemo; the name alone is also a valid wake trigger and arms one immediate follow-up command.
- `Hey Bemo, go to sleep` — Go back to standby; the next Hey Bemo starts him again.
- `Hey Bemo, list commands` — Summarize the built-in deterministic voice commands.
- `Hey Bemo, what hardware am I on?` — CPU, memory (vs the 28 GB minimum), graphics and whether my models use it, free disk, sound, microphone, screen; saved to ~/.bemo/hardware-profile.json.
- `Hey Bemo, what can you do on this computer?` — What works on this computer, what doesn't and why (from what is installed); full list: print_capabilities.py.

## Lifecycle

- `Hey Bemo, reboot yourself` — Restart only the Bemo program; Linux stays running.
- `Hey Bemo, reload yourself` — Alias for restarting only the Bemo program.
- `Hey Bemo, shutdown yourself` — Stop full Bemo and return to the tiny Hey Bemo standby listener.

## Memory

- `Hey Bemo, remember that <something>` — Saved word for word ("remember that my sister's birthday is May 3rd"); every brain knows it from then on.
- `Hey Bemo, what do you remember about me?` — BMO reads back what you asked him to remember.
- `Hey Bemo, forget that <something>` — Removes the closest note; "forget everything about me" asks first.

## Timers

- `Hey Bemo, set a timer for <time>` — A timer ("set a pasta timer for 8 minutes", "set a 5 minute timer"); it rings with a chime and BMO's voice, even while he sleeps.
- `Hey Bemo, remind me <when> to <something>` — A reminder: "in 20 minutes", "at 5 PM", "tomorrow at 9"; BMO says the time back and the reminder when it comes, even while he sleeps.
- `Hey Bemo, how much time is left?` — Time left on each timer; "what reminders do I have?" lists reminders.
- `Hey Bemo, cancel the timer` — Cancel the timer (or "cancel the pasta timer", "cancel all timers", "cancel the reminder to water the basil").

## Models

- `Hey Bemo, current model` — Report the active model profile.
- `Hey Bemo, list models` — List configured model profiles.
- `Hey Bemo, switch to <profile> model` — Manually switch model profiles.
- `Hey Bemo, ask <specialist> to <task>` — Run one task on a specialist, then return normally.
- `Hey Bemo, show me the shapes` — The 3D shapes menu: the six basic shapes turning on the screen for a minute (it also shows while BMO makes a part).
- `Hey Bemo, make a <part>` — A simple 3D-printable part from one of six basic shapes with holes, walls and rounded edges (docs/3D_PARTS.md): in seconds when BMO understands every word, else by the Engineering model; saves .scad and .stl in ~/BMO-3D (or the open project's models folder) after checking its size, holes and that it is one piece.
- `Hey Bemo, make it <number> millimeters tall` — Change one size of the last part (tall, wide, deep, thick, across, the hole, the walls) in seconds; the earlier part is kept.

## Voice

- `Hey Bemo, voice status` — Say which voice Bemo is using.
- `Hey Bemo, test voice` — Speak a short test phrase.
- `Hey Bemo, learn my voice` — Say Hey Bemo six times and talk for ten seconds, so he wakes for your voice.

## Projects

- `Hey Bemo, list projects` — List known projects.
- `Hey Bemo, current project` — Report the active project.
- `Hey Bemo, create project <name>` — Create and open a project.
- `Hey Bemo, import project from USB` — Discover, copy, verify, and open a project from mounted removable media.
- `Hey Bemo, import files into current project` — Copy and verify removable-drive files into the active project without overwriting existing files.
- `Hey Bemo, open project <name>` — Open an existing project.
- `Hey Bemo, close project` — Close the active project.
- `Hey Bemo, reindex project` — Refresh the active project's file index.
- `Hey Bemo, save project snapshot` — Create a manual Git safety snapshot.
- `Hey Bemo, undo last Bemo change` — Revert the most recent Bemo project change when safely identifiable.
- `Hey Bemo, export <file-or-folder> to USB` — Copy and verify selected active-project files or folders to removable storage without moving or overwriting originals.
- `Hey Bemo, export project` — Export and verify the active project to removable storage.
- `Hey Bemo, remember for this project <note>` — Append a durable project note.
- `Hey Bemo, what do you remember about this project?` — Read the open project's saved notes aloud, word for word (also: project notes, project memory).

## Interface

- `Hey Bemo, text mode` — Switch the normal display from the animated face to the text-only conversation view.
- `Hey Bemo, face mode` — Switch back to the animated Bemo face and ensure TTS is on.
- `Hey Bemo, TTS off` — Disable spoken replies only while Text mode is active; Face mode always keeps TTS on.
- `Hey Bemo, TTS on` — Enable spoken replies; the voice loads on demand if it was skipped at startup.
- `Hey Bemo, interface status` — Report Face/Text mode and whether TTS is enabled.

## System

- `Hey Bemo, system status` — Report Supervisor/model/managed-app status.
- `Hey Bemo, check for updates` — Look for a newer signed release; installing needs Hey Bemo, yes.
- `Hey Bemo, how are the downloads?` — Progress of the brain downloads after installing.
- `Hey Bemo, display status` — Report the live resolution, safe-area crop, and whether a display test is pending.
- `Hey Bemo, list screen resolutions` — List display modes advertised by the current X11 monitor/output.
- `Hey Bemo, calibrate the screen` — Guided whole-desktop fit for CRTs/TVs: a calibration pattern replaces the face; say smaller, bigger, move left/right/up/down, the left side is cut off, more, too far, lower resolution, 60 hertz, keep it, or cancel (no Hey Bemo needed inside the session). Silence puts it back.
- `Hey Bemo, test screen resolution <width> by <height>` — Temporarily test a resolution; it is only saved after approval and otherwise auto-reverts.
- `Hey Bemo, test screen crop <pixels> pixels` — Temporarily inset all screen edges to compensate for CRT/adapter overscan.
- `Hey Bemo, test <edge> crop <pixels> pixels` — Temporarily adjust left, right, top, bottom, horizontal, or vertical safe-area crop.
- `Hey Bemo, reset screen crop` — Temporarily test zero safe-area crop on every edge.
- `Hey Bemo, approve display changes` — Save the currently tested resolution/crop as the startup display settings.
- `Hey Bemo, reject display changes` — Revert the current resolution/crop test to the previous settings.
- `Hey Bemo, list apps` — List allowlisted apps Bemo can launch directly.
- `Hey Bemo, launch <app>` — Launch an allowlisted app such as games or the media player.
- `Hey Bemo, close <app>` — Close an allowlisted managed app.
- `Hey Bemo, disable computer control` — Disable model-requested full computer control.
- `Hey Bemo, enable computer control` — Re-enable model-requested full computer control.
- `Hey Bemo, computer control status` — Report full-computer-control state and pending actions.
- `Hey Bemo, confirm computer action` — Approve a pending non-critical computer action.
- `Hey Bemo, confirm critical computer action` — Approve a pending critical computer action.
- `Hey Bemo, cancel computer action` — Cancel the pending computer action.

## Desktop apps

- `Hey Bemo, open <app>` — Open any installed desktop app by name (e.g. Calculator, Thunar); verified by its window. Misheard names get 'Did you mean…?'.
- `Hey Bemo, switch to <app>` — Bring an open app's window to the front.
- `Hey Bemo, close <app>` — Close an app's window; reports if it is asking about unsaved work.

## App controls

- `Hey Bemo, press <button> in <app>` — Press a named button; 'press 6, then equals' does several. Ordinary changes ask once per app (3 minutes).
- `Hey Bemo, switch to the <name> tab in <app>` — Switch tabs (no question: navigation only).
- `Hey Bemo, turn <setting> on|off in <app>` — Flip a named switch or checkbox.
- `Hey Bemo, what can I press in <app>?` — List the app's visible controls.
- `Hey Bemo, <task> in <app>` — Multi-step tasks (e.g. make Thunar show hidden files): one OK for the plan, every step verified; buttons not shared by the app (web pages) are found by looking. Critical steps (Delete, Don't Save) always ask.

## Sound

- `Hey Bemo, volume up|down / louder / quieter` — Change the volume by 10 points ('a little' 5, 'a lot' 20); read back after.
- `Hey Bemo, set the volume to <percent>` — Set an exact level; jumps over 30 points or above 85% ask first.
- `Hey Bemo, mute / unmute` — Mute or unmute the computer's sound (BMO's voice too).
- `Hey Bemo, what's the volume?` — Report the level and which output is playing.
- `Hey Bemo, switch sound to <output>` — Play sound through the TV/HDMI, speakers, or a named device.

## Readability

- `Hey Bemo, make the text bigger|smaller` — Text size in steps (100-200%); a preview: say keep it, bigger, smaller, or cancel; silence puts it back after 30 s.
- `Hey Bemo, make the mouse pointer bigger|smaller` — Pointer size 24-96 pixels, previewed the same way.
- `Hey Bemo, hide|show the panel` — Auto-hide the desktop panel (Xfce), previewed the same way.
- `Hey Bemo, normal text size / normal pointer size` — Back to the desktop's defaults.

## Weather

- `Hey Bemo, set my weather location to <town>` — Opt-in weather (Open-Meteo); the town is confirmed before saving.
- `Hey Bemo, what's the weather?` — Current weather or forecast for the saved location.
- `Hey Bemo, do you remember the weather earlier?` — The last weather report he gave, and when.
- `Hey Bemo, forget my weather location` — Remove the saved location.

## Movies

- `Hey Bemo, play <movie or show>` — Play from the Videos folder, full screen, English audio first; a show carries on where it left off.
- `Hey Bemo, play <show> season <n> episode <n>` — Also: the first episode of <show>, episode 3 of <show>, season 2 of <show>. The rest of the season follows.
- `Hey Bemo, pause / resume` — While a video plays; plain pause and play work without Hey Bemo too.
- `Hey Bemo, next episode / previous episode` — While a show plays.
- `Hey Bemo, skip / rewind / skip ahead 30 seconds / go back 2 minutes` — While a video plays; skip alone is 30 seconds, rewind 10.
- `Hey Bemo, stop / stop the show / close <title> / turn it off` — Close the player; BMO's face comes back.

## Games

- `Hey Bemo, boot games` — Open the GameCube library frontend.
- `Hey Bemo, list games` — Report discovered GameCube games.
- `Hey Bemo, import games from USB` — Copy and verify supported GameCube ROMs from mounted removable media into Bemo's permanent library.
- `Hey Bemo, rescan games` — Rescan the GameCube library and refresh ES-DE if it is currently open.
- `Hey Bemo, play <game>` — Launch a uniquely matched GameCube title in Dolphin. Gamepads plugged in become players 1-4; with none, the keyboard is player 1.
- `Hey Bemo, close current game` — Close the active Dolphin game session.

## Maintenance

- `Hey Bemo, backup Bemo to USB` — Create and verify a full lightweight Bemo backup on removable storage.
- `Hey Bemo, enable Bemo autostart` — Enable Bemo standby automatically when the Xfce desktop logs in.
- `Hey Bemo, disable Bemo autostart` — Disable future Xfce-login standby autostart without stopping the current session.
- `Hey Bemo, Bemo autostart status` — Report autostart status.
- `Hey Bemo, screensaver status` — Report Wine/SCR screensaver readiness or current saver.
- `Hey Bemo, active task` — Report the current unfinished task.
- `Hey Bemo, resume task` — Resume the most recent incomplete task/checkpoint.
