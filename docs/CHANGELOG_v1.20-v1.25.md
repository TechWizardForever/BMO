### Voice (v1.28.71, 2026-10-19)

Bemo speaks with Piper and his trained epoch-23 voice. Chatterbox, its voice cloning, `voice_setup.py` and the voice-import commands described in the V1.22–V1.23 notes below were removed in v1.28.71. Files already under `~/.bemo/voice/` are left alone.

### V1.25.4 installer path initialization fix

V1.25.4 fixes the fresh-install shell error `SCRIPT_DIR: unbound variable` introduced by the NumPy constraints hotfix. `install.sh` now resolves and defines its own absolute script directory before any constrained pip stage uses `constraints.txt`. Dependency pins and runtime behavior are otherwise unchanged from V1.25.3/V1.25.

# Bemo / WIB

### V1.25.3 global NumPy ABI lock

V1.25.3 fixes a fresh-install failure where a later pip stage could upgrade NumPy after the CadQuery stage, leaving `nptyping` to import against NumPy 2.x and fail on the removed `np.bool8` alias. The installer now uses a shared `constraints.txt` across every dependency-resolving pip stage to keep NumPy at **1.26.4**, updates `nptyping` to **2.5.0** (which explicitly requires NumPy <2), and re-asserts both ABI pins immediately before core import verification. This release also keeps the V1.25.2 CadQuery 2.4.0 / OCP 7.7.2 / NLopt 2.7.1 stack and the V1.25 streamed runtime unchanged.

### V1.25.2 CadQuery dependency correction

V1.25.2 corrects the fresh-install CAD pin from V1.25.1. CadQuery 2.5.2 itself declares NLopt >=2.9, so pairing it with NLopt 2.7.1 was invalid. Bemo now uses the Python-3.12-compatible CadQuery 2.4.0 / cadquery-ocp 7.7.2 / NLopt 2.7.1 generation, with the remaining CadQuery dependencies pinned explicitly and CadQuery installed with `--no-deps`. This prevents pip from reopening the full Bemo dependency graph while preserving NumPy 1.26.4 for the local speech stack. The installer now also runs `pip check` after the staged installs. Runtime behavior is unchanged from V1.25.

### V1.25.1 Linux installer dependency fix

V1.25.1 introduced staged fresh-install dependency handling after pip could hit `resolution-too-deep`, including CPU-only PyTorch and a Chatterbox install without its unused Gradio demo tree. Its CadQuery pin was later found to be internally inconsistent (CadQuery 2.5.2 requires NLopt >=2.9), so **V1.25.2 supersedes that CAD pin**. Runtime behavior remained unchanged from V1.25.

### V1.25 true streamed response pipeline

V1.25 makes normal Bemo conversation genuinely low-latency. Ollama chat requests now use the official streaming API, so Text mode renders the answer as tokens arrive instead of waiting for a complete response. In Face/TTS mode, generated text is split at complete sentence boundaries (or a short word-count fallback) and fed into a two-stage speech pipeline: one worker runs Chatterbox Nano while a second worker plays the previous synthesized WAV. Ollama generation continues in parallel with both. This means Bemo can begin speaking the first completed sentence while the Main Brain is still generating later sentences, and Chatterbox can prepare the next phrase while the current phrase is playing.

The first no-punctuation speech chunk is capped at **12 words** for fast time-to-first-audio; later fallback chunks use **20 words** to reduce TTS overhead and audible seams. Natural sentence endings are always preferred. Internal `ROUTE:` supervisor signals are withheld from both the display and TTS. Streamed tool calls are accumulated according to Ollama's streaming tool-call contract and are not treated as final user-facing text. Heavy Coding intentionally keeps its existing compact spoken-summary behavior rather than reading raw code aloud, while its text still streams live. The Python Ollama client floor is now **0.6.3** so the installed SDK matches the streaming/tool-call behavior this release relies on.

### V1.24.1 voice-only interface controls + Face/TTS invariant

V1.24.1 keeps the Face/Text runtime switch and TTS control but makes the interaction rules explicit. **Every launch starts in Face mode with TTS ON**, regardless of the previous session. Runtime changes are made by voice command only; the V1.24 F2/F3 keyboard toggles have been removed.

**Face mode always requires TTS.** `Hey Bemo, TTS off` is accepted only in Text mode. If it is requested while the face is active, Bemo explains that Text mode must be enabled first and leaves TTS on. Switching from silent Text mode back to Face mode automatically enables TTS before the face becomes active. Microphone/STT and the `Hey Bemo` wake/session gate stay active when Text mode is silent.

Deterministic local commands are **`Hey Bemo, text mode`**, **`Hey Bemo, face mode`**, **`Hey Bemo, TTS off`**, **`Hey Bemo, TTS on`**, and **`Hey Bemo, interface status`**. Valid runtime combinations are therefore Face+TTS, Text+TTS, and Text+silent; Face+silent is intentionally impossible. The existing CRT idle/screensaver behavior still applies in either display mode.

### V1.24 Face/Text mode + TTS controls

V1.24 introduced the Text conversation view and runtime TTS toggle. V1.24.1 supersedes its persistence, keyboard-toggle, and Face+silent behavior with the stricter voice-only rules above.

### V1.23.3 approved default voice — profile 03

The user-approved Bemo voice is now **profile 03: OG character window, temperature 0.70**. The bundled mono/24 kHz reference is the exact 8.8465–23.7974 second character-heavy window from the submitted original Bemo sample, with **no pitch shift and no formant processing**. The TTS-only self-name alias from v1.23.2 remains active, so written/wake-word `Bemo` is synthesized as `Beemo` ("Bee-mo").

On a fresh machine, the first Chatterbox Nano load builds and persists conditionals from `assets/default_voice/reference.wav` into `~/.bemo/voice/`. That makes the approved voice the factory default without reconditioning every utterance. If a user later imports another permanent voice, its persistent conditionals remain authoritative and the bundled default does not overwrite them during updates. The default Nano sampling values are temperature **0.70**, top-p **0.95**, top-k **1000**, and repetition penalty **1.20**.

### V1.23.2 spoken-name pronunciation alias

Bemo remains the canonical project name, UI spelling, filename prefix, and wake word, but the TTS boundary now rewrites standalone spoken `Bemo` references to `Beemo` before Chatterbox synthesis. This is a pronunciation-only alias so the cloned voice says **"Bee-mo"** instead of drifting toward **"Beh-mo"**. Stored chat text, logs, prompts, voice-command parsing, and the canonical **"Hey Bemo"** wake phrase are unchanged. Set `BEMO_TTS_SPOKEN_SELF_NAME` only if a different TTS-friendly spelling is ever needed.

### V1.23.1 Chatterbox Nano source-package compatibility fix

V1.23.1 fixes an upstream packaging mismatch discovered during the Windows voice-clone preview. The public PyPI artifact named `chatterbox-tts 0.1.7` predates the `nano=True` loader API, while Resemble AI's current official source tree uses the same package version number and includes Nano. Bemo now installs Chatterbox from the current official Resemble AI Git repository and `install.sh` explicitly verifies that `ChatterboxTurboTTS.from_pretrained` exposes the `nano` parameter before installation continues. This prevents a successful dependency install from later failing at runtime with `TypeError: ... unexpected keyword argument 'nano'`.

### V1.23 staged Bemo voice candidate (approval-safe)

V1.23 includes the submitted 31.56-second `BemoVoice.mp3` as a **non-active candidate** plus a 14.913-second mono/24 kHz reference selected from the latter half at natural pause boundaries. The candidate is intentionally not promoted into Bemo's permanent/default voice yet. Run `.venv/bin/python voice_setup.py preview-bundled` to have Chatterbox Nano build candidate conditionals and render `~/.bemo/voice/candidate/test.wav`. The preview path is isolated: it restores any currently loaded conditionals before returning and does not touch `~/.bemo/voice/bemo-conditionals.pt`, so listening approval remains a hard barrier before promotion.

Use `.venv/bin/python voice_setup.py candidate-status` to inspect the staged candidate. Once the generated test is explicitly approved, the exact approved conditionals/reference/profile can be moved into `assets/default_voice/` in the next base release, while user-installed custom voices continue to take precedence.

### V1.22 permanent cloned voice + Chatterbox Nano

V1.22 replaces the old single-speaker Coqui Glow-TTS path with **Resemble AI Chatterbox Nano**, a CPU-capable zero-shot voice-cloning model. Bemo can still speak immediately with Chatterbox's built-in voice, but once a reference clip is imported it creates a permanent local voice profile under `~/.bemo/voice/`. The normalized reference WAV, compact saved speaker conditionals, metadata, and test WAV are all part of Bemo's persistent data/backup tree. Runtime speech loads the saved conditionals directly, so the reference audio is not re-analyzed for every sentence.

