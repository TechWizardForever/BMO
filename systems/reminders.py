"""Timers and reminders, kept by the Supervisor so they go off even while BMO sleeps (2026-10-24).

"Set a timer for 10 minutes", "set a pasta timer for 8 minutes", "remind me in 20 minutes to check the oven",
"remind me at 5 pm to water the basil", "remind me to call my mom tomorrow at 9", "how much time is left?",
"what reminders do I have?", "cancel the timer". BMO reads these himself (no brain), the way he reads simple 3D
parts: a sentence is taken only when every part of it is understood, and the time he heard is said back.

Kept in ~/.bemo/reminders.json (the owner's data: in backups, private like the rest of ~/.bemo). The logs get the
kind and the time, never the words of a reminder.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path

TIMER, REMINDER = "timer", "reminder"
MISSED_REMINDER_HOURS = 12  # a reminder that came due while the PC was off is still said this long after

_ONES = {w: i for i, w in enumerate("zero one two three four five six seven eight nine ten eleven twelve thirteen "
                                    "fourteen fifteen sixteen seventeen eighteen nineteen".split())}
_TENS = {w: 10 * i for i, w in enumerate("twenty thirty forty fifty sixty seventy eighty ninety".split(), 2)}
_UNITS = {"second": 1, "seconds": 1, "sec": 1, "secs": 1, "minute": 60, "minutes": 60, "min": 60, "mins": 60,
          "hour": 3600, "hours": 3600, "hr": 3600, "hrs": 3600}
_UNIT = r"(?:seconds?|secs?|minutes?|mins?|hours?|hrs?)"
_NUM = r"(?:\d+(?:\.\d+)?|a|an)"
# "1 hour and 20 minutes", "an hour and a half", "half an hour", "90 seconds", "1.5 hours"
_DURATION = (rf"(?:half an? hour|{_NUM} (?:and a half )?{_UNIT}(?: and a half)?"
             rf"(?:(?: and|,)? {_NUM} {_UNIT})*)")
_CLOCK = (r"(?:noon|midday|midnight|\d{1,2}(?: \d{2})?(?: ?(?:am|pm|a m|p m|in the morning|in the afternoon|"
          r"in the evening|at night|tonight))?)")


def _words_to_numbers(text: str) -> str:
    out, value = [], None
    for word in text.split():
        if word in _ONES or word in _TENS:
            value = (value or 0) + (_ONES.get(word) if word in _ONES else _TENS[word])
            continue
        if value is not None:
            out.append(str(value))
            value = None
        out.append(word)
    if value is not None:
        out.append(str(value))
    return " ".join(out)


def plain(text: str) -> str:
    """Lower case, '5:30' -> '5 30', 'p.m.' -> 'p m', number words -> digits, no punctuation."""
    text = str(text or "").lower()
    text = re.sub(r"(\d):(\d\d)", r"\1 \2", text)
    text = re.sub(r"\b([ap])\.\s?m\.?", r"\1 m", text)
    text = re.sub(r"(\d)(am|pm)\b", r"\1 \2", text)
    text = re.sub(r"[^a-z0-9.' ]+", " ", text).replace("'", "")
    text = re.sub(r"\.(?!\d)", " ", text)
    return _words_to_numbers(re.sub(r"\s+", " ", text).strip())


def duration_seconds(text: str) -> int | None:
    """'1 hour and 20 minutes' -> 4800; None unless the whole text is a duration."""
    text = text.strip()
    if text in ("half an hour", "half a hour"):
        return 1800
    if not re.fullmatch(_DURATION, text):
        return None
    total = 0.0
    for number, half_before, unit, half_after in re.findall(
            rf"({_NUM}) (and a half )?({_UNIT})( and a half)?", text):
        amount = 1.0 if number in ("a", "an") else float(number)
        if half_before or half_after:
            amount += 0.5
        total += amount * _UNITS[unit]
    return int(round(total)) if total > 0 else None


def spoken_duration(seconds: float) -> str:
    seconds = int(round(seconds))
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    parts = [f"{n} {word}{'s' if n != 1 else ''}" for n, word in ((hours, "hour"), (minutes, "minute"),
                                                                    (secs, "second")) if n]
    if not parts:
        return "less than a second"
    return parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]


def timer_name(item: dict) -> str:
    """'pasta timer', '10 minute timer', 'timer for 1 hour and 20 minutes'."""
    if item.get("name"):
        return f"{item['name']} timer"
    words = spoken_duration(item.get("seconds") or 0)
    if " and " not in words and "," not in words:
        return words.rstrip("s") + " timer" if not words.startswith("1 ") else words + " timer"
    return f"timer for {words}"


def spoken_clock(when: datetime, now: datetime) -> str:
    hour = when.hour % 12 or 12
    clock = f"{hour}:{when.minute:02d}" if when.minute else f"{hour}"
    clock += " AM" if when.hour < 12 else " PM"
    if when.hour == 12 and when.minute == 0:
        clock = "noon"
    elif when.hour == 0 and when.minute == 0:
        clock = "midnight"
    if when.date() == now.date():
        return clock if clock in ("noon", "midnight") else f"{clock}"
    if when.date() == (now + timedelta(days=1)).date():
        return f"{clock} tomorrow"
    return f"{clock} on {when.strftime('%A')}"


def clock_time(text: str, now: datetime, *, tomorrow: bool = False) -> datetime | None:
    """'5 pm', '5 30', 'noon', '9 in the morning' -> the next such time after now (or tomorrow's)."""
    text = text.strip()
    if text in ("noon", "midday"):
        hour, minute, half = 12, 0, "fixed"
    elif text == "midnight":
        hour, minute, half = 0, 0, "fixed"
    else:
        found = re.fullmatch(r"(\d{1,2})(?: (\d{2}))?(?: ?(am|pm|a m|p m|in the morning|in the afternoon|"
                             r"in the evening|at night|tonight))?", text)
        if not found:
            return None
        hour, minute = int(found.group(1)), int(found.group(2) or 0)
        if hour > 23 or minute > 59:
            return None
        tag = found.group(3) or ""
        half = ("am" if tag in ("am", "a m", "in the morning") else
                "pm" if tag in ("pm", "p m", "in the afternoon", "in the evening", "at night", "tonight") else "")
        if hour > 12:
            half = "fixed"
        elif half and hour == 0:
            return None
    day = (now + timedelta(days=1)).date() if tomorrow else now.date()
    if half == "fixed":
        options = [hour]
    elif half == "am":
        options = [hour % 12]
    elif half == "pm":
        options = [hour % 12 + 12]
    elif tomorrow:
        # "tomorrow at 9" means the morning; "tomorrow at 3" the afternoon.
        options = [hour % 12 + 12 if 1 <= hour <= 6 else hour % 12]
    elif 1 <= hour <= 6:
        options = [hour + 12]  # "at 2" means the afternoon, not 2 in the night
    else:
        options = sorted({hour % 12, hour % 12 + 12})
    for days in range(0, 2):
        for h in options:
            candidate = datetime.combine(day + timedelta(days=days), datetime.min.time()).replace(hour=h, minute=minute)
            if candidate > now:
                return candidate
    return None


def second_person(task: str) -> str:
    """'call my mom' -> 'call your mom' (what BMO says back)."""
    swaps = {"my": "your", "me": "you", "myself": "yourself", "mine": "yours", "im": "youre", "i": "you",
             "our": "your", "us": "you"}
    words = task.split()
    out = [swaps.get(w, w) for w in words]
    return " ".join(out).replace("youre", "you're")


# --- understanding a request ------------------------------------------------------------------------------------

_POLITE = r"^(?:(?:please|can you|could you|would you|hey|ok|okay|now|and) )*"


def parse(request: str, now: datetime | None = None) -> dict | None:
    """What the owner asked, or None when it isn't a timer or reminder request BMO fully understands."""
    now = now or datetime.now()
    text = re.sub(_POLITE, "", plain(request))
    text = re.sub(r" please$", "", text)

    # Timers
    for pattern in (rf"^(?:set|start|make)(?: me)? (?:a |an )?(?:(?P<name>[a-z]+) )?timer (?:for |of )?(?P<dur>.+)$",
                    rf"^(?:set|start|make)(?: me)? (?:a |an )?(?P<dur>{_DURATION}) (?:(?P<name>[a-z]+) )?timer$",
                    rf"^(?P<dur>{_DURATION}) (?:(?P<name>[a-z]+) )?timer$",
                    rf"^timer (?:for )?(?P<dur>.+)$"):
        found = re.match(pattern, text)
        if found:
            # "a 10 minute timer": the unit is singular before "timer"
            seconds = duration_seconds(found.group("dur"))
            name = (found.groupdict().get("name") or "").strip()
            if seconds and 0 < seconds <= 24 * 3600 and name not in ("a", "an", "the", "new"):
                return {"action": "add", "kind": TIMER, "seconds": seconds, "name": name}
            return None

    # Reminders
    found = re.match(r"^remind me (?P<rest>.+)$", text)
    if found:
        rest = f" {found.group('rest')} "
        due, spans, relative = None, [], 0
        for match in re.finditer(rf" in (?P<dur>{_DURATION})(?= )", rest):
            seconds = duration_seconds(match.group("dur"))
            if seconds:
                due, spans, relative = now + timedelta(seconds=seconds), [match.span()], seconds
        if due is None:
            for match in re.finditer(rf"(?: (?P<pre>tomorrow|today|tonight))? at (?P<clock>{_CLOCK})"
                                     rf"(?: (?P<post>tomorrow|today|tonight))?(?= )", rest):
                tomorrow = "tomorrow" in ((match.group("pre") or ""), (match.group("post") or ""))
                clock = match.group("clock")
                if "tonight" in ((match.group("pre") or ""), (match.group("post") or "")) and not re.search(
                        r"am|pm|a m|p m|morning|afternoon|evening|night", clock):
                    clock += " pm"
                when = clock_time(clock, now, tomorrow=tomorrow)
                if when:
                    due, spans = when, [match.span()]
        if due is None:
            return None
        for start, end in reversed(spans):
            rest = rest[:start] + " " + rest[end:]
        task = re.sub(r"\s+", " ", rest).strip()
        task_match = re.fullmatch(r"(to|that|about) (.+)", task)
        if not task_match:
            return None
        return {"action": "add", "kind": REMINDER, "due": due.timestamp(), "task": task_match.group(2),
                "connector": task_match.group(1), "seconds": relative}

    # Questions and cancelling
    if re.fullmatch(r"(?:how (?:much time|long) (?:is |s )?(?:left|remaining)(?: on (?:the |my )?(?:[a-z]+ )?timers?)?|"
                    r"time left|whats left on (?:the |my )?timers?|how long until (?:the |my )?timer(?: is done)?|"
                    r"is my timer done)", text):
        return {"action": "list", "kind": TIMER}
    if re.fullmatch(r"(?:what|which) (?:timers|reminders|timers and reminders) (?:do i have|are (?:set|there)|"
                    r"have i (?:got|set))|(?:list|read) (?:my |the )?(?:timers|reminders)|"
                    r"do i have any (?:timers|reminders)|any reminders|my reminders", text):
        kind = TIMER if "timer" in text and "reminder" not in text else REMINDER if "reminder" in text else None
        return {"action": "list", "kind": kind}
    found = re.fullmatch(r"(?:cancel|stop|delete|remove|clear|turn off) (?:the |my |all (?:the |my )?|all )?"
                         r"(?:(?P<name>[a-z]+) )?(?P<kind>timers?|reminders?)(?: (?:to|about) (?P<task>.+))?", text)
    if found:
        name = found.group("name") or ""
        if name in ("all", "both", "every"):
            name = ""
        return {"action": "cancel", "kind": TIMER if found.group("kind").startswith("timer") else REMINDER,
                "name": name, "task": found.group("task") or "", "all": "all" in text.split()[:3]}
    return None


# --- the book ---------------------------------------------------------------------------------------------------

class ReminderBook:
    def __init__(self, path: str | Path, clock=time.time):
        self.path, self.clock = Path(path), clock
        self._lock = threading.Lock()

    def _load(self) -> list[dict]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return [item for item in data.get("items", []) if isinstance(item, dict) and item.get("due")]
        except (OSError, ValueError):
            return []

    def _save(self, items: list[dict]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps({"items": items}, indent=2), encoding="utf-8")
        os.chmod(tmp, 0o600)
        os.replace(tmp, self.path)

    def items(self, kind: str | None = None) -> list[dict]:
        with self._lock:
            return sorted((i for i in self._load() if kind in (None, i["kind"])), key=lambda i: i["due"])

    def add(self, kind: str, due: float, *, name: str = "", task: str = "", seconds: int = 0,
            connector: str = "to") -> dict:
        item = {"id": uuid.uuid4().hex[:8], "kind": kind, "due": float(due), "name": name, "task": task,
                "connector": connector, "seconds": int(seconds), "created": self.clock()}
        with self._lock:
            items = self._load()
            items.append(item)
            self._save(items)
        return item

    def cancel(self, kind: str, *, name: str = "", task: str = "", everything: bool = False) -> list[dict]:
        """Cancel matching items: all of them, the named one, the one about `task`, or the only/soonest one."""
        with self._lock:
            items = self._load()
            mine = sorted((i for i in items if i["kind"] == kind), key=lambda i: i["due"])
            if name:
                chosen = [i for i in mine if i.get("name") == name]
            elif task:
                words = set(task.split())
                chosen = [max(mine, key=lambda i: len(words & set(i.get("task", "").split())))] if mine else []
                chosen = [i for i in chosen if words & set(i.get("task", "").split())]
            elif everything:
                chosen = mine
            else:
                chosen = mine[:1]
            ids = {i["id"] for i in chosen}
            self._save([i for i in items if i["id"] not in ids])
            return chosen

    def take_due(self, now: float | None = None) -> list[dict]:
        """Items whose time has come, removed from the book."""
        now = self.clock() if now is None else now
        with self._lock:
            items = self._load()
            due = [i for i in items if i["due"] <= now]
            if due:
                self._save([i for i in items if i["due"] > now])
            return sorted(due, key=lambda i: i["due"])


# --- what BMO says ----------------------------------------------------------------------------------------------

def confirmation(item: dict, now: datetime | None = None) -> str:
    now = now or datetime.now()
    if item["kind"] == TIMER:
        what = f"{item['name'].capitalize()} timer" if item.get("name") else "Timer"
        return f"{what} set for {spoken_duration(item['seconds'])}."
    when = datetime.fromtimestamp(item["due"])
    at = (f"in {spoken_duration(item['seconds'])}" if item.get("seconds")
          else f"at {spoken_clock(when, now)}")
    connector = "to" if item.get("connector", "to") == "to" else "that" if item.get("connector") == "that" else "about"
    return f"Okay, {at} I'll remind you {connector} {second_person(item['task'])}."


def alert(item: dict, *, late_minutes: float = 0, now: datetime | None = None) -> str:
    """What BMO says when an item comes due."""
    if item["kind"] == TIMER:
        return f"Ding ding! Your {timer_name(item)} is done."
    task = second_person(item.get("task", ""))
    if late_minutes >= 2:
        when = datetime.fromtimestamp(item["due"])
        return f"While I was off, you had a reminder at {spoken_clock(when, now or datetime.now())}: {task}."
    return f"Reminder: {task}."


def listing(items: list[dict], kind: str | None, now: datetime | None = None) -> str:
    now = now or datetime.now()
    if not items:
        return {TIMER: "There are no timers running.", REMINDER: "You don't have any reminders."}.get(
            kind, "You don't have any timers or reminders.")
    lines = []
    for item in items[:6]:
        left = item["due"] - now.timestamp()
        if item["kind"] == TIMER:
            lines.append(f"the {timer_name(item)} has {spoken_duration(max(0, left))} left")
        else:
            lines.append(f"at {spoken_clock(datetime.fromtimestamp(item['due']), now)}: "
                         f"{second_person(item.get('task', ''))}")
    more = f" And {len(items) - 6} more." if len(items) > 6 else ""
    text = "; ".join(lines)
    return text[0].upper() + text[1:] + "." + more


def cancelled(chosen: list[dict], kind: str) -> str:
    if not chosen:
        return "There's no timer like that running." if kind == TIMER else "I couldn't find that reminder."
    if len(chosen) > 1:
        return f"Cancelled {len(chosen)} {kind}s."
    item = chosen[0]
    if kind == TIMER:
        return f"Cancelled the {item['name'] + ' ' if item.get('name') else ''}timer."
    return f"Cancelled the reminder to {second_person(item.get('task', ''))}."


# --- the Supervisor's side --------------------------------------------------------------------------------------

class ReminderKeeper:
    """Runs in the Supervisor: answers BMO's requests and says each item when it comes due, awake or asleep."""

    def __init__(self, path: str | Path, *, say=None, clock=time.time, poll_seconds: float = 1.0):
        import logging
        self.book = ReminderBook(path, clock=clock)
        self.clock, self.poll_seconds = clock, float(poll_seconds)
        self._say = say or self._say_aloud
        self._stop = threading.Event()
        self.log = logging.getLogger("bemo-reminders")

    @staticmethod
    def _say_aloud(text: str) -> None:
        import subprocess
        import sys
        app = Path(__file__).resolve().parents[1]
        subprocess.run([sys.executable, str(app / "say_cli.py"), text], cwd=str(app), timeout=120,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)

    def handle(self, request: dict) -> dict:
        op, kind = request.get("op"), request.get("kind")
        now = datetime.fromtimestamp(self.clock())
        if op == "add" and kind in (TIMER, REMINDER):
            seconds = int(request.get("seconds") or 0)
            due = now.timestamp() + seconds if kind == TIMER else float(request.get("due") or 0)
            if due <= now.timestamp():
                return {"ok": False, "say": "That time has already passed."}
            item = self.book.add(kind, due, name=str(request.get("name") or ""), task=str(request.get("task") or ""),
                                 seconds=seconds, connector=str(request.get("connector") or "to"))
            self.log.info("%s set, due in %.0f s", kind.capitalize(), due - now.timestamp())
            return {"ok": True, "say": confirmation(item, now)}
        if op == "list":
            return {"ok": True, "say": listing(self.book.items(kind), kind, now)}
        if op == "cancel" and kind in (TIMER, REMINDER):
            chosen = self.book.cancel(kind, name=str(request.get("name") or ""), task=str(request.get("task") or ""),
                                      everything=bool(request.get("all")))
            self.log.info("%d %s(s) cancelled", len(chosen), kind)
            return {"ok": True, "say": cancelled(chosen, kind)}
        return {"ok": False, "say": "I didn't understand that timer request."}

    def check_once(self) -> list[str]:
        """Say everything that is due; returns what was said (for tests and logs)."""
        said = []
        now = self.clock()
        for item in self.book.take_due(now):
            late = now - item["due"]
            if item["kind"] == TIMER and late > 120:
                self.log.info("A timer finished while BMO was off (%.0f s ago); not said", late)
                continue
            if item["kind"] == REMINDER and late > MISSED_REMINDER_HOURS * 3600:
                self.log.info("A reminder came due %.1f h ago, while BMO was off; not said", late / 3600)
                continue
            text = alert(item, late_minutes=late / 60, now=datetime.fromtimestamp(now))
            self.log.info("%s came due; saying it", item["kind"].capitalize())
            try:
                self._say(text)
            except Exception:
                self.log.exception("Could not say a %s", item["kind"])
            said.append(text)
        return said

    def start(self) -> threading.Thread:
        def loop():
            while not self._stop.wait(self.poll_seconds):
                try:
                    self.check_once()
                except Exception:
                    self.log.exception("Reminder check failed")
        thread = threading.Thread(target=loop, name="bemo-reminders", daemon=True)
        thread.start()
        return thread

    def stop(self) -> None:
        self._stop.set()
