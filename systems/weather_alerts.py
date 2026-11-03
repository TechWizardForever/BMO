"""Severe weather warnings for your town (owner 2026-11-03), from the National Weather Service.

Free and open, no account (their page says a key may come later), and it covers the United States, Alaska and Hawaii
included. Nothing is contacted until the owner asks or turns the alerts on by voice; then the Supervisor checks every two
minutes, asleep too, with the saved coordinates rounded to about a kilometer.

Most alerts stay quiet. What he says out loud depends on how serious the alert is (level()):
  urgent   Severe or Extreme, Immediate, a Warning: right away, day or night, even over a movie.
  serious  other Severe or Extreme alerts: once, when it's daytime and nothing's on. It waits, it isn't dropped.
  notice   the rest: only on the screen, when asked.
An update to an alert stays quiet, unless it's gotten more dangerous (danger()): a tornado seen on the ground, or a tornado
warning turned tornado emergency, is said again, by the same rules.
This isn't a replacement for a weather radio. If he can't reach the service for half an hour he says so, because a warning
system that has stopped working and doesn't say so is worse than none.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

from systems.sound_alikes import put_right

ALERTS_URL = "https://api.weather.gov/alerts/active"
USER_AGENT = "BMO-voice-assistant (github.com/TechWizardForever/BMO)"  # they ask every program to name itself
SOURCE_NAME = "National Weather Service"
POLL_SECONDS = 120.0
UNREACHABLE_SECONDS = 1800.0
_SEVERE = {"Severe", "Extreme"}
_ORDER = {"urgent": 0, "serious": 1, "notice": 2}
# The tags the Weather Service puts on a warning, in the feed's "parameters" (read live 2026-11-03). tornadoDamageThreat wasn't
# in the feed that day; the same words are in their documentation.
_THREATS = {"tornadoDamageThreat": {"CONSIDERABLE": (1, "This is a particularly dangerous situation."),
                                    "CATASTROPHIC": (2, "This is a tornado emergency.")},
            "flashFloodDamageThreat": {"CONSIDERABLE": (1, "Considerable flood damage is expected."),
                                       "CATASTROPHIC": (2, "This is a flash flood emergency.")},
            "thunderstormDamageThreat": {"CONSIDERABLE": (1, "Considerable damage is expected."),
                                         "DESTRUCTIVE": (2, "Destructive winds or hail are expected.")}}
_SEEN = {"tornadoDetection": {"POSSIBLE": (1, "A tornado is possible."), "RADAR INDICATED": (2, ""),
                              "OBSERVED": (3, "A tornado has been seen.")},
         "flashFloodDetection": {"RADAR INDICATED": (1, ""), "RADAR AND GAUGE INDICATED": (1, ""),
                                 "OBSERVED": (2, "Flash flooding has been seen.")}}
_SEVERITY = {"Severe": 1, "Extreme": 2}
_LEVEL = {"notice": 0, "serious": 1, "urgent": 2}


class AlertError(Exception):
    """A spoken-safe reason the alerts couldn't be read: they may still exist."""


class OutsideCoverage(AlertError):
    """The place isn't in the United States: a 400 from the service, not a failure."""


