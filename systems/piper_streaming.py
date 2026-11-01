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
from loguru import logger

import config


@dataclass(frozen=True)
class PCMChunk:
    audio: np.ndarray
    sample_rate: int
    index: int
    is_final: bool
    generated_tokens: int


def quiet_session(voice, path, threads: int) -> None:
    """Give the voice a few threads of its own that sleep between sentences. ONNX's default is one spinning thread per
    core, and while BMO talks those take the CPU from the brain (2026-11-01 on the 5700U: the brain wrote at 9.4-12
    tok/s beside them and 14.5-15.8 beside 4 quiet ones, and the voice itself went a third faster). The same model, so
    the same voice; if this fails the voice keeps its default session."""
    try:
        import onnxruntime
        options = onnxruntime.SessionOptions()
        options.intra_op_num_threads = int(threads)
        options.inter_op_num_threads = 1
        options.add_session_config_entry("session.intra_op.allow_spinning", "0")
        voice.session = onnxruntime.InferenceSession(str(path), sess_options=options,
                                                     providers=["CPUExecutionProvider"])
    except Exception:
        logger.exception("Could not give the voice its quiet threads; keeping the default ones")


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
        quiet_session(self.voice, path, config.PIPER_THREADS)
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
