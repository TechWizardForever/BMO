"""Placeholder so llama.cpp's converter reaches its 'no tokenizer.model' check.

Qwen-family models ship a BPE tokenizer (tokenizer.json), never a
sentencepiece model; the converter imports sentencepiece before checking,
then falls back to BPE when the file is absent. This class must never run.
"""


class SentencePieceProcessor:
    def __init__(self, *args, **kwargs):
        raise RuntimeError("sentencepiece placeholder used: a real tokenizer.model exists; install sentencepiece")
