"""Opt-in local weather via Open-Meteo (no account or API key).

Nothing is contacted until the owner saves a location by voice. The location
lives only in ~/.bemo/weather.json, coordinates are rounded to ~1 km before
any request, and every spoken report names its source and update time.
Failures are reported plainly; BMO never fills gaps with guesses.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
import json
from pathlib import Path
import re
import time
import urllib.parse
import urllib.request

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
SOURCE_NAME = "Open-Meteo"

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
            "current": "temperature_2m,apparent_temperature,weather_code,wind_speed_10m",
            "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max,weather_code",
            "temperature_unit": "fahrenheit" if self.fahrenheit else "celsius",
            "wind_speed_unit": "mph" if self.fahrenheit else "kmh",
            "timezone": place.timezone or "auto", "forecast_days": 3,
        }
        data = self._fetch(FORECAST_URL, params, self.timeout)
        if "current" not in data or "daily" not in data:
            raise WeatherError("the weather service sent an answer I couldn't read")
        self._cache = (key, self._clock(), data)
        return data

    def report(self, place: Place, question: str = "") -> str:
        data = self.forecast(place)
        daily, current = data["daily"], data["current"]
        speed = "miles per hour" if self.fahrenheit else "kilometers per hour"
        updated = _spoken_time(current.get("time", ""))
        source = f"That's from {SOURCE_NAME}" + (f", updated at {updated}." if updated else ".")
        if re.search(r"\btomorrow\b", question, re.IGNORECASE) and len(daily.get("time", [])) > 1:
            return (f"Tomorrow in {place.name}: {_day_phrase(daily, 1)}. " + source)
        now = (f"Right now in {place.name} it's {round(current['temperature_2m'])} degrees and "
               f"{_WMO.get(int(current.get('weather_code', -1)), 'unclear conditions')}, "
               f"wind {round(current.get('wind_speed_10m', 0))} {speed}.")
        return f"{now} Today: {_day_phrase(daily, 0)}. {source}"


def _day_phrase(daily: dict, i: int) -> str:
    sky = _WMO.get(int(daily["weather_code"][i]), "mixed conditions")
    rain = daily.get("precipitation_probability_max", [None] * 3)[i]
    text = f"{sky}, high {round(daily['temperature_2m_max'][i])}, low {round(daily['temperature_2m_min'][i])}"
    if rain is not None:
        text += f", {int(rain)} percent chance of rain"
    return text


def _spoken_time(stamp: str) -> str:
    try:
        moment = datetime.fromisoformat(stamp)
    except ValueError:
        return ""
    hour = moment.hour % 12 or 12
    return f"{hour}:{moment.minute:02d} {'AM' if moment.hour < 12 else 'PM'}"
