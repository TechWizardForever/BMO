"""Things the owner asks BMO to remember, outside any project (2026-10-24).

"Remember that my sister's birthday is May 3rd", "remember I'm allergic to peanuts" -> saved word for word in
~/.bemo/memory/notes.json (the owner's data: private, in backups). "What do you remember about me?" -> BMO reads
them back himself, in the second person. "Forget that my favorite color is green" removes the closest one. Every
brain gets the notes with each question, so "when is my sister's birthday?" is answered from them.

Like the project notes, BMO keeps the owner's own words: nothing is summarised or invented by a brain, and the logs
only count notes.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid
from pathlib import Path

MAX_NOTES = 200
CONTEXT_CHARS = 1500

_REMEMBER = re.compile(r"^(?:(?:please|can you|could you|hey|ok|okay|and) )*(?:remember|keep in mind|note)"
                       r"(?: that)? (?P<note>.+)$", re.I)
_NOT_A_NOTE = re.compile(r"^(?:for (?:this|the) project|the weather|what|when|where|who|how|why|if|me\b|this\b|"
                         r"that\b$|it\b$|to remind)", re.I)
_RECALL = re.compile(r"^(?:(?:please|can you|could you|hey|ok|okay) )*(?:what do you (?:remember|know) about me|"
                     r"what have i (?:told|asked) you to remember|what did i (?:tell|ask) you to remember|"
                     r"read (?:me )?my notes|my notes|what do you remember)$", re.I)
_FORGET = re.compile(r"^(?:(?:please|can you|could you) )*forget(?: that| about| the note about)? (?P<what>.+)$", re.I)
_FORGET_ALL = re.compile(r"^(?:(?:please|can you|could you) )*forget (?:everything|all of it|all my notes|"
                         r"everything (?:you know |you remember )?about me)$", re.I)

_SWAPS = [(r"\bI am\b", "you are"), (r"\bI'm\b", "you're"), (r"\bI've\b", "you've"), (r"\bI'll\b", "you'll"),
          (r"\bI\b", "you"), (r"\bmy\b", "your"), (r"\bme\b", "you"), (r"\bmine\b", "yours"),
          (r"\bmyself\b", "yourself"), (r"\bwe\b", "you"), (r"\bour\b", "your"), (r"\bus\b", "you")]


def second_person(text: str) -> str:
    """'my sister's birthday is May 3rd' -> 'your sister's birthday is May 3rd'."""
    for pattern, replacement in _SWAPS:
        text = re.sub(pattern, replacement, text, flags=re.I)
    return text


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip().rstrip(".!?").strip()


def parse(request: str) -> dict | None:
    """{"action": "add"/"list"/"forget"/"forget_all", ...} for a memory request BMO handles himself, else None."""
    text = _clean(request)
    if _RECALL.match(text):
        return {"action": "list"}
    if _FORGET_ALL.match(text):
        return {"action": "forget_all"}
    found = _FORGET.match(text)
    if found and not re.match(r"^(?:it|that|this|about it)$", found.group("what"), re.I):
        return {"action": "forget", "what": found.group("what")}
    found = _REMEMBER.match(text)
    if (found and not _NOT_A_NOTE.match(found.group("note")) and len(found.group("note").split()) >= 3
            and not found.group("note").lower().startswith("to ")):
        return {"action": "add", "note": found.group("note")}
    return None


class PersonalNotes:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._lock = threading.Lock()

    def notes(self) -> list[dict]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return [n for n in data.get("notes", []) if isinstance(n, dict) and n.get("text")]
        except (OSError, ValueError):
            return []

    def _save(self, notes: list[dict]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps({"notes": notes}, indent=2, ensure_ascii=False), encoding="utf-8")
        os.chmod(tmp, 0o600)
        os.replace(tmp, self.path)

    def add(self, text: str) -> dict:
        note = {"id": uuid.uuid4().hex[:8], "text": _clean(text), "at": time.strftime("%Y-%m-%d")}
        with self._lock:
            notes = [n for n in self.notes() if n["text"].lower() != note["text"].lower()]
            notes.append(note)
            self._save(notes[-MAX_NOTES:])
        return note

    def forget(self, what: str) -> dict | None:
        """The note sharing the most words with `what` (at least two, or all of a short one), removed."""
        words = set(re.findall(r"[a-z0-9']+", what.lower())) - {"the", "a", "an", "my", "that", "is", "about"}
        with self._lock:
            notes = self.notes()
            scored = [(len(words & set(re.findall(r"[a-z0-9']+", n["text"].lower()))), n) for n in notes]
            best = max(scored, key=lambda pair: pair[0], default=(0, None))
            if best[1] is None or best[0] < min(2, len(words)):
                return None
            self._save([n for n in notes if n["id"] != best[1]["id"]])
            return best[1]

    def forget_all(self) -> int:
        with self._lock:
            count = len(self.notes())
            self._save([])
            return count

    def context(self) -> str:
        """For the brains: the owner's notes, newest last, within CONTEXT_CHARS."""
        lines, used = [], 0
        for note in reversed(self.notes()):
            line = f"- {note['text']}"
            if used + len(line) > CONTEXT_CHARS:
                break
            lines.append(line)
            used += len(line) + 1
        if not lines:
            return ""
        return ("Things the owner asked you to remember (their own words; 'I'/'my' means the owner). Use them when "
                "they help; never invent others:\n" + "\n".join(reversed(lines)))


def spoken_list(notes: list[dict]) -> str:
    if not notes:
        return "You haven't asked me to remember anything yet. Say, Hey BMO, remember that, and then the thing."
    texts = [second_person(n["text"]) for n in notes[-8:]]
    more = f" And {len(notes) - 8} older ones." if len(notes) > 8 else ""
    return "I remember that " + "; that ".join(texts) + "." + more
