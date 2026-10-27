import os

from voice_commands import CANONICAL_SLEEP_PHRASE, CANONICAL_WAKE_PHRASE, model_command_catalog
import tempfile
from pathlib import Path

from hardware import apply_thread_environment, detect_hardware

# -----------------------------------------------------------------------------
# Hardware auto-tuning
# -----------------------------------------------------------------------------
# CPU tuning is topology-based and empirically refined: Bemo detects the
# physical/logical core count on whatever CPU is installed, then uses the saved
# hardware efficiency benchmark when one matches this CPU. No CPU-model profile
# list is required. Advanced benchmarking/recovery can still use the explicit
# thread overrides below.
AUTO_TUNE_CPU = True
AUTO_TUNE_CONTEXT = True

HARDWARE = detect_hardware()

LEGACY_CPU_THREADS = 4
LEGACY_CONTEXT = 2048

# Any override left as None uses the detected hardware value.
OLLAMA_THREAD_OVERRIDE = None
STT_THREAD_OVERRIDE = None
TORCH_THREAD_OVERRIDE = None
TORCH_INTEROP_THREAD_OVERRIDE = None
OLLAMA_CONTEXT_OVERRIDE = None


def _auto_or(value, detected):
    return detected if value is None else value


# Apply the math-library cap before NumPy/Torch are imported by systems.chat.
_ENV_TORCH_THREADS = _auto_or(
    TORCH_THREAD_OVERRIDE,
    HARDWARE.torch_threads if AUTO_TUNE_CPU else LEGACY_CPU_THREADS,
)
apply_thread_environment(HARDWARE, _ENV_TORCH_THREADS)



# -----------------------------------------------------------------------------
# Durable storage / project workspaces
# -----------------------------------------------------------------------------
# Bemo's long-term data lives outside the source tree by default, so replacing
# or upgrading the app cannot erase conversations or projects. Set BEMO_DATA_DIR
# to move the whole store to another disk.
APP_ROOT = Path(__file__).resolve().parent
DATA_ROOT = Path(os.environ.get("BEMO_DATA_DIR", str(Path.home() / ".bemo"))).expanduser()
MEMORY_DB_PATH = DATA_ROOT / "memory" / "conversation.sqlite3"
MEMORY_BACKUP_DIR = DATA_ROOT / "memory" / "backups"
# "Remember that ..." outside projects (systems/personal_memory.py): the owner's own words, private, in backups.
PERSONAL_NOTES_PATH = DATA_ROOT / "memory" / "notes.json"
PROJECTS_ROOT = DATA_ROOT / "projects"
TASKS_ROOT = DATA_ROOT / "tasks"
APP_VERSION = "supervisor-v1.31.1-updates-take-their-time"

# Raw messages stay indefinitely on disk. New sessions start with an empty
# live prompt. Historical summary and archive hits are retrieved on recall.
MAX_RECENT_MESSAGES = 32
HISTORY_CONTEXT_CHAR_BUDGET = 4800
HISTORY_COMPACTION_BATCH = 8
RECENT_MESSAGE_CHAR_LIMIT = 4000
SUMMARY_ARCHIVE_BATCH_MESSAGES = 12
# Durable summaries are no longer forced into tiny word-count boxes. They are
# stored in full on disk; prompt construction decides how much to retrieve.
SUMMARY_NUM_PREDICT = 2048
CONVERSATION_MEMORY_CONTEXT_CHAR_BUDGET = 5000
MEMORY_RETRIEVAL_COUNT = 4
MEMORY_RETRIEVAL_CHAR_BUDGET = 2800
# Older memories use a proportional, decaying heat score. Merely appearing in a
# search result does not reinforce a memory; only snippets actually injected into
# a task/answer receive reinforcement. One use stays cold, repeated useful use
# moves a memory through warm toward hot, and inactivity gradually cools it.
MEMORY_HEAT_HALF_LIFE_DAYS = 30.0
MEMORY_HEAT_REINFORCEMENT = 1.0
MEMORY_HEAT_WARM_THRESHOLD = 2.5
MEMORY_HEAT_HOT_THRESHOLD = 6.0
MEMORY_HEAT_MAX = 10.0
# Heat is deliberately only a bounded reranking bonus so semantic relevance can
# still beat a frequently used but weakly related memory.
MEMORY_HEAT_RANK_MAX_BOOST = 2.0
MEMORY_RETRIEVAL_CANDIDATE_MULTIPLIER = 8
PROMPT_CHAR_PER_TOKEN_BUDGET = 2.6
PROJECT_TOOL_SCHEMA_CHAR_RESERVE = 3500
MEMORY_BACKUP_INTERVAL_HOURS = 24
MEMORY_BACKUP_RETAIN = 7

# Active project context is also bounded: project memory + the most relevant
# source chunks, retrieved locally with SQLite FTS. No embedding model is needed.
PROJECT_CONTEXT_ENABLED = True
PROJECT_CONTEXT_CHAR_BUDGET = 6000
PROJECT_RETRIEVAL_CHUNKS = 5
PROJECT_REINDEX_INTERVAL_SECONDS = 30
PROJECT_FILE_MAX_BYTES = 1_000_000
PROJECT_INDEX_EXTENSIONS = (
    ".py", ".pyi", ".js", ".jsx", ".ts", ".tsx", ".html", ".css", ".scss",
    ".json", ".jsonc", ".toml", ".yaml", ".yml", ".ini", ".cfg", ".md",
    ".txt", ".rst", ".c", ".h", ".cpp", ".hpp", ".cc", ".cxx", ".java",
    ".kt", ".kts", ".rs", ".go", ".sh", ".bash", ".zsh", ".ps1", ".bat",
    ".sql", ".xml", ".csv", ".env",
)
PROJECT_EXCLUDE_DIRS = (
    ".git", ".venv", "venv", "node_modules", "dist", "build", "target",
    "__pycache__", ".pytest_cache", ".mypy_cache", ".idea", ".vscode",
)
PROJECT_SUMMARY_BATCH_MESSAGES = 12
PROJECT_MEMORY_HISTORY_RETAIN = 100
PROJECT_IMPORT_REQUEST_TIMEOUT_SECONDS = 24 * 60 * 60  # verified removable-drive copies may be large
PROJECT_FILE_EXPORT_REQUEST_TIMEOUT_SECONDS = 24 * 60 * 60  # large verified file/folder exports may take time

# Automatic task/project association. Explicit project commands always win.
# Otherwise Bemo first uses deterministic name/keyword matching and may ask the
# Main Brain for a tiny classifier response when the match is ambiguous.
AUTO_PROJECT_CLASSIFICATION = True
AUTO_PROJECT_CLASSIFICATION_USE_LLM = False  # no extra inference for ordinary conversation
PROJECT_CLASSIFIER_NUM_PREDICT = 32
PROJECT_CLASSIFIER_MEMORY_CHARS = 1200

# Persistent task handoff ledger shared by all specialists.
ENABLE_TASK_LEDGER = True
TASK_CONTEXT_MAX_CHECKPOINTS = 8
MODEL_ROUTE_MAX_HOPS = 1  # One verified handoff per original user turn; no specialist chaining
SPECIALIST_SWITCH_APPROVAL_TIMEOUT_SECONDS = 20.0  # Every model change requires an explicit spoken YES

