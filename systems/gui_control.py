"""GUI control through the accessibility tree: tiers, lookup, verification.

The platform part lives behind AccessibilityBackend (Linux: AT-SPI via
systems/adapters/a11y_helper.py run with the system Python). Everything here is pure
logic so it can be tested against recorded snapshots (tests/fixtures/gui/).

Safety tiers (approved 2026-10-18, docs/GUI_CONTROLLER_PLAN.md):
  0 look      read controls/values                       free
  1 navigate  tabs, opening menus/lists, selecting items  free, verified
  2 change    buttons, toggles, menu commands, typing      one spoken OK per task
  3 critical  delete/discard/send/log out/admin ...       "Hey BMO, yes" per action
Password fields are never typed into.
"""
from __future__ import annotations

import difflib
import json
import re

LOOK, NAVIGATE, CHANGE, CRITICAL = 0, 1, 2, 3
TIER_NAMES = {LOOK: "look", NAVIGATE: "navigate", CHANGE: "change", CRITICAL: "critical"}

_CRITICAL_WORDS = re.compile(
    r"\b(?:delete|remove|erase|format|discard|don[\u2019']?t save|do not save|close without saving|empty trash|"
    r"move to trash|uninstall|log ?out|log off|sign out|shut ?down|power off|restart|reboot|send|buy|"
    r"purchase|pay|checkout|submit|overwrite|replace all|wipe|factory reset|reset|authenticate|install)\b",
    re.IGNORECASE)
_NAVIGATION_BUTTONS = re.compile(r"^(?:back|forward|next|previous|open parent|up|home)$", re.IGNORECASE)


def display_name(control: dict) -> str:
    return (control.get("name") or control.get("label") or control.get("text") or "").strip()


def classify(control: dict, action: str) -> int | None:
    """Tier for doing `action` on `control`; None means never allowed."""
    # LibreOffice says "Don’t Save" with a curly apostrophe (bench 2026-10-18).
    role, name = control.get("role", ""), display_name(control).replace("\u2019", "'")
    if role == "password text":
        return None
    if action == "look":
        return LOOK
    if _CRITICAL_WORDS.search(name):
        return CRITICAL
    if action in ("set_text", "append_text", "set_value"):
        return CHANGE
    if role == "page tab" or (role == "menu" and action == "click") or (role == "combo box" and action == "press"):
        return NAVIGATE  # opens/shows something; changes no data
    if role in ("table cell", "list item", "tree item") and action in ("click", "select"):
        return NAVIGATE  # selecting an item; "activate" (open) counts as a change
    if role == "push button" and _NAVIGATION_BUTTONS.match(name):
        return NAVIGATE
    return CHANGE


def context_of(controls: list[dict], index: int) -> str:
    """The label that names a control's row, e.g. which stream a 'Mute audio' button belongs to.

    Among the labels just before it, the one sharing the deepest container
    wins, and the first of those (a row's title comes before its details:
    Volume Control shows "Firefox", ": YouTube…", "on").
    """
    parts = controls[index].get("path", "").split("/")
    best, best_depth = "", -1
    for item in controls[max(0, index - 12):index]:
        name = display_name(item)
        if item.get("role") not in ("label", "text", "heading") or not name or item.get("actions"):
            continue
        depth = 0
        for a, b in zip(parts, item.get("path", "").split("/")):
            if a != b:
                break
            depth += 1
        if depth > best_depth:
            best, best_depth = name, depth
    return best


