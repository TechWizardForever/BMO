# Using apps with BMO (GUI control)

BMO can open apps and use their buttons, menus, tabs, switches and text fields by voice. It all runs on
your PC, and he only says something worked after he's checked that it did.

## What to say

| You say | What happens | Speed |
|---|---|---|
| "Hey BMO, open Thunar" | Opens the app and waits for its window. A misheard name gets "Did you mean Thunar?" | 1–3 s |
| "Hey BMO, switch to Firefox" / "close the calculator" | Brings the window forward / closes it (and tells you if it asks about saving) | 1 s |
| "Hey BMO, press 7 in Calculator", then "press times, then 6, then equals" | Presses named buttons; reads the display back ("It shows 42") | 1 s each |
| "Hey BMO, switch to the Recording tab in Volume Control" | Switches tabs | 1 s |
| "Hey BMO, what can I press in Calculator?" | Lists the buttons he can see | 1 s |
| "Hey BMO, make Thunar show hidden files" | The Main Brain plans the steps (menu, then the item), you approve once | 30–60 s |
| "Hey BMO, in the text editor, type BMO was here, then close it without saving" | Types, closes, answers the save prompt (asks you about "Close without Saving") | ~60 s |
| "Hey BMO, in Firefox, click the Say hello button" | Buttons on web pages: he finds them by looking at the screen | ~40–50 s |

After a command, the app is remembered for 3 minutes, so "press equals" works without "in Calculator".
Wait until BMO has stopped talking before answering: the microphone is off while he speaks.

## When he asks first

| Kind of action | Examples | Rule |
|---|---|---|
| Looking | listing buttons, reading a display | never asks |
| Moving around | tabs, opening a menu, selecting a list item, Back/Forward | never asks |
| Changing something | pressing a button, a switch, typing, clicking something found by looking | asks once ("I'll press 7 in Calculator. Say Hey BMO, yes, or just say no."); that yes covers the same app for 3 minutes, or the whole plan of a multi-step task |
| Hard to undo | Delete, Remove, Don't Save, Close without Saving, Empty Trash, Log Out, Send, Buy, Install | asks every single time |
| Password fields | — | never typed into, even if asked |

- Approving needs "Hey BMO, yes" (a clipped "…emo, yes" counts while he is waiting). "No" alone is enough to decline.
- No answer within 35 s is not a no: he says he didn't hear a yes, and "Hey BMO, yes" within a minute still does it.
- He never operates his own window or the terminal he runs in. Paste/copy/cut only when you asked for the clipboard.

## How it works (short)

1. **Accessibility first.** Most Linux apps describe their controls (name, role, on/off, text) to the
   desktop's accessibility service (AT-SPI). BMO reads that, acts on the control directly, and reads it back.
2. **Multi-step tasks** go to the Main Brain with a `gui` tool: it sees the controls as a numbered list,
   asks for one plan OK, and acts one control at a time; every step comes back VERIFIED or NOT VERIFIED,
   with any dialog that opened ("A DIALOG IS OPEN: Save changes…?"), at most 8 actions per request.
3. **Looking at the screen (fallback).** For things not in the accessibility list (web pages; apps that
   don't share their controls, like Firefox by default), BMO screenshots that window and MAI-UI-2B finds the
   point (89–92% exact on the benchmark, ~25–36 s). Before clicking, the Supervisor brings the window to the
   front and refuses if it isn't in front or looks different from when he looked; afterwards it compares
   screenshots and reports whether anything changed.
4. Every action goes through the Supervisor and is logged in `~/.bemo/logs/computer-actions.jsonl`
   (typed text and field contents are never logged).

## Requirements

- Linux desktop on X11 (tested: Linux Mint 22.3 Xfce). On Wayland, accessibility works but clicking by sight does not.
- Packages: `wmctrl`, `xdotool`, `python3-pyatspi`, `at-spi2-core` (installed by `install.sh`).
- Clicking by sight (optional): ≥ 28 GB of memory installed and ≥ 4.5 GB free at that moment, plus the model
  files in `~/.bemo/models/gui/` (2.6 GB). The helper starts when needed (~2–5 s) and stops after 10 idle minutes.
- Check this PC: `bash tools/gui_vision/setup_gui_vision.sh` (also prints the pinned build steps if files are missing).

## Settings (`config.py`, or environment variables)

| Setting | Default | Meaning |
|---|---|---|
| `ENABLE_GUI_COMMANDS` / `ENABLE_GUI_TASKS` / `ENABLE_GUI_VISION` | on | one-step commands / multi-step tool / looking at the screen |
| `GUI_TASK_OK_SECONDS` | 180 | how long one yes covers ordinary changes in the same app |
| `GUI_TASK_MAX_STEPS` | 8 | actions per multi-step request |
| `GUI_APPROVAL_TIMEOUT_SECONDS` / `GUI_LATE_YES_SECONDS` | 35 / 60 | time to answer / a late yes still counts |
| `BEMO_GUI_VISION_MIN_TOTAL_GIB` / `BEMO_GUI_VISION_MIN_AVAILABLE_GIB` | 28 / 4.5 | memory gate for clicking by sight |
| `BEMO_GUI_VISION_DIR` | `~/.bemo/models/gui` | vision model folder |
| `BEMO_STT_APP_HOTWORDS` | `auto` | app names Whisper is told to expect (auto: installed file managers it can't spell, e.g. Thunar); `""` turns it off |

## When something doesn't work

- **"I can't find X in App"**: say "what can I press in App?" to hear the real names; or describe the task
  normally and the Main Brain will look further (menus, or by looking at the screen).
- **Qt/KDE apps** share their controls only with `QT_ACCESSIBILITY=1`; **Chromium/Electron** apps need
  `--force-renderer-accessibility`. Without it BMO falls back to looking, which is slower.
- **"Vision isn't available"**: he says why (memory, missing files, no X11). Close something big, or run the check script.
- **An app name keeps being misheard**: say what it does ("open the file manager"), or answer "Did you mean…?".
- **A click by sight was refused**: another window covered the app, or it changed while he was looking. Don't
  move windows during the ~30 s look.