# Shared file discovery. Models may search the user's home directory, mounted
# removable drives, and (when explicitly requested) the broader local machine.
# Full read/write/app/terminal/desktop control is separately mediated by the
# Supervisor below, so every model role can use the same logged-in-user tools.
ENABLE_SHARED_FILE_TOOLS = True
ENABLE_FULL_PC_FILE_SEARCH = True
FILE_SEARCH_MAX_SCAN_FILES = 80_000
FILE_SEARCH_TIMEOUT_SECONDS = 4.0
FILE_SEARCH_EXCLUDE_DIRS = (
    ".cache", ".Trash", ".Trash-1000", "node_modules", ".git", "__pycache__",
    "proc", "sys", "dev", "run",
)
FILE_SEARCH_SYSTEM_ROOTS = ("/etc", "/opt", "/usr/local", "/var/log")
VISION_IMAGE_MAX_BYTES = 100 * 1024 * 1024
# Screenshots for a vision brain (2026-10-19: a full 1920x1080 screen took Deep Vision 2 minutes).
# A quick scan sends a copy at most this wide; a detailed scan (or a request to read text) sends it whole.
# Measured on a known test screen: 640 wide ~30 s, windows and large text right, small (16 px) text
# misread; 960 and 1280 cost the same as each other (same image tiles, ~65 s); full size ~130 s.
VISION_QUICK_SCAN_MAX_WIDTH = int(os.environ.get("BEMO_VISION_QUICK_SCAN_MAX_WIDTH", "640"))
# Detailed scans: 1280 wide read the test screen's small (16 px) text exactly, like full size, in about half
# the time (~65 s vs ~130-190 s on a 1920 screen). 0 sends the full screenshot.
VISION_DETAILED_SCAN_MAX_WIDTH = int(os.environ.get("BEMO_VISION_DETAILED_SCAN_MAX_WIDTH", "1280"))
VISION_IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tif", ".tiff")

# Shared full-computer control. All model roles request actions through the
# Supervisor; models never execute OS calls themselves. Normal actions run with
# the logged-in Bemo user's permissions. Destructive/high-impact operations are
# queued for explicit voice confirmation. Administrator actions are never
# silently elevated; after confirmation they use the OS pkexec authorization
# prompt when available.
ENABLE_SHARED_COMPUTER_CONTROL = True
COMPUTER_CONTROL_DEFAULT_ENABLED = True
COMPUTER_CONTROL_STATE_PATH = DATA_ROOT / "runtime" / "computer_control.json"
COMPUTER_PENDING_ACTIONS_PATH = DATA_ROOT / "runtime" / "pending_computer_actions.json"
COMPUTER_ACTION_LOG_PATH = DATA_ROOT / "logs" / "computer-actions.jsonl"
COMPUTER_SCREENSHOT_DIR = DATA_ROOT / "runtime" / "screenshots"

# Runtime presentation controls. Every launch starts Face + TTS on. Voice commands
# may switch to Text mode; TTS may be disabled only while Text mode is active.
# Microphone/STT and the Hey Bemo wake gate remain available when Text mode is silent.
INTERFACE_SETTINGS_PATH = DATA_ROOT / "runtime" / "interface.json"
DEFAULT_DISPLAY_MODE = "face"
DEFAULT_TTS_ENABLED = True
COMPUTER_ACTION_TIMEOUT_SECONDS = 30.0
COMPUTER_ACTION_OUTPUT_MAX_CHARS = 40_000
COMPUTER_TEXT_READ_MAX_BYTES = 2_000_000
COMPUTER_CONFIRMATION_TTL_SECONDS = 600
COMPUTER_PENDING_ACTION_LIMIT = 8

# All current model roles can use shared tools (task checkpoints, file discovery,
# full computer control, project facts/assets). Project-editing tools remain
# separately available to the coding profile for Git-backed project changes.
SHARED_TOOL_MAX_ROUNDS = 10

# Git is completely local/offline. Bemo uses it as the project safety net.
AUTO_GIT_SNAPSHOTS = True
GIT_AUTHOR_NAME = "Bemo"
GIT_AUTHOR_EMAIL = "bemo@local"

# Leave None for automatic removable-drive detection. Set a path while testing
# or when a particular mounted drive should always be used.
EXPORT_ROOT_OVERRIDE = None
EXPORT_INCLUDE_GIT_HISTORY = True
EXPORT_CREATE_ZIP = True

# Full verified backup of Bemo's persistent state + app config to removable
# storage. Voice command: "backup Bemo to USB".
FULL_BACKUP_CREATE_ZIP = True

# Coding profiles can safely edit only the active workspace. They receive file
# tools, not an unrestricted shell. Every editing turn gets a pre-change Git
# baseline and a post-change Bemo commit when files actually changed.
ENABLE_PROJECT_TOOLS = True
PROJECT_TOOL_MAX_ROUNDS = 8
PROJECT_TOOL_CHECK_TIMEOUT_SECONDS = 90
CODING_SPOKEN_MAX_WORDS = 24  # whole sentences only; never cut mid-sentence
# Conversational questions to a specialist (no tools needed) are answered
# briefly and streamed like Main. Real work turns keep their full budgets.
SPECIALIST_CHAT_NUM_PREDICT = int(os.environ.get("BEMO_SPECIALIST_CHAT_TOKENS", "320"))
SPECIALIST_CHAT_MAX_WORDS = 35  # ~10 s of speech, ended at a sentence/line boundary
SPECIALIST_CHAT_INSTRUCTION = (
    "Spoken question, not a work request: answer in 1-2 short sentences of plain speech. "
    "No lists, headings, markdown or code."
)
SPECIALIST_WORK_STARTED_CUE = "On it."

# Engineering specialist deterministic tool suite. These are explicit, bounded
# APIs rather than arbitrary shell/Python execution. Generated plots live under
# the persistent Bemo data root so upgrades do not erase them.
ENABLE_ENGINEERING_TOOLS = True
ENGINEERING_TOOL_MAX_ROUNDS = 10
ENGINEERING_OUTPUT_ROOT = DATA_ROOT / "engineering"
ENGINEERING_FILE_MAX_BYTES = 50 * 1024 * 1024
ENGINEERING_TEXT_MAX_CHARS = 30_000
# 3D models (2026-10-20): Engineering writes OpenSCAD and BMO renders it to an STL, then measures it.
# 1 OpenSCAD unit = 1 mm. With a project open the files go to its models/ folder, otherwise here.
CAD_OUTPUT_DIR = Path(os.environ.get("BEMO_CAD_DIR", str(Path.home() / "BMO-3D")))
OPENSCAD_BINARY = os.environ.get("BEMO_OPENSCAD_BINARY", "openscad")
OPENSCAD_TIMEOUT_SECONDS = 180.0

# Grounding / diagnostics / laboratory tool suite. Vision tools are deliberately
# small and local: crop/zoom plus Tesseract OCR. Hardware diagnostics and the
# reference/instrument/logger tools are read-only or explicitly user-populated.
ENABLE_VISION_ASSIST_TOOLS = True
VISION_TOOL_OUTPUT_ROOT = DATA_ROOT / "vision"
VISION_OCR_LANGUAGE = "eng"
ENABLE_HARDWARE_DIAGNOSTICS = True
ENABLE_TECHNICAL_REFERENCE_LIBRARY = True
REFERENCE_LIBRARY_ROOT = DATA_ROOT / "references"
REFERENCE_LIBRARY_MAX_BYTES = 100 * 1024 * 1024
ENABLE_INSTRUMENT_TELEMETRY = True
INSTRUMENT_CONFIG_PATH = DATA_ROOT / "instruments.json"
ENABLE_DATA_LOGGER = True
TELEMETRY_LOG_ROOT = DATA_ROOT / "engineering" / "telemetry"


