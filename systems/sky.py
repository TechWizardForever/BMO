"""What's in the sky where you are (owner 2026-11-03): the moon, the planets, the space station, eclipses, meteor showers,
and spoken alerts for the big ones.

Worked out on this PC by PyEphem, which needs no data files and gets the sky from your saved weather location. Only three
things come from outside: the space station's current orbit (CelesTrak, says nothing about where you are, at most every
12 hours), the aurora outlook (NOAA, the same) and tonight's clouds (Open-Meteo, which has your rounded coordinates for the
weather already). Alerts are off until the owner turns them on by voice, because BMO talks out loud for them. The Supervisor
keeps them, so they go off while he sleeps too, but not at night (SKY_ALERT_HOURS) and not over a movie or a game.
Predictions from the orbit go stale: past a week he says he has no pass times rather than guess.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import logging
import math
import os
from pathlib import Path
import re
import threading
import time
import urllib.request
from zoneinfo import ZoneInfo

from systems.sound_alikes import put_right

try:
    import ephem
except ImportError:  # an older install before its packages are updated: he says so, the rest of BMO carries on
    ephem = None

ISS_TLE_URL = "https://celestrak.org/NORAD/elements/gp.php?CATNR=25544&FORMAT=TLE"
KP_URL = "https://services.swpc.noaa.gov/products/noaa-planetary-k-index-forecast.json"
ORBIT_REFRESH_SECONDS = 12 * 3600
ORBIT_TOO_OLD_DAYS = 7
PASS_HORIZON = "10"           # trees and houses: a pass counts from here up
PASS_MIN_HEIGHT = 20          # lower than this isn't worth going out for
PASS_ALERT_HEIGHT = 30
PASS_ALERT_MINUTES = 5
CLOUDY_PERCENT = 85           # no alert: nothing to see through this
EVENING_HOUR = 20             # when the alert for a night's event is said
EVENT_LEAD_MINUTES = 15
LATE_MINUTES = 10             # an alert this late is dropped, not said
ISS_MPH, MILES_PER_METER = 17100, 1 / 1609.34

_UTC = timezone.utc
_NAMES = {"N": "north", "NE": "northeast", "E": "east", "SE": "southeast", "S": "south", "SW": "southwest", "W": "west",
          "NW": "northwest"}
_COMPASS = tuple(_NAMES)
PLANETS = ("Mercury", "Venus", "Mars", "Jupiter", "Saturn")
# Each peak moves a day or so from year to year, so he says "around". Rates are the usual best under a dark sky.
SHOWERS = (("Quadrantids", 1, 3, 110, True), ("Lyrids", 4, 22, 18, False), ("Eta Aquariids", 5, 6, 50, False),
           ("Delta Aquariids", 7, 30, 25, False), ("Perseids", 8, 12, 100, True), ("Orionids", 10, 21, 20, False),
           ("Leonids", 11, 17, 15, False), ("Geminids", 12, 14, 150, True), ("Ursids", 12, 22, 10, False))
_MOON_NAMES = ((0.03, "new moon"), (0.22, "waxing crescent"), (0.28, "first quarter"), (0.47, "waxing gibbous"),
               (0.53, "full moon"), (0.72, "waning gibbous"), (0.78, "last quarter"), (0.97, "waning crescent"),
               (1.01, "new moon"))


class SkyError(Exception):
    """A spoken-safe reason he can't say."""


def available() -> bool:
    return ephem is not None


