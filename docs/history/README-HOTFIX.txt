Bemo v1.26.1 -> v1.26.2 hotfix

Fixes:
- "Ready" is not spoken until the real production Main Brain prompt/tool prefix has been evaluated once.
- Supervisor weight preload remains parallel, but the misleading generic 512-token warmup is disabled.
- Full-mode microphone end-of-speech detection is tightened and the calibrated energy threshold is stabilized.
- Streaming TTS uses a smaller first chunk and balanced follow-up chunks.
- Adds timing logs for capture, Whisper, first LLM text, TTS synthesis, and inter-chunk audio gaps.
- Hardware diagnostics are explicitly limited to Bemo's Linux computer, never human anatomy/medical language.