# -----------------------------------------------------------------------------
# Controller-first GameCube-only game subsystem
# -----------------------------------------------------------------------------
# ES-DE is the fullscreen/controller library and standalone Dolphin is the only
# emulator. Dolphin's dedicated user directory lives under Bemo's persistent
# data root so controller mappings, memory cards and save states survive updates.
ENABLE_GAME_SUBSYSTEM = True
GAME_ROOT = DATA_ROOT / "games"
GAME_ROM_ROOT = GAME_ROOT / "roms"
GAME_GAMECUBE_ROM_DIR = GAME_ROM_ROOT / "gamecube"
GAME_DOLPHIN_USER_DIR = GAME_ROOT / "dolphin-user"
GAME_FRONTEND_WIDTH = 640
GAME_FRONTEND_HEIGHT = 480
GAME_ESDE_RELEASE_METADATA_URL = "https://gitlab.com/es-de/emulationstation-de/-/raw/master/latest_release.json"
GAME_IMPORT_REQUEST_TIMEOUT_SECONDS = 24 * 60 * 60  # a large USB library copy may run for hours

# -----------------------------------------------------------------------------
# Always-running supervisor / application lifecycle
# -----------------------------------------------------------------------------
# start.sh launches supervisor.py as Bemo's tiny parent process. It owns model,
# app, and shared computer-control execution and can restart the UI if it crashes.
# The Unix socket is mode 0600. Model computer requests are structured/audited;
# command execution uses argv arrays and destructive/admin work is confirmation-gated.
ENABLE_SUPERVISOR = True
SUPERVISOR_RUNTIME_DIR = DATA_ROOT / "runtime"
SUPERVISOR_SOCKET_PATH = SUPERVISOR_RUNTIME_DIR / "supervisor.sock"
SUPERVISOR_STATE_PATH = SUPERVISOR_RUNTIME_DIR / "supervisor_state.json"
SUPERVISOR_LOG_DIR = DATA_ROOT / "logs"
SUPERVISOR_REQUEST_TIMEOUT_SECONDS = 3.0
SUPERVISOR_MODEL_SWITCH_TIMEOUT_SECONDS = 180.0
SUPERVISOR_PRELOAD_ACTIVE_MODEL_ON_START = True
SUPERVISOR_PRELOAD_MODELS_ON_SWITCH = True
SUPERVISOR_PARALLEL_PRELOAD_ON_STANDBY_WAKE = True
SUPERVISOR_WARM_MAIN_MODEL_ON_PRELOAD = False
RUNTIME_WARM_ACTIVE_PROMPT_ON_STANDBY_WAKE = False  # legacy behavior; use RUNTIME_WARM_ACTIVE_PROMPT_ON_START
# Pre-read the active model's prompt in the background after startup and every
# model switch, while Bemo listens. Saves ~8 s on the first answer (live test).
WARM_PROMPT_AFTER_SWITCH = os.environ.get("BEMO_WARM_PROMPT_AFTER_SWITCH", "1").lower() not in {"0", "false", "off"}
# On (2026-10-19): "ready" must mean he can answer now; the ~9 s warm-up runs while the loading
# message plays, before "ready" (it used to run after it, and the first question waited 17 s).
RUNTIME_WARM_ACTIVE_PROMPT_ON_START = os.environ.get("BEMO_WARM_RUNTIME_PROMPT", "1").lower() not in {"0", "false", "off"}
SUPERVISOR_MAX_CRASH_RESTARTS = 3
SUPERVISOR_RESTART_DELAY_SECONDS = 2.0
SUPERVISOR_APP_STOP_TIMEOUT_SECONDS = 5.0
SUPERVISOR_CLOSE_APPS_ON_EXIT = True
SUPERVISOR_UNLOAD_MODEL_ON_EXIT = True

# Only allowlisted programs can be started by voice.  Commands are argv lists,
# never shell strings.  Adjust these paths/names after the Linux install. Add a
# separate entry for another allowlisted application if desired. GameCube title
# launches are handled by the dedicated Dolphin game subsystem.
SUPERVISOR_APPS = {
    "games": {
        "command": [str(APP_ROOT / "game_launcher.sh"), "frontend"],
        "aliases": ("games", "game library", "gamecube", "game cube", "dolphin", "emulator", "game emulator"),
    },
    "media": {
        "command": ["vlc"],
        "aliases": ("media", "media player", "vlc", "movie player", "video player"),
    },
}

# Opt-in weather (Open-Meteo, no key). Inactive until the owner says "set my
# weather location to <town>" and confirms; the place is stored only here and
# the service is contacted only when weather is asked about.
ENABLE_WEATHER = True
WEATHER_SETTINGS_PATH = DATA_ROOT / "weather.json"
WEATHER_FAHRENHEIT = True

# "Open <app>" is handled without any model: the app comes from the host's
# installed-app catalog, launches through the Supervisor, and success is only
# reported after its window appears (X11 today; other hosts say they can't check).
ENABLE_APP_LAUNCHER = True
# One-step GUI commands ("press 7 in Calculator") through the accessibility tree.
# One spoken OK covers ordinary changes in the same app for this many seconds
# (approved "one OK per task"); critical actions always ask again.
ENABLE_GUI_COMMANDS = True
GUI_TASK_OK_SECONDS = 180.0
# Multi-step GUI tasks through the Main Brain's gui tool (milestone 3): one
# spoken OK for the plan, at most this many actions per request, every one verified.
ENABLE_GUI_TASKS = True
# Time to answer a GUI approval question. Live 2026-10-18 two clipped
# "Hey BMO, yes" answers used up the 20 s window and the task was dropped.
GUI_APPROVAL_TIMEOUT_SECONDS = 35.0
# A "Hey BMO, yes" this soon after an unanswered GUI question still counts.
GUI_LATE_YES_SECONDS = 60.0
# Readability by voice (settings plan S3): text size, pointer size, panel
# auto-hide. Each change is a preview kept with "keep it"; silence reverts it.
ENABLE_DESKTOP_SETTINGS = True
DESKTOP_SETTING_PREVIEW_SECONDS = 30.0
DESKTOP_SETTINGS_JOURNAL = DATA_ROOT / "settings" / "pending-revert.json"
# Coding writes files as fenced code blocks and BMO saves them (roadmap: whole
# programs inside one tool call broke Ollama's parser live, 2026-10-18).
# Without an open project they go here.
CODE_SAVE_BLOCKS = True
CODE_OUTPUT_DIR = Path(os.environ.get("BEMO_CODE_DIR", str(Path.home() / "BMO-code")))
# Sound by voice (settings plan S1): volume, mute, output. Big jumps and very
# loud levels ask first (systems/sound.py ASK_JUMP / ASK_ABOVE, approved).
ENABLE_SOUND_COMMANDS = True
# Movies and shows by voice (systems/media.py, 2026-10-20): the library and the player's settings.
ENABLE_MEDIA_COMMANDS = True
MEDIA_DIRS = [Path(p).expanduser() for p in os.environ.get("BEMO_MEDIA_DIRS", str(Path.home() / "Videos")).split(":")]
MEDIA_STATE_PATH = DATA_ROOT / "media.json"
MEDIA_WATCH_LATER_DIR = DATA_ROOT / "watch-later"  # where the player saves its place (systems/media.py)
LIBRARY_STILLS_DIR = DATA_ROOT / "library-stills"  # stills for the movie library (systems/library.py)
# Vision fallback (milestone 4): MAI-UI-2B 8-bit in llama.cpp, started on first
# use, stopped when idle, only with enough memory (approved plan).
ENABLE_GUI_VISION = True
GUI_VISION_DIR = Path(os.environ.get("BEMO_GUI_VISION_DIR", str(DATA_ROOT / "models" / "gui")))
GUI_VISION_MODEL = GUI_VISION_DIR / "mai-ui-2b-Q8_0.gguf"
GUI_VISION_MMPROJ = GUI_VISION_DIR / "mmproj-mai-ui-2b-f16.gguf"
GUI_VISION_SERVER = Path(os.environ.get("BEMO_GUI_VISION_SERVER", str(GUI_VISION_DIR / "llama.cpp" / "llama-server")))
GUI_VISION_CTX = 4096  # one screenshot (co-residency test 2026-10-18)
GUI_VISION_IDLE_SECONDS = 600.0
GUI_VISION_MIN_TOTAL_GIB = float(os.environ.get("BEMO_GUI_VISION_MIN_TOTAL_GIB", "28"))
# Plan said 6 GB free; measured 2026-10-18 with Main loaded: the helper peaks at
# 3.45 GiB RSS but free memory fell only ~0.5 GiB (its weights are a file
# mapping the kernel can drop), and 6 GB blocked it on the 28 GiB test PC
# (5.3-5.8 GiB free in normal use). 4.5 then refused it live on 2026-10-20 with 4.1 GiB free (Main + Chrome +
# Firefox open), although the helper takes only ~0.5 GiB of free memory: 3.5 leaves ~3 GiB to spare.
GUI_VISION_MIN_AVAILABLE_GIB = float(os.environ.get("BEMO_GUI_VISION_MIN_AVAILABLE_GIB", "3.5"))
GUI_TASK_MAX_STEPS = 8
APP_LAUNCH_VERIFY_SECONDS = 20.0
APP_CLOSE_VERIFY_SECONDS = 6.0  # graceful close; a save prompt keeps the window open