def compass(azimuth_radians: float) -> str:
    return _COMPASS[int((math.degrees(float(azimuth_radians)) + 22.5) // 45) % 8]


def direction_word(short: str) -> str:
    return _NAMES.get(short, short)


def clock(when: datetime) -> str:
    hour = when.hour % 12 or 12
    return f"{hour}:{when.minute:02d} {'AM' if when.hour < 12 else 'PM'}"


def day_and_clock(when: datetime, now: datetime) -> str:
    if when.date() == now.date():
        return clock(when)
    if when.date() == (now + timedelta(days=1)).date():
        return f"tomorrow {clock(when)}"
    return f"{when.strftime('%a')} {clock(when)}"


def _e(when: datetime):
    return ephem.Date(when.astimezone(_UTC).replace(tzinfo=None))


def _degrees(angle) -> float:
    return math.degrees(float(angle))


def _arcsec_to_radians(value: float) -> float:
    return math.radians(float(value) / 3600.0)


class Sky:
    """The sky over one place. Times go in and come out in that place's time zone."""

    def __init__(self, latitude: float, longitude: float, zone: str = "UTC"):
        if ephem is None:
            raise SkyError("I don't have my star charts installed yet")
        self.latitude, self.longitude = float(latitude), float(longitude)
        try:
            self.zone = ZoneInfo(zone) if zone and zone != "auto" else _UTC
        except Exception:
            self.zone = _UTC

    def _site(self, when=None, horizon: str = "0"):
        site = ephem.Observer()
        site.lat, site.lon = str(self.latitude), str(self.longitude)
        site.pressure, site.horizon = 0, horizon
        if when is not None:
            site.date = when if isinstance(when, ephem.Date) else _e(when)
        return site

    def _local(self, moment) -> datetime:
        return ephem.Date(moment).datetime().replace(tzinfo=_UTC).astimezone(self.zone)

    def now(self, when: datetime | None = None) -> datetime:
        return (when or datetime.now(_UTC)).astimezone(self.zone)

    # --- The moon ---------------------------------------------------------------------------------------

    def moon(self, when: datetime) -> dict:
        moment = _e(when)
        body = ephem.Moon(moment)
        new_before, new_after = ephem.previous_new_moon(moment), ephem.next_new_moon(moment)
        cycle = (moment - new_before) / (new_after - new_before)
        site = self._site(when)
        info = {"lit": round(float(body.phase)), "cycle": cycle, "name": next(n for edge, n in _MOON_NAMES if cycle < edge),
                "age_days": round(moment - new_before, 1), "next_full": self._local(ephem.next_full_moon(moment)),
                "next_new": self._local(new_after),
                "miles": round(float(body.earth_distance) * 149597870.7 / 1.609344 / 1000) * 1000}
        for key, finder in (("rise", site.next_rising), ("set", site.next_setting)):
            try:
                info[key] = self._local(finder(ephem.Moon()))
            except (ephem.AlwaysUpError, ephem.NeverUpError):
                info[key] = None
        return info

    # --- The night, and the planets in it ---------------------------------------------------------------

    def night(self, when: datetime) -> tuple[datetime, datetime]:
        """The dark hours we're in, or the next ones: an hour after sunset to an hour before sunrise, near enough."""
        site = self._site(when)
        sun = ephem.Sun(site)
        try:
            if _degrees(sun.alt) < -6:
                return when, self._local(site.next_rising(ephem.Sun())) - timedelta(minutes=40)
            sunset = self._local(site.next_setting(ephem.Sun()))
            site.date = _e(sunset)
            return sunset + timedelta(minutes=40), self._local(site.next_rising(ephem.Sun())) - timedelta(minutes=40)
        except (ephem.AlwaysUpError, ephem.NeverUpError):
            return when, when + timedelta(hours=8)

    def planets(self, when: datetime) -> list[dict]:
        """Planets you can see in the dark hours, brightest first, each with where and when it's best."""
        start, end = self.night(when)
        steps = max(1, int((end - start).total_seconds() // 1200))
        seen = []
        for name in PLANETS:
            body = getattr(ephem, name)()
            best, first = None, None
            for step in range(steps + 1):
                moment = start + (end - start) * step / steps
                body.compute(self._site(moment))
                height = _degrees(body.alt)
                if height >= 12:
                    if first is None:
                        first = moment
                    if best is None or height > best[0]:
                        best = (height, moment, compass(body.az), float(body.mag))
            if best:
                seen.append({"name": name, "height": round(best[0]), "best": best[1], "from": first,
                             "direction": best[2], "mag": round(best[3], 1)})
        return sorted(seen, key=lambda planet: planet["mag"])

    # --- The space station ------------------------------------------------------------------------------

    def iss_passes(self, orbit: list[str], when: datetime, days: float = 5.0, min_height: float = PASS_MIN_HEIGHT) -> list[dict]:
        """Passes you can actually see: the station in sunlight, you in the dark. Times are when it can be seen."""
        satellite = ephem.readtle(*orbit)
        site = self._site(when, PASS_HORIZON)
        limit, found = _e(when + timedelta(days=days)), []
        for _ in range(80):
            try:
                rise, _az, _top, _height, setting, _az2 = site.next_pass(satellite)
            except (ValueError, ephem.CircumpolarError):
                break
            if rise is None or setting is None or rise > limit:
                break
            watched = []
            for step in range(25):
                moment = ephem.Date(rise + (setting - rise) * step / 24)
                look = self._site(moment, PASS_HORIZON)
                satellite.compute(look)
                if not satellite.eclipsed and _degrees(ephem.Sun(look).alt) < -4 and _degrees(satellite.alt) > 0:
                    watched.append((moment, _degrees(satellite.alt), compass(satellite.az)))
            if watched:
                height = max(row[1] for row in watched)
                if height >= min_height:
                    found.append({"start": self._local(watched[0][0]), "end": self._local(watched[-1][0]),
                                  "height": round(height), "from": watched[0][2], "to": watched[-1][2],
                                  "minutes": round((watched[-1][0] - watched[0][0]) * 1440)})
            site.date = ephem.Date(setting + ephem.minute)
        return found

    @staticmethod
    def iss_now(orbit: list[str], when: datetime) -> dict:
        """Where the station is this moment, for the screen."""
        satellite = ephem.readtle(*orbit)
        satellite.compute(_e(when))
        latitude, longitude = _degrees(satellite.sublat), _degrees(satellite.sublong)
        return {"over": f"{abs(round(latitude))}{'N' if latitude >= 0 else 'S'} {abs(round(longitude))}{'E' if longitude >= 0 else 'W'}",
                "miles_up": round(float(satellite.elevation) * MILES_PER_METER), "mph": ISS_MPH}

    # --- Eclipses ---------------------------------------------------------------------------------------

    def lunar_eclipses(self, when: datetime, days: int = 365) -> list[dict]:
        """Partial and total eclipses of the moon you can see from here. A penumbral one hardly shows, so it isn't here."""
        moment, limit, found = _e(when), _e(when + timedelta(days=days)), []
        while True:
            full = ephem.next_full_moon(moment)
            if full > limit:
                return found
            moment = ephem.Date(full + 1)
            if abs(_degrees(ephem.Ecliptic(ephem.Moon(full)).lat)) > 1.7:
                continue
            times = []
            for step in range(-72, 73):
                at = ephem.Date(full + step * 5 * ephem.minute)
                moon, sun = ephem.Moon(at), ephem.Sun(at)
                km = float(moon.earth_distance) * 149597870.7
                umbra = 1.02 * (0.99834 * math.asin(6378.137 / km) - _arcsec_to_radians(sun.size) / 2 + _arcsec_to_radians(8.794))
                radius = _arcsec_to_radians(moon.size) / 2
                gap = float(ephem.separation((moon.g_ra, moon.g_dec),
                                             (ephem.degrees(float(sun.g_ra) + math.pi), ephem.degrees(-float(sun.g_dec)))))
                if gap < umbra + radius:
                    times.append((at, gap, umbra, radius))
            if not times:
                continue
            seen = []
            for row in times:
                look = self._site(row[0])
                if _degrees(ephem.Moon(look).alt) > 0:
                    seen.append(row)
            if not seen:
                continue
            middle = min(times, key=lambda row: row[1])
            total = middle[1] < middle[2] - middle[3]
            found.append({"kind": "lunar eclipse", "total": total, "start": self._local(seen[0][0]),
                          "end": self._local(seen[-1][0]), "peak": self._local(middle[0]),
                          "title": f"{'Total' if total else 'Partial'} eclipse of the moon", "important": True})

    def solar_eclipses(self, when: datetime, days: int = 365) -> list[dict]:
        """Eclipses of the sun that reach you, however partly."""
        moment, limit, found = _e(when), _e(when + timedelta(days=days)), []
        while True:
            new = ephem.next_new_moon(moment)
            if new > limit:
                return found
            moment = ephem.Date(new + 1)
            if abs(_degrees(ephem.Ecliptic(ephem.Moon(new)).lat)) > 1.7:
                continue
            times = []
            for step in range(-72, 73):
                at = ephem.Date(new + step * 5 * ephem.minute)
                look = self._site(at)
                sun, moon = ephem.Sun(look), ephem.Moon(look)
                if _degrees(sun.alt) <= 0:
                    continue
                gap = float(ephem.separation(sun, moon))
                sun_radius, moon_radius = _arcsec_to_radians(sun.size) / 2, _arcsec_to_radians(moon.size) / 2
                if gap < sun_radius + moon_radius:
                    times.append((at, gap, sun_radius, moon_radius))
            if not times:
                continue
            middle = min(times, key=lambda row: row[1])
            at_peak, gap, sun_radius, moon_radius = middle
            covered = (sun_radius + moon_radius - gap) / (2 * sun_radius)
            central = gap < abs(sun_radius - moon_radius)
            kind = ("Total" if moon_radius > sun_radius else "Annular") if central else "Partial"
            found.append({"kind": "solar eclipse", "total": kind == "Total", "start": self._local(times[0][0]),
                          "end": self._local(times[-1][0]), "peak": self._local(at_peak), "covered": round(min(1.0, covered) * 100),
                          "title": f"{kind} eclipse of the sun", "important": True})

    # --- Meteors and close meetings ---------------------------------------------------------------------

    def meteor_showers(self, when: datetime, days: int = 365) -> list[dict]:
        found = []
        last = when + timedelta(days=days)
        for year in (when.year, when.year + 1):
            for name, month, day, rate, major in SHOWERS:
                peak = datetime(year, month, day, 21, 0, tzinfo=self.zone)
                if when.date() <= peak.date() <= last.date():
                    moon = self.moon(peak)
                    found.append({"kind": "meteors", "name": name, "peak": peak, "start": peak, "rate": rate,
                                  "moon_lit": moon["lit"], "title": f"{name} meteor shower", "important": major})
        return found

    def close_meetings(self, when: datetime, days: int = 45) -> list[dict]:
        """Two planets, or the moon and a planet, close together in the evening sky."""
        pairs = [(a, b, 1.5) for n, a in enumerate(PLANETS) for b in PLANETS[n + 1:]] + [("Moon", p, 2.5) for p in PLANETS]
        best: dict[tuple, dict] = {}
        for offset in range(days):
            start, _end = self.night(when + timedelta(days=offset))
            look = self._site(start)
            for first, second, limit in pairs:
                one, two = getattr(ephem, first)(look), getattr(ephem, second)(look)
                if _degrees(one.alt) < 10 or _degrees(two.alt) < 10:
                    continue
                apart = _degrees(ephem.separation(one, two))
                if apart < limit and ((first, second) not in best or apart < best[(first, second)]["apart"]):
                    bright = first == "Moon" and second in ("Venus", "Jupiter") and apart < 1.5
                    bright = bright or (first != "Moon" and apart < 1.0 and "Mercury" not in (first, second))
                    best[(first, second)] = {"kind": "conjunction", "start": start, "peak": start, "apart": round(apart, 1),
                                             "direction": compass(two.az), "important": bright,
                                             "title": f"{'The moon' if first == 'Moon' else first} and {second} close together"}
        return list(best.values())

    def events(self, when: datetime, days: int = 365) -> list[dict]:
        found = (self.lunar_eclipses(when, days) + self.solar_eclipses(when, days) + self.meteor_showers(when, days)
                 + self.close_meetings(when, min(days, 45)))
        return sorted(found, key=lambda event: event["start"])


# --- The orbit, the aurora, the clouds --------------------------------------------------------------------

def _get(url: str, timeout: float = 8.0) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": "Bemo-local-assistant"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8")


def valid_orbit(lines: list[str]) -> bool:
    return (len(lines) == 3 and lines[1].startswith("1 25544") and lines[2].startswith("2 25544")
            and len(lines[1]) >= 60 and len(lines[2]) >= 60)


def orbit_age_days(orbit: list[str], now: datetime) -> float:
    year, day = int(orbit[1][18:20]), float(orbit[1][20:32])
    epoch = datetime(2000 + year, 1, 1, tzinfo=_UTC) + timedelta(days=day - 1)
    return (now - epoch).total_seconds() / 86400


class OrbitStore:
    """The station's latest orbit, kept on disk. Asks CelesTrak at most every 12 hours (they ask for no more)."""

    def __init__(self, path: str | Path, *, fetch=_get, clock=time.time):
        self.path, self._fetch, self._clock = Path(path), fetch, clock

    def _load(self) -> dict:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def get(self) -> list[str] | None:
        """The orbit, or None when he has none young enough to be trusted."""
        kept = self._load()
        orbit = kept.get("orbit") if isinstance(kept.get("orbit"), list) else None
        if self._clock() - float(kept.get("fetched") or 0) > ORBIT_REFRESH_SECONDS:
            try:
                lines = [line.strip() for line in self._fetch(ISS_TLE_URL).splitlines() if line.strip()]
                if valid_orbit(lines):
                    orbit = lines
                    tmp = self.path.with_suffix(".tmp")
                    self.path.parent.mkdir(parents=True, exist_ok=True)
                    tmp.write_text(json.dumps({"orbit": lines, "fetched": self._clock()}), encoding="utf-8")
                    tmp.replace(self.path)
            except (OSError, ValueError):
                pass  # the one he has will do until it's too old
        if not orbit or not valid_orbit(orbit):
            return None
        try:
            age = orbit_age_days(orbit, datetime.fromtimestamp(self._clock(), _UTC))
        except ValueError:
            return None
        return orbit if age <= ORBIT_TOO_OLD_DAYS else None


def aurora_needs(latitude: float) -> float:
    """The storm level (Kp) that usually brings the aurora into view this far from the pole. Rough on purpose."""
    latitude = abs(float(latitude))
    for edge, level in ((60, 3), (55, 5), (50, 6), (45, 7), (40, 8), (35, 9)):
        if latitude >= edge:
            return level
    return 99


def aurora_chance(rows: list, latitude: float, now: datetime) -> float | None:
    """The strongest storm level forecast in the next 18 hours, if it could reach this far; else None."""
    strongest = None
    for row in rows or []:
        try:
            moment = datetime.fromisoformat(str(row["time_tag"])).replace(tzinfo=_UTC)
            level = float(row["kp"])
        except (KeyError, TypeError, ValueError):
            continue
        if now - timedelta(hours=3) <= moment <= now + timedelta(hours=18):
            strongest = level if strongest is None else max(strongest, level)
    return strongest if strongest is not None and strongest >= aurora_needs(latitude) else None


def fetch_aurora(latitude: float, now: datetime, fetch=_get) -> float | None:
    try:
        return aurora_chance(json.loads(fetch(KP_URL)), latitude, now)
    except (OSError, ValueError):
        return None


def clouds_at(forecast: dict | None, when: datetime) -> int | None:
    """Percent of cloud at that hour, from a weather forecast in the same time zone, if it has one."""
    hourly = (forecast or {}).get("hourly") or {}
    stamp = when.strftime("%Y-%m-%dT%H")
    for moment, value in zip(hourly.get("time") or [], hourly.get("cloud_cover") or []):
        if str(moment).startswith(stamp):
            return None if value is None else round(float(value))
    return None


def sky_words(percent: int | None) -> str:
    if percent is None:
        return ""
    if percent < 25:
        return "clear"
    if percent < 60:
        return "partly cloudy"
    return "mostly cloudy" if percent < CLOUDY_PERCENT else "overcast"


# --- What was said ----------------------------------------------------------------------------------------

_PLANET = r"(?:mercury|venus|mars|jupiter|saturn)"
_BIG_EVENT = r"(?:eclipses?|meteor showers?|meteors|shooting stars?|falling stars?|aurora|northern lights)"
_WATCHED = r"(?:space|sky|iss|space station|astronomy)"
_NOTICES = r"(?:alerts?|notifications?|warnings?)"
_TONIGHT = re.compile(
    r"\b(?:what(?:s| is)(?: up| out| visible)? in the (?:night )?sky|show(?: me)? the (?:night )?sky|night sky|sky tonight|"
    r"what can (?:i|we) see (?:in the sky|tonight|up there)|what planets|which planets|planets? (?:tonight|out|up|visible)|"
    r"anything (?:cool |good |interesting )?(?:in the sky|to see tonight)|stargazing|"
    r"what(?:s| is) (?:up there|overhead|visible)|what(?:s| is) out tonight|what stars|which stars|stars (?:tonight|out|visible)|"
    rf"(?:where(?: is)?|where can (?:i|we) (?:see|find)|can (?:i|we) see|could (?:i|we) see) (?:the planet )?{_PLANET}|"
    rf"is {_PLANET} (?:out|up|visible))\b")
_MOON = re.compile(r"\b(?:moon phase|phase of the moon|what phase is the moon|show(?: me)? the moon|how full is the moon|"
                   r"(?:is|whats|what is) (?:it |there |the moon )?(?:a )?(?:full|new) moon|when(?: is)?(?: the)?(?: next)? (?:full|new) moon|"
                   r"what does the moon look like|moonrise|moonset|when does the moon|"
                   r"^(?:the )?moon(?: tonight)?$|what(?:s| is) the moon (?:doing|look)|how(?:s| is) the moon(?: look\w*| doing| tonight)?$|"
                   r"is the moon (?:full|new|out|up)|where(?: is)? the moon|next (?:full|new) moon)\b")
_STATION = r"(?:space station|the iss\b|iss\b|i s s\b)"
_ISS = re.compile(rf"\b(?:when|where|show|track|next|see|find|pull up|bring up|spot|what time)\b.*{_STATION}|"
                  rf"{_STATION}.*\b(?:pass|passes|passing|overhead|tonight|fly|flying|going over|go over|visible|above)\b|"
                  rf"^(?:the )?(?:international )?{_STATION}$|^(?:is|will|can)\b.*{_STATION}.*\b(?:up|out|coming)\b")
_EVENTS = re.compile(
    r"\b(?:(?:space|sky|astronomy|astronomical|celestial) events?|(?:whats|what is) coming up in (?:the sky|space)|"
    r"(?:whats|what is) (?:happening|going on) in (?:the sky|space)|"
    r"anything (?:cool |big |good )?(?:happening|going on|coming up) in (?:the sky|space)|space weather|"
    r"(?:cool|big) (?:things|stuff) in the sky|"
    rf"(?:when|next|any|show|coming up|upcoming).*\b{_BIG_EVENT}\b|"
    rf"\b(?:eclipses?|meteor showers?)\b.*\b(?:coming|this (?:year|month)|next)\b|"
    rf"(?:is there|are there|will there be)(?: going to be)?(?: an?| any| some)?(?: \w+)? {_BIG_EVENT}\b|"
    rf"(?:can|could|will|might) (?:i|we) see(?: the| an?| any)? {_BIG_EVENT}\b|"
    rf"(?:is|are) (?:the )?{_BIG_EVENT} (?:out|visible|up|tonight|happening)\b|chance of(?: the| an?| any)? {_BIG_EVENT}\b|"
    rf"^(?:the |an? )?{_BIG_EVENT}(?: tonight| this (?:week|month|year))?$)")
_ALERTS_ON = re.compile(
    rf"\b(?:turn on|enable|start|switch on|i want|give me)\b.*\b{_WATCHED} {_NOTICES}\b|"
    rf"\b(?:turn on|enable|start|switch on)\b.*\b{_NOTICES} (?:for|about|on) (?:the )?{_WATCHED}\b|"
    rf"\b(?:turn|switch) (?:the |my )?{_WATCHED} {_NOTICES} on\b|^(?:the )?{_WATCHED} {_NOTICES} on$|"
    r"\b(?:tell|alert|warn|let|notify) (?:me|us)\b.*\b(?:when|if)\b.*\b(?:space station|iss|sky)\b")
_ALERTS_OFF = re.compile(
    rf"\b(?:turn off|shut off|disable|stop|switch off|no more|quiet|mute|silence|cancel|kill)\b.*\b{_WATCHED} {_NOTICES}\b|"
    rf"\b(?:turn|shut|switch) (?:the |my )?{_WATCHED} {_NOTICES} off\b|^(?:the )?{_WATCHED} {_NOTICES} off$|"
    r"\b(?:stop|quit|dont|do not|no more)\b.*\b(?:telling|tell|alerting|warning|bugging|notifying)\b.*\b(?:space station|iss|sky)\b")


def request(text: str) -> str | None:
    """'alerts on', 'alerts off', 'moon', 'iss', 'events' or 'tonight'."""
    said = re.sub(r"[^\w\s]", "", put_right(text).lower())
    said = re.sub(r"\s+", " ", said).strip()
    said = re.sub(r"\b(when|where)s\b", r"\1", said)  # "where's" without its apostrophe
    if _ALERTS_OFF.search(said):
        return "alerts off"
    if _ALERTS_ON.search(said):
        return "alerts on"
    if _MOON.search(said):
        return "moon"
    if _ISS.search(said):
        return "iss"
    if _EVENTS.search(said):
        return "events"
    if _TONIGHT.search(said):
        return "tonight"
    return None


# --- What he says -----------------------------------------------------------------------------------------

def _article_moon(name: str) -> str:
    return f"a {name}" if name in ("full moon", "new moon") else f"at {name}" if name.endswith("quarter") else f"a {name}"


def moon_words(moon: dict) -> str:
    return f"The moon is {_article_moon(moon['name'])}, {moon['lit']} percent lit."


def pass_words(found: dict, now: datetime) -> str:
    return (f"The space station passes over at {day_and_clock(found['start'], now)}, from the "
            f"{direction_word(found['from'])} to the {direction_word(found['to'])}, reaching {found['height']} degrees up.")


def event_when(event: dict, now: datetime) -> str:
    when = event["start"]
    if when.date() == now.date():
        return "tonight" if when.hour >= 17 else f"today at {clock(when)}"
    if when.date() == (now + timedelta(days=1)).date():
        return "tomorrow"
    if (when - now).days < 7:
        return f"on {when.strftime('%A')}"
    return f"on {when.strftime('%B')} {when.day}"


def event_words(event: dict, now: datetime) -> str:
    when = event_when(event, now)
    if event["kind"] == "meteors":
        moon = " with bright moonlight in the way" if event["moon_lit"] >= 60 else ""
        return f"The {event['title']} peaks around {event['peak'].strftime('%B')} {event['peak'].day}, up to {event['rate']} an hour{moon}."
    if event["kind"] == "conjunction":
        return f"{event['title']} {when}, in the {direction_word(event['direction'])}."
    if event["kind"] == "solar eclipse":
        share = f", covering about {event['covered']} percent of the sun" if event.get("covered", 100) < 100 else ""
        return f"{event['title']} {when}, peaking at {clock(event['peak'])}{share}. Never look at it without eclipse glasses."
    return f"{event['title']} {when}, peaking at {clock(event['peak'])}."


def tonight_words(moon: dict, planets: list[dict], passes: list[dict], events: list[dict], clouds, now: datetime) -> str:
    parts = [moon_words(moon)]
    if planets:
        names = [f"{p['name']} in the {direction_word(p['direction'])}" for p in planets[:3]]
        parts.append("You can see " + (" and ".join(names) if len(names) < 3 else f"{names[0]}, {names[1]} and {names[2]}") + ".")
    if passes and (passes[0]["start"] - now).total_seconds() < 24 * 3600:
        parts.append(pass_words(passes[0], now))
    weather = sky_words(clouds)
    if weather:
        parts.append(f"The sky looks {weather} for it." if planets or passes else f"The sky looks {weather}.")
    big = next((e for e in events if e.get("important")), None)
    if big:
        parts.append("Coming up: " + event_words(big, now))
    return " ".join(parts)


# --- The alerts -------------------------------------------------------------------------------------------

class SkyWatcher:
    """Runs in the Supervisor: says each alert once, when it's due. BMO asks for the last one, to show it if he's awake."""

    def __init__(self, state_path, orbit_path, load_place, *, say=None, busy=lambda: False, forecast=None, hours=(8, 23),
                 fetch=_get, clock=time.time, poll_seconds: float = 30.0, plan_seconds: float = 3 * 3600):
        self.state_path = Path(state_path)
        self.orbits = OrbitStore(orbit_path, fetch=fetch, clock=clock)
        self.load_place, self.busy, self.forecast, self.hours = load_place, busy, forecast, hours
        self.fetch, self.clock, self.poll_seconds, self.plan_seconds = fetch, clock, float(poll_seconds), float(plan_seconds)
        self._say = say or self._say_aloud
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._plan: list[dict] = []
        self._planned_at = 0.0
        self._for_screen: dict | None = None
        self.log = logging.getLogger("bemo-sky")

    @staticmethod
    def _say_aloud(text: str) -> None:
        import subprocess
        import sys
        app = Path(__file__).resolve().parents[1]
        subprocess.run([sys.executable, str(app / "say_cli.py"), text], cwd=str(app), timeout=120,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)

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
            self._plan, self._planned_at = [], 0.0

    def _allowed(self, when: datetime) -> bool:
        return self.hours[0] <= when.hour < self.hours[1]

    def build_plan(self, place, now: datetime) -> list[dict]:
        sky = Sky(place.latitude, place.longitude, place.timezone)
        now = sky.now(now)
        plan = []
        orbit = self.orbits.get()
        if orbit:
            for found in sky.iss_passes(orbit, now, days=2.0, min_height=PASS_ALERT_HEIGHT):
                at = found["start"] - timedelta(minutes=PASS_ALERT_MINUTES)
                if self._allowed(at):
                    plan.append({"id": f"iss-{found['start']:%Y%m%d%H%M}", "at": at, "kind": "iss", "check_clouds": found["start"],
                                 "pass": found,
                                 "text": (f"Heads up! The space station passes over in {PASS_ALERT_MINUTES} minutes. Look to the "
                                          f"{direction_word(found['from'])} at {clock(found['start'])}. It climbs to "
                                          f"{found['height']} degrees and goes on to the {direction_word(found['to'])}.")})
        for event in sky.events(now, days=3):
            if not event.get("important"):
                continue
            start, key = event["start"], f"{event['kind']}-{event['start']:%Y%m%d}"
            words = event_words(event, now)
            if event["kind"] in ("lunar eclipse", "solar eclipse"):
                soon = start - timedelta(minutes=EVENT_LEAD_MINUTES)
                if self._allowed(soon):
                    plan.append({"id": key + "-soon", "at": soon, "kind": event["kind"], "event": event,
                                 "text": f"Heads up! The {event['title'].lower()} starts in {EVENT_LEAD_MINUTES} minutes."
                                         + (" Don't look at the sun without eclipse glasses." if event["kind"] == "solar eclipse" else "")})
                evening = start.replace(hour=EVENING_HOUR, minute=0, second=0, microsecond=0)
                if evening >= start:
                    evening -= timedelta(days=1)
                plan.append({"id": key + "-before", "at": evening, "kind": event["kind"], "event": event,
                             "text": "Something to look forward to. " + event_words(event, evening)})
            else:
                evening = start.replace(hour=EVENING_HOUR, minute=0, second=0, microsecond=0)
                plan.append({"id": key, "at": evening, "kind": event["kind"], "event": event,
                             "text": f"Tonight in the sky: {words}", "check_clouds": evening + timedelta(hours=2)})
        level = fetch_aurora(place.latitude, now, self.fetch)
        if level is not None:
            plan.append({"id": f"aurora-{now:%Y%m%d}", "at": now, "kind": "aurora",
                         "text": (f"There's a strong solar storm forecast, level {level:g}. The northern lights might reach "
                                  "this far tonight, low in the north, if it's dark and clear. It's only a chance.")})
        return plan

    def _cloudy(self, place, when: datetime) -> bool:
        if self.forecast is None:
            return False
        try:
            percent = clouds_at(self.forecast(place), when)
        except Exception:
            return False
        return percent is not None and percent >= CLOUDY_PERCENT

    def check_once(self) -> list[str]:
        """Say what's due; returns what was said (for tests and logs)."""
        if not self.enabled:
            return []
        place = self.load_place()
        if place is None or not available():
            return []
        now_stamp = self.clock()
        now = datetime.fromtimestamp(now_stamp, _UTC)
        if now_stamp - self._planned_at > self.plan_seconds:
            self._plan, self._planned_at = self.build_plan(place, now), now_stamp
            self.log.info("Sky plan: %d alerts in the next days", len(self._plan))
        said = []
        for alert in list(self._plan):
            at = alert["at"]
            if at > now:
                continue
            self._plan.remove(alert)
            if (now - at).total_seconds() > LATE_MINUTES * 60:
                continue
            with self._lock:
                state = self._load()
                done = state.get("said") if isinstance(state.get("said"), dict) else {}
                if alert["id"] in done:
                    continue
                done[alert["id"]] = now_stamp
                state["said"] = {key: stamp for key, stamp in done.items() if now_stamp - float(stamp) < 30 * 86400}
                self._save(state)
            if alert.get("check_clouds") and self._cloudy(place, alert["check_clouds"]):
                self.log.info("Sky alert %s skipped: overcast", alert["kind"])
                continue
            if self.busy():
                self.log.info("Sky alert %s skipped: a movie or game is on", alert["kind"])
                continue
            self.log.info("Sky alert: %s", alert["kind"])
            self._for_screen = {"kind": alert["kind"], "text": alert["text"], "at": now_stamp}
            try:
                self._say(alert["text"])
            except Exception:
                self.log.exception("Could not say a sky alert")
            said.append(alert["text"])
        return said

    def handle(self, request_: dict) -> dict:
        op = request_.get("op")
        if op == "alert":
            alert, self._for_screen = self._for_screen, None
            return {"ok": True, "alert": alert}
        if op == "set":
            self.set_enabled(bool(request_.get("on")))
            return {"ok": True}
        if op == "get":
            return {"ok": True, "on": self.enabled}
        return {"ok": False}

    def start(self) -> threading.Thread:
        def loop():
            while not self._stop.wait(self.poll_seconds):
                try:
                    self.check_once()
                except Exception:
                    self.log.exception("Sky check failed")
        thread = threading.Thread(target=loop, name="bemo-sky", daemon=True)
        thread.start()
        return thread

    def stop(self) -> None:
        self._stop.set()
