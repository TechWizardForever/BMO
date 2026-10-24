"""Coding writes programs as code blocks; BMO saves them (roadmap: "Coding saves code blocks itself").

Live 2026-10-18 the coder put a whole snake game inside one write tool call and
Ollama's tool parser failed after 82 s. Plain fenced code blocks don't have that
problem; this module finds which file each block is meant to be, checks the
name is safe, and leaves the writing to the caller (project workspace or
~/BMO-code), which asks before replacing an existing file.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath

EXTENSIONS = {
    "python": ".py", "py": ".py", "javascript": ".js", "js": ".js", "typescript": ".ts", "ts": ".ts",
    "html": ".html", "css": ".css", "bash": ".sh", "sh": ".sh", "shell": ".sh", "c": ".c", "cpp": ".cpp",
    "c++": ".cpp", "java": ".java", "rust": ".rs", "go": ".go", "json": ".json", "yaml": ".yaml", "yml": ".yaml",
    "toml": ".toml", "sql": ".sql", "lua": ".lua", "ruby": ".rb", "php": ".php", "markdown": ".md", "md": ".md",
}
MAX_BYTES = 400_000
_NAME = r"[\w.\-/]+\.[A-Za-z0-9]{1,8}"
# The closing fence may have junk after it: live 2026-10-19 Coding ended its block with
# "```<tool_call>", and a stricter pattern found no file at all.
_FENCE = re.compile(r"^```[ \t]*([^\n`]*)\n(.*?)^```[^\n]*$", re.S | re.M)
STRAY_TAGS = re.compile(r"</?tool_call>|<tool_\s*$")
_BEFORE = re.compile(rf"(?:^|\n)[ \t]*(?:[*_#>\-\s]*)(?:(?:file(?:name)?|path|save (?:as|to)|create)\s*[:=]\s*)?"
                     rf"[*_`\"']*(?P<name>{_NAME})[*_`\"']*\s*:?[ \t]*$", re.I)
_FIRST_LINE = re.compile(rf"^\s*(?:#|//|<!--|--|/\*)\s*(?:file(?:name)?\s*:\s*)?(?P<name>{_NAME})\s*(?:-->|\*/)?\s*$", re.I)


# Files are saved only when the owner asked for code to be written or changed. Live
# 2026-10-19 (headless bench of the same turn): "where's the file for the game?" made Coding
# write the whole game again, and BMO asked to replace the file in answer to a question.
_WANTS_FILES = re.compile(r"\b(write|make|build|create|generate|rewrite|redo|fix|change|update|add|edit|improve|"
                          r"refactor|modify|implement|save|convert|port|rename|remove|delete)\b"
                          # Whisper heard "Bright a Python Program for a Snake Game." (live 2026-10-19)
                          r"|\b(?:a|an)\s+(?:[\w+#-]+\s+){0,3}(?:program|script|game|app|tool|function|class|module)\b",
                          re.I)
_QUESTION = re.compile(r"^\W*(?:(?:hey |okay |ok |so |and )?(?:bmo|bemo)\W+)?(?:hey |okay |ok |so |and )?"
                       r"(?:where|did|was|were|is|are|does|do|why|how come|what did)\b", re.I)


# Asking for a program again makes a new one; only these words mean "change the one you made".
_CHANGES_EXISTING = re.compile(r"\b(fix|change|update|edit|improve|modify|add|remove|rename|refactor|rewrite|redo|"
                               r"replace|convert|port|overwrite|tweak|adjust)\b", re.I)


def changes_existing(request: str) -> bool:
    """'fix the snake game' replaces the file; 'write a snake game' again makes snake_game_2.py
    (live 2026-10-19: the repeat asked to overwrite the earlier game)."""
    return bool(_CHANGES_EXISTING.search(request or ""))


def wants_files(request: str) -> bool:
    """True when the request asks for code to be written or changed (not a question about it)."""
    request = request or ""
    return bool(_WANTS_FILES.search(request)) and not _QUESTION.search(request)


@dataclass(frozen=True)
class CodeFile:
    path: str        # relative, forward slashes, e.g. "snake_game.py" or "src/app.js"
    language: str
    content: str


def safe_relative(name: str) -> str | None:
    """A relative path inside the destination, or None (absolute, '..', odd characters)."""
    name = name.strip().strip("`'\"").replace("\\", "/")
    if not name or name.startswith(("/", "~")) or ":" in name:
        return None
    parts = PurePosixPath(name).parts
    if not parts or any(p in ("..", ".") or p.startswith(".") and len(parts) == 1 and p != ".gitignore" for p in parts):
        return None
    if not re.fullmatch(r"[\w.\-/]+", name) or len(name) > 120:
        return None
    return "/".join(parts)


def name_from_request(request: str, language: str) -> str:
    """'write a python program for a snake game' -> 'snake_game.py'."""
    ext = EXTENSIONS.get(language.lower(), ".txt")
    words = re.findall(r"[a-z0-9]+", request.lower())
    stop = {"write", "make", "build", "create", "generate", "me", "a", "an", "the", "for", "of", "that", "which",
            "python", "javascript", "program", "script", "code", "app", "simple", "small", "little", "please",
            "can", "you", "could", "would", "hey", "bemo", "bmo", "in", "with", "to", "and", "my", "new", "quick"}
    useful = [w for w in words if w not in stop][:3]
    return ("_".join(useful) or "program") + ext


def extract_files(text: str, request: str = "") -> list[CodeFile]:
    """The files Coding wrote in its answer, in order; unnamed snippets are left alone,
    except a single program when the owner asked for one."""
    found, unnamed = [], []
    for match in _FENCE.finditer(text or ""):
        info, body = match.group(1).strip(), match.group(2)
        language = info.split()[0].lower() if info else ""
        name = None
        title = re.search(rf"(?:title|file(?:name)?)\s*=\s*[\"']?({_NAME})", info) or (
            re.fullmatch(rf"\S+\s+({_NAME})", info))
        if title:
            name = title.group(1)
        if not name:
            before = text[max(0, match.start() - 200):match.start()].rstrip("\n")
            last_line = before.rsplit("\n", 1)[-1]
            hit = _BEFORE.search("\n" + last_line)
            if hit:
                name = hit.group("name")
        if not name:
            first = body.split("\n", 1)[0]
            hit = _FIRST_LINE.match(first)
            if hit:
                name = hit.group("name")
        if not language and name and "." in name:
            language = next((k for k, v in EXTENSIONS.items() if name.endswith(v)), "")
        if len(body.encode("utf-8")) > MAX_BYTES:
            continue
        path = safe_relative(name) if name else None
        if path:
            found.append(CodeFile(path, language, body if body.endswith("\n") else body + "\n"))
        elif not name and body.count("\n") >= 4:
            unnamed.append(CodeFile("", language, body if body.endswith("\n") else body + "\n"))
    asked_for_program = re.search(r"\b(write|make|build|create|generate)\b.*\b(program|script|game|app|tool|code)\b",
                                  request or "", re.I)
    if not found and len(unnamed) == 1 and asked_for_program:
        block = unnamed[0]
        found.append(CodeFile(name_from_request(request, block.language or "python"), block.language, block.content))
    # The same file written twice: the last version wins.
    latest = {}
    for item in found:
        latest[item.path] = item
    return list(latest.values())


def alternative_name(path: str, exists) -> str:
    """'snake_game.py' -> 'snake_game_2.py' (the first free one)."""
    stem, dot, ext = path.rpartition(".")
    for n in range(2, 100):
        candidate = f"{stem}_{n}.{ext}" if dot else f"{path}_{n}"
        if not exists(candidate):
            return candidate
    return f"{stem}_new.{ext}"