# Specialist tasks may propose a return to Main after a completed turn, but the
# central switch gate requires explicit user approval for that return too. No
# model is ever unloaded or loaded merely because a model requested it.
MAIN_MODEL_PROFILE = "normal"
AUTO_RETURN_SPECIALISTS_TO_MAIN = True

# -----------------------------------------------------------------------------
# Display / animation
# -----------------------------------------------------------------------------
# These are the reference layout dimensions, not a required monitor mode. On
# startup Bemo detects the live X11 mode (or reapplies the last approved mode)
# and scales/repositions the face for the resulting safe viewport.
SCREEN_WIDTH = 1280
SCREEN_HEIGHT = 800
DISPLAY_SETTINGS_PATH = DATA_ROOT / "display" / "settings.json"
DISPLAY_PREVIEW_TIMEOUT_SECONDS = 45.0
FPS = 30
SHOW_DEBUG_OVERLAY = False

# Bemo face background. Keep this in config so the CRT color can be changed
# without editing renderer logic. #a3e6ca = RGB(163, 230, 202).
FACE_BACKGROUND_COLOR_HEX = "#a3e6ca"
FACE_BACKGROUND_COLOR = (163, 230, 202)

# CRT protection is built into Bemo rather than delegated to the desktop. The
# normal BMO face remains visible while Bemo is active; after a few minutes with
# no meaningful user interaction, the renderer switches to a dark, constantly
# moving saver and rotates styles so no bright shape sits in one place for long.
# Any accepted voice command, keyboard input, or mouse movement wakes it.
ENABLE_CRT_SCREENSAVER = True
CRT_SCREENSAVER_IDLE_SECONDS = 240.0
CRT_SCREENSAVER_MODE_SECONDS = 45.0
CRT_SCREENSAVER_MODES = ("star_drift", "bmo_bounce", "pixel_rain")
CRT_SCREENSAVER_STAR_COUNT = 72
CRT_SCREENSAVER_RAIN_COLUMNS = 28

# While the normal face is sitting still, briefly replace it with full-screen
# moving TV static. This reduces uninterrupted exposure to the same bright face
# geometry without interfering with model loading, active speech, games, or the
# full idle screensaver. The refresh waits until Bemo is idle/ready if it becomes
# due during an active response.
ENABLE_CRT_STATIC_REFRESH = True
CRT_STATIC_REFRESH_INTERVAL_SECONDS = 90.0
CRT_STATIC_REFRESH_DURATION_SECONDS = 7.0
CRT_STATIC_REFRESH_FRAME_WIDTH = 160
CRT_STATIC_REFRESH_FRAME_HEIGHT = 100
CRT_STATIC_REFRESH_FRAME_COUNT = 6

# Preferred CRT backend: legacy Windows .scr files run through Wine while Bemo
# keeps listening underneath. A normal online install automatically fetches the
# configured legacy screensaver pack into the folder below; user-added .scr files
# and removable-drive savers are discovered too. If Wine or usable .scr files are
# absent, the built-in pygame saver is used.
ENABLE_WINDOWS_SCR_SCREENSAVERS = True
WINDOWS_SCREENSAVER_DIR = DATA_ROOT / "screensavers" / "windows"
WINDOWS_SCREENSAVER_WINEPREFIX = DATA_ROOT / "wine-screensavers"
WINDOWS_SCREENSAVER_WINE_BINARY = os.environ.get("BEMO_WINE_BINARY", "wine")
WINDOWS_SCREENSAVER_SEARCH_REMOVABLE = True
WINDOWS_SCREENSAVER_ROTATE_SECONDS = 180.0
# A saver that dies almost immediately is treated as incompatible/crashed, not
# as user activity. It is skipped for the rest of the Bemo process and another
# saver (or the built-in CRT-safe fallback) is tried without restarting the
# three-minute idle countdown.
WINDOWS_SCREENSAVER_STARTUP_GRACE_SECONDS = 2.0
WINDOWS_SCREENSAVER_MAX_DISCOVERED = 200

# Model swaps happen on the Chat worker while pygame keeps rendering, so Bemo can
# show an animated transition instead of appearing frozen. The included Data
# Catch mini-game uses Left/Right or A/D; it also animates unattended. The
# indeterminate loading bar intentionally does not pretend to know a percentage.
ENABLE_MODEL_SWITCH_LOADING_SCREEN = True
MODEL_SWITCH_MIN_DISPLAY_SECONDS = 1.25
# Classic Windows 95-style indeterminate progress bar. The blocks animate
# continuously because Supervisor cannot know a truthful load percentage.
MODEL_SWITCH_RETRO_PROGRESS = True
MODEL_SWITCH_RETRO_PROGRESS_BLOCKS = 18
MODEL_SWITCH_RETRO_PROGRESS_LIT_BLOCKS = 5
MODEL_SWITCH_RETRO_PROGRESS_STEP_SECONDS = 0.11
ENABLE_MODEL_SWITCH_MINIGAME = True  # the runner game (systems/runner_game.py)
# BMO's own screens' lettering (systems/render.py ui_font): Silkscreen, a pixel font that ships in assets/fonts
# (SIL Open Font License, 2026-10-27). It runs a little large, hence the scale.
UI_FONT = "assets/fonts/Silkscreen-Regular.ttf"
UI_FONT_SCALE = 0.85
RUNNER_SCORES_PATH = DATA_ROOT / "runner-game.json"  # its best score and level

# Lip-sync sampling. The original sprite-driven animation structure is preserved.
VALUES_PER_SECOND = 29
TALK_DELAY = 0.07
LIP_SYNC_SILENCE_RMS = 0.012

