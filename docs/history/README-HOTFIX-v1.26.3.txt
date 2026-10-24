Bemo v1.26.3 hotfix

Fixes supervisor startup IPC contention found on the Ryzen 7 5700U installation:
- Slow Ollama model load/unload now uses a dedicated model lifecycle lock.
- Fast supervisor ping/status/UI requests remain responsive during 30B preload.
- Disconnected Unix-socket clients no longer produce BrokenPipe traceback storms.
- "Ready." semantics remain unchanged: Bemo still waits for the real production prompt/tool warm-up and microphone calibration before announcing readiness.
