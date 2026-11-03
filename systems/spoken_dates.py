"""Dates as they're said and as Whisper writes them (owner 2026-11-03, "the weather for November 8th"): "November 8th",
"November eighth", "the 8th of November", "11/8", "on the 8th", "November 8th through the 10th", "in five days", "a week
from Friday", and a month alone, "in December". Only reads the words: what's too far out is the forecast's business.
"""
from __future__ import annotations

from datetime import date, timedelta
import re
from typing import NamedTuple

_MONTH_NAMES = ("january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
                "november", "december")
_MONTHS = {name: n for n, name in enumerate(_MONTH_NAMES, 1)}
# Not "mar", "jun" or "jul": names and sounds more often than months.
_MONTHS.update(jan=1, feb=2, apr=4, aug=8, sep=9, sept=9, oct=10, nov=11, dec=12)
_WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")

_SMALL = ("one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve", "thirteen",
          "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen")
_SMALL_ORDINAL = ("first", "second", "third", "fourth", "fifth", "sixth", "seventh", "eighth", "ninth", "tenth", "eleventh",
                  "twelfth", "thirteenth", "fourteenth", "fifteenth", "sixteenth", "seventeenth", "eighteenth", "nineteenth")
_CARDINALS = {word: n for n, word in enumerate(_SMALL, 1)}
_CARDINALS.update(twenty=20, thirty=30, thirty_one=31)
_CARDINALS.update({f"twenty_{word}": 20 + n for n, word in enumerate(_SMALL[:9], 1)})
_ORDINALS = {word: n for n, word in enumerate(_SMALL_ORDINAL, 1)}
_ORDINALS.update(twentieth=20, thirtieth=30, thirty_first=31, forth=4, fith=5)  # as Whisper may spell them
_ORDINALS.update({f"twenty_{word}": 20 + n for n, word in enumerate(_SMALL_ORDINAL[:9], 1)})


def _either(words) -> str:
    return "|".join(word.replace("_", "[- ]") for word in sorted(words, key=len, reverse=True))


MONTH = _either(_MONTHS)
ORDINAL = r"\d{1,2} ?(?:st|nd|rd|th)|" + _either(_ORDINALS)
DAY = ORDINAL + r"|\d{1,2}|" + _either(_CARDINALS)
_AFTER_A_DAY = r"(?=\W*$| (?:in|at|for|and|to|through|thru|until|till)\b)"
_MORE_DAYS = rf"(?:(?: (?:and|to|through|thru|until|till)(?: the)? | ?- ?)(?:{DAY}))*"
# Regex text for a date with no groups, for the other patterns: a question about the weather on one.
SPOKEN_DATE = (rf"(?:(?:{MONTH})\.?,?(?: the)? (?:{DAY})(?:,? 20\d\d)?{_MORE_DAYS}|(?:the )?(?:{ORDINAL})(?: of| in)? (?:{MONTH})|"
               rf"(?:1[0-2]|0?[1-9])/\d{{1,2}}(?:/\d{{2,4}})?|(?:on|for|from) the (?:{ORDINAL}){_AFTER_A_DAY}{_MORE_DAYS})"
               r"(?!\w)")
ALONE_MONTH = rf"(?:{'|'.join(_MONTH_NAMES)})(?=\W*$| (?:in|at|for)\b)"
_NUMBER = r"\d{1,2}|" + _either(_SMALL)
FROM_NOW = (rf"(?:in (?:about )?(?:{_NUMBER}|a|an) (?:days?|weeks?)|"
            rf"(?:{_NUMBER}|a) (?:days?|weeks?) from (?:now|today|tomorrow|{'|'.join(_WEEKDAYS)}))(?!\w)")

_MENTION = re.compile(
    rf"\b(?P<month>{MONTH})(?: the)? (?P<day>{DAY})(?: (?P<year>20\d\d))?\b|"
    rf"\b(?P<day2>{ORDINAL})(?: of| in)? (?P<month2>{MONTH})\b(?: (?P<year2>20\d\d)\b)?|"
    rf"\b(?P<month3>1[0-2]|0?[1-9])/(?P<day3>\d{{1,2}})(?:/\d{{2,4}})?(?!\w)|"
    rf"\b(?:on|for|from) the (?P<day4>{ORDINAL})\b{_AFTER_A_DAY}")
_MORE = re.compile(rf" (?P<word>and|to|through|thru|until|till)(?: the)? (?P<day>{DAY})\b(?! (?:days?|weeks?)\b)(?! (?:{MONTH})\b)")
_SPAN = re.compile(r"\s*\b(?:to|through|thru|until|till)\b\s*(?:the\s*)?")
_FROM_NOW = re.compile(
    rf"\bin (?:about )?(?P<count>{_NUMBER}|a|an) (?P<unit>day|week)s?\b|"
    rf"\b(?P<count2>{_NUMBER}|a) (?P<unit2>day|week)s? from (?P<base>now|today|tomorrow|{'|'.join(_WEEKDAYS)})\b")