def find_controls(controls: list[dict], query: str, role: str | None = None) -> list[dict]:
    """Best-matching actionable controls for a spoken name; several means ambiguous."""
    q = re.sub(r"\s+", " ", str(query or "").strip().lower())
    if not q:
        return []
    scored = []
    for i, c in enumerate(controls):
        if not c.get("actions") and "editable" not in c.get("states", []) and "value" not in c:
            continue
        if role and c.get("role") != role:
            continue
        # A field's contents are not its name: Calculator's display reading "7"
        # is not the 7 key.
        editable = "editable" in c.get("states", [])
        name = (c.get("name") or c.get("label") or ("" if editable else c.get("text")) or "").strip().lower()
        if not name:
            continue
        bare = re.sub(r"\s*\[[^\]]*\]$", "", name)  # "Clear Display [Escape]" -> "clear display"
        if q in (name, bare):
            score = 3.0
        elif bare.startswith(q) or q in bare.split():
            score = 2.0
        else:
            ratio = difflib.SequenceMatcher(None, q, bare).ratio()
            score = ratio if ratio >= 0.8 else 0.0
        if score:
            scored.append((score, i, c))
    if not scored:
        return []
    top = max(s for s, _, _ in scored)
    return [dict(c, context=context_of(controls, i)) for s, i, c in scored if s == top]


def compact_view(controls: list[dict], limit: int = 80) -> str:
    """Text a language model can read: one actionable control per line."""
    lines = []
    for c in controls:
        name = display_name(c)
        if not name or not (c.get("actions") or "editable" in c.get("states", []) or "value" in c):
            continue
        extra = [s for s in c.get("states", []) if s in ("checked", "selected", "expanded")]
        if "value" in c:
            extra.append(f"value={c['value'].get('current')}")
        lines.append(f"[{c['path']}] {c['role']}: {name}" + (f" ({', '.join(extra)})" if extra else ""))
        if len(lines) >= limit:
            lines.append("... more controls not shown")
            break
    return "\n".join(lines)


# Only where a number means something: LibreOffice toggles report value=0.0 and
# Main read that as "Bold is already on" (bench 2026-10-18).
VALUE_ROLES = {"slider", "spin button", "scroll bar", "progress bar", "dial", "level bar"}
TOGGLE_ROLES = {"toggle button", "check box", "check menu item", "radio button", "radio menu item", "switch"}


def numbered_view(controls: list[dict], limit: int = 70, known: dict | None = None,
                  ) -> tuple[str, dict[int, dict]]:
    """Milestone 3: what the Main Brain sees. '#7 check menu item: Show Hidden Files (checked)'.

    Short numbers instead of tree paths (fewer tokens, nothing to mistype);
    editable fields show their text; a table row's repeated cells appear once.
    With `known` (path -> number, kept for one task) a control keeps its
    number across looks, so after an action only the changes need sending.
    Returns the text and the number -> control map for the gui tool's 'act'.
    """
    known = {} if known is None else known
    lines, ids, seen = [], {}, set()
    for c in controls:
        editable = "editable" in c.get("states", [])
        name = display_name(c) if not editable else (c.get("name") or c.get("label") or "")
        if c.get("role") == "frame" or not (c.get("actions") or editable or "value" in c):
            continue
        if not name and not editable:
            continue
        parent = c.get("path", "").rsplit("/", 1)[0]
        key = (c.get("role"), name, parent)
        if key in seen:
            continue
        seen.add(key)
        if c.get("path") not in known:
            known[c.get("path")] = max(known.values(), default=0) + 1
        number = known[c.get("path")]
        ids[number] = c
        extra = [s for s in c.get("states", []) if s in ("selected", "expanded")]
        if c.get("role") in TOGGLE_ROLES:
            # Spelled out: with no marker Main said "Bold is already active" (bench 2026-10-18).
            extra.insert(0, "on" if "checked" in c.get("states", []) else "off")
        elif "checked" in c.get("states", []):
            extra.insert(0, "checked")
        if "enabled" not in c.get("states", []):
            extra.append("disabled")
        if "value" in c and c.get("role") in VALUE_ROLES:
            extra.append(f"value={c['value'].get('current')}")
        if editable:
            text = (c.get("text") or "").replace("\n", " ")
            extra.append(f"text={text[:80]!r}" + ("..." if len(text) > 80 else ""))
        line = f"#{number} {c.get('role')}: {say_label(name) or '(unnamed field)'}"
        lines.append(line + (f" ({', '.join(extra)})" if extra else ""))
        if len(lines) >= limit:
            lines.append("... more controls not shown")
            break
    return "\n".join(lines), ids


