BEMO v1.27.0 — ALL-IN-ONE PIPER EPOCH 23 VOICE
===============================================

This ZIP includes the FULL Bemo v1.27 source AND the exact 61 MB epoch 23
ONNX voice and matching JSON. You do NOT need the old Piper patch, USB drives,
or separate voice downloads. Python Piper runtime may need an internet
connection for a one-time pip install.

UPDATE AN EXISTING, WORKING BEMO v1.26.5 INSTALLATION
-----------------------------------------------------
1. Stop Bemo using its normal shutdown. Keep the Ollama service as-is.
2. Download this ZIP on the 5700U mini-PC.
3. Open a terminal on the mini-PC and run:

   mkdir -p ~/Downloads/BemoPiperUpdate
   unzip ~/Downloads/bemo-supervisor-v1.27.0-ALL-IN-ONE-epoch23.zip -d ~/Downloads/BemoPiperUpdate
   cd ~/Downloads/BemoPiperUpdate/bemo-master
   bash UPDATE_EXISTING_BEMO.sh

4. Read the existing-Bemo path the updater prints. Type YES to proceed.
   The updater installs piper-tts 1.8.0 in the EXISTING .venv, backs up
   any source files it overwrites, and installs the bundled Piper voice
   to ~/.bemo/voice/piper/. Then it benchmarks the 5700U locally.
5. Restart Bemo the same way you did before the update.

If the updater cannot find your current Bemo directory, call it with an
explicit path instead, e.g.:

   bash UPDATE_EXISTING_BEMO.sh ~/your-current-bemo-folder

The folder must contain config.py, start.sh, systems/chat.py and .venv/bin/python.
DO NOT blindly force an update into some other application directory.
The archive expands to a staging folder, NOT directly over your live Bemo files.

What stays intact: ~/.bemo data/memory/settings, Ollama models and service,
existing Python .venv (only Piper is added), BIOS/boot settings, systemd
startup configuration, and your prior backup voice files.

DIAGNOSTICS
-----------
The updater displays: model load seconds, first audio chunk latency,
synthesis time, audio duration, and realtime factor on the actual mini-PC.
Bemo's standard audio playback and lip-sync remain in place. Piper is loaded
once per Bemo active process. No intermediate WAVs during normal conversation.

If the update reports an error, don't repeatedly run ./install.sh; save the
terminal output so we can correct the specific problem without resetting
Ollama or the installed environment.

OTHER INSTALLS
--------------
For new, clean installs use the source's install.sh. It auto-imports the
included ONNX model. Full install may download large Ollama models and
configure Mint services, so it is not recommended for a source-only update.

License information for third-party dependencies remains in the source tree.
Piper's pip distribution is licensed under GPL-3.0.