Use `.venv/bin/python voice_setup.py import /path/to/voice.mp3` for a local file or `.venv/bin/python voice_setup.py import-usb` for removable media. Common MP3/WAV/FLAC/M4A/AAC/OGG/OPUS/WMA inputs are normalized with ffmpeg to a mono 24 kHz reference, obvious end silence is trimmed, and up to 15 seconds of speech is retained. The reference must contain more than about five seconds of actual speech. Voice commands include **“Hey Bemo, voice status.”**, **“Hey Bemo, test voice.”**, and **“Hey Bemo, import voice from USB.”** If more than one audio file is found, Bemo names the candidates and waits for the requested filename.

The Chatterbox speaker conditionals are generated once and saved as `~/.bemo/voice/bemo-conditionals.pt`; this is the permanent voice representation Bemo loads at startup. Replacing the permanent voice invalidates the empirical CPU benchmark because TTS/LLM overlap may change, so `hardware_autotune.py ensure` recalibrates the efficient Ollama thread count. The existing FFT-driven lip-sync remains unchanged because Chatterbox still produces a normal WAV for the renderer.

### V1.21 efficient empirical CPU auto-tuning

V1.21 keeps the model-independent hardware detection from v1.20, but no longer assumes that physical-core count is automatically the best Ollama setting. After the speech stack and Main 30B model are installed, `hardware_autotune.py ensure` benchmarks a few thread counts derived from the detected topology. It measures prompt processing and generation throughput and, when the current Chatterbox Nano voice is available, measures TTS latency while Ollama is simultaneously generating. The selection rule deliberately finds the performance knee: any candidate within 5% of the best balanced result is treated as effectively tied, and the lower thread count wins. This preserves CPU headroom for Whisper/TTS, pygame/audio, and Linux instead of spending extra logical threads for a negligible LLM gain.

The result is saved under `~/.bemo/hardware/autotune.json` with a CPU/topology fingerprint. A matching result is reused automatically. A different CPU/topology invalidates it; `start.sh` then reruns the benchmark before launching the Supervisor, so a hardware swap self-calibrates without adding a CPU profile. `install.sh` also runs the benchmark automatically when the required model is present. Use `.venv/bin/python hardware_autotune.py status` to inspect it or `.venv/bin/python hardware_autotune.py run --force` to retest. Set `BEMO_SKIP_HARDWARE_AUTOTUNE=1` only when intentionally skipping calibration.

### V1.20 live display calibration + CPU-topology auto tuning

V1.20 removed fixed per-CPU tuning presets. Bemo derives Whisper and Torch/TTS worker counts directly from the host's detected physical/logical core topology, and v1.21 empirically refines the Ollama count on top of that generic detection. On systems with four or more physical cores, short STT/TTS workloads reserve one physical core for Pygame/audio/OS responsiveness. Explicit numeric thread overrides remain available for benchmarking or recovery.

Display handling is now monitor-independent and preview-based. On startup Bemo reads the live X11 mode and fingerprints the connected display from EDID when available. It reapplies the last approved resolution/crop only for the same display; a different screen starts safely on its live mode with zero inherited crop. The Bemo face rescales/repositions to the current safe viewport instead of assuming one fixed monitor resolution. Voice commands can list modes, temporarily test a new resolution, adjust all or individual safe-area crop edges for CRT/video-adapter overscan, and report current settings. No test is persisted until the user explicitly approves it; rejection restores the previous state, and an unanswered preview automatically rolls back after 45 seconds. Approved settings are stored under `~/.bemo/display/settings.json`.

Canonical examples include **“Hey Bemo, test screen resolution 640 by 480.”**, **“Hey Bemo, test screen crop 20 pixels.”**, **“Hey Bemo, test left crop 12 pixels.”**, **“Hey Bemo, approve display changes.”**, and **“Hey Bemo, reject display changes.”** During an active pending preview, simple follow-ups such as **“yes”**, **“keep it”**, **“no”**, or **“revert it”** are also accepted.

### V1.19 verified project-file export to removable media

V1.19 adds deterministic copy-and-verify export for individual files and folders from the active project. Say **“Hey Bemo, export FinalEyeCup.step to USB.”** (or natural variants such as “copy FinalEyeCup.step to the USB” / “put FinalEyeCup.step on the removable drive”) and Bemo resolves the name only inside the active project workspace, copies it to `Bemo Files/` on the first writable removable drive, SHA-256 verifies files/tree contents, leaves the source untouched, skips an already-identical USB copy, and uses collision-safe `(... exported N)` names rather than overwriting different existing data. Folder exports are supported; symbolic-link selections are refused rather than following links outside the project.

**“Hey Bemo, export files to USB.”**, “copy these files to USB,” “export current file to USB,” and “copy this folder to USB” use project files already selected on the active task when available. If no project asset is selected, Bemo asks for a file/folder name instead of guessing. The terminal equivalent is `.venv/bin/python project_cli.py export-files-usb <project-relative-path-or-name> [...]`. Whole-project export remains the separate **“Hey Bemo, export project.”** verified bundle workflow.

### V1.18 verified USB project import

V1.18 makes removable-drive project intake a first-class deterministic workflow. **“Hey Bemo, import project from USB.”** scans mounted removable media for sensible project folders, copies the selected project into Bemo's persistent project storage, SHA-256 verifies the complete copied tree, leaves the source drive unchanged, opens the imported project, and immediately indexes it for project-grounded retrieval. Existing `.git` repositories are preserved with their commit history instead of being replaced; folders without Git receive Bemo's normal local repository and initial snapshot.

If the drive contains multiple project candidates, Bemo names them and waits for a selection such as **“Hey Bemo, import project Motor Controller from USB.”** Natural variants such as “import this project,” “add the project from this USB,” and “make this folder a project” route to the same deterministic importer. A verified Bemo project export (`project.json` + `workspace/`) is recognized specially: project memory, facts, memory history, workspace, and Git history are restored, while the stale text index is rebuilt locally. If an export has `SHA256SUMS.txt`, Bemo validates that source manifest before accepting it.

**“Hey Bemo, import files into current project.”** handles the other common workflow: removable-drive files are merged into the already-open project's workspace, every copied file is hash-verified, exact duplicates are skipped, conflicting names are retained under unique `(... imported N)` filenames rather than overwriting project data, `.git` folders from the USB are ignored, and the project index is refreshed afterward. Terminal recovery equivalents are available through `project_cli.py import-usb` and `project_cli.py import-files-usb`.

### V1.17 removable-drive GameCube importer

V1.17 adds a deterministic Supervisor-owned GameCube importer. Plug in a mounted USB/removable drive and say **“Hey Bemo, import games from USB.”** Bemo recursively finds only supported GameCube images (`.rvz`, `.iso`, `.gcm`, `.gcz`, `.ciso`, `.wia`), copies them into `~/.bemo/games/roms/gamecube/`, preserves useful folders below a GameCube/GC source folder, SHA-256 verifies every completed copy, skips previously imported duplicates, and leaves the source drive unchanged. If an imported file collides with a different existing file at the same destination, Bemo keeps both by choosing a unique `(... imported N)` filename instead of overwriting either one.

The importer keeps a verified hash index under Bemo's game configuration so rerunning the same USB import does not repeatedly hash the entire fixed-size ISO library. Bemo immediately rescans its permanent library after import; if ES-DE is already open, the Supervisor restarts that frontend so the new titles appear. Natural variants such as “add the games from this USB” and “copy the GameCube games off this drive” are accepted by the deterministic voice layer. **“Hey Bemo, rescan games.”** is also available for files copied manually. The same importer can be tested from a terminal with `.venv/bin/python game_launcher.py import-usb`.

### V1.16 Linux Mint 22.3 Xfce appliance target + automatic login

V1.16 locks the official Bemo appliance OS for both target PCs to **Linux Mint 22.3 Xfce 64-bit**. `install.sh` now validates that target, configures LightDM automatic login for the account running the installer (preserving unrelated `/etc/lightdm/lightdm.conf` settings), and installs an Xfce XDG-autostart bridge that imports the real X11/DBus/audio session environment before starting Bemo's restartable systemd user service. This avoids starting the Supervisor too early in boot with no `DISPLAY`/desktop environment.

After power-on the firmware/BIOS stage is unchanged, Linux Mint boots, LightDM logs into the Bemo account, Xfce starts, and only the tiny Supervisor + `tiny.en` **Hey Bemo** listener comes online. The full Bemo UI/models remain off until a bare **“Hey Bemo”** is heard. `bemo-session-start.sh` also disables the X server's own blanking/DPMS for that session so Bemo's three-minute Windows-95 screensaver logic remains the CRT idle owner.