def say_label(name: str) -> str:
    return re.sub(r"\s*\[[^\]]*\]$", "", name or "")  # "Clear Display [Escape]" -> "Clear Display"


def view_changes(before: str, after: str) -> str:
    """Only the lines that are new or different, and how many disappeared."""
    old = {line.split(" ", 1)[0]: line for line in before.splitlines() if line.startswith("#")}
    new = {line.split(" ", 1)[0]: line for line in after.splitlines() if line.startswith("#")}
    changed = [line for key, line in new.items() if old.get(key) != line]
    gone = [key for key in old if key not in new]
    parts = changed or ["(no control changed)"]
    if gone:
        parts.append(f"({len(gone)} controls no longer showing: {' '.join(gone[:12])}{' ...' if len(gone) > 12 else ''})")
    return "\n".join(parts)


def open_dialog_text(controls: list[dict]) -> str | None:
    """Text of a dialog the app opened (a second top-level window), e.g. a save prompt."""
    tops = {}
    for c in controls:
        tops.setdefault(c.get("path", "0").split("/", 1)[0], []).append(c)
    if len(tops) < 2:
        return None
    main = max(tops, key=lambda k: (k == "0", len(tops[k])))
    texts = []
    for top, items in tops.items():
        if top == main:
            continue
        for c in items:
            text = (c.get("text") or c.get("name") or "").strip()
            if c.get("role") in ("label", "text", "heading", "alert", "dialog") and text and text not in texts:
                texts.append(text[:150])
    return " ".join(texts[:3]) or "a dialog with buttons"


def verify(action: str, before: dict, after: dict | None, value=None) -> tuple[bool | None, str]:
    """Did the control itself end up as asked? None = cannot tell from the control alone."""
    if after is None:
        return None, "the control disappeared (a window or menu may have closed)"
    if action == "set_text":
        ok = (after.get("text") or "") == str(value)
        return ok, "text matches" if ok else f"text is {after.get('text')!r}"
    if action == "append_text":
        ok = bool(after.get("appended"))
        return ok, "text added at the end" if ok else "the text did not appear at the end"
    if action == "set_value":
        current = (after.get("value") or {}).get("current")
        ok = current is not None and abs(float(current) - float(value)) <= max(1e-6, abs(float(value)) * 0.01)
        return ok, f"value is {current}"
    role, b, a = before.get("role"), set(before.get("states", [])), set(after.get("states", []))
    if role in ("toggle button", "check box", "check menu item", "radio button") and action in (
            "click", "press", "activate", "toggle"):
        flipped = ("checked" in b) != ("checked" in a)
        return flipped, "checked" if "checked" in a else "unchecked"
    if role == "page tab":
        return "selected" in a, "selected" if "selected" in a else "not selected"
    return None, ""


def snapshot_changed(before: dict, after: dict) -> bool:
    """Any visible difference: texts, states, values, or which controls exist."""
    def key(snap):
        return {(c["path"], c.get("role"), display_name(c), c.get("text"), tuple(c.get("states", [])),
                 json.dumps(c.get("value"), sort_keys=True)) for c in snap.get("controls", [])}
    return key(before) != key(after)


def action_summary(control: dict, action: str, app: str, value=None) -> str:
    name = display_name(control) or control.get("role", "control")
    if action == "set_text":
        return f"type {len(str(value))} characters into '{name}' in {app}"
    if action == "append_text":
        return f"add {len(str(value))} characters at the end of '{name}' in {app}"
    if action == "set_value":
        return f"set '{name}' to {value} in {app}"
    verb = {"page tab": "switch to the", "toggle button": "toggle", "check box": "toggle", "menu": "open the"}.get(
        control.get("role", ""), "press")
    return f"{verb} '{name}' {control.get('role', '')} in {app}".replace("  ", " ")


# The AT-SPI backend lives with the other OS adapters (portability plan P2, step 2).
from systems.adapters.atspi import AtspiBackend, host_accessibility_backend  # noqa: E402,F401


