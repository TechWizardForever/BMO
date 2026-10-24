"""Tool calls a model wrote as text instead of making (Qwen3-Coder's XML form).

Live 2026-10-19: asked for a snake game, Coding answered with
`<function=computer_control><parameter=action>write_text</parameter><parameter=args>{"path": ..., "content": ...}`
as plain text; Ollama didn't turn it into a tool call, so nothing was saved and
BMO read out a non-answer. BMO decodes that form itself and runs it as the tool
call it was meant to be (same tools, same Supervisor checks). Only tools that
were offered for the turn are accepted.
"""
from __future__ import annotations

import json
import re

_CALL = re.compile(r"<function=([\w.-]+)>(.*?)</function>", re.S)
_PARAM = re.compile(r"<parameter=([\w.-]+)>\n?(.*?)\n?</parameter>", re.S)


def _value(raw: str):
    text = raw.strip()
    if text[:1] in "{[":
        try:
            return json.loads(text)
        except ValueError:
            return text
    return text


def recover_tool_calls(content: str, offered: set[str]) -> tuple[list[dict], str]:
    """(calls in Ollama's message shape, the text with those calls removed). Empty list if none."""
    calls = []
    for match in _CALL.finditer(content or ""):
        name = match.group(1)
        if name not in offered:
            continue
        arguments = {p.group(1): _value(p.group(2)) for p in _PARAM.finditer(match.group(2))}
        if arguments:
            calls.append({"function": {"name": name, "arguments": arguments}})
    if not calls:
        return [], content
    rest = _CALL.sub("", content)
    rest = re.sub(r"</?tool_call>|<tool_\s*$", "", rest).strip()
    return calls, rest