### V1.15 appliance standby + self-reload lifecycle

V1.15 makes **Hey Bemo** the appliance gate from a cold logged-in desktop. `start.sh` now leaves the full Bemo UI/LLM/TTS stack off and keeps only the permanent Supervisor plus a lightweight `tiny.en` wake listener alive. A bare **“Hey Bemo”** releases the standby microphone/model, launches full Bemo, carries the wake across the process boundary, and opens the normal conversation session. There is deliberately no separate “start Bemo” voice command.

Program lifecycle is also Supervisor-owned and never reboots Linux: **“Hey Bemo, reboot yourself”** and **“Hey Bemo, reload yourself”** restart only the Bemo program, while **“Hey Bemo, shutdown yourself”** stops full Bemo, unloads its active Ollama model, and returns to tiny Hey-Bemo standby. These lifecycle actions require an explicit Hey Bemo on that turn even if a normal conversation session is already open. `install.sh` enables the Xfce-session-triggered standby service automatically unless `BEMO_SKIP_AUTOSTART_ENABLE=1` is set. V1.16 also configures LightDM automatic login by default, so the Supervisor/wake listener becomes available after every power-on without a manual login; this happens after firmware boot and does not change BIOS/UEFI access.

### V1.14 one-shot online installer + resumable exact model roster

V1.14 turns `install.sh` into the intended fresh-machine installer. On an online Debian/Ubuntu/Mint system it now installs/starts Ollama, installs the Linux/Python dependencies, fetches the Windows 95/98 `.scr` pack, installs/prepares Dolphin + ES-DE, prefetches Whisper and Chatterbox Nano TTS, pulls all six locked Bemo models at the exact qualified Q4_K_M tags/refs, creates the stable local aliases used by `config.py`, and runs the Bemo self-test. The locked roster is about **98 GB** of model downloads, so allow comfortably more than 100 GB of free disk space for Ollama plus normal caches.

The installer is deliberately restartable. Re-running `./install.sh` after a freeze reuses completed apt/pip/cache work, skips model aliases already recorded from the exact locked source, retries the interrupted exact GGUF pull, preserves/resumes the ES-DE `.download` file with HTTP Range when supported, and skips the legacy screensaver fetch once the persistent `.scr` destination is complete. `model_installer.py` records completion only after both the exact pull and Bemo alias verification succeed, so an interrupted role is never falsely marked complete.

### V1.13 Hey Bemo wake sessions + shared command catalog

V1.13 replaces the old `Bemo ... over` gate with one canonical wake phrase: **“Hey Bemo.”** Every accepted spoken request must start with **“Hey Bemo”** (or a tightly scoped Whisper spelling alias). A bare “Hey Bemo” is acknowledgement-only; it does not open an unprefixed follow-up window. Even **“go to sleep”** must be spoken as **“Hey Bemo, go to sleep.”** Background speech while Bemo is asleep is discarded before it reaches the LLM, task ledger, project memory, or conversation archive; no trailing `over` is required.

The deterministic voice layer, the permanent Supervisor, documentation, self-tests, and every model profile now consume one shared command registry in `voice_commands.py`. The generated human-readable list is `VOICE_COMMANDS.md`. Models are explicitly told which fixed commands exist so they do not invent command phrases, while the local deterministic layer still executes those commands before an LLM turn. The Supervisor exposes the same catalog version/count/list in its status payload so drift is detectable.

### V1.12.1 sanity hardening

V1.12.1 is a focused reliability pass over v1.12. The Windows 95/98 `.scr` idle backend is explicitly locked to a 180-second idle trigger and 180-second rotation, launches each saver from its own directory so companion assets resolve correctly, and quarantines a saver that dies during startup instead of incorrectly treating that crash as user activity. If every legacy saver is unavailable or incompatible, Bemo immediately keeps the CRT protected with its built-in moving saver. `windows_screensaver_cli.py status` now gives a quick target-machine check for Wine, the 3-minute timer, and discovered `.scr` files.

### V1.12 cache-friendly model context + Main personality

V1.12 gives the Main Brain a compact, distinct Bemo personality while preserving the qualified routing policy. Prompt construction is now intentionally cache-friendly for Ollama: each role's immutable personality/policy/handoff block stays first, the slow-changing durable summary and prior conversation follow, and query-specific task/project/memory retrieval is pushed to the tail immediately before the current user request. This keeps the largest practical prefix identical between consecutive turns so a resident model can reuse Ollama's prompt/KV cache; policies are still supplied as system context and are not permanently learned by the weights.

A newly-created task no longer injects a redundant copy of the current request. Task context enters the prompt only after it contains material state such as a checkpoint, selected asset, handoff, resume, or project change. Model switching behavior is unchanged: a specialist receives its own qualified static profile when activated, and Main's personality/policy prefix is restored when Main returns. The detailed benchmark policies remain reference-only under `policies/benchmark_notes/`.

### V1.11 GameCube-only game appliance

V1.11 simplifies the game subsystem to **Nintendo GameCube only**. ES-DE remains the controller-first fullscreen library, while standalone **Dolphin** is now the sole emulator. Game images live under `~/.bemo/games/roms/gamecube/`; Dolphin's dedicated user directory lives under `~/.bemo/games/dolphin-user/`, keeping controller mappings, memory cards, save states and per-game settings inside Bemo's persistent data. Voice commands include **“Hey Bemo, boot games.”**, **“Hey Bemo, import games from USB.”**, **“Hey Bemo, rescan games.”**, **“Hey Bemo, play <game>.”**, and **“Hey Bemo, close current game.”**

The GameCube library accepts Dolphin-supported GameCube image formats including ISO/GCM, GCZ, CISO, RVZ and WIA. RVZ is a good compact choice for a dedicated appliance. Normal GameCube launching in Dolphin does **not** require a GameCube BIOS/IPL; an IPL dump is optional for authentic boot behavior and some font behavior. ES-DE stays locked to a single `gc` system so Arcade and PlayStation entries no longer appear.

Run `.venv/bin/python game_launcher.py doctor` to verify ES-DE, Dolphin and the discovered GameCube count. The installer first tries the distro `dolphin-emu` package and can fall back to the Flathub package `org.DolphinEmu.dolphin-emu`. If Dolphin is installed elsewhere, set `BEMO_DOLPHIN_BINARY` to the executable/command. Bemo's removable-drive backup skips the potentially huge ROM/artwork/frontend binaries but preserves `games/dolphin-user`, so GameCube memory cards, controller setup and save states are retained.

# Bemo / WIB — supervisor + persistent project build

A local animated robot buddy using Pygame, Ollama, faster-whisper, Chatterbox Nano voice cloning, and the original FFT-driven mouth sprites.

### V1.10 Arcade + PlayStation game appliance

V1.10 adds a focused controller-first game subsystem without changing the qualified model roster. ES-DE is the fullscreen library frontend and RetroArch is the common emulator layer: FinalBurn Neo is the default Arcade core, MAME is available as an Arcade fallback, and SwanStation handles PlayStation 1. Bemo keeps ROMs, BIOS, saves, states, artwork and frontend configuration under `~/.bemo/games/`, can launch the library with **“Hey Bemo, boot games.”**, can directly launch a uniquely named title with **“Hey Bemo, play <game>.”**, and preserves voice listening underneath.

The frontend is deliberately limited to two systems: `arcade` and `psx`. The managed ES-DE configuration uses an exclusive two-system list, 640×480 padded-fullscreen defaults for the CRT, and Bemo's launch bridge so both controller and voice launches use the same emulator/core, BIOS, save and state paths. RetroArch's Bemo overlay sets **Start + Select** to quit the current game back to ES-DE and **L1 + R1 + Start + Select** for the Quick Menu. While ES-DE, a direct game, or the media player owns the display, Bemo suppresses its CRT idle screensaver so a Wine `.scr` cannot pop over gameplay.

Run `.venv/bin/python game_launcher.py doctor` to check ES-DE, RetroArch, FBNeo, MAME, SwanStation, BIOS candidates and discovered game counts. ROM directories are `~/.bemo/games/roms/arcade/` and `~/.bemo/games/roms/psx/`; PS1 BIOS files go in `~/.bemo/games/bios/`. ES-DE is fetched from its current Linux AppImage release metadata during `install.sh` unless `BEMO_SKIP_GAME_SETUP=1` is set.
Bemo's normal removable-drive backup preserves game saves, states, BIOS and game configuration, but intentionally skips the potentially huge `games/roms`, `games/artwork`, and downloaded `games/bin` directories. Back those content libraries separately if desired.

### V1.9 grounded tools + lab/diagnostics expansion