# ----------------------------------------------------------------------------
# One-step voice commands (milestone 2)
# ----------------------------------------------------------------------------
_POLITE = re.compile(r"^(?:(?:can|could|would|will) you |please |go ahead and |hey bmo,? )+")
_APP_TAIL = r"(?:\s+(?:in|on|inside)\s+(?:the\s+)?(?P<app>[\w .'-]+?))?(?:\s+(?:please|for me|now))?\s*$"
_ROLE_WORD = r"(?:\s+(?P<role>button|key|menu|tab|item|option|checkbox|check box|switch|toggle))?"
_PATTERNS = (
    ("list", re.compile(r"^(?:what|which)\s+(?:buttons|controls|options|things)?\s*(?:can|could)?\s*i?\s*"
                        r"(?:press|click|use|do|are there)\s+(?:in|on)\s+(?:the\s+)?(?P<app>[\w .'-]+?)\s*$")),
    ("on", re.compile(r"^(?:turn|switch|set)\s+on\s+(?:the\s+)?(?P<control>.+?)" + _ROLE_WORD + _APP_TAIL)),
    ("off", re.compile(r"^(?:turn|switch|set)\s+off\s+(?:the\s+)?(?P<control>.+?)" + _ROLE_WORD + _APP_TAIL)),
    ("on", re.compile(r"^(?:turn|switch)\s+(?:the\s+)?(?P<control>.+?)" + _ROLE_WORD + r"\s+on" + _APP_TAIL)),
    ("off", re.compile(r"^(?:turn|switch)\s+(?:the\s+)?(?P<control>.+?)" + _ROLE_WORD + r"\s+off" + _APP_TAIL)),
    ("on", re.compile(r"^check\s+(?:the\s+)?(?P<control>.+?)" + _ROLE_WORD + _APP_TAIL)),
    ("off", re.compile(r"^uncheck\s+(?:the\s+)?(?P<control>.+?)" + _ROLE_WORD + _APP_TAIL)),
    ("toggle", re.compile(r"^toggle\s+(?:the\s+)?(?P<control>.+?)" + _ROLE_WORD + _APP_TAIL)),
    ("select", re.compile(r"^(?:switch(?:ed)? to|go to|open|select|choose|show)\s+(?:the\s+)?(?P<control>.+?)\s+"
                          r"(?P<role>tab|item|option)" + _APP_TAIL)),
    ("press", re.compile(r"^(?:press|click|tap|hit|push|select|choose)\s+(?:on\s+)?(?:the\s+)?(?P<control>.+?)"
                         + _ROLE_WORD + _APP_TAIL)),
)
_SPOKEN_SYMBOLS = {
    "times": "×", "multiplied by": "×", "multiply": "×", "x": "×", "plus": "+", "add": "+",
    "minus": "−", "subtract": "−", "divide": "÷", "divided by": "÷", "divide by": "÷",
    "equals": "=", "equal": "=", "equal sign": "=", "point": ".", "dot": ".", "decimal point": ".",
    "percent": "%", "square root": "√", "pi": "π", "open bracket": "(", "close bracket": ")",
    "clear": "clear display", "backspace": "backspace",
}
_NUMBER_WORDS = {w: str(i) for i, w in enumerate(
    "zero one two three four five six seven eight nine".split())}
_SYMBOL_WORDS = {"×": "times", "÷": "divide", "−": "minus", "+": "plus", "=": "equals", ".": "point",
                 "%": "percent", "√": "square root", "π": "pi"}
ROLE_HINTS = {"button": ("push button", "toggle button"), "key": ("push button",), "menu": ("menu", "menu item"),
              "tab": ("page tab",), "item": ("table cell", "list item", "tree item", "menu item"),
              "option": ("radio button", "menu item", "check box"), "checkbox": ("check box",),
              "check box": ("check box",), "switch": ("toggle button", "check box"),
              "toggle": ("toggle button", "check box")}