# -----------------------------------------------------------------------------
# Microphone / local speech recognition
# -----------------------------------------------------------------------------
# Keep the original microphone index by default. Set to None to use the OS
# default microphone. Run print_microphones.py to list available devices.
DEVICE_INDEX = None

# Finish capture sooner when the user stops talking. Keep this configurable
# so longer natural pauses can be accommodated without editing Bemo source.
PAUSE_THRESHOLD = float(os.environ.get("BEMO_PAUSE_THRESHOLD", "1.10"))
ENERGY_THRESHOLD = 600
DYNAMIC_ENERGY_THRESHOLD = False
AMBIENT_CALIBRATION_SECONDS = 1.0
PHRASE_THRESHOLD = 0.20
NON_SPEAKING_DURATION = 0.50
LISTEN_TIMEOUT = 1.0
PHRASE_TIME_LIMIT = 30.0
POST_SPEECH_COOLDOWN_SECONDS = 0.35

STT_MODEL = "base.en"
STT_DEVICE = "cpu"
STT_COMPUTE_TYPE = "int8"
STT_CPU_THREADS = _auto_or(
    STT_THREAD_OVERRIDE,
    HARDWARE.stt_threads if AUTO_TUNE_CPU else LEGACY_CPU_THREADS,
)
STT_NUM_WORKERS = 1
STT_BEAM_SIZE = 2
STT_LANGUAGE = "en"
STT_SAMPLE_RATE = 16000
STT_MIN_SILENCE_MS = 450
# SpeechRecognition has already detected/captured a phrase before faster-whisper runs.
# A second Silero VAD pass can discard short wake phrases on USB microphones.
STT_VAD_FILTER = False
# How a spoken phrase starts and ends. "vad": Silero voice detection (offline,
# bundled with faster-whisper) - ignores fan/room noise. "energy": the old
# loudness threshold, which never saw silence in a room measuring ~1,500
# against its 420 cap (2026-10-18). PAUSE_THRESHOLD is the silence that ends
# a phrase in both modes.
STT_ENDPOINTING = os.environ.get("BEMO_STT_ENDPOINTING", "vad").strip().lower()
# Measured 2026-10-18 on the test host: room/fan noise peaked at 0.05-0.13;
# short replies like a single "no" scored 0.30-0.43 and were missed at 0.5.
STT_VAD_START_THRESHOLD = 0.30
STT_VAD_END_THRESHOLD = 0.20
STT_VAD_STRONG_START_THRESHOLD = 0.40  # one step this sure starts a phrase (short words)
# Reject only unmistakable Whisper repeated-word loops; never gate ordinary speech.
STT_FILTER_REPETITION_LOOPS = True
# Plausibility limits checked before any transcript reaches the wake gate.
# Fast human speech is ~4 words/s; the v1.28.7 hallucination was 72 words/s.
STT_MAX_WORDS_PER_SECOND = float(os.environ.get("BEMO_STT_MAX_WORDS_PER_SECOND", "6.0"))
STT_MAX_COMPRESSION_RATIO = 2.4  # Whisper's own repetition threshold
STT_MAX_TOKENS_PER_AUDIO_SECOND = 12  # ~2x fast speech; ends decoder loops early
STT_MAX_NEW_TOKENS_CAP = 300  # must stay below Whisper's 448 limit minus prompt
# Long captures transcribed without a wake word get their first seconds
# re-transcribed alone; Whisper drops a leading "Hey BMO" from long audio.
STT_WAKE_HEAD_SECONDS = float(os.environ.get("BEMO_STT_WAKE_HEAD_SECONDS", "2.5"))
# Off: rejected captures are room sound, and recordings aren't kept (audit 2026-10-24). For a debugging session,
# BEMO_STT_SAVE_REJECTED_AUDIO=20 keeps the last 20 in runtime/stt-rejects.
STT_SAVE_REJECTED_AUDIO = int(os.environ.get("BEMO_STT_SAVE_REJECTED_AUDIO", "0"))
STT_REJECTED_AUDIO_DIR = DATA_ROOT / "runtime" / "stt-rejects"
# Timers and reminders (systems/reminders.py), kept by the Supervisor; the owner's data, so in backups.
REMINDERS_PATH = DATA_ROOT / "reminders.json"
# Give Whisper a tiny spelling hint for Bemo's invented name. This does not
# bypass the wake gate; it only nudges transcription toward the intended name.
# Do not seed Whisper with a full sentence about Bemo: on the target mic it
# began hallucinating that exact sentence during silence. Hotwords provide the
# name bias without giving the decoder a phrase to parrot.
STT_INITIAL_PROMPT = os.environ.get("BEMO_STT_INITIAL_PROMPT", "").strip()
# faster-whisper 1.2.1 inserts hotwords as previous-text context, so a list
# of repeated names primed repeated-name output: on 2026-10-18 "Hey Bemo,
# yes" came back as "Hey Bemo" x6 (three loops that day). One name only.
# Previous value, for an A/B test: "Hey Bemo Hey Beemo Hey BMO Bemo Beemo BMO".
STT_HOTWORDS = os.environ.get("BEMO_STT_HOTWORDS", "Bemo")
# Installed file managers with names Whisper can't spell are added to the
# hotwords ("auto"; "" turns it off). Live 2026-10-18 "Thunar" came back as
# "soon R", "sooner", "the center", "thunder". A/B with BMO's Piper voice:
# "Bemo Thunar" fixed it without changing other speech; five extra app
# names did not help more and made noise hallucinate, so this stays small.
STT_APP_HOTWORDS = os.environ.get("BEMO_STT_APP_HOTWORDS", "auto")
# "Did you mean Thunar?" when an app name was misheard (similar sound only;
# BMO opens it only after a spoken yes).
APP_SOUNDS_LIKE_MIN = 0.75
# Ambient calibration can otherwise raise the trigger above the user's softer
# initial 'Hey Bemo'. Background captures are still discarded by the wake gate.
MIC_ENERGY_THRESHOLD_MAX = float(os.environ.get("BEMO_MIC_ENERGY_THRESHOLD_MAX", "420"))

# -----------------------------------------------------------------------------
# Spoken conversation gate
# -----------------------------------------------------------------------------
# "Hey Bemo" is the canonical wake phrase. A command may include the wake
# prefix directly, or a bare wake can arm one short one-shot follow-up command.
# This remains explicit-trigger behavior rather than an open conversation mic.
VOICE_WAKE_PHRASE = CANONICAL_WAKE_PHRASE.lower()
VOICE_WAKE_ALIASES = (
    "hey bemo", "hey beemo", "hey bmo", "hey be mo", "hey be more",
    "hey people", "he'd be more",
    # "He'd be most switched to Maine.", "It'd be most switch to main brain." (live 2026-10-19)
    "he'd be most", "it'd be most",
    # The robot's name itself is now an explicit wake trigger too. Keep this
    # list narrow: do not add ordinary phrases such as bare "be more".
    "bemo", "beemo", "bmo", "be mo",
)
VOICE_REQUIRE_WAKE_PREFIX_EVERY_TURN = True
# decided 2026-10-19: after BMO answers, one follow-up within 8 s needs no "Hey BMO" (each answer
# opens one). Only short ones count, so TV talk that happens to fall in the window isn't taken.
VOICE_ANSWER_FOLLOWUP_SECONDS = float(os.environ.get("BEMO_ANSWER_FOLLOWUP_SECONDS", "8"))
VOICE_FOLLOWUP_MAX_WORDS = 20
VOICE_SESSION_TIMEOUT_SECONDS = 90.0
# A bare "Hey Bemo" explicitly arms one immediate unprefixed follow-up. This is
# only a fallback for wake-name recognition; it does not open an always-listening session.
VOICE_WAKE_FOLLOWUP_SECONDS = 12.0
VOICE_WAKE_ACKNOWLEDGEMENT = "Yeah?"
VOICE_STARTUP_LOADING_ANNOUNCEMENT = "Hi friend! Give me a couple minutes to full boot up and I'll let you know when Im ready!"
VOICE_STARTUP_READY_ANNOUNCEMENT = "Hi friend I am all ready to talk to! All done loading"
VOICE_SLEEP_ACKNOWLEDGEMENT = "Going to sleep."
VOICE_SLEEP_PHRASES = (CANONICAL_SLEEP_PHRASE, "go back to sleep")