All five primary roles remain locked. V1.9 adds the reliability layer around them: local crop/zoom + OCR for both vision roles, live read-only PC diagnostics, a persistent local technical-manual library, read-only serial/SCPI instrument telemetry, background CSV experiment logging/plotting, deeper CAD interrogation, and a deterministic capability-handoff tool so a model can request the correct specialist/tool instead of guessing. The existing v1.8 numerical/symbolic/mechanical/electrical/thermal engineering toolkit remains intact.

## V1.7.3 compact qualified-model profiles + retro model-loader architecture

This build adds the cross-model/project features that sit above the original persistent-project foundation:

- **Compact qualified-model profiles:** Main Brain, Engineering / Deep Reasoning, Heavy Coding, Fast / General Vision, and Deep Technical Vision use compact benchmark-derived role profiles during inference. The longer benchmark-derived policies are preserved under `policies/benchmark_notes/` for reference instead of being injected on every call. Fast / General Vision is now locked after its final ordinary-UI validation.
- **Persistent task/handoff ledger:** every task can survive specialist switches, crashes, and reboots. Specialists can save factual checkpoints before returning a `ROUTE:` signal, so chains such as Main → Engineering → Heavy Coding continue from prior work instead of starting over. Say **“Hey Bemo, resume task.”** after an interrupted task.
- **Automatic project association:** new requests are categorized before storage. Explicit project commands win; strong project-name/context matches are deterministic and ambiguous cases can use a tiny Main-Brain classifier. Unrelated chat can remain general instead of contaminating the active project.
- **Authoritative project facts with supersession:** each project now has `facts.json`. Setting a newer value for the same durable fact key automatically marks the older value superseded. Current facts are injected ahead of raw old conversation; superseded facts remain available for history but are not treated as current state.
- **Structured project memory without the old tiny word cap:** `memory.md` is maintained with Current State, Locked Decisions, Requirements and Constraints, Known Issues, Important Files and Assets, Pending Work, and Historical / Superseded sections. Durable conversation/project summaries are no longer forced into the old 120/220-word limits; prompt retrieval remains bounded instead.
- **Project asset catalog:** `index.sqlite3` now catalogs images, CAD/mesh files, PDFs/documents, source text, and other project files even when their binary contents are not placed in FTS.
- **Shared full-computer control for every model role:** every active model can use the same Supervisor-mediated computer tool for filesystem browsing/read/write/copy/move/delete, opening files/folders, arbitrary argv-based user commands, launching programs, process inspection/control, screenshots, and desktop mouse/keyboard actions. Normal actions run only with the logged-in Bemo user's permissions. Potentially destructive/high-impact operations pause for explicit voice confirmation; administrator commands use a separate confirmed `admin_run` path and the OS `pkexec` authorization prompt rather than permanent root access. All actions are audited.
- **PC/USB/system file discovery:** shared filename/path search covers home and removable drives by default and can explicitly search the broader local computer with bounded scanning. A text-only model can find an image anywhere it can read, attach it to the task, and route to Vision.
- **Real multimodal delivery:** vision-capable Ollama profiles receive selected images through the message `images` field, so they see actual pixels rather than only a pathname. Project images can also be selected from the asset catalog.
- **Verified full removable-drive backup:** say **“Hey Bemo, backup Bemo to USB.”** to back up persistent conversation, tasks, projects/Git history, project facts/memory, and the complete lightweight Bemo application tree (source/config/policies/sprites/audio; caches/virtualenv/Git internals are excluded). The live SQLite DB is copied with SQLite's backup API; a SHA-256 manifest is verified before completion. `restore_backup.py` verifies/restores data and, optionally, the app tree to empty destinations while Bemo is stopped. Local Ollama/model weights are intentionally not duplicated.
- **Voice-managed Linux autostart:** say **“Hey Bemo, enable Bemo autostart.”**, **“Hey Bemo, disable Bemo autostart.”**, or ask its status. On the official Mint/Xfce target, an XDG desktop-autostart bridge imports the live graphical/audio environment and starts the restartable user-systemd Supervisor service. Full Bemo launches only when the wake phrase is heard.
- **Generous output safety ceilings:** `num_predict` is now a runaway guard, not a normal answer target: Main 1024, Fast Vision 1024, Engineering 4096, Heavy Coding 4096, and Deep Technical Vision 4096. The existing Qwen3-4B-Instruct-2507 emergency/recovery brain remains available as a manual low-RAM fallback and is not part of normal specialist routing.
- **Original Windows `.scr` screensaver support through Wine:** after the 3-minute idle timer, Bemo runs Windows 95/98-era `.scr` files through a dedicated Wine prefix while voice listening continues underneath. A normal online `install.sh` run automatically fetches the configured legacy screensaver pack into `~/.bemo/screensavers/windows/`; user-added `.scr` files and savers on mounted removable drives are discovered too. Savers rotate every 3 minutes by default. If Wine or usable `.scr` files are unavailable, the existing dark pygame savers (`star_drift`, `bmo_bounce`, `pixel_rain`) remain the automatic CRT-safe fallback.
- **Retro model-switch loading screen + Data Catch:** every real profile swap publishes renderer-visible switch state before the model lifecycle call blocks. Pygame stays responsive and displays the source/target roles plus a Windows 95-style sunken segmented progress bar. The bar is deliberately indeterminate—blue blocks march across it continuously rather than showing a fake percentage—and the optional Data Catch mini-game remains available while the next brain loads. Left/Right or A/D moves the catcher; mouse movement also works. Specialist→Main return swaps use the same screen.
- **Validation harnesses:** `self_test.py` covers the storage/routing primitives; `soak_test.py --cycles 20` repeatedly exercises persistence, task handoff, supersession, assets, Git/export, backup and restore; `live_validation.py --image /path/to/test.png` checks real installed Ollama models and real image delivery on the final machine. `visual_smoke_test.py` can exercise the CRT saver/model-loader through pygame on the final install.

This build is designed around two priorities:

1. **Keep Bemo responsive on CPU-only machines.**
2. **Never make the model's context window the only copy of important conversation or project state.**

The original BMO face, mouth/eye sprites, positions, blink behavior, lip-sync vocabulary, and audio assets are preserved. `systems/render.py` now also owns the CRT-safe idle modes and model-switch loading/game views, so those visuals remain available even while an LLM is unloading/loading.

## CRT display behavior

Bemo protects the CRT in the application itself; no desktop screensaver is required. The default idle timer is three minutes (`CRT_SCREENSAVER_IDLE_SECONDS=180`). When idle, Bemo first looks for user-supplied legacy Windows `.scr` files. The default local folder is `~/.bemo/screensavers/windows/`, and removable-drive scanning is also enabled, so a saver can be tested directly from a mounted USB drive. If Wine is installed and at least one `.scr` is available, Bemo launches one with the standard `/s` full-screen argument and keeps the Bemo process/microphone alive behind it. Savers rotate every 180 seconds by default.

Classic Windows savers normally exit themselves on mouse/keyboard activity. Bemo detects that exit and resets its own idle timer so another saver is not immediately relaunched. A valid Bemo voice command also wakes the display: the renderer notices the activity reset, terminates the Wine saver, and restores the face. If Wine is missing, a particular saver crashes, or no `.scr` is available, the built-in dark moving pygame savers take over automatically. Background listening timeouts and background speech while the voice session is asleep do **not** keep the screen artificially awake.

A normal online `install.sh` run automatically fetches the configured legacy Windows `.scr` pack. The runtime also works with compatible `.scr` files you add yourself. Check readiness with `./.venv/bin/python windows_screensaver_cli.py status`, list discovery with `./.venv/bin/python windows_screensaver_cli.py list`, and test one manually with `./.venv/bin/python windows_screensaver_cli.py run pipes` (the name is matched as a filename substring). Set `BEMO_SKIP_WINE=1` before `install.sh` if you intentionally do not want Wine installed.

A model change does not freeze the face. `Chat` publishes a small thread-safe visual status record before Supervisor model lifecycle work begins, and the pygame thread immediately switches to the loader. The loader now uses a CRT-friendly Windows 95-style segmented progress bar with a raised/sunken 3-D border and marching blue blocks. It stays honest by remaining indeterminate because Bemo does not have a trustworthy percentage for model loading. Data Catch is deliberately lightweight and does not call an LLM: falling packets animate on their own and the player can move the catcher with Left/Right, A/D, or the mouse. The loader remains on-screen for the entire swap and has a short minimum display time for very fast swaps.

Key tuning values are in `config.py`:

```text
ENABLE_CRT_SCREENSAVER = True
CRT_SCREENSAVER_IDLE_SECONDS = 180
CRT_SCREENSAVER_MODE_SECONDS = 45
CRT_SCREENSAVER_MODES = (star_drift, bmo_bounce, pixel_rain)
ENABLE_WINDOWS_SCR_SCREENSAVERS = True
WINDOWS_SCREENSAVER_DIR = ~/.bemo/screensavers/windows
WINDOWS_SCREENSAVER_ROTATE_SECONDS = 180
WINDOWS_SCREENSAVER_SEARCH_REMOVABLE = True
ENABLE_MODEL_SWITCH_LOADING_SCREEN = True
MODEL_SWITCH_RETRO_PROGRESS = True
MODEL_SWITCH_RETRO_PROGRESS_BLOCKS = 18
MODEL_SWITCH_RETRO_PROGRESS_LIT_BLOCKS = 5
MODEL_SWITCH_RETRO_PROGRESS_STEP_SECONDS = 0.11
ENABLE_MODEL_SWITCH_MINIGAME = True
MODEL_SWITCH_MIN_DISPLAY_SECONDS = 1.25
```

## Memory and project storage hierarchy

Bemo separates live context from durable storage so long-running use does not require stuffing years of history into every model prompt.

### 1. Live conversation context

Only the latest `MAX_RECENT_MESSAGES` messages are kept verbatim in the normal Ollama prompt. Long individual messages can be prompt-truncated while the full original remains on disk.

### 2. Persistent active-task / handoff state

Active work lives under `~/.bemo/tasks/`. The task ledger stores the original request, project association, current/previous roles, selected assets, factual checkpoints, completed work, decisions/results, remaining work, and handoff events. It survives specialist switches, crashes, and reboots. It intentionally does **not** store hidden chain-of-thought.

### 3. Project memory, facts, files, and Git

Projects are separate from general conversation memory:

```text
~/.bemo/projects/<project-slug>/
├── project.json
├── memory.md
├── facts.json
├── memory_history/
├── index.sqlite3
└── workspace/
    ├── .git/
    └── your actual project files...
```

The active project selection survives reboots. `memory.md` stores structured durable project state, `facts.json` stores authoritative keyed current facts with supersession history, and previous memory revisions are retained in `memory_history/`. The actual workspace gets local/offline Git history. Binary/non-text files are cataloged as project assets even when their contents are not placed in text FTS.

### 4. Long-term conversation archive

Every user and assistant message is transactionally appended to:

```text
~/.bemo/memory/conversation.sqlite3
```

The database uses SQLite WAL mode and `synchronous=FULL`. Raw messages are **not deleted when they leave the model context**. Bemo periodically creates compact structured long-term memory and uses SQLite full-text search to retrieve a bounded number of older relevant exchanges. This lets the on-disk archive grow for years without the model receiving years of conversation every turn.

#### Adaptive memory heat

Older chat memory uses a proportional, non-destructive heat score instead of a hard age cutoff. Each archived message keeps its original creation time plus `last_used_at`, `use_count`, and `heat_score`. Merely matching a search does **not** reheat it. Only a retrieved snippet that is actually inserted into model context receives reinforcement.

A single old reference normally remains cold. Repeated meaningful reuse accumulates heat through cold → warm → hot, with diminishing returns. Heat decays continuously with a configurable half-life when the memory is not used. Semantic relevance and project/task fit choose the candidate set first, and heat is capped to a small reranking bonus, so a highly relevant cold memory can still beat a frequently used but weakly related one. No conversation is deleted because it cooled down.

Default tuning lives in `config.py` (`MEMORY_HEAT_*`). The current defaults use a 30-day heat half-life, warm at 2.5, hot at 6.0, and a maximum stored heat of 10.0.

Consistent SQLite backups are created by default once per day under:

```text
~/.bemo/memory/backups/
```

The newest seven are retained. If the main SQLite archive is detected as corrupt during startup, Bemo preserves the damaged files and attempts to restore the newest backup rather than overwriting the evidence.

### 5. Cold durability and removable backups

Git history, superseded project facts, historical project-memory revisions, completed/interrupted task records, and raw conversation remain available without being injected into normal prompts. Full removable-drive backups preserve the persistent data tree plus the Bemo application source/configuration so an SSD failure is not the only copy of project state.

Set the environment variable `BEMO_DATA_DIR` if you want all persistent Bemo data somewhere other than `~/.bemo`.

## Project retrieval without a giant context

Bemo does **not** paste an entire repository into Ollama every turn. Project source files are incrementally indexed with local SQLite FTS. For each request, Bemo supplies:

- compact project memory;
- the active project identity;
- a few source-code chunks that match the current request;
- a small recent conversation window;
- compact long-term conversation memory;
- a few relevant older conversation snippets.

No embedding model is required, so retrieval has very little CPU/RAM overhead.

## Coding models can work on the real project

The `coding` profile still has dedicated Git-backed project tools when a project is active. These are optimized for safe source edits and automatic snapshots:

- list project files;
- read bounded line ranges;
- search project source;
- atomically write a project text file;
- exact-text replacement after reading the source;
- view the Git diff;
- run one of a small fixed group of checks (`python-compile`, `pytest`, `npm-test`, `cargo-test`, `go-test`).

Those dedicated project-editing tools remain scoped to the active workspace; `../` traversal and absolute project-tool paths are rejected. Separately, every model has the shared Supervisor-mediated `computer_control` capability for ordinary whole-PC work. Command execution uses argv arrays (not implicit `shell=True`), runs as the logged-in user, and places destructive/inline-shell/admin work behind confirmation instead of silently elevating privileges.

Before a coding-agent turn, Bemo attempts a local Git safety snapshot of any pre-existing changes. If Bemo writes files, it creates a post-change commit whose subject begins with `Bemo:`. That makes the spoken command **“undo last Bemo change”** safe to distinguish from ordinary/user commits.

If an installed model does not produce tool calls, Bemo can still answer normally; it will not fabricate successful file edits.

## Engineering / Deep Reasoning deterministic tools

The locked `engineering` profile receives the v1.8 deterministic toolkit plus the v1.9 diagnostics/reference/lab tools in addition to the shared handoff/file/computer-control layer:

- `calculate_python`: restricted numerical expressions plus linear solve, interpolation, polynomial fitting, numerical integration, root finding, and scalar minimization; no imports, files, network, or shell.
- `solve_math`: SymPy-backed solve/simplify/differentiate/integrate/roots/system solving.
- `convert_units`: common mechanical/electrical/thermal unit conversion with dimensional checks.
- `mechanical_calculator`: gear/sprocket ratios, RPM↔vehicle speed, torque multiplication, belt/chain speed, pulley/CVT ratios, beam stress/deflection, shaft torsion, bearing L10, and approximate bolt preload/torque.
- `electrical_calculator`: Ohm's law, series/parallel resistance, copper-wire voltage drop and voltage-drop sizing, battery runtime, solar string/parallel combination, DC-DC sizing, RC time constants, and capacitor energy.
- `thermal_calculator`: temperature conversion, heat energy, thermal resistance, steady-state rise, conduction, and convection.
- `inspect_engineering_file`: bounded reads of text/JSON/CSV/TSV/XLSX/XLSM/PDF engineering inputs.
- `plot_engineering_data`: creates persistent PNG plots under `~/.bemo/engineering/plots/`.
- `inspect_cad`: STEP/STP and STL/OBJ/PLY/OFF/3MF bounds/extents/area/volume/watertightness, STEP minimum-distance/interference checks with bounded top-level shape-pair reporting, revision comparison, arbitrary plane sections, FDM overhang screening, pull-direction draft screening, closed-shell/enclosed-volume detection, approximate sampled wall thickness, and uniform-density mass/inertia properties.
- `lookup_technical_reference`: bounded public technical web search/fetch that returns URLs; the policy prefers manufacturer documentation, standards bodies, and other primary technical sources.

Exact/tool-derived results are kept separate from model inference. Wire sizing is explicitly voltage-drop-only, bearing life and bolt torque carry approximation warnings, STEP native units must be verified, and technical web facts should be cited with the returned URL. Technical images still route to Vision and substantial source-code edits still route to Heavy Coding.

Manual smoke test after installation:

```bash
.venv/bin/python engineering_tools_cli.py smoke
```

## V1.9 grounding, diagnostics, references, instruments, and logging