_APP_FIRST = re.compile(r"^(?:on|in) (?:the )?(?P<app>[\w .'-]+?),\s*(?P<rest>.+)$")
# "press 6, then press equals" / "press 6 and equals" (live 2026-10-18: Main
# answered the two-step form itself and invented "Result: 42").
_STEP_SPLIT = re.compile(r"\s*(?:,\s*(?:and\s+)?(?:then\s+)?|\s+and\s+then\s+|\s+then\s+|\s+and\s+)"
                         r"(?:(?:press|click|tap|hit|push)\s+)?(?:the\s+)?")
MAX_STEPS = 8


def parse_gui_command(text: str) -> dict | None:
    """'press 7 in calculator' -> {'kind': 'press', 'control': '7', 'app': 'calculator', 'role': None}.

    A command after a short lead-in ("I'm giving you access. Can you click the
    Recording tab?") counts: when the whole text doesn't parse, the last
    sentence is tried. 'steps' lists the parts of "press 6, then equals".
    """
    t = re.sub(r"[?!.,]+$", "", str(text or "").strip().lower())
    parsed = _parse_one(t)
    if parsed is None:
        sentences = [x for x in re.split(r"(?<=[.?!])\s+", t) if x.strip()]
        if len(sentences) > 1:
            parsed = _parse_one(re.sub(r"[?!.,]+$", "", sentences[-1]))
    if parsed and parsed["kind"] == "press":
        steps = [x.strip() for x in _STEP_SPLIT.split(parsed["control"]) if x.strip()]
        if 1 < len(steps) <= MAX_STEPS:
            parsed["steps"] = steps
    return parsed


_TYPE_VERB = re.compile(r"^(?:type|write|enter)\b[\s,:]*(?P<text>.+)$")
_TYPE_INTO = re.compile(r"^(?:type|write|enter)\b[\s,:]*(?P<text>.+?)\s+(?:in|into)\s+(?:the\s+)?"
                        r"(?P<app>(?:text editor|editor|mousepad|notepad|xed|gedit)|[\w.'-]+\s+(?:editor|window|app))$")


def _parse_one(t: str) -> dict | None:
    t = _POLITE.sub("", t).strip()
    first = _APP_FIRST.match(t)
    if re.search(r"\b(?:type|write|enter)\b.*(?:,\s*|\s+and\s+)then\s", t):
        return None  # "type X, then close it without saving": several steps, the Main Brain's gui tool
    if first and _TYPE_VERB.match(first.group("rest")):
        # "in the text editor, type hello there": everything after the verb is the text, even "in love".
        return {"kind": "type", "control": _TYPE_VERB.match(first.group("rest")).group("text").strip(),
                "app": first.group("app").strip(), "role": None}
    into = _TYPE_INTO.match(t)
    if into:  # "type hello there in the text editor"
        return {"kind": "type", "control": into.group("text").strip(), "app": into.group("app").strip(), "role": None}
    if first:  # "on the calculator, press 6"
        inner = _parse_one(first.group("rest"))
        if inner and not inner["app"]:
            inner["app"] = first.group("app").strip()
            return inner
    for kind, pattern in _PATTERNS:
        m = pattern.match(t)
        if m:
            groups = m.groupdict()
            control = (groups.get("control") or "").strip()
            if kind != "list" and not control:
                continue
            return {"kind": kind, "control": control, "app": (groups.get("app") or "").strip() or None,
                    "role": groups.get("role")}
    return None


def spoken_control_names(spoken: str) -> list[str]:
    """Names to try for a spoken control: 'seven' -> '7', 'times' -> '×'."""
    s = re.sub(r"\s+", " ", spoken.strip().lower())
    names = [s]
    if s in _NUMBER_WORDS:
        names.append(_NUMBER_WORDS[s])
    if s in _SYMBOL_WORDS.values() or s in _SPOKEN_SYMBOLS:
        names.append(_SPOKEN_SYMBOLS.get(s, s))
    return list(dict.fromkeys(names))


_SAY_EXTRA = {"(": "open bracket", ")": "close bracket", "−": "minus", "-": "minus", "/": "divided by",
              "×": "times", "÷": "divided by", "=": "equals", "√": "square root of", "π": "pi"}


