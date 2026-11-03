"""Opt-in local weather via Open-Meteo (no account or API key).

Nothing is contacted until the owner saves a location by voice. The location
lives only in ~/.bemo/weather.json, coordinates are rounded to ~1 km before
any request, and every spoken report names its source and update time.
Failures are reported plainly; BMO never fills gaps with guesses.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime
import json
from pathlib import Path
import re
import time
import urllib.parse
import urllib.request

from systems.sound_alikes import put_right

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
SOURCE_NAME = "Open-Meteo"
MAX_DAYS = 16  # as far ahead as Open-Meteo goes

_US_STATES = {
    "al": "alabama", "ak": "alaska", "az": "arizona", "ar": "arkansas", "ca": "california",
    "co": "colorado", "ct": "connecticut", "de": "delaware", "fl": "florida", "ga": "georgia",
    "hi": "hawaii", "id": "idaho", "il": "illinois", "in": "indiana", "ia": "iowa", "ks": "kansas",
    "ky": "kentucky", "la": "louisiana", "me": "maine", "md": "maryland", "ma": "massachusetts",
    "mi": "michigan", "mn": "minnesota", "ms": "mississippi", "mo": "missouri", "mt": "montana",
    "ne": "nebraska", "nv": "nevada", "nh": "new hampshire", "nj": "new jersey", "nm": "new mexico",
    "ny": "new york", "nc": "north carolina", "nd": "north dakota", "oh": "ohio", "ok": "oklahoma",
    "or": "oregon", "pa": "pennsylvania", "ri": "rhode island", "sc": "south carolina",
    "sd": "south dakota", "tn": "tennessee", "tx": "texas", "ut": "utah", "vt": "vermont",
    "va": "virginia", "wa": "washington", "wv": "west virginia", "wi": "wisconsin", "wy": "wyoming",
}

# WMO weather interpretation codes -> short spoken phrases.
_WMO = {
    0: "clear", 1: "mostly clear", 2: "partly cloudy", 3: "overcast", 45: "foggy", 48: "foggy",
    51: "light drizzle", 53: "drizzle", 55: "heavy drizzle", 56: "freezing drizzle", 57: "freezing drizzle",
    61: "light rain", 63: "rain", 65: "heavy rain", 66: "freezing rain", 67: "freezing rain",
    71: "light snow", 73: "snow", 75: "heavy snow", 77: "snow grains", 80: "rain showers",
    81: "rain showers", 82: "heavy rain showers", 85: "snow showers", 86: "heavy snow showers",
    95: "thunderstorms", 96: "thunderstorms with hail", 99: "thunderstorms with hail",
}


class WeatherError(Exception):
    """A spoken-safe reason the weather could not be fetched."""


@dataclass
class Place:
    name: str
    region: str
    country: str
    latitude: float
    longitude: float
    timezone: str = "auto"

    @property
    def spoken(self) -> str:
        return ", ".join(p for p in (self.name, self.region, self.country) if p)


def _get_json(url: str, params: dict, timeout: float) -> dict:
    full = url + "?" + urllib.parse.urlencode(params)
    request = urllib.request.Request(full, headers={"User-Agent": "Bemo-local-assistant"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError) as exc:
        raise WeatherError("I couldn't reach the weather service") from exc


def geocode(query: str, *, timeout: float = 6.0, fetch=_get_json) -> Place | None:
    """'Springfield, OR' / '97477' / 'Salem, Oregon 97301' -> best place, or None.

    A ZIP code wins when present and must match exactly; a state or country
    name/abbreviation narrows town names. No match means None, never a guess.
    """
    text = re.sub(r"\s+", " ", str(query or "").strip().rstrip(".!?"))
    zip_match = re.search(r"\b(\d{5})(?:-\d{4})?\b", text)
    if zip_match:
        zip_code = zip_match.group(1)
        data = fetch(GEOCODE_URL, {"name": zip_code, "count": 10, "language": "en", "format": "json"}, timeout)
        results = [r for r in (data.get("results") or []) if zip_code in (r.get("postcodes") or [])]
        return _place(results[0]) if results else None
    name, _, hint = text.partition(",")
    name, hint = name.strip(), hint.strip().lower()
    data = fetch(GEOCODE_URL, {"name": name, "count": 10, "language": "en", "format": "json"}, timeout)
    results = data.get("results") or []
    if hint:
        state = _US_STATES.get(hint, hint)
        results = [r for r in results if state in {str(r.get("admin1", "")).lower(),
                                                   str(r.get("country", "")).lower(),
                                                   str(r.get("country_code", "")).lower()}]
    return _place(results[0]) if results else None


def _place(row: dict) -> Place:
    return Place(
        name=str(row.get("name", "")),
        region=str(row.get("admin1", "")),
        country=str(row.get("country", "")),
        latitude=round(float(row["latitude"]), 2),
        longitude=round(float(row["longitude"]), 2),
        timezone=str(row.get("timezone") or "auto"),
    )


class WeatherSettings:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def load(self) -> Place | None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return Place(**{k: data[k] for k in ("name", "region", "country", "latitude", "longitude", "timezone")})
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def save(self, place: Place) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = asdict(place) | {"provider": SOURCE_NAME, "saved_at": datetime.now().astimezone().isoformat()}
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def forget(self) -> bool:
        try:
            self.path.unlink()
            return True
        except FileNotFoundError:
            return False


class WeatherService:
    def __init__(self, *, fahrenheit: bool = True, cache_seconds: float = 600.0, timeout: float = 6.0,
                 fetch=_get_json, clock=time.monotonic):
        self.fahrenheit = fahrenheit
        self.cache_seconds = cache_seconds
        self.timeout = timeout
        self._fetch = fetch
        self._clock = clock
        self._cache: tuple[tuple, float, dict] | None = None

    def forecast(self, place: Place) -> dict:
        key = (place.latitude, place.longitude, self.fahrenheit)
        if self._cache and self._cache[0] == key and self._clock() - self._cache[1] < self.cache_seconds:
            return self._cache[2]
        params = {
            "latitude": f"{place.latitude:.2f}", "longitude": f"{place.longitude:.2f}",
            "current": "temperature_2m,relative_humidity_2m,apparent_temperature,dew_point_2m,is_day,weather_code,"
                       "cloud_cover,pressure_msl,wind_speed_10m,wind_direction_10m,wind_gusts_10m,visibility,uv_index",
            "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,"
                     "precipitation_sum,snowfall_sum,wind_speed_10m_max,wind_gusts_10m_max,"
                     "wind_direction_10m_dominant,uv_index_max,sunrise,sunset",
            "hourly": "temperature_2m,precipitation_probability,weather_code,cloud_cover",
            "temperature_unit": "fahrenheit" if self.fahrenheit else "celsius",
            "wind_speed_unit": "mph" if self.fahrenheit else "kmh",
            "precipitation_unit": "inch" if self.fahrenheit else "mm",
            "timezone": place.timezone or "auto", "forecast_days": MAX_DAYS,
        }
        data = self._fetch(FORECAST_URL, params, self.timeout)
        if "current" not in data or "daily" not in data:
            raise WeatherError("the weather service sent an answer I couldn't read")
        _drop_empty_last_days(data["daily"])
        self._cache = (key, self._clock(), data)
        return data

    def report(self, place: Place, question: str = "") -> str:
        data = self.forecast(place)
        daily, current = data["daily"], data["current"]
        speed = "miles per hour" if self.fahrenheit else "kilometers per hour"
        updated = _spoken_time(current.get("time", ""))
        source = f"That's from {SOURCE_NAME}" + (f", updated at {updated}." if updated else ".")
        days = days_asked(question, daily.get("time", []))
        if len(days) > 2:
            return f"{_range_phrase(place, daily, days)} {source}"
        if days != [0]:
            said = " ".join(f"{_day_name(daily, i)} in {place.name}: {_day_phrase(daily, i)}." for i in days)
            return f"{said} {source}"
        temperature, wind = current.get("temperature_2m"), current.get("wind_speed_10m")
        sky = _sky_words(current.get("weather_code"), "unclear conditions")
        now = f"Right now in {place.name} it's " + (f"{round(temperature)} degrees and {sky}"
                                                    if temperature is not None else sky)
        now += f", wind {round(wind)} {speed}." if wind is not None else "."
        return f"{now} Today: {_day_phrase(daily, 0)}. {source}"


def _drop_empty_last_days(daily: dict) -> None:
    """The last day can come back with a date and no high or low (live 2026-11-03, day 16 of a 16-day forecast), which
    isn't a forecast for that day, so the forecast ends before it."""
    count = len(daily.get("time") or [])
    while count and _at(daily, "temperature_2m_max", count - 1) is None and _at(daily, "temperature_2m_min", count - 1) is None:
        count -= 1
    for name, values in daily.items():
        if isinstance(values, list):
            daily[name] = values[:count]