- **Vision crop/zoom + OCR:** Fast Vision and Deep Technical Vision can call `crop_zoom_image` and `ocr_image`. Crops are automatically attached to the next vision tool round. OCR returns confidence and source-image bounding boxes and is explicitly treated as supporting evidence, not ground truth.
- **Local hardware diagnostics:** Main and Engineering can query live CPU/RAM/disk/network/process state, `lm-sensors`, SMART/NVMe data, PCI/USB inventory, bounded journal entries, and Ollama resident-model state. Fixed allowlisted read-only commands are used; no arbitrary shell is exposed.
- **Technical reference library:** user-supplied PDF/TXT/MD/CSV/JSON technical documents can be explicitly added under `~/.bemo/references/`, indexed with local SQLite FTS5, searched, and read by document/page locator. Web lookup remains a fallback rather than the first source when the user's own manual exists.
- **Read-only bench/embedded telemetry:** `instrument_telemetry` can passively read serial streams, read standard VESC `COMM_GET_VALUES` telemetry, issue a single SCPI query ending in `?`, or run a query explicitly allowlisted by the user in `~/.bemo/instruments.json`. This supports Arduino/Teensy streams, VESC, common BMS/USB-meter serial adapters, and SCPI power-supply/scope workflows without giving the model arbitrary instrument writes.
- **Background experiment logger:** Engineering can start a CSV logger for system metrics or allowlisted instrument queries, check/stop it later, summarize min/max/mean/median, and create a PNG plot. Logs live under `~/.bemo/engineering/telemetry/`.
- **Capability handoff instead of guessing:** every role has `request_capability_handoff`. If a role needs CAD measurement, live diagnostics, document search, instrument telemetry, data logging, heavy code editing, or a different vision level, the tool saves a factual checkpoint and routes deterministically to the owning specialist.

Additional smoke test:

```bash
.venv/bin/python grounded_tools_cli.py smoke
```

## Offline Git

Git is local version control and does not require GitHub or internet access. Bemo configures only a repository-local author identity:

```text
Bemo <bemo@local>
```

Nothing is pushed anywhere automatically.

## Removable-drive export

With an active project and a writable removable drive mounted, say:

> **“Hey Bemo, export project.”**

Bemo:

1. refreshes the project index;
2. takes an export Git snapshot if appropriate;
3. copies the project into `Bemo Exports/` on the removable drive using a `.partial` staging folder;
4. calculates SHA-256 hashes for every copied file;
5. verifies those hashes before the partial folder is renamed finished;
6. creates a ZIP by default;
7. creates a `.sha256` checksum beside the ZIP.

By default the exported project includes the workspace's `.git` folder, so the removable copy carries the complete local version history too.

If automatic removable-drive detection does not fit a particular installation, set `EXPORT_ROOT_OVERRIDE` in `config.py` to a known mounted path.

## Voice project commands

The complete current deterministic command list is generated from the same registry used by Bemo and is in `VOICE_COMMANDS.md`. Start a sleeping session with **“Hey Bemo”**; `over` is no longer used.

With the current Hey Bemo wake-session gate, examples include:

> **“Hey Bemo, create project motor controller.”**
>
> **“Hey Bemo, import project from USB.”**
>
> **“Hey Bemo, import files into current project.”**
>
> **“Hey Bemo, open project motor controller.”**
>
> **“Hey Bemo, what project is active.”**
>
> **“Hey Bemo, list projects.”**
>
> **“Hey Bemo, remember for this project preserve the serial protocol.”**
>
> **“Hey Bemo, save project snapshot.”**
>
> **“Hey Bemo, undo last Bemo change.”**
>
> **“Hey Bemo, export project.”**

For importing an existing local folder or using removable media from a terminal:

```bash
.venv/bin/python project_cli.py import "Bemo" /path/to/existing/repo
.venv/bin/python project_cli.py import-usb
.venv/bin/python project_cli.py import-usb "Motor Controller"
.venv/bin/python project_cli.py import-files-usb
.venv/bin/python project_cli.py list
.venv/bin/python project_cli.py open Bemo
.venv/bin/python project_cli.py status
.venv/bin/python project_cli.py export
```

Run `project_cli.py --help` for all commands.


## Supervisor — model/app/computer lifecycle

`start.sh` launches `supervisor.py` as Bemo's permanent appliance parent. In normal appliance mode it does **not** immediately launch the Pygame/LLM process: it starts a lightweight `tiny.en` standby wake listener and waits for **“Hey Bemo.”** After the wake phrase is detected, the standby listener releases the microphone/model and the Supervisor launches full Bemo. The Supervisor still exposes its structured mode-0600 Unix-socket API for model/app/computer lifecycle.

It provides:

- one-resident-Bemo-model-at-a-time lifecycle management;
- explicit unload-before-load when switching large brains;
- optional model preloading so the next voice command does not pay the full load delay;
- a permanently resident main brain (`keep_alive=-1`) while the games/media app is running;
- automatic return from a one-turn specialist to the main brain;
- allowlisted application launch/close/status voice shortcuts;
- shared whole-PC filesystem/program/terminal/process/screenshot/desktop actions for every model role;
- explicit confirmation queues for destructive/high-impact actions, including a stronger critical-action phrase;
- opt-in administrator execution through the OS `pkexec` authorization prompt after confirmation, never permanent root;
- a voice kill switch to disable/re-enable model computer control;
- Bemo UI crash restart (up to the configured limit), then return to wake standby if crashes persist;
- deterministic program-only reload/shutdown commands with no Linux reboot command;
- tiny Hey-Bemo standby wake service while full Bemo is intentionally stopped;
- supervisor/app logs under `~/.bemo/logs/`;
- machine-readable state under `~/.bemo/runtime/supervisor_state.json`.

The intended 32 GB workflow is:

```text
main brain resident
    -> unload main
    -> load big coding/engineering specialist
    -> complete one task
    -> unload specialist
    -> preload main brain again
    -> optionally launch RetroArch/VLC while main brain remains idle in RAM
```

The main LLM being resident does not mean it is continuously using CPU. While idle, its weights mostly occupy RAM and Bemo's voice/UI services keep listening.

### Shared full-computer control

Every model profile receives the same `computer_control` tool. It can list/read/write/copy/move/delete exact filesystem paths, open files and folders with the desktop, launch programs, run argv-based commands, inspect/terminate processes, capture screenshots, and control the mouse/keyboard when a supported desktop backend is present. This means Engineering, Coding, Main, and Vision can all continue at the same workbench without routing merely because they need to touch the computer.

The OS user account is the normal permission boundary. Bemo is **not** run permanently as root. A normal `run` action rejects `sudo`, `su`, `doas`, and `pkexec` wrappers. When administrator access is actually required, the model must request `admin_run`; Bemo pauses, asks for explicit confirmation, then invokes the OS `pkexec` authorization prompt.

Actions with meaningful destructive impact are confirmation-gated. Existing-file overwrites, recursive directory deletion, force-killing a process, package/system/service commands, inline shell/interpreter code, disk-partition/format tools, and all administrator commands are not executed until confirmed. Especially dangerous disk operations use the stronger phrase:

> **“Hey Bemo, confirm critical computer action.”**

Normal pending actions use:

> **“Hey Bemo, confirm computer action.”**

You can cancel instead with **“Hey Bemo, cancel computer action.”** A voice kill switch is also available:

> **“Hey Bemo, disable computer control.”**
>
> **“Hey Bemo, enable computer control.”**
>
> **“Hey Bemo, computer control status.”**

Pending confirmations survive a UI/model restart for a short TTL and are stored in a mode-0600 runtime file. Computer actions are written to `~/.bemo/logs/computer-actions.jsonl`; text/file contents and typed text are redacted from the audit log.

Desktop mouse/keyboard control currently uses `xdotool` when an X11/XWayland `DISPLAY` is available. Screenshot capture auto-detects `grim`, `gnome-screenshot`, `scrot`, or ImageMagick `import`. `computer_control` can report the detected desktop backends. If the final machine runs pure Wayland without XWayland, screenshot capture may still work while mouse/keyboard control will need a compatible input backend added/validated on that machine.

### Voice app control

Default examples after installing/configuring the applications:

> **“Hey Bemo, boot games.”**
>
> **“Hey Bemo, close games.”**
>
> **“Hey Bemo, import games from USB.”**
>
> **“Hey Bemo, rescan games.”**
>
> **“Hey Bemo, play F-Zero GX.”**
>
> **“Hey Bemo, start media player.”**
>
> **“Hey Bemo, what is running.”**

`SUPERVISOR_APPS` is an allowlist and uses argv lists rather than `shell=True`. The dedicated game subsystem resolves voice game names only against files inside the configured Bemo GameCube ROM directory before launching Dolphin through a trusted argv command.

If the UI crashes, the parent Supervisor restarts it up to the configured limit. If full Bemo exits normally or is intentionally shut down, the Supervisor remains alive and returns to Hey-Bemo standby. Set `BEMO_NO_SUPERVISOR=1` only when intentionally debugging the old direct-launch path.

## Qualified model roster and routing

The current roster is based on real local benchmarks, with role policies stored under `policies/` and loaded into each profile at runtime.