# Standby wake listener.  When the full Bemo process is stopped, the permanent
# Supervisor loads one small speech model and waits for "Hey Bemo".  Hearing it
# releases the standby microphone/model first, then starts the full Bemo stack.
# base.en + the "Bemo" hint, measured on the owner's own recording (2026-10-19:
# 10 "Hey BMO"s + 37 room sounds, tools/compare_wake_models.py): woke 9/10 with 0
# false starts and 0 room sounds heard as "Bemo"; tiny.en woke 5-8/10 and, with the
# hint, heard room sound as "Bemo" 4 times. ~0.8 s per heard phrase on 2 threads.
STANDBY_WAKE_ENABLED = os.environ.get("BEMO_STANDBY_WAKE_ENABLED", "1").strip().lower() not in {"0", "false", "no", "off"}
STANDBY_WAKE_STT_MODEL = os.environ.get("BEMO_STANDBY_WAKE_STT_MODEL", "base.en")
STANDBY_WAKE_HOTWORDS = os.environ.get("BEMO_STANDBY_WAKE_HOTWORDS", "Bemo")
# BMO's own "Hey BMO" detector (openWakeWord), trained on this PC with tools/wakeword/make_hey_bmo.py and
# used beside Whisper: BMO wakes if either hears it (chosen 2026-10-19). Files: ~2.6 MB.
WAKEWORD_DIR = DATA_ROOT / "models" / "wakeword"
WAKEWORD_MODEL_PATH = Path(os.environ.get("BEMO_WAKEWORD_MODEL", str(WAKEWORD_DIR / "hey_bmo.onnx")))
WAKEWORD_MELSPEC_PATH = WAKEWORD_DIR / "melspectrogram.onnx"
WAKEWORD_EMBEDDING_PATH = WAKEWORD_DIR / "embedding_model.onnx"
# 0.7: cross-validated on the owner's recordings (9/9 usable held-out "Hey BMO"s, <=0.37 false starts/hour on
# held-back speech/music, 0/37 room sounds; 2026-10-19).
WAKEWORD_THRESHOLD = float(os.environ.get("BEMO_WAKEWORD_THRESHOLD", "0.7"))
ENABLE_WAKEWORD_MODEL = os.environ.get("BEMO_WAKEWORD", "1").strip().lower() not in {"0", "false", "no", "off"}
STANDBY_WAKE_STT_COMPUTE_TYPE = "int8"
STANDBY_WAKE_STT_CPU_THREADS = max(1, min(2, int(STT_CPU_THREADS)))
STANDBY_WAKE_PAUSE_THRESHOLD = 0.65
STANDBY_WAKE_ENERGY_THRESHOLD = ENERGY_THRESHOLD
STANDBY_WAKE_DYNAMIC_ENERGY_THRESHOLD = True
STANDBY_WAKE_AMBIENT_CALIBRATION_SECONDS = 0.5
STANDBY_WAKE_LISTEN_TIMEOUT = 1.0
STANDBY_WAKE_PHRASE_TIME_LIMIT = 3.5
STANDBY_WAKE_MIN_SILENCE_MS = 300
STANDBY_WAKE_VAD_FILTER = False
STANDBY_WAKE_RETRY_SECONDS = 3.0

# Compatibility names retained for old config consumers. The old Bemo/.../over
# gate is intentionally disabled in v1.13 and later.
REQUIRE_WAKE_WORD = True
REQUIRE_END_WORD = False
WAKE_WORDS = (VOICE_WAKE_PHRASE,)
END_WORDS = ()


# -----------------------------------------------------------------------------
# Model policy files
# -----------------------------------------------------------------------------
POLICY_DIR = Path(__file__).resolve().parent / "policies"

def _load_policy(filename: str) -> str:
    path = POLICY_DIR / filename
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""

# -----------------------------------------------------------------------------
# LLM / Ollama model profiles
# -----------------------------------------------------------------------------
INITIAL_MESSAGE = (
    "You are WIB, Bemo's local all-purpose computer companion made by Tech Wizard. "
    "Be concise when a short answer is enough, but use as much detail as the task actually requires. "
    "Do not invent completed actions, files, measurements, or observations."
)

def _host_facts() -> str:
    """Detected facts about this PC, so models stop guessing paths like /home/user."""
    import platform
    name = platform.system() or "an unknown OS"
    try:
        for line in Path("/etc/os-release").read_text().splitlines():
            if line.startswith("PRETTY_NAME="):
                name = line.split("=", 1)[1].strip().strip('"') or name
                break
    except OSError:
        pass
    desktop = os.environ.get("XDG_CURRENT_DESKTOP", "").strip()
    from datetime import datetime
    zone = datetime.now().astimezone().tzname() or "unknown"
    return (f"This PC runs {name}" + (f" ({desktop} desktop)" if desktop else "")
            + f"; the user's home folder is {Path.home()}; its clock uses {zone} time.")


HOST_FACTS = _host_facts()


def _capability_facts() -> str:
    """What BMO can really do on this PC, from detection (portability plan P3).

    Replaces nothing that was there; it fills the gap that let Main improvise
    ("I can fix broken apps…", live 2026-10-19). Built once per start, so the
    prompt prefix stays cache-friendly.
    """
    try:
        from systems.host import capabilities, detect_host
        caps = capabilities(detect_host(), vision_files=GUI_VISION_MODEL.exists())
    except Exception:
        return ""
    ok = {c.key for c in caps if c.ok}
    can = ["chat", "project notes", "coding/engineering/vision specialists"]
    if {"apps", "app_controls"} & ok:
        can.append("open and use apps")
    if "click_by_sight" in ok:
        can.append("click by looking")
    change = [w for k, w in (("screen", "screen"), ("sound", "sound"), ("readability", "text size")) if k in ok]
    if change:
        can.append("change " + (", ".join(change[:-1]) + " and " + change[-1] if len(change) > 1 else change[0]))
    can += (["weather"] if ENABLE_WEATHER else []) + (["games"] if ENABLE_GAME_SUBSYSTEM else [])
    return ("Real abilities here (mostly BMO's voice commands): " + ", ".join(can) + ". Claim no others.")


CAPABILITY_FACTS = _capability_facts()
HARDWARE_PROFILE_PATH = DATA_ROOT / "hardware-profile.json"