_MONTH_ALONE = re.compile(rf"\b(?:in|for|during|this|next|rest of(?: the month of)?) (?:the month of )?(?P<month>{'|'.join(_MONTH_NAMES)})\b(?=\W*$| (?:in|at|for)\b)")


class Said(NamedTuple):
    dates: list[date]
    label: str     # how to name them when they can't be answered: "November 30th", "December"
    no_such: str   # "November 31st", when there isn't one


def ordinal(n: int) -> str:
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def spoken(day: date) -> str:
    return f"{day.strftime('%B')} {ordinal(day.day)}"


def _day_number(word: str) -> int:
    word = word.replace("-", " ").strip()
    digits = re.match(r"\d+", word)
    if digits:
        return int(digits.group())
    word = word.replace(" ", "_")
    return _ORDINALS.get(word) or _CARDINALS.get(word) or 0


def _plain(text: str) -> str:
    said = str(text or "").lower().replace("'s", "").replace("’s", "")
    said = re.sub(r"(\d(?:st|nd|rd|th)?) ?- ?(\d)", r"\1 to \2", said)  # "November 8-10"
    return re.sub(r"\s+", " ", re.sub(r"[,.?!;:-]", " ", said)).strip()


def _month_and_day(month: int, day: int, year: str | None, today: date) -> date:
    """That date, this year unless it was more than a couple of months ago. Raises ValueError for November 31st."""
    if year:
        return date(int(year), month, day)
    when = date(today.year, month, day)
    return when.replace(year=today.year + 1) if (today - when).days > 60 else when


def _just_a_day(day: int, today: date, since: date | None = None) -> date:
    """"The 8th": the next one, from today or from the date just said."""
    since = since or today
    year, month = since.year, since.month
    for _ in range(3):
        try:
            when = date(year, month, day)
        except ValueError:
            when = None
        if when and when >= since:
            return when
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    raise ValueError(day)


def _between(start: date, end: date) -> list[date]:
    return [start + timedelta(days=n) for n in range(1, (end - start).days)]


def _from_now(said: str, today: date) -> date | None:
    found = _FROM_NOW.search(said)
    if not found:
        return None
    count = found.group("count") or found.group("count2")
    number = 1 if count in ("a", "an", "one") else int(count) if count.isdigit() else _CARDINALS[count]
    days = number * (7 if (found.group("unit") or found.group("unit2")) == "week" else 1)
    base = found.group("base") or "today"
    if base == "tomorrow":
        days += 1
    elif base in _WEEKDAYS:
        days += (_WEEKDAYS.index(base) - today.weekday()) % 7
    return today + timedelta(days=days)


def _month_alone(said: str, today: date) -> Said | None:
    found = _MONTH_ALONE.search(said)
    if not found:
        return None
    month = _MONTHS[found.group("month")]
    year = today.year if month >= today.month else today.year + 1
    day, days = date(year, month, 1), []
    while day.month == month:
        if day >= today:
            days.append(day)
        day += timedelta(days=1)
    return Said(days, found.group("month").capitalize(), "")


def read(text: str, today: date) -> Said | None:
    """The dates in what was said, in order, or None if it doesn't name any."""
    said = _plain(text)
    days: list[date] = []
    no_such = ""
    place = 0
    for found in _MENTION.finditer(said):
        between = said[place:found.start()]
        month_word = found.group("month") or found.group("month2")
        month = _MONTHS[month_word] if month_word else int(found.group("month3") or 0)
        number = _day_number(found.group("day") or found.group("day2") or found.group("day3") or found.group("day4"))
        if month_word in ("may", "march") and found.group("day") and found.group("day") in _CARDINALS:
            continue  # "the weather may one day", not May 1st
        try:
            if month:
                when = _month_and_day(month, number, found.group("year") or found.group("year2"), today)
            else:
                when = _just_a_day(number, today)
        except ValueError:
            no_such = f"{date(2024, month, 1).strftime('%B')} {ordinal(number)}" if 1 <= month <= 12 else ordinal(number)
            place = found.end()
            continue
        if days and _SPAN.fullmatch(between) and days[-1] < when <= days[-1] + timedelta(days=62):
            days.extend(_between(days[-1], when))
        days.append(when)
        place = found.end()
        more = _MORE.match(said, place)
        while more:
            number = _day_number(more.group("day"))
            try:
                later = _just_a_day(number, today, days[-1])
            except ValueError:
                break
            if more.group("word") != "and":
                days.extend(_between(days[-1], later))
            days.append(later)
            place = more.end()
            more = _MORE.match(said, place)
    days = list(dict.fromkeys(days))
    if days or no_such:
        return Said(days, spoken(days[0]) if days else "", no_such)
    soon = _from_now(said, today)
    if soon:
        return Said([soon], spoken(soon), "")
    return _month_alone(said, today)