```python
MODEL_PROFILES = {
    "normal": {"route_label": "HANDLE_YOURSELF", ...},
    "coding": {"route_label": "HEAVY_CODING", ...},
    "engineering": {"route_label": "ENGINEERING", "engineering_tools": True, ...},
    "fast-vision": {"route_label": "FAST_VISION", ...},
    "deep-vision": {"route_label": "DEEP_TECHNICAL_VISION", ...},
}
```

Current qualification state:

- **Main Brain — locked:** Qwen3-30B-A3B-Instruct-2507
- **Heavy Coding — locked:** Qwen3-Coder-30B-A3B
- **Engineering / Deep Reasoning — locked:** Qwen3-30B-A3B-Thinking-2507
- **Fast / General Vision — locked:** Qwen3-VL-30B-A3B-Instruct; final ordinary-UI validation passed on a normal Amazon homepage screenshot at 9.8 tok/s prompt processing and 5.4 tok/s generation on the CPU-only benchmark setup
- **Fast Coding — removed:** Qwen2.5-Coder-3B repeatedly failed raw and policy-validation tests. Tiny/simple coding stays with the Main Brain; substantial coding routes to Heavy Coding.
- **Deep Technical Vision — locked:** InternVL3.5-30B-A3B

The Main Brain routes by **role**, not by hard-coded model name. When a specialist is needed it returns one machine-readable line such as:

```text
ROUTE: ENGINEERING
```

Bemo consumes that line locally, asks the Supervisor to switch to the configured profile, runs the user's original task on the specialist, then returns to the Main Brain after the specialist turn. A route is accepted only when the model's entire response is one valid `ROUTE:` line, preventing ordinary prose from accidentally triggering a switch.

Fast Vision can escalate technical imagery with `ROUTE: DEEP_TECHNICAL_VISION`; the Supervisor now maps that role to the locked InternVL3.5 profile.

Computer/OS control remains in the local Supervisor layer. All model roles may request it, but they must use returned tool results as truth. Commands execute as argv arrays under the logged-in user, destructive/high-impact actions require confirmation, and administrator work requires a separate confirmed OS authorization step.

### Model tags

Bemo still uses Ollama for the integrated runtime. Point these environment variables at the local Ollama tags you create/import for the exact benchmarked GGUFs:

```text
BEMO_MAIN_MODEL
BEMO_CODER_MODEL
BEMO_ENGINEERING_MODEL
BEMO_FAST_VISION_MODEL
BEMO_DEEP_VISION_MODEL
```

The config includes readable local-alias defaults (`qwen3-main:30b`, `qwen3-coder:30b`, `qwen3-engineering:30b`, `qwen3-vl-fast:30b`, `internvl3.5-deep:30b`), but Bemo never silently downloads a multi-gigabyte model. Override the environment variables if your imported tags use different names.

Manual voice switching still works, for example:

> **“Hey Bemo, switch to coding model.”**
>
> **“Hey Bemo, switch to engineering model.”**
>
> **“Hey Bemo, switch to vision model.”**
>
> **“Hey Bemo, switch to normal model.”**
>
> **“Hey Bemo, ask coder to fix the failing tests.”**

The same conversation archive, recent context, active project, project memory, file index, and Git history are supplied regardless of which brain is selected. The previous Ollama model is explicitly unloaded on a switch to avoid keeping two large models resident in RAM.

### Model-profile design

The active per-role prompts are intentionally compact: roughly three sentences covering the role, its most important failure guardrail, and escalation/quality behavior. Shared routing, handoff, project-memory, file, and computer-control behavior remains centralized in `shared_handoff.md` so it is not duplicated in every model call.

- `main_brain.md`: normal conversation, explanations, planning, simple reasoning, and routing; route by required action rather than topic; prefer clear useful answers over unnecessary depth.
- `engineering.md`: calculations/design/science from known inputs; track units and fact-vs-assumption boundaries; return `INSUFFICIENT DATA` when a critical unknown blocks a reliable conclusion.
- `heavy_coding.md`: substantial coding/debugging/repo work; read before editing, preserve scope, verify changes and test when possible; report uncertainty or unverified behavior rather than claiming success.
- `fast_vision.md`: ordinary screenshots/UI/photos/labels/text; separate direct observation from inference and do not invent unclear details; route CAD/schematics/ambiguous geometry/deep technical visual work to Deep Technical Vision.
- `deep_technical_vision.md`: ground labels/values/units/geometry before reasoning, cross-check dense technical pairings, and refuse unsupported diagnoses.
- `engineering_tools.md`: prefer deterministic engineering tools for exact math, measurements, plots, and published specifications.

The longer benchmark-derived qualification policies are preserved under `policies/benchmark_notes/` for reference and future requalification, but are no longer injected into every model call.

## Hardware auto-tuning

Bemo detects the actual CPU topology and installed RAM at startup; it does not maintain a table of CPU-model profiles. Default behavior:

- before empirical calibration exists, Ollama safely falls back to the detected physical-core count;
- the installer benchmarks topology-derived Ollama candidates (for example 4/6/8 on a 4C/8T CPU or 8/12/16 on an 8C/16T CPU);
- generation speed is weighted more heavily than prompt speed, while concurrent TTS slowdown is penalized when the TTS probe is available;
- candidates within 5% of the best balanced score prefer the lower thread count, preserving headroom instead of chasing tiny throughput gains;
- Whisper and TTS keep their own bounded worker pools and reserve one physical core on 4+ core systems for Pygame/audio/OS responsiveness;
- common BLAS/OpenMP pools are capped before NumPy/Torch load;
- requested Ollama context is chosen conservatively from installed RAM;
- Linux physical-core detection uses psutil when available, then sysfs and `/proc/cpuinfo` topology fallbacks.

The measured result lives at `~/.bemo/hardware/autotune.json`. Its CPU name, physical/logical topology, benchmark algorithm version, tested model, results, TTS overlap measurements, and chosen Ollama thread count are retained for inspection. A mismatched CPU/topology is never trusted. Explicit numeric overrides remain in `config.py` for controlled benchmarks or unusual recovery cases.

Current RAM-based context policy:

- under 12 GiB: 1536
- 12–20 GiB: 2048
- 20–28 GiB: 4096
- 28–96 GiB: 8192
- 96+ GiB: 16384

Everything can still be manually overridden in `config.py`.

Check what a machine detected:

```bash
.venv/bin/python print_hardware.py
```

## Local speech / animation

The existing optimized speech path remains:

- faster-whisper `tiny.en`, CPU INT8 only while full Bemo is off and waiting for **Hey Bemo**;
- faster-whisper `base.en`, CPU INT8 for full conversation STT;
- ambient calibration once at startup;
- local Chatterbox Nano TTS with a persistent cloned-speaker profile;
- no CPU-burning busy-wait loops;
- sentence-level streaming TTS for ordinary chat;
- real audio amplitude + FFT lip-sync;
- the original BMP / AE / consonant / F / L / O / QW / R / TH mouth sprites;
- a **Hey Bemo** wake-session gate so room chatter is discarded while Bemo is asleep; no trailing end word is required.

Coding-agent turns are intentionally non-streamed while tool calls are being resolved. Bemo speaks only a short status at the end rather than reading source code aloud.

## Install — official appliance target: Linux Mint 22.3 Xfce 64-bit

Use **Linux Mint 22.3 Xfce 64-bit on both Bemo PCs**. Bemo now treats this as the official appliance target and checks it at the beginning of `install.sh`. Xfce/X11 keeps the CRT/Pygame/Wine/desktop-control path simple and lightweight. `BEMO_ALLOW_UNSUPPORTED_OS=1` exists only as a developer escape hatch when intentionally testing another Debian-family system.

Run the installer as the normal Bemo desktop account, **not** as root. The installer calls `sudo` itself for apt, LightDM, and other system-level setup. Python 3.11 is recommended.

```bash
chmod +x install.sh start.sh
./install.sh
./start.sh
# then say: Hey Bemo
```

`install.sh` now configures **LightDM automatic login itself** for the current Bemo account and installs the Xfce desktop-autostart bridge. No separate Login Window step is required. Automatic login does not affect BIOS/UEFI entry because firmware and the boot menu run before Linux/LightDM.
The Bemo systemd user service is deliberately **not** enabled against `default.target`; Xfce's desktop autostart launches `bemo-session-start.sh`, which imports `DISPLAY`, `XAUTHORITY`, DBus/audio variables, disables X11 blanking/DPMS, and then starts the restartable Supervisor service.