def _get(url: str, params: dict, timeout: float = 15.0) -> dict:
    full = url + "?" + urllib.parse.urlencode(params)
    request_ = urllib.request.Request(full, headers={"User-Agent": USER_AGENT, "Accept": "application/geo+json"})
    try:
        with urllib.request.urlopen(request_, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 400:
            raise OutsideCoverage("the National Weather Service only covers the United States") from exc
        raise AlertError("I couldn't reach the National Weather Service") from exc
    except (OSError, ValueError) as exc:
        raise AlertError("I couldn't reach the National Weather Service") from exc


def _moment(stamp) -> datetime | None:
    try:
        return datetime.fromisoformat(str(stamp))
    except ValueError:
        return None


def level(alert: dict) -> str:
    if alert.get("severity") not in _SEVERE:
        return "notice"
    urgent = alert.get("urgency") == "Immediate" and str(alert.get("event", "")).endswith("Warning")
    return "urgent" if urgent else "serious"


def danger(alert: dict, parameters: dict) -> tuple[int, str]:
    """How dangerous, as a number to compare with the same alert's last update, and what makes it so in words."""
    score, said = _LEVEL[level(alert)] + _SEVERITY.get(alert.get("severity"), 0), []
    for table in (_THREATS, _SEEN):
        for name, words in table.items():
            values = parameters.get(name) if isinstance(parameters, dict) else None
            found = words.get(str(values[0]).upper()) if isinstance(values, list) and values else None
            if found:
                score += found[0]
                said.append(found[1])
    return score, " ".join(part for part in said if part)


def _first_sentence(text, room: int = 180) -> str:
    """The first thing to do, short enough to say. NWS text is wrapped in lines, and now and then all capitals."""
    plain = re.sub(r"\s+", " ", str(text or "")).strip()
    if plain.isupper():
        plain = plain.capitalize()
    plain = re.split(r"(?<=[.!?]) ", plain)[0]
    if len(plain) > room:
        plain = plain[:room].rsplit(" ", 1)[0].rstrip(" ,;") + "."
    return plain


def read_alerts(data: dict, now: datetime) -> list[dict]:
    """The alerts that count, most serious first: real ones (the feed carries test messages), not cancelled, not over."""
    alerts = []
    for feature in data.get("features") or []:
        p = feature.get("properties") or {}
        if p.get("status") != "Actual" or p.get("messageType") == "Cancel" or not p.get("event") or p.get("event") == "Test Message":
            continue
        over = _moment(p.get("ends") or p.get("expires"))
        if over is not None and over <= now:
            continue
        alert = {"id": str(p.get("id") or ""), "event": str(p["event"]), "severity": p.get("severity"), "urgency": p.get("urgency"),
                 "until": p.get("ends"), "area": str(p.get("areaDesc") or ""), "instruction": _first_sentence(p.get("instruction")),
                 "references": [str(r.get("identifier")) for r in (p.get("references") or []) if isinstance(r, dict)]}
        alert["level"] = level(alert)
        alert["danger"], alert["why"] = danger(alert, p.get("parameters") or {})
        alerts.append(alert)
    return sorted(alerts, key=lambda alert: _ORDER[alert["level"]])


def fetch_alerts(latitude: float, longitude: float, *, fetch=_get, now: datetime | None = None) -> list[dict]:
    data = fetch(ALERTS_URL, {"point": f"{latitude:.2f},{longitude:.2f}"})
    if "features" not in data:
        raise AlertError("the National Weather Service sent an answer I couldn't read")
    return read_alerts(data, now or datetime.now(timezone.utc))


def _zone(place):
    try:
        return ZoneInfo(place.timezone)
    except Exception:
        return None  # "auto": this computer's own


def until_words(alert: dict, place, now: datetime) -> str:
    """'until 12:45 PM', 'until Thursday at 4:00 PM', or nothing when the service gave no end."""
    end = _moment(alert.get("until"))
    if end is None:
        return ""
    zone = _zone(place)
    local, today = end.astimezone(zone), now.astimezone(zone)
    hour = local.hour % 12 or 12
    clock = f"{hour}:{local.minute:02d} {'AM' if local.hour < 12 else 'PM'}"
    return f"until {clock}" if local.date() == today.date() else f"until {local.strftime('%A')} at {clock}"


def spoken_alert(alert: dict, place, now: datetime, *, worse: bool = False) -> str:
    """What he says when it comes in, the most useful thing first."""
    until = until_words(alert, place, now)
    opening = "Weather alert! It just got more dangerous." if worse else "Weather alert!"
    parts = [f"{opening} {alert['event']} here{', ' + until if until else ''}, from the {SOURCE_NAME}.", alert.get("why"), alert.get("instruction")]
    return " ".join(part for part in parts if part)


def spoken_summary(alerts: list[dict], place, now: datetime) -> str:
    """The answer to "any weather alerts?"."""
    if not alerts:
        return f"No weather alerts for {place.name} right now. That's from the {SOURCE_NAME}."
    said = [" ".join(part for part in (alert["event"], until_words(alert, place, now)) if part) for alert in alerts[:3]]
    if len(alerts) == 1:
        first = alerts[0]
        ending = " ".join(part for part in (first.get("why"), first.get("instruction")) if part) if first["level"] != "notice" else ""
        ending = f" {ending}" if ending else ""
        return f"{said[0]}, for {place.name}.{ending} That's from the {SOURCE_NAME}."
    more = f", and {len(alerts) - 3} more" if len(alerts) > 3 else ""
    return f"{len(alerts)} weather alerts for {place.name}: {'; '.join(said)}{more}. That's from the {SOURCE_NAME}."


def screen_card(alert: dict, place, now: datetime) -> dict:
    return {"event": alert["event"], "level": alert["level"], "until": until_words(alert, place, now),
            "area": alert.get("area", ""), "instruction": " ".join(part for part in (alert.get("why"), alert.get("instruction")) if part)}


# --- What was said ----------------------------------------------------------------------------------------

_KIND = r"(?:weather|severe weather|storm|tornado|flood|flash flood|thunderstorm|severe thunderstorm|winter storm|blizzard|hurricane|tsunami|fire|red flag|heat|wind)"
_NOTICE = r"(?:alerts?|warnings?|watch|watches|advisory|advisories)"
_MY_ALERTS = r"(?:weather|severe weather|storm) (?:alerts?|warnings?|notifications?)"
_OTHER_TOPIC = re.compile(r"\b(?:movies?|films?|videos?|tv shows?|episodes?|songs?|games?|scenes?|books?|pictures?|photos?)\b")
_CLOSING = re.compile(r"\b(?:close|clothes|hide|exit|quit|dismiss|get rid of|take away|put away) (?:the |this |that |my )?(?:\w+ ){0,2}"
                      r"(?:alerts?|warnings?)\b")
_OFF = re.compile(rf"\b(?:turn off|shut off|disable|stop|switch off|no more|quiet|mute|silence|cancel|kill)\b.*\b{_MY_ALERTS}\b|"
                  rf"\b(?:turn|shut|switch) (?:the |my )?{_MY_ALERTS} off\b|^(?:the )?{_MY_ALERTS} off$|"
                  r"\b(?:stop|quit|dont|do not)\b.*\b(?:telling|tell|warning|alerting|bugging|notifying)\b.*\b(?:weather|storms?)\b")
_ON = re.compile(rf"\b(?:turn on|enable|start|switch on|i want|give me)\b.*\b{_MY_ALERTS}\b|"
                 rf"\b(?:turn|switch) (?:the |my )?{_MY_ALERTS} on\b|^(?:the )?{_MY_ALERTS} on$|"
                 r"\b(?:tell|warn|alert|let|notify) (?:me|us)\b.*\b(?:when|if)\b.*\b(?:severe weather|bad weather|bad storm|storm|tornado)s?\b(?!.*\b(?:work|mean)\b)")
_ADJECTIVE = r"(?: active| current| severe| local| new| big| bad)?"
_LIST = re.compile(
    rf"\b(?:any|are there(?: any)?|is there(?: a| an| any)?|do we have(?: a| an| any)?|do i have(?: a| an| any)?|"
    rf"show(?: me)?(?: the| any| my)?|check(?: for| the)?(?: any)?|what(?:s| is| are)(?: the)?|read(?: me)?(?: the)?|tell me(?: the| about the)?|got(?: a| an| any)?)"
    rf"{_ADJECTIVE} {_KIND} {_NOTICE}\b|"
    rf"^(?:the )?{_MY_ALERTS}$|\b(?:any|is there(?: any)?|are there(?: any)?){_ADJECTIVE} (?:severe|dangerous|bad) weather\b|^severe weather$|"
    rf"\b(?:is|are) (?:there )?(?:a |an |any )?{_KIND} {_NOTICE} (?:out|active|on|here|near|in effect)\b")
_ALERTS_ALONE = re.compile(r"^(?:turn on|turn off|enable|disable|stop|mute|cancel)(?: (?:the|my|all))* alerts?$|"
                           r"^turn (?:(?:the|my|all) )*alerts? (?:on|off)$")


def request(text: str) -> str | None:
    """'alerts on', 'alerts off', 'list', 'which' ("turn off the alerts": space or weather?), or None."""
    said = re.sub(r"[^\w\s]", "", put_right(text).lower())
    said = re.sub(r"\s+", " ", said).strip()
    if _OTHER_TOPIC.search(said) or _CLOSING.search(said):
        return None
    if _OFF.search(said):
        return "alerts off"
    if _ON.search(said):
        return "alerts on"
    if _ALERTS_ALONE.search(said):
        return "which"
    return "list" if _LIST.search(said) else None


# --- The watcher, in the Supervisor -----------------------------------------------------------------------

class AlertWatcher:
    """Checks for alerts while the owner wants them, says each once, and keeps the latest for BMO's screens."""

    def __init__(self, state_path, load_place, *, say=None, busy=lambda: False, hours=(8, 23), fetch=_get, clock=time.time,
                 poll_seconds: float = POLL_SECONDS):
        from systems.sky import SkyWatcher  # the same voice, chime and all
        self.state_path = Path(state_path)
        self.load_place, self.busy, self.hours, self.fetch, self.clock = load_place, busy, hours, fetch, clock
        self.poll_seconds = float(poll_seconds)
        self._say = say or SkyWatcher._say_aloud
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._active: list[dict] = []
        self._for_screen: list[dict] | None = None
        self._failing_since: float | None = None
        self._told_failing = False
        self.log = logging.getLogger("bemo-weather-alerts")

    def _load(self) -> dict:
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save(self, state: dict) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(state), encoding="utf-8")
        tmp.replace(self.state_path)

    @property
    def enabled(self) -> bool:
        return bool(self._load().get("alerts"))

    def set_enabled(self, on: bool) -> None:
        with self._lock:
            state = self._load()
            state["alerts"] = bool(on)
            self._save(state)
            if not on:
                self._active, self._failing_since, self._told_failing = [], None, False

    def _daytime(self, place, now: datetime) -> bool:
        return self.hours[0] <= now.astimezone(_zone(place)).hour < self.hours[1]

    def _speak(self, text: str) -> None:
        try:
            self._say(text)
        except Exception:
            self.log.exception("Could not say a weather alert")

    def _note_failure(self, place, now: datetime, stamp: float) -> str | None:
        if self._failing_since is None:
            self._failing_since = stamp
        if (not self._told_failing and stamp - self._failing_since >= UNREACHABLE_SECONDS
                and self._daytime(place, now) and not self.busy()):
            self._told_failing = True
            text = f"I can't reach the {SOURCE_NAME}, so I can't warn you about severe weather right now. I'll keep trying."
            self._speak(text)
            return text
        return None

    def check_once(self) -> list[str]:
        """Say what's new; returns what was said (for tests and logs)."""
        if not self.enabled:
            return []
        place = self.load_place()
        if place is None:
            return []
        stamp = self.clock()
        now = datetime.fromtimestamp(stamp, timezone.utc)
        try:
            alerts = fetch_alerts(place.latitude, place.longitude, fetch=self.fetch, now=now)
        except OutsideCoverage:
            self.log.info("Weather alerts: the saved place is outside the United States")
            self._active = []
            return []
        except AlertError as exc:
            self.log.warning("Weather alerts: %s", exc)
            text = self._note_failure(place, now, stamp)
            return [text] if text else []
        said = []
        if self._told_failing:
            text = f"I can reach the {SOURCE_NAME} again, so severe weather alerts are back on."
            self._speak(text)
            said.append(text)
        self._failing_since, self._told_failing = None, False
        self._active = alerts
        for alert in alerts:
            if alert["level"] == "notice":
                continue
            if alert["level"] == "serious" and (not self._daytime(place, now) or self.busy()):
                continue  # still active next time, so it isn't lost
            news = self._news(alert, stamp)
            if not news:
                continue
            text = spoken_alert(alert, place, now, worse=news == "worse")
            self.log.info("Weather alert: %s", alert["event"])
            self._for_screen = [screen_card(a, place, now) for a in alerts]
            self._speak(text)
            said.append(text)
        return said

    def _news(self, alert: dict, stamp: float) -> str | None:
        """'new', 'worse', or None. An update comes with a new id and names the alert it updates: the same warning isn't said
        twice, unless it's more dangerous than it has ever been (down and back up again is nothing new)."""
        with self._lock:
            state = self._load()
            done = state.get("said") if isinstance(state.get("said"), dict) else {}
            earlier = [done[ref] for ref in alert["references"] if done.get(ref, {}).get("event") == alert["event"]]
            if alert["id"] in done:
                earlier.append(done[alert["id"]])
            worst = max((float(row.get("danger", alert["danger"])) for row in earlier), default=None)
            news = "new" if not earlier else "worse" if alert["danger"] > worst and alert["id"] not in done else None
            done[alert["id"]] = {"event": alert["event"], "at": stamp, "danger": max(alert["danger"], worst or 0)}
            state["said"] = {key: row for key, row in done.items() if stamp - float(row.get("at", 0)) < 7 * 86400}
            self._save(state)
        return news

    def handle(self, request_: dict) -> dict:
        op = request_.get("op")
        if op == "alert":
            cards, self._for_screen = self._for_screen, None
            return {"ok": True, "cards": cards}
        if op == "set":
            self.set_enabled(bool(request_.get("on")))
            return {"ok": True}
        if op == "get":
            serious = [alert["event"] for alert in self._active if alert["level"] != "notice"]
            return {"ok": True, "on": self.enabled, "serious": serious}
        return {"ok": False}

    def start(self) -> threading.Thread:
        def loop():
            while not self._stop.wait(self.poll_seconds):
                try:
                    self.check_once()
                except Exception:
                    self.log.exception("Weather alert check failed")
        thread = threading.Thread(target=loop, name="bemo-weather-alerts", daemon=True)
        thread.start()
        return thread

    def stop(self) -> None:
        self._stop.set()
