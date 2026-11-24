"""Internet radio (2026-11-24): stations by name or style from Radio Browser, played by the Supervisor's music player.

Radio Browser (radio-browser.info) is a free, open directory of stations; no account or key. It's told only what was
asked for ("jazz", "NPR"), like a town name for the weather. The streams come from the stations themselves. The last
station and the saved ones are kept in ~/.bemo/radio.json.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import urllib.parse
import urllib.request

API = "https://all.api.radio-browser.info/json/stations/search"
AGENT = "BMO/1.36 (a voice companion; github.com/TechWizardForever/BMO)"
RESULTS = 10  # the best-voted matches, so "next station" has somewhere to go

# Styles searched as a station's tags rather than its name ("jazz radio" is jazz stations, not one called "Jazz").
STYLES = {
    "jazz", "smooth jazz", "blues", "rock", "classic rock", "hard rock", "metal", "punk", "indie", "alternative",
    "pop", "top 40", "dance", "electronic", "edm", "house", "techno", "trance", "ambient", "chillout", "chill", "lofi",
    "lo-fi", "hip hop", "hiphop", "rap", "r&b", "rnb", "soul", "funk", "disco", "reggae", "latin", "salsa",
    "country", "bluegrass", "folk", "classical", "opera", "piano", "news", "talk", "sports", "comedy", "christian",
    "gospel", "christmas", "oldies", "60s", "70s", "80s", "90s", "2000s", "soundtrack", "video game", "anime",
    "public radio", "college radio", "kids",
}
# What people say for a style -> the tag stations use for it
STYLE_WORDS = {"eighties": "80s", "nineties": "90s", "seventies": "70s", "sixties": "60s", "hip-hop": "hip hop",
               "lo fi": "lofi", "chill out": "chillout", "video game music": "video game", "game music": "video game",
               "sport": "sports", "christmas music": "christmas", "classic": "classical", "top forty": "top 40"}


def _clean(text: str) -> str:
    """A station's name as BMO says and shows it: no emoji, pipes or shouting punctuation."""
    text = re.sub(r"\s*[|/]\s*", " - ", str(text or ""))
    text = re.sub(r"[^\w\s&'.,:()+-]", " ", text)
    return " ".join(text.split()).strip(" -.,") or "a station"


def _about(row: dict) -> str:
    tags = [t.strip() for t in str(row.get("tags") or "").split(",") if t.strip()][:2]
    place = str(row.get("countrycode") or row.get("country") or "").strip()
    return " - ".join(x for x in (", ".join(tags), place) if x)


def search(query: str, *, fetch=None, timeout: float = 8.0) -> list[dict]:
    """The best-voted working stations for a style or a name: [{name, url, about}], best first."""
    query = " ".join(str(query or "").lower().split())
    query = STYLE_WORDS.get(query, query)
    fetch = fetch or _fetch
    tries = []
    if query in STYLES:
        tries += [{"tag": query, "countrycode": "US"}, {"tag": query}]
    tries += [{"name": query, "countrycode": "US"}, {"name": query}]
    if query not in STYLES:
        tries += [{"tag": query}]
    for params in tries:
        rows = fetch(dict(params, limit=RESULTS, hidebroken="true", order="votes", reverse="true"), timeout)
        stations = []
        for row in rows or []:
            url = str(row.get("url_resolved") or row.get("url") or "").strip()
            if url.startswith(("http://", "https://")) and not any(s["url"] == url for s in stations):
                stations.append({"name": _clean(row.get("name")), "url": url, "about": _about(row)})
        if stations:
            return stations
    return []