def _sky_words(code, otherwise: str) -> str:
    return otherwise if code is None else _WMO.get(int(code), otherwise)


def _day_phrase(daily: dict, i: int) -> str:
    parts = [_sky_words(_at(daily, "weather_code", i), "mixed conditions")]
    for word, name in (("high", "temperature_2m_max"), ("low", "temperature_2m_min")):
        value = _at(daily, name, i)
        if value is not None:
            parts.append(f"{word} {round(value)}")
    text = ", ".join(parts)
    rain = _at(daily, "precipitation_probability_max", i)
    if rain is not None:
        text += f", {int(rain)} percent chance of rain"
    return text


def _range_phrase(place: Place, daily: dict, days: list[int]) -> str:
    where = (f"The next {len(days)} days in {place.name}" if days[0] == 0 else
             f"{_day_name(daily, days[0])} to {_day_name(daily, days[-1])} in {place.name}")
    spans = []
    for word, name in (("highs", "temperature_2m_max"), ("lows", "temperature_2m_min")):
        values = [round(value) for value in (_at(daily, name, i) for i in days) if value is not None]
        if values:
            spans.append(f"{word} from {min(values)} to {max(values)}")
    text = f"{where}: {', '.join(spans)}." if spans else f"{where}: the service didn't send any temperatures."
    chances = [(_at(daily, "precipitation_probability_max", i), i) for i in days]
    chances = [pair for pair in chances if pair[0] is not None]
    if chances:
        chance, day = max(chances, key=lambda pair: pair[0])
        name = _day_name(daily, day)
        name = name.lower() if day < 2 else name
        when = _date(_at(daily, "time", day))
        if day >= 7 and when:  # a bare "Saturday" would be heard as the coming one
            name += f", {when.strftime('%B')} {when.day}"
        text += f" Rain is most likely {name}, at {int(chance)} percent." if chance else " No rain in the forecast."
    if days[-1] >= 7:
        text += " Past about a week, treat it as a rough guide."
    return text