def say_name(name: str) -> str:
    """How BMO says a control name aloud: '×' -> 'times', 'Clear Display [Escape]' -> 'Clear Display'."""
    name = re.sub(r"\s*\[[^\]]*\]$", "", name.strip())  # tooltip shortcut hints
    return _SYMBOL_WORDS.get(name, _SAY_EXTRA.get(name, name))


def say_text(text: str) -> str:
    """Read a display aloud: '7×6' -> '7 times 6' (Piper does not know the symbols)."""
    spoken = "".join(f" {_SAY_EXTRA[ch]} " if ch in _SAY_EXTRA else ch for ch in text.strip())
    return re.sub(r"\s+", " ", spoken).strip()


def resolve_window(windows: list[dict], spoken_app: str) -> dict | None:
    """Match 'calculator' / 'volume control' to an open window (title or app name)."""
    q = re.sub(r"[^a-z0-9 ]+", " ", spoken_app.lower()).strip()
    q = re.sub(r"\b(?:app|application|window|program)\b", "", q).strip()
    best, best_score = None, 0.0
    for w in windows:
        if not w.get("title"):
            continue
        for candidate in (w["title"], w.get("app", "")):
            c = re.sub(r"[^a-z0-9 ]+", " ", candidate.lower()).strip()
            if not c:
                continue
            if q == c or (q and (q in c.split(" - ")[-1] or c.endswith(q) or q in c)):
                score = 1.0 if q == c else 0.9
            else:
                score = difflib.SequenceMatcher(None, q, c).ratio()
            if score > best_score:
                best, best_score = w, score
    return best if best_score >= 0.75 else None


# ----------------------------------------------------------------------------
# Multi-step tasks (milestone 3): the Main Brain's gui tool
# ----------------------------------------------------------------------------
GUI_TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "gui",
        "description": (
            "Use an OPEN app's buttons, menus, tabs, checkboxes and text fields by name, for tasks that need "
            "several steps (e.g. Thunar: show hidden files). Prefer this over computer_control clicks/typing. "
            "BMO usually shows you the app's numbered controls first; otherwise observe {app}. "
            "plan {app, summary}: before the first change, the whole task in a few plain words, e.g. "
            "'open the View menu and turn on Show Hidden Files'; the owner is asked once. "
            "act {app, id, kind, text}: kind click (buttons, menus, menu items, check items), select (tabs, list "
            "items), set_text (replace a short field's text), append_text (add text at the end of a document or field, keeping what is there: the way to type into an editor; never Paste or toolbar buttons for that). A menu's items appear "
            "after clicking the menu. Numbers stay the same; each result lists only what changed. "
            "click_seen {app, target}: when what you need is not in the list (web pages, apps without "
            "accessible controls), BMO finds it by looking at the screen and clicks it; target is a short "
            "description, e.g. 'the Say hello button'. Slow (~30 s) and a guess, so it needs the plan OK. "
            "close {app}: close the app's window (use this to close an app, not its File menu). If a DIALOG is open (e.g. save changes?), the task is not done: "
            "answer it with act. You may send several calls in one reply when you know the ids (e.g. plan and "
            "the first act). At most 8 acts. When done, say one short sentence about what was VERIFIED only."
        ),
        "parameters": {
            "type": "object",
            "required": ["action", "app"],
            "properties": {
                "action": {"type": "string", "enum": ["observe", "plan", "act", "click_seen", "close"]},
                "app": {"type": "string", "description": "App or window name, e.g. Thunar, Text Editor."},
                "summary": {"type": "string", "description": "plan only: the whole task in a few words."},
                "id": {"type": "integer", "description": "act only: control number from the latest list."},
                "kind": {"type": "string", "enum": ["click", "select", "set_text", "append_text"]},
                "text": {"type": "string", "description": "act with set_text or append_text only: the text."},
                "target": {"type": "string", "description": "click_seen only: what to click, in a few words."},
            },
        },
    },
}