def _fetch(params: dict, timeout: float) -> list:
    request = urllib.request.Request(API + "?" + urllib.parse.urlencode(params), headers={"User-Agent": AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as reply:
        return json.loads(reply.read().decode("utf-8"))


class Stations:
    """The last station played and the saved ones."""

    def __init__(self, path: Path):
        self.path = Path(path)

    def load(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)

    def last(self) -> list[dict]:
        return [s for s in self.load().get("last") or [] if isinstance(s, dict) and s.get("url")]

    def played(self, stations: list[dict], index: int = 0) -> None:
        data = self.load()
        data["last"] = stations[index:] + stations[:index]  # the one playing first, then the rest of that search
        self._save(data)

    def saved(self) -> list[dict]:
        return [s for s in self.load().get("saved") or [] if isinstance(s, dict) and s.get("url")]

    def save(self, station: dict) -> bool:
        """False when it was already saved."""
        data = self.load()
        kept = [s for s in data.get("saved") or [] if isinstance(s, dict)]
        if any(s.get("url") == station.get("url") for s in kept):
            return False
        data["saved"] = kept + [{k: station.get(k, "") for k in ("name", "url", "about")}]
        self._save(data)
        return True


# --- What was said --------------------------------------------------------------------------------------------
# Every radio command says "radio" or "station" (or a well-known station's name), so "play Ponyo" stays a movie and
# "play Wind Waker" a game. Whisper's ways of writing them are in _put_right.

_RADIO = r"(?:the |some |my )?(?:radio|radio station|station)"
_PLAY = r"(?:play|put on|turn on|start|listen to|tune (?:in )?to|tune into|switch to|change to|go to|find me|find)"
_KNOWN = r"(?:npr|bbc(?: radio| world service)?(?: \d)?|kexp|wnyc|kcrw|wfmu|soma ?fm|radio paradise|classic fm)"

_FORMS = (
    ("next", re.compile(rf"^(?:next|another|a different|change(?: the)?|skip(?: the)?|switch(?: the)?) (?:radio )?stations?$"
                        rf"|^(?:next|skip) (?:on the )?radio$|^change the (?:radio )?station$|^try another (?:radio )?station$")),
    ("previous", re.compile(r"^(?:previous|last|go back(?: a| one| to the (?:last|previous))?|back (?:a|one)) (?:radio )?station$"
                            r"|^(?:one )?station back$|^(?:the|that) (?:station|one) before$|^(?:go )?back to (?:that|the other) station$")),
    ("what", re.compile(rf"^(?:what|which) (?:radio )?station is (?:this|that|playing|on)$|^what(?: s| is) (?:this|that) station$"
                        rf"|^what(?: s| is) (?:playing )?on the radio$|^what(?: s| is) the station$|^name (?:of )?(?:this|the) station$")),
    ("save", re.compile(rf"^(?:save|keep|remember|favou?rite|like) (?:this|that|the) (?:radio )?station$"
                        rf"|^add (?:this|that) (?:radio )?station to my (?:stations|favou?rites)$|^i like this station$")),
    ("saved", re.compile(rf"^(?:play|put on|start) my (?:saved |favou?rite )?(?:radio )?stations$|^my (?:radio )?stations$"
                         rf"|^(?:play|put on) (?:one of )?my favou?rite (?:radio )?stations?$")),
    ("off", re.compile(rf"^(?:turn|switch|shut) (?:off )?{_RADIO}(?: off)?$|^(?:stop|close|kill|end) {_RADIO}$|^{_RADIO} off$"
                       rf"|^no more radio$")),
    ("pause", re.compile(rf"^pause {_RADIO}$|^{_RADIO} pause$")),
    ("resume", re.compile(rf"^(?:resume|unpause|continue) {_RADIO}$")),
    ("last", re.compile(rf"^(?:{_PLAY} )?{_RADIO}(?: on| please| for me| again| back on)?$|^radio time$")),
)
_PLAY_STYLE = re.compile(rf"^{_PLAY} (?:a |an |some |the )?(?P<what>.+?) (?:radio|radio station|station)s?(?: on| please| for me)?$")
_PLAY_ON_RADIO = re.compile(rf"^{_PLAY} (?P<what>.+?) on the radio$")
_PLAY_KNOWN = re.compile(rf"^{_PLAY} (?P<what>{_KNOWN})(?: radio)?$")


# "play some jazz", "put on jazz music": a style alone means the radio, but only a real style (never a title), and
# only styles nobody names a movie or game after (2026-11-24 command check: "play some jazz" launched a game).
_STYLE_ALONE = sorted((s for s in STYLES | set(STYLE_WORDS) if s not in {"kids", "talk", "comedy", "anime", "video game",
                                                                        "soundtrack", "piano", "house", "chill"}),
                      key=len, reverse=True)
_PLAY_STYLE_ALONE = re.compile(rf"^{_PLAY} (?:some |a little |a bit of |more |the )?(?P<what>"
                               + "|".join(re.escape(s) for s in _STYLE_ALONE) + r")(?: music| songs| tunes)?$")


def _put_right(said: str) -> str:
    said = re.sub(r"\b(?:radial|radios|ready oh|rady o|raido|radeo)\b", "radio", said)
    said = re.sub(r"\b(?:stay shun|stations s|statian)\b", "station", said)
    said = re.sub(r"\b(?:n p r|in pr|enpr|and pr)\b", "npr", said)
    return said


def request(text: str) -> tuple[str, str] | None:
    """(what, about) for a radio command, else None. what: play (about = a style or name), last, next, previous,
    what, save, saved, off, pause or resume."""
    said = str(text or "").lower().replace("'", "")
    said = re.sub(r"[^a-z0-9&+ -]", " ", said)
    said = _put_right(" ".join(said.split()))
    said = re.sub(r"\bwhats\b", "what is", said)  # "what's", once the apostrophe is gone
    said = re.sub(r"^(?:hey )?(?:bmo|bemo|beemo)\b ?", "", said)
    said = re.sub(r"^(?:can you|could you|would you|will you|please|i want to|i wanna|let s|lets) ", "", said)
    said = re.sub(r" please$", "", said).strip()
    for name, form in _FORMS:
        if form.match(said):
            return (name, "")
    for form in (_PLAY_KNOWN, _PLAY_ON_RADIO, _PLAY_STYLE, _PLAY_STYLE_ALONE):
        found = form.match(said)
        if found:
            what = found.group("what").strip()
            what = re.sub(r"^(?:music|songs) (?:from the |from )?", "", what)
            what = re.sub(r"^(?:the|some|a|an) ", "", what)
            return ("play", what) if what and what not in {"the", "a", "my", "some"} else ("last", "")
    return None
