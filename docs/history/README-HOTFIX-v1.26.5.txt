Bemo v1.26.5 true-streaming/global-idle hotfix

Base: v1.26.3 nonblocking Supervisor IPC

Changes:
- Replaces per-chunk Chatterbox WAV synthesis/playback with incremental T3 speech-token generation, streaming S3Gen decoding, and direct continuous PCM playback through PortAudio.
- Keeps current Chatterbox Nano and saved voice conditionals; does not downgrade to an older Turbo-only package.
- Adds streaming PCM lip sync and latency/starvation diagnostics.
- Uses global X11 desktop idle (`xprintidle`) so activity in other apps prevents the CRT saver.
- Distinguishes immediate saver exit caused by real user input from an actual crashing/incompatible saver.
- Applies the requested friendly startup/loading announcements while retaining the truthful Ready barrier.
