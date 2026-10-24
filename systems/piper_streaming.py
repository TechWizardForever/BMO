"""Persistent, CPU-only Piper ONNX backend feeding Bemo's live PCM queue.

Piper's Python synthesize() yields audio chunks, generally sentence-sized.
Text streaming from Ollama feeds this engine before the entire response is
complete. No WAV files and no network requests are involved.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path

import numpy as np


@dataclass(frozen=True)
class PCMChunk:
    audio: np.ndarray
    sample_rate: int
    index: int
    is_final: bool
    generated_tokens: int


class PiperSpeechEngine:
    def __init__(self, model_path: str | Path):
        from piper import PiperVoice

        path = Path(model_path).expanduser()
        config_path = Path(str(path) + ".json")
        if not path.is_file() or not config_path.is_file():
            raise FileNotFoundError(
                f"Bemo Piper voice is missing: {path} or {config_path}. "
                "Run piper_deploy.py install /path/to/en_US-bemo-best-v3.onnx "
                "and ensure its matching .onnx.json is beside it."
            )
        self.path = path
        self.voice = PiperVoice.load(str(path), use_cuda=False)
        self.sample_rate = int(self.voice.config.sample_rate)
        self._synthesis_lock = threading.Lock()

    def stream(self, text: str, *, stop_requested=None):
        """Yield PCM as Piper finishes each synthesis chunk.

        A shared lock protects the reused ONNX session against simultaneous
        spoken system cues and LLM turns; no model is reloaded per phrase.
        """
        with self._synthesis_lock:
            for index, chunk in enumerate(self.voice.synthesize(str(text))):
                if stop_requested is not None and stop_requested():
                    break
                if int(chunk.sample_rate) != self.sample_rate:
                    raise RuntimeError("Piper changed sample rate mid-utterance")
                # The float PCM path integrates with Bemo's existing
                # lip-sync, bounded audio queue, and live PortAudio output.
                pcm = np.frombuffer(chunk.audio_int16_bytes, dtype="<i2").astype(np.float32)
                pcm /= 32768.0
                if pcm.size:
                    yield PCMChunk(
                        audio=pcm,
                        sample_rate=self.sample_rate,
                        index=index,
                        is_final=False,
                        generated_tokens=0,
                    )