A normal online `./install.sh` now performs the full Mint/Xfce appliance setup: system packages, Wine, Dolphin/Flatpak fallback, Ollama, Python environment, Windows 95/98 screensaver pack, ES-DE, Whisper, Chatterbox Nano TTS, and the complete locked Bemo model roster. No manual `ollama pull` step is required.

Locked Ollama sources and stable Bemo aliases:

| Role | Exact source pulled | Bemo alias | Approx. download |
|---|---|---|---:|
| Main | `qwen3:30b-a3b-instruct-2507-q4_K_M` | `qwen3-main:30b` | 19 GB |
| Engineering | `qwen3:30b-a3b-thinking-2507-q4_K_M` | `qwen3-engineering:30b` | 19 GB |
| Heavy Coding | `qwen3-coder:30b-a3b-q4_K_M` | `qwen3-coder:30b` | 19 GB |
| Fast Vision | `qwen3-vl:30b-a3b-instruct-q4_K_M` | `qwen3-vl-fast:30b` | 20 GB |
| Deep Vision | `hf.co/bartowski/OpenGVLab_InternVL3_5-30B-A3B-GGUF:Q4_K_M` | `internvl3.5-deep:30b` | ~18.6 GB |
| Emergency | `qwen3:4b-instruct-2507-q4_K_M` | `qwen3-emergency:4b` | 2.5 GB |

If the machine freezes or the network drops, run `./install.sh` again. The installer is idempotent: completed exact-model aliases are skipped, the unfinished model is pulled again against Ollama's existing blob/partial state, ES-DE preserves its `.download` partial and requests the remaining byte range, and completed screensaver/speech assets are reused. A package manager may occasionally need to redownload the single file that was being transferred at the instant of failure, but the whole Bemo install does not restart from zero.

Useful status commands:

```bash
.venv/bin/python mint_appliance.py status
.venv/bin/python model_installer.py status
.venv/bin/python prefetch_runtime_models.py status
.venv/bin/python fetch_legacy_screensavers.py status
.venv/bin/python windows_screensaver_cli.py status
.venv/bin/python game_launcher.py doctor
```

## Microphone

Bemo uses the operating system default microphone (`DEVICE_INDEX = None`) so fresh Linux installs do not depend on unstable PortAudio device numbers. List devices with:

```bash
.venv/bin/python print_microphones.py
```

Set `DEVICE_INDEX` in `config.py` only if you intentionally want to override the OS default.

## Tests

The non-audio self-test exercises generic hardware detection/autotune selection plus the durability layer:

```bash
.venv/bin/python self_test.py
```

It creates temporary conversation/project stores, closes and reopens the SQLite archive as a simulated reboot, exercises source retrieval, verifies path traversal is rejected, runs a Python compile check, creates local Git history, exports to a fake removable drive, and verifies the exported checksums/ZIP.


### Legacy Windows screensaver auto-fetch

A normal online `install.sh` run automatically downloads the published legacy screensaver pack used by the Wine idle backend. Offline installs remain functional and fall back to Bemo's built-in CRT savers. Manual commands: `./.venv/bin/python fetch_legacy_screensavers.py status` and `./.venv/bin/python fetch_legacy_screensavers.py fetch --force`.

### v1.25.8 robust Hey-Bemo transcription aliases

The wake gate now accepts a deliberately small set of phonetic spellings that Faster-Whisper emitted for the invented name on the target ATR4650 USB microphone, including `BMO`, stretched `beee mo`, and occasional `P-mo`. These relaxed forms are accepted only after an initial `Hey` at the beginning of the utterance, so ordinary mentions of those strings do not wake Bemo. Standby and full conversational wake parsing share the same matcher.

### v1.25.9 strict Hey-Bemo prefix on every utterance

Every accepted spoken utterance must now begin with `Hey Bemo` (or one of the tightly scoped Whisper spelling aliases from v1.25.8). A bare wake phrase is acknowledgement-only and no longer opens a 90-second unprefixed conversation window. Background speech and Whisper hallucinations such as repeated `Okay` or `You` are discarded before local commands, LLM routing, history, or project memory. Ignored unprefixed transcripts are demoted to TRACE logging so normal debug output is not flooded by room-noise hallucinations.


### v1.26.1 explicit loading/ready startup cues

- The first standby `Hey Bemo` now launches full Bemo and announces **"Loading."** as soon as the approved TTS voice is online.
- The Main Brain continues preloading/warming in parallel with UI/STT/TTS startup.
- Bemo announces **"Ready."** only after the warmed Main Brain, startup/project work, and microphone calibration are complete.
- Speech received before `Ready.` is not accepted because the normal microphone loop does not begin until startup completes.
- Subsequent bare wake acknowledgements while Bemo is already running still use the normal `Yeah?` cue.

### v1.26.0 warm wake + smooth streaming speech

- A standby Hey Bemo now starts main-brain preload/warmup in parallel with full UI/STT/TTS startup.
- Main-brain preload includes one tiny hidden chat inference so the first real user turn does not pay a second cold-start penalty.
- Duplicate preload requests are skipped when Ollama already reports the model resident.
- Streaming TTS keeps a low-latency first chunk (10-18 words) and buffers follow-up chunks to 24-36 words, preventing short sentence WAVs from outrunning Chatterbox synthesis on CPU-only hardware.

### v1.26.2 truthful Ready barrier and latency diagnostics

The first standby wake now treats **Ready.** as a hard usability promise instead of a weight-load status. The Supervisor still begins mapping the Main Brain in parallel with face/STT/TTS startup, but the child performs one hidden runtime warm-up using the real Main Brain system prompt, current conversation prefix, active tool schemas, production context size, and normal keep-alive settings before announcing Ready. This pays the expensive first full prompt/tool-prefix evaluation during the visible **Loading.** phase so the first real `Hey Bemo, ...` turn can reuse the same prefix cache instead of appearing to cold-start after Bemo already claimed readiness.

Full-mode speech capture is tuned for the dedicated ATR4650 appliance path: the post-speech pause is 0.65 s, ambient calibration is retained but the resulting energy threshold is then stable instead of continuously drifting, and SpeechRecognition phrase/non-speaking thresholds are shortened modestly. Debug logs now separate microphone capture time from Whisper time, report LLM first-text latency, report each Chatterbox chunk synthesis time/word count, and measure real inter-chunk playback gaps. Streaming keeps a small 6-10-word first chunk for responsiveness and 20-30-word follow-up chunks as a compromise between Chatterbox setup cost and continuous audio.

The `hardware_diagnostics` tool description and Main Brain policy now explicitly scope hardware telemetry to Bemo's local Linux computer. Human anatomy, injuries, health/medical statements, figurative language, and ordinary conversation must never trigger computer hardware diagnostics.

### v1.26.3 nonblocking Supervisor startup IPC

- Ollama model load/unload uses a dedicated lifecycle lock instead of the general Supervisor state lock.
- Fast status/ping/UI polling remains responsive while a large brain is loading or warming.
- Unix-socket clients that disconnect before a reply are treated as normal instead of producing BrokenPipe traceback storms.
- The truthful startup barrier from v1.26.2 is unchanged: `Ready.` is spoken only after the production prompt/tool warm-up and microphone calibration finish.

### v1.26.5 true Chatterbox streaming + global desktop idle

- Normal Bemo speech no longer synthesizes temporary WAV files. Chatterbox Nano now emits speech tokens incrementally; Bemo decodes those tokens through an S3Gen streaming path with stable mel noise, HiFT source caching, and short crossfades, then feeds the resulting 24 kHz mono PCM directly into one persistent PortAudio output stream. Ollama text generation, T3 token generation, S3Gen decoding, and playback therefore overlap instead of waiting for a finished waveform.
- The first S3Gen decode is triggered after 10 speech tokens and later decodes every 24 speech tokens by default. The bounded PCM queue absorbs short synthesis jitter without building an unbounded spoken backlog. Streaming lip-sync analysis is generated from the same live PCM immediately before playback.
- The current Chatterbox Nano loader and Bemo's saved permanent voice conditionals remain unchanged. The streaming adapter lives in Bemo so the install does not downgrade Nano to an older Turbo-only Chatterbox branch.
- CRT idle now follows the X server's desktop-wide keyboard/mouse idle time via `xprintidle`, rather than only input events delivered to Bemo's pygame window. Working in another application therefore keeps Bemo awake. If a classic `.scr` exits inside its startup grace period immediately after real desktop input, it is treated as a normal wake instead of being falsely quarantined as a crashing saver.
- The first standby boot cue is now **"Hi friend! Give me a couple minutes to full boot up and I'll let you know when Im ready!"** and the final cue is **"Hi friend I am all ready to talk to! All done loading"**. The v1.26.2 truthful-ready barrier remains in place: the final cue occurs only after production prompt/tool warm-up, project startup work, and microphone calibration.

