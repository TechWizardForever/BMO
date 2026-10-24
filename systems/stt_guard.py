"""Deterministic guard for obvious repeated-word Whisper hallucinations.

The guard deliberately avoids audio-energy heuristics: short real wake phrases
and quiet speech must still be accepted. It only rejects textual repetition
patterns that are overwhelmingly unlikely to be deliberate commands.
"""
from __future__ import annotations

from collections import Counter
import re

_COMMON_WHISPER_LOOP_WORDS = {
    "okay", "ok", "thanks", "thank", "bye", "hello", "yeah", "yes",
}


def is_runaway_repetition(text: str) -> bool:
    """Reject obvious one-word/phrase loops without blocking normal repetition."""
    words = re.findall(r"[a-z0-9]+(?:'[a-z0-9]+)?", str(text or "").lower())
    n = len(words)
    if not n:
        return False

    counts = Counter(words)
    top_word, top_count = counts.most_common(1)[0]

    # The exact failure observed on the target machine is Whisper emitting
    # 'Okay. Okay. Okay...' in silence. Catch even short versions of that loop.
    if n >= 3 and len(counts) == 1 and top_word in _COMMON_WHISPER_LOOP_WORDS:
        return True

    # General one-word runaway loops need stronger evidence so normal speech
    # such as 'no no no, don't do that' remains valid.
    if n >= 6 and top_count >= 6 and top_count / n >= 0.75:
        return True

    # Whisper can repeat multi-word phrases too (e.g. 'Thank you very much').
    # Three identical consecutive phrases covering >=90% is enough evidence.
    # Include longer phrases too; v1.28.5 observed Whisper looping the seven-word
    # sentence "The name Bemo is pronounced Bee-mo" from its old initial prompt.
    for width in range(2, 13):
        if n < width * 3:
            continue
        repeated = 0
        first = words[:width]
        for i in range(0, n - width + 1, width):
            if words[i : i + width] != first:
                break
            repeated += width
        if repeated >= 3 * width and repeated / n >= 0.90:
            return True

    # v1.28.7 live failure: a 1.95 s capture became 140 words of
    # 'Beemo Hey BMO Bemo Hey BMO ...'. The loop started off-phase and changed
    # period mid-stream, so the index-0 check above missed it. Long transcripts
    # built from a handful of distinct words are never real commands.
    if n >= 16 and len(counts) / n <= 0.20:
        return True

    # Phase-independent loop: any n-gram repeated back-to-back that covers
    # most of the transcript, wherever it starts.
    for width in range(1, 13):
        if n < width * 4:
            break
        best = 0
        for start in range(width):
            run = width
            for i in range(start + width, n - width + 1, width):
                if words[i : i + width] == words[i - width : i]:
                    run += width
                    best = max(best, run)
                else:
                    run = width
        if n >= 8 and best >= max(4 * width, 6) and best / n >= 0.60:
            return True
    return False


def implausible_speech_rate(text: str, audio_seconds: float, *, max_words_per_second: float = 6.0, min_words: int = 10) -> bool:
    """True when the transcript has far more words than the audio could hold.

    Fast human speech is about 4 words/s. The v1.28.7 hallucination was 72
    words/s. Short phrases are exempt so 'Hey BMO, yes' in half a second passes.
    """
    words = re.findall(r"[a-z0-9]+(?:'[a-z0-9]+)?", str(text or "").lower())
    if len(words) < int(min_words):
        return False
    return len(words) / max(float(audio_seconds or 0.0), 0.5) > float(max_words_per_second)


def output_is_looping(text: str, *, window_words: int = 80) -> bool:
    """True when the newest model output is one phrase repeated over and over.

    v1.28.7 Coding spent 99 s copying a hallucinated 'Hey BMO Bemo ...' loop
    into a tool call. Real prose and code use far more distinct words than a
    loop, so only the most recent window is checked.
    """
    words = re.findall(r"[a-z0-9_]+", str(text or "").lower())[-int(window_words):]
    if len(words) < int(window_words):
        return False
    return len(set(words)) / len(words) <= 0.12