# Static (prefix-cache friendly) statement of what Bemo can actually sense.
# Python guards in systems/grounding.py enforce it; this only sets expectations.
GROUNDING_RULE = (
    # Weather is real when enabled (BMO's own weather command answers it); saying
    # "no weather" here made Main deny it while the capability line listed it (live 2026-10-19).
    ("Your only senses are the wake-word mic and tool results here; no camera, GPS or sensors; "
     "BMO's weather command answers weather. " if ENABLE_WEATHER else
     "Your only senses are the wake-word mic and tool results here; no camera, GPS, weather or sensors otherwise. ")
    +
    "If you lack live data, say so briefly; never guess or pretend to check. "
    + HOST_FACTS
    + (" " + CAPABILITY_FACTS if CAPABILITY_FACTS else "")
)

VOICE_COMMAND_CATALOG = model_command_catalog()  # UI/docs only; never injected into an LLM prompt

# Add as many profiles as you want. `num_ctx=None` means use the RAM-based
# automatic context size. install.sh installs Ollama, pulls the locked exact
# model roster, and creates the stable local aliases below automatically.
MODEL_PROFILES = {
    "normal": {
        # Qualified benchmark target: Qwen3-30B-A3B-Instruct-2507.
        # BEMO_MAIN_MODEL should point at the local Ollama tag created/imported
        # for the benchmarked GGUF on the final machine.
        "model": os.environ.get("BEMO_MAIN_MODEL", "qwen3-main:30b"),
        "benchmark_model": "Qwen3-30B-A3B-Instruct-2507",
        "qualification": "locked",
        "route_label": "HANDLE_YOURSELF",
        "route_signals": True,
        # Keep this prefix immutable while Main is active. Ollama can reuse a
        # matching prompt/KV prefix instead of re-evaluating the policy text.
        "system_prompt": (
            _load_policy("main_personality.md").replace("practical copilot", "robot friend") + "\n\n"
            + _load_policy("main_brain.md")
            + "\n\nYou're Bemo, the user's robot friend inspired by BMO from Adventure Time, running on this real computer, not a generic AI. "
            + GROUNDING_RULE
            + " Engineering, Coding, Vision, Emergency need Supervisor user approval. Handle normal questions in 1-2 sentences, 15-35 words; 2-4 short sentences if needed; expand only when needed or asked. No stage acts or name fights. End cleanly."
        ),
        # "maine"/"mane": Whisper's "main" (live 2026-10-19 "Bemo switched to Maine.").
        "aliases": ("normal", "default", "chat", "regular", "main", "main brain", "maine", "mane"),
        "num_ctx": None,
        # Main uses a Python-selected per-turn cap below this ceiling. Specialists
        # retain their large independent budgets. Keep the high ceiling so an
        # explicitly detailed Main request can still finish naturally.
        "num_predict": 1024,
        "temperature": 0.35,
        "keep_alive": -1,
        "recommended_ram_gb": 24,
        "return_to_main_after_turn": False,
        "diagnostic_tools": False,
        "reference_tools": False,
    },
    "emergency": {
        # Existing lightweight recovery brain; not part of normal routing.
        "model": os.environ.get("BEMO_EMERGENCY_MODEL", "qwen3-emergency:4b"),
        "benchmark_model": "Qwen3-4B-Instruct-2507",
        "qualification": "fallback",
        "route_label": "HANDLE_YOURSELF",
        "route_signals": True,
        "system_prompt": (
            "You are WIB, Bemo's lightweight emergency/recovery brain made by Tech Wizard.\n\n"
            + _load_policy("emergency.md") + "\n\n" + GROUNDING_RULE
        ),
        "aliases": ("emergency", "emergency brain", "light brain", "fallback", "recovery brain"),
        "num_ctx": None,
        "num_predict": 1024,
        "temperature": 0.30,
        "keep_alive": -1,
        "recommended_ram_gb": 8,
        "return_to_main_after_turn": False,
        "diagnostic_tools": True,
        "reference_tools": True,
    },
    "coding": {
        # Qualified benchmark target: Qwen3-Coder-30B-A3B.
        "model": os.environ.get("BEMO_CODER_MODEL", "qwen3-coder:30b"),
        "benchmark_model": "Qwen3-Coder-30B-A3B",
        "qualification": "locked",
        "route_label": "HEAVY_CODING",
        "route_signals": True,
        "system_prompt": (
            "You are WIB, Bemo's heavy-coding specialist made by Tech Wizard.\n\n"
            + _load_policy("heavy_coding.md") + "\n\n" + GROUNDING_RULE
        ),
        "project_tools": True,
        "aliases": (
            "coding",
            "coder",
            # Whisper's versions of "coding" (live 2026-10-18/19); every switch still needs a spoken yes.
            "codeine", "code aim", "cody", "code in", "codey", "coating", "code",
            "heavy coding",
            "heavy coder",
            "coding model",
            "qwen coder",
            "qwen3 coder",
            "qwen 3 coder",
            "qwen three coder",
        ),
        "num_ctx": None,
        "num_predict": 4096,
        "temperature": 0.20,
        "keep_alive": "10m",
        "recommended_ram_gb": 28,
        "return_to_main_after_turn": True,
    },
    "engineering": {
        # Qualified benchmark target: Qwen3-30B-A3B-Thinking-2507.
        "model": os.environ.get("BEMO_ENGINEERING_MODEL", "qwen3-engineering:30b"),
        "benchmark_model": "Qwen3-30B-A3B-Thinking-2507",
        "qualification": "locked",
        "route_label": "ENGINEERING",
        "route_signals": True,
        "system_prompt": (
            "You are WIB, Bemo's engineering and deep-reasoning specialist made by Tech Wizard.\n\n"
            + _load_policy("engineering.md") + "\n\n"
            + "Check dimensions and arithmetic; state units, assumptions and failure modes. Use available engineering tools for verification.\n\n"
            + GROUNDING_RULE
        ),
        "engineering_tools": True,
        "diagnostic_tools": True,
        "reference_tools": True,
        "instrument_tools": True,
        "data_logger_tools": True,
        "aliases": (
            "engineering",
            "engineer",
            "engineering model",
            "deep reasoning",
            "reasoning model",
        ),
        "num_ctx": None,
        "num_predict": 4096,
        "temperature": 0.20,
        "keep_alive": "10m",
        "recommended_ram_gb": 28,
        "return_to_main_after_turn": True,
    },
    "deep-vision": {
        # Qualified benchmark target: InternVL3.5-30B-A3B. Chosen after direct
        # comparison with Qwen3-VL-Thinking and Mistral Small 3.1 on technical images.
        "model": os.environ.get("BEMO_DEEP_VISION_MODEL", "internvl3.5-deep:30b"),
        "benchmark_model": "InternVL3.5-30B-A3B",
        "qualification": "locked",
        "route_label": "DEEP_TECHNICAL_VISION",
        # Ollama refuses this model outright when any tool is offered (HTTP 400 "does not support
        # tools", checked 2026-10-19, checklist B3); it answers images fine without them.
        "tools_supported": False,
        "route_signals": True,
        "system_prompt": (
            "You are WIB, Bemo's visual-understanding specialist made by Tech Wizard: photos, screenshots and "
            "technical images alike.\n\n"
            + _load_policy("deep_technical_vision.md") + "\n\n" + GROUNDING_RULE
        ),
        "aliases": (
            "deep vision", "technical vision", "deep technical vision",
            "cad vision", "engineering vision", "internvl",
            # The only vision brain since 2026-10-24 (Fast Vision dropped: Qwen3-VL, 19 GB).
            "vision", "vision model", "general vision", "fast vision",
        ),
        "num_ctx": None,
        "num_predict": 4096,
        "temperature": 0.10,
        "keep_alive": "10m",
        "recommended_ram_gb": 28,
        "return_to_main_after_turn": True,
        "vision": True,
        "vision_assist_tools": True,
    },
}

