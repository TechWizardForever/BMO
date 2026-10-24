# Bemo v1.27 — local Piper epoch-23 deployment

Based on `bemo-supervisor-v1.26.5-true-streaming-global-idle`. The only
functional runtime change is Piper TTS; the existing supervisor, voice wake,
STT, Ollama model routing, lip-sync, playback queue, and CRT idle remain.
Audio is passed directly from Piper's `PiperVoice.synthesize()` iterator to
the PortAudio worker. Piper returns audio by utterance/sentence chunks;
it is **not** sample-by-sample neural decoding. Ollama text streams while
earlier chunks are synthesized and played. No runtime WAV intermediates.

## Fastest path: all-in-one ZIP, no separate voice transfer

Download `bemo-supervisor-v1.27.0-ALL-IN-ONE-epoch23.zip` directly on the
5700U mini-PC. Read `README-ALL-IN-ONE-EPOCH23.txt`, then stop Bemo,
extract to a **separate staging folder** and run `bash UPDATE_EXISTING_BEMO.sh`
from the extracted `bemo-master` directory. The updater checks the exact
61MB bundled epoch-23 ONNX against a SHA256 digest, installs the model and
its JSON in Bemo's persistent data directory, installs Piper in your existing
virtual environment, saves source backups, and runs a local benchmark.
No USB, no old patch and no manual model copying is needed.

The USB/manual instructions below apply only to the earlier source-only
package and are **not necessary** for this all-in-one build.

## A. On the training PC

Both of these **actual** exported files are required:

```text
~/BemoVoiceFactory/exports/en_US-bemo-best-v3.onnx
~/BemoVoiceFactory/exports/en_US-bemo-best-v3.onnx.json
```

Copy these to the 5700U via USB, SFTP, or `scp`. For example on training PC:

```bash
mkdir -p /media/$USER/YOUR_USB_NAME/bemo-piper
cp ~/BemoVoiceFactory/exports/en_US-bemo-best-v3.onnx* /media/$USER/YOUR_USB_NAME/bemo-piper/
sync
```

Replace `YOUR_USB_NAME` with the mounted drive name.

## B. On the Ryzen 5700U Bemo computer

Back up your current Bemo source directory and preserve its separate
`~/.bemo` data tree. Replace the **source** with this package or apply the
included patch to unmodified v1.26.5. Do not extract over a running Bemo.

From the new source directory:

```bash
.venv/bin/python -m pip install -c constraints.txt 'piper-tts==1.8.0'
.venv/bin/python piper_deploy.py install '/media/$USER/YOUR_USB_NAME/bemo-piper/en_US-bemo-best-v3.onnx'
.venv/bin/python piper_deploy.py benchmark
.venv/bin/python self_test.py
```

Your existing Bemo `.venv` is **not** distributed in the ZIP and must be
retained/recreated from your current working Bemo install. Running the full
installer is not needed for a source update and could re-run large model
downloads. The example USB mount path must match your actual username/drive.

A successful benchmark writes only one explicit diagnostic WAV at
`~/.bemo/voice/piper/benchmark.wav`. Play with `gio open`.

Restart Bemo normally using its existing Supervisor flow. The new default
`BEMO_TTS_BACKEND=piper` loads the ONNX model exactly once on Bemo startup;
Piper stays in RAM throughout the active Bemo process. The old Chatterbox
dependencies and approved voice reference are preserved, but not loaded.

To temporarily revert to Chatterbox without changing files, start Bemo with:

```bash
BEMO_TTS_BACKEND=chatterbox-nano ./start.sh
```

This environment setting must reach the child Bemo process (if a systemd
user service starts Bemo, configure the service environment instead).
Restoring the previous Bemo source archive is the full rollback.

## Practical cautions

- Bemo's data, `~/.bemo/voice/`, are not shipped or modified except for
  the two new Piper files. Epoch 15 remains in Voice Factory as a fallback.
- An ONNX file **without its matching `.onnx.json`** will fail intentionally.
- PyAudio and the existing output device must already work.
- Expect sentence-sized Piper audio chunks, not one giant final WAV;
  the first audible frame still depends on the first LLM text chunk and
  the audio device buffer. Piper prefers natural sentence boundaries and
  uses 24/45-word limits for cases without punctuation. The benchmark is CPU-only.
- Piper 1.8.0 uses the GPL-3.0 license. Keep that in mind if redistributing
  Bemo commercially; consult the official license for obligations.
