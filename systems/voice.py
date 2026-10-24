"""Bemo's spoken text and voice fingerprint.

Bemo speaks with Piper and his trained epoch-23 voice (systems/piper_streaming.py).
Chatterbox and its voice cloning were dropped on 2026-10-19
(installer decision C); old files under ~/.bemo/voice/ are left alone.
"""
from __future__ import annotations

import re
from pathlib import Path

import config


def voice_profile_signature() -> str:
    """Changes when the voice model changes, so the hardware benchmark knows to run again."""
    path = Path(config.PIPER_MODEL_PATH)
    try:
        size = path.stat().st_size
    except OSError:
        size = 0
    return f"piper:{path.name}:{size}"


def prepare_spoken_text(text: str) -> str:
    """Convert display Markdown/punctuation into natural speech text.

    The written response stays untouched. This is only the string sent to TTS,
    so Bemo never literally says "asterisk", "hyphen", Markdown bullets, or
    dash punctuation while preserving useful math such as multiplication,
    numeric ranges, and negative values in speakable form.
    """
    spoken_name = str(getattr(config, "TTS_SPOKEN_SELF_NAME", "Beemo")).strip() or "Beemo"
    spoken = str(text)
    # Preserve mathematical meaning before removing Markdown punctuation.
    spoken = re.sub(r"(?<=\d)\s*\*\s*(?=\d)", " times ", spoken)
    spoken = re.sub(r"(?<=\d)\s*[-–]\s*(?=\d)", " to ", spoken)
    spoken = re.sub(r"(?<![\w\d])-(?=\d)", "minus ", spoken)
    # Markdown/emphasis/list markers should never be pronounced literally.
    spoken = re.sub(r"\*\*(.*?)\*\*", r"\1", spoken, flags=re.DOTALL)
    spoken = re.sub(r"__(.*?)__", r"\1", spoken, flags=re.DOTALL)
    spoken = spoken.replace("```", " ").replace("`", "")
    spoken = re.sub(r"(?m)^\s*[-*•+]\s+", "", spoken)
    spoken = re.sub(r"[*_#]+", "", spoken)
    # Convert remaining dash punctuation/hyphenated words into pauses/spaces.
    spoken = spoken.replace("—", ", ").replace("–", ", ")
    spoken = re.sub(r"(?<=[A-Za-z])-(?=[A-Za-z])", " ", spoken)
    spoken = re.sub(r"\s+-\s+", ", ", spoken)
    spoken = re.sub(r"-{2,}", " ", spoken)
    spoken = re.sub(r"\s+", " ", spoken).strip()
    spoken = re.sub(r"\bBemo\b", spoken_name, spoken, flags=re.IGNORECASE)
    return spoken
