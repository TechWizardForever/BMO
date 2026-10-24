"""Load a Whisper model from this computer first; go online only if it isn't here yet.

faster-whisper asked Hugging Face for a newer revision at every start (supervisor log, 2026-10-19),
even with the model cached. Without internet that is a delay or a failure; BMO must start offline.
"""
from __future__ import annotations

import logging

LOG = logging.getLogger("bemo-whisper")


def load_whisper(name: str, **options):
    from faster_whisper import WhisperModel
    try:
        return WhisperModel(name, local_files_only=True, **options)
    except Exception as exc:  # not downloaded yet (first start, or a newly chosen model)
        LOG.info("Whisper %s is not on this computer yet (%s); downloading it", name, type(exc).__name__)
        return WhisperModel(name, **options)