def _at(block: dict, name: str, i: int):
    values = block.get(name) or []
    return values[i] if 0 <= i < len(values) else None


def _date(stamp) -> date | None:
    try:
        return datetime.fromisoformat(str(stamp)).date()
    except ValueError:
        return None


def _day_name(daily: dict, i: int) -> str:
    if i in (0, 1):
        return ("Today", "Tomorrow")[i]
    day = _date(_at(daily, "time", i))
    return day.strftime("%A") if day else "That day"


# --- Which days were asked about ------------------------------------------------------------------------

_WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
_NUMBERS = {"two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
            "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16}
_NUMBER = r"\d+|" + "|".join(_NUMBERS)
_LONG_TERM = re.compile(r"\b(?:long ?(?:term|range)|extended|(?:two|2|couple(?: of)?) weeks?|month)\b")
_MANY_DAYS = re.compile(rf"\b(?:next |coming )?({_NUMBER}) days?\b")
_ONE_DAY = re.compile(r"\b(day after tomorrow|tomorrow|today|tonight|" + "|".join(_WEEKDAYS) + r")(?:s)?\b")
# Words about when, not where: "the weather for Friday" isn't a town called Friday.
_WHEN_WORDS = re.compile(
    r"\b(?:the |this |next |on |for |in |coming |upcoming )*(?:long ?(?:term|range)|extended|(?:two|2) weeks?|"
    rf"(?:{_NUMBER}) days?|week(?:end)?|weekly|day after tomorrow|tomorrow|today|tonight|right now|now|month|forecast|"
    + "|".join(_WEEKDAYS) + r")(?:s)?\b", re.I)
# What's left of "the weather for the week ahead" or "for the weekend looking like".
_ASKING_WORDS = re.compile(r"\b(?:ahead|looking like|look like|looking|going to be|gonna be)\b", re.I)


def without_when_words(text: str) -> str:
    return re.sub(r"\s+", " ", _ASKING_WORDS.sub(" ", _WHEN_WORDS.sub(" ", str(text)))).strip(" ,.?!")


def days_asked(question: str, dates) -> list[int]:
    """Which forecast days (0 is today) were mentioned, in order, never past what the forecast has: "tomorrow", "on
    Friday", "Friday and Saturday", "this weekend", "this week", "next week", "the ten day forecast", "long term"."""
    text = re.sub(r"[-_]", " ", put_right(question).lower()).replace("'s", "")
    first = _date(dates[0]) if len(dates) else None
    available = len(dates) or 1
    weekday = first.weekday() if first else 0

    def within(days):
        return [day for day in days if day < available] or [0]

    if _LONG_TERM.search(text):
        return list(range(available))
    many = _MANY_DAYS.search(text)
    if many:
        number = _NUMBERS.get(many.group(1)) or int(many.group(1))
        return within(range(min(max(1, number), MAX_DAYS)))
    if first and re.search(r"\bnext week\b", text):
        start = 7 - weekday
        return within(range(start, start + 7))
    if first and re.search(r"\bweek ?end\b", text):
        return [0] if weekday == 6 else within(range(5 - weekday, 7 - weekday))
    if re.search(r"\b(?:week|weekly|7 day)\b", text):
        return within(range(7))
    found = []
    for word in _ONE_DAY.findall(text):
        if word in ("today", "tonight"):
            day = 0
        elif word == "tomorrow":
            day = 1
        elif word == "day after tomorrow":
            day = 2
        elif not first:
            continue
        else:
            day = (_WEEKDAYS.index(word) - weekday) % 7
            if day == 0 and re.search(rf"\bnext {word}\b", text):
                day = 7
        if day not in found:
            found.append(day)
    return within(found) if found else [0]


# --- What's on the weather screens ----------------------------------------------------------------------

_COMPASS = ("N", "NE", "E", "SE", "S", "SW", "W", "NW")


def compass(degrees) -> str:
    return "" if degrees is None else _COMPASS[int((float(degrees) + 22.5) // 45) % 8]


def sky_kind(code) -> str:
    """Which little animation goes with a WMO weather code."""
    code = -1 if code is None else int(code)
    if code in (0, 1):
        return "clear"
    if code == 2:
        return "partly"
    if code == 3:
        return "cloudy"
    if code in (45, 48):
        return "fog"
    if code in (51, 53, 55, 61, 80):
        return "light rain"
    if code in (63, 65, 81, 82):
        return "rain"
    if code in (56, 57, 66, 67):
        return "ice"
    if code in (71, 73, 75, 77, 85, 86):
        return "snow"
    if code in (95, 96, 99):
        return "storm"
    return "cloudy"


def _clock_text(stamp) -> str:
    return _spoken_time(str(stamp)) if stamp else ""


def _round(value):
    return None if value is None else round(float(value))


def _amount(data: dict, name: str, i: int) -> str | None:
    value = _at(data["daily"], name, i)
    if value is None:
        return None
    unit = str((data.get("daily_units") or {}).get(name, ""))
    return f"{float(value):.2f} in" if unit.startswith("inch") else f"{float(value):.1f} {unit}".strip()


def day_card(data: dict, i: int) -> dict:
    """One day for the screen. A number the service didn't send stays None and shows as a dash."""
    daily = data["daily"]
    code = _at(daily, "weather_code", i)
    day = _date(_at(daily, "time", i))
    return {"name": {0: "Today", 1: "Tomorrow"}.get(i) or (day.strftime("%a") if day else "?"),
            "date": f"{day.strftime('%b')} {day.day}" if day else "",
            "kind": sky_kind(code), "sky": _WMO.get(int(code), "mixed") if code is not None else "unknown",
            "high": _round(_at(daily, "temperature_2m_max", i)), "low": _round(_at(daily, "temperature_2m_min", i)),
            "rain": _round(_at(daily, "precipitation_probability_max", i)),
            "precip": _amount(data, "precipitation_sum", i), "snow": _amount(data, "snowfall_sum", i),
            "wind": _round(_at(daily, "wind_speed_10m_max", i)), "gust": _round(_at(daily, "wind_gusts_10m_max", i)),
            "wind_from": compass(_at(daily, "wind_direction_10m_dominant", i)),
            "uv": _round(_at(daily, "uv_index_max", i)),
            "sunrise": _clock_text(_at(daily, "sunrise", i)), "sunset": _clock_text(_at(daily, "sunset", i))}


def _visibility_miles(current: dict, units: dict):
    value = current.get("visibility")
    if value is None:
        return None
    unit = str(units.get("visibility", "m"))
    return float(value) / 5280.0 if unit.startswith("ft") else float(value) / 1609.34


def station(data: dict, fahrenheit: bool = True) -> dict:
    """Right now, for today's screen."""
    current = data.get("current") or {}
    pressure = current.get("pressure_msl")
    miles = _visibility_miles(current, data.get("current_units") or {})
    return {"temp": _round(current.get("temperature_2m")), "feels": _round(current.get("apparent_temperature")),
            "humidity": _round(current.get("relative_humidity_2m")), "dew": _round(current.get("dew_point_2m")),
            "kind": sky_kind(current.get("weather_code")), "night": current.get("is_day") == 0,
            "sky": _sky_words(current.get("weather_code"), "unclear"),
            "clouds": _round(current.get("cloud_cover")),
            "pressure": None if pressure is None else (f"{float(pressure) * 0.02953:.2f} in" if fahrenheit
                                                       else f"{round(float(pressure))} hPa"),
            "wind": _round(current.get("wind_speed_10m")), "gust": _round(current.get("wind_gusts_10m")),
            "wind_from": compass(current.get("wind_direction_10m")),
            "visibility": None if miles is None else (f"{min(miles, 99):.0f} mi" if fahrenheit
                                                      else f"{min(miles * 1.609, 99):.0f} km"),
            "uv": _round(current.get("uv_index")), "updated": _clock_text(current.get("time"))}


def hours(data: dict, i: int, count: int = 12) -> list[dict]:
    """The next hours for today, the daytime hours for another day: time, temperature, chance of rain."""
    hourly = data.get("hourly") or {}
    times = hourly.get("time") or []
    day = _at(data["daily"], "time", i)
    if i == 0:
        now = str((data.get("current") or {}).get("time") or "")[:13]
        start = next((n for n, stamp in enumerate(times) if str(stamp)[:13] >= now), 0) if now else 0
    else:
        start = next((n for n, stamp in enumerate(times) if str(stamp) >= f"{day}T07"), len(times))
    rows = []
    for n in range(start, min(start + count, len(times))):
        moment = datetime.fromisoformat(str(times[n]))
        hour = moment.hour % 12 or 12
        rows.append({"hour": f"{hour}{'a' if moment.hour < 12 else 'p'}", "temp": _round(_at(hourly, "temperature_2m", n)),
                     "rain": _round(_at(hourly, "precipitation_probability", n))})
    return rows


def _spoken_time(stamp: str) -> str:
    try:
        moment = datetime.fromisoformat(stamp)
    except ValueError:
        return ""
    hour = moment.hour % 12 or 12
    return f"{hour}:{moment.minute:02d} {'AM' if moment.hour < 12 else 'PM'}"