# Role labels are stable policy/API names. Model/profile names can change later
# without rewriting the Main Brain routing policy.
MODEL_ROUTE_TO_PROFILE = {
    profile.get("route_label"): name
    for name, profile in MODEL_PROFILES.items()
    if profile.get("route_label") not in (None, "HANDLE_YOURSELF")
}

# SYSTEM_CONTROL is now a shared Supervisor-mediated capability available to
# every model, not a separate LLM profile. Keep it unmapped for compatibility
# with older route parsing. Deep Technical Vision is now a qualified InternVL profile.
MODEL_ROUTE_TO_PROFILE.pop("SYSTEM_CONTROL", None)
# Fast Vision was dropped (2026-10-24); a brain that still asks for it gets the one vision brain.
MODEL_ROUTE_TO_PROFILE["FAST_VISION"] = "deep-vision"

DEFAULT_MODEL_PROFILE = "normal"

# Spoken local commands such as "Hey Bemo, switch to coding model" are
# intercepted locally and do not waste an LLM turn.
ENABLE_VOICE_MODEL_SWITCHING = True
REQUIRE_INSTALLED_MODEL_FOR_SWITCH = True

OLLAMA_KEEP_ALIVE = "10m"
OLLAMA_NUM_CTX = _auto_or(
    OLLAMA_CONTEXT_OVERRIDE,
    HARDWARE.default_context if AUTO_TUNE_CONTEXT else LEGACY_CONTEXT,
)
OLLAMA_NUM_THREAD = _auto_or(
    OLLAMA_THREAD_OVERRIDE,
    HARDWARE.ollama_threads if AUTO_TUNE_CPU else LEGACY_CPU_THREADS,
)


def refresh_ollama_threads():
    """The speed test can finish after BMO and the Supervisor started (the install screen runs it): use
    its thread count from now on, without a restart."""
    global HARDWARE, OLLAMA_NUM_THREAD
    if OLLAMA_THREAD_OVERRIDE is None and AUTO_TUNE_CPU:
        HARDWARE = detect_hardware()
        OLLAMA_NUM_THREAD = HARDWARE.ollama_threads
    return OLLAMA_NUM_THREAD

# Compatibility aliases for older code/config references.
CHAT_MODEL = MODEL_PROFILES[DEFAULT_MODEL_PROFILE]["model"]
OLLAMA_NUM_PREDICT = MODEL_PROFILES[DEFAULT_MODEL_PROFILE]["num_predict"]
OLLAMA_TEMPERATURE = MODEL_PROFILES[DEFAULT_MODEL_PROFILE]["temperature"]

STREAM_TTS = True
# Start the first spoken chunk quickly, then use slightly larger chunks while
# Ollama continues generating underneath.
STREAM_FIRST_CHUNK_MIN_WORDS = 6
STREAM_FIRST_CHUNK_MAX_WORDS = 10
STREAM_CHUNK_MIN_WORDS = 20
STREAM_CHUNK_MAX_WORDS = 30
# Piper's synthesis chunks are utterance/sentence-sized, so prefer complete
# sentences and larger clauses instead of very short pieces.
# We still impose a hard word ceiling when an LLM produces no punctuation.
# Piper is much faster than real-time on the 5700U, so keep spoken units small
# enough that the LLM can continuously feed playback instead of waiting for a
# 30-45 word block. Tiny fragments are still coalesced by _pop_speech_chunks.
PIPER_FIRST_CHUNK_MIN_WORDS = int(os.environ.get("BEMO_PIPER_FIRST_MIN_WORDS", "4"))
PIPER_FIRST_CHUNK_MAX_WORDS = int(os.environ.get("BEMO_PIPER_FIRST_MAX_WORDS", "10"))
PIPER_CHUNK_MIN_WORDS = int(os.environ.get("BEMO_PIPER_MIN_WORDS", "6"))
PIPER_CHUNK_MAX_WORDS = int(os.environ.get("BEMO_PIPER_MAX_WORDS", "14"))

# -----------------------------------------------------------------------------
# Local text-to-speech: Piper with Bemo's trained epoch-23 voice. Chatterbox (voice
# cloning) was dropped on 2026-10-19 (installer decision C).
# -----------------------------------------------------------------------------
TTS_BACKEND = "piper"
PIPER_MODEL_PATH = Path(os.environ.get(
    "BEMO_PIPER_MODEL", str(DATA_ROOT / "voice" / "piper" / "en_US-bemo-best-v3.onnx")
)).expanduser()
TTS_MODEL = str(PIPER_MODEL_PATH)
TTS_PCM_QUEUE_CHUNKS = int(os.environ.get("BEMO_TTS_PCM_QUEUE_CHUNKS", "8"))
TTS_PCM_FRAMES_PER_BUFFER = int(os.environ.get("BEMO_TTS_PCM_FRAMES_PER_BUFFER", "1024"))

# Keep the product/wake-word spelling as "Bemo" everywhere, but give TTS a
# phonetic-friendly spelling so the voice says "Bee-mo" instead of "Beh-mo".
# This affects speech synthesis only; UI text, logs, prompts, wake recognition,
# filenames, and project identity remain Bemo.
TTS_SPOKEN_SELF_NAME = os.environ.get("BEMO_TTS_SPOKEN_SELF_NAME", "Beemo").strip() or "Beemo"
VOICE_TEST_PHRASE = "Hey. This is Bemo's permanent voice profile."

TORCH_NUM_THREADS = _auto_or(
    TORCH_THREAD_OVERRIDE,
    HARDWARE.torch_threads if AUTO_TUNE_CPU else LEGACY_CPU_THREADS,
)
TORCH_INTEROP_THREADS = _auto_or(
    TORCH_INTEROP_THREAD_OVERRIDE,
    HARDWARE.torch_interop_threads if AUTO_TUNE_CPU else 1,
)
RESPONSE_OUTPUT_WAV = str(Path(tempfile.gettempdir()) / "ai_response.wav")

# Bounded per-turn tool outputs; full files and tool data remain on disk.
MODEL_TOOL_RESULT_CHAR_BUDGET = 10000
EXECUTION_REQUEST_NUM_PREDICT = 192

# Main Brain response-efficiency budgets. These are runtime ceilings, not target
# lengths: the static Main policy asks the model to finish naturally well before
# the ceiling. Specialist profiles are explicitly excluded from these caps.
MAIN_RESPONSE_BRIEF_NUM_PREDICT = int(os.environ.get("BEMO_MAIN_BRIEF_TOKENS", "320"))
MAIN_RESPONSE_DEFAULT_NUM_PREDICT = int(os.environ.get("BEMO_MAIN_DEFAULT_TOKENS", "384"))
MAIN_RESPONSE_CAREFUL_NUM_PREDICT = int(os.environ.get("BEMO_MAIN_CAREFUL_TOKENS", "640"))
MAIN_RESPONSE_DETAILED_NUM_PREDICT = int(os.environ.get("BEMO_MAIN_DETAILED_TOKENS", "960"))
# Bound how much additional Main output can be generated to finish an incomplete
# sentence. This is *not* a new conversational turn or a tool/action request.
MAIN_FINISH_NUM_PREDICT = int(os.environ.get("BEMO_MAIN_FINISH_TOKENS", "96"))
MAIN_FINISH_MAX_ATTEMPTS = 2
# Automatic model-based summary maintenance is deferred to explicit maintenance workflows.
AUTO_LLM_MEMORY_MAINTENANCE = False
