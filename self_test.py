"""Non-audio sanity tests for Bemo hardware, memory, projects, Git, and export."""

from __future__ import annotations

import tempfile
import os
import json
import subprocess
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import config
from hardware import detect_hardware, profile_from_counts, autotune_record_matches, AUTOTUNE_ALGORITHM_VERSION
from hardware_autotune import candidate_thread_counts, choose_efficient_result
from systems.persistence import MemoryStore
from systems.projects import ProjectManager
from systems.tasks import TaskLedger
from systems.assets import FileLocator
from systems.backup import FullBackupManager
from systems.autostart import AutostartManager
from systems.display import DisplayManager
from systems.interface import InterfaceSettings
from systems.voice import voice_profile_signature, prepare_spoken_text
from systems.supervisor import SupervisorClient
from systems.computer import ComputerController
from systems.routing import parse_route_signal
from systems.windows_screensaver import WindowsScreensaverManager
from systems.engineering import EngineeringToolkit, engineering_tool_schemas
from systems.games import GameLibrary
from fetch_legacy_screensavers import install_from_directory
from model_installer import MODELS as INSTALL_MODEL_ROSTER
from supervisor import SupervisorService
from voice_commands import (
    CANONICAL_WAKE_PHRASE, COMMAND_CATALOG_VERSION, VOICE_COMMANDS,
    VoiceSessionGate, markdown_catalog,
)
from standby_wake import is_standby_wake_phrase
from mint_appliance import render_lightdm_autologin, update_lightdm_config_text


class FakeOllama:
    def __init__(self):
        self.loaded = []
        self.unloaded = []

    def chat(self, *, model, messages, stream=False, keep_alive=None, **kwargs):
        if keep_alive == 0:
            self.unloaded.append(model)
        return {"message": {"content": ""}}

    def generate(self, *, model, prompt, stream=False, keep_alive=None, **kwargs):
        self.loaded.append((model, keep_alive))
        return {"response": ""}


class SlowFakeOllama(FakeOllama):
    def __init__(self, delay=0.35):
        super().__init__()
        self.delay = float(delay)

    def generate(self, *, model, prompt, stream=False, keep_alive=None, **kwargs):
        time.sleep(self.delay)
        return super().generate(model=model, prompt=prompt, stream=stream, keep_alive=keep_alive, **kwargs)


def make_project_manager(root: Path, export_root: Path) -> ProjectManager:
    return ProjectManager(
        data_root=root,
        projects_root=root / "projects",
        index_extensions=config.PROJECT_INDEX_EXTENSIONS,
        exclude_dirs=config.PROJECT_EXCLUDE_DIRS,
        max_file_bytes=config.PROJECT_FILE_MAX_BYTES,
        context_char_budget=3000,
        retrieval_chunks=4,
        reindex_interval_seconds=0,
        auto_git=True,
        git_name="Bemo Test",
        git_email="bemo-test@local",
        export_root_override=export_root,
        export_include_git=True,
        export_create_zip=True,
        memory_history_retain=100,
    )


def main():
    # Voice wake/session behavior is deterministic and independent of an LLM.
    assert CANONICAL_WAKE_PHRASE == "Hey Bemo"
    assert config.VOICE_WAKE_PHRASE == "hey bemo"
    assert config.REQUIRE_END_WORD is False
    assert config.VOICE_REQUIRE_WAKE_PREFIX_EVERY_TURN is True
    gate = VoiceSessionGate(wake_aliases=("hey bemo", "hey beemo"), timeout_seconds=90.0)
    assert gate.apply("random room conversation", now=100.0) is None
    assert gate.apply("Bemo what time is it over", now=101.0) is None
    assert gate.has_wake_prefix("Hey Bemo")
    assert gate.has_wake_prefix("Hey Beemo, reload yourself")
    assert gate.has_wake_prefix("Hey BMO.")
    assert gate.has_wake_prefix("Hey beee mo")
    assert gate.has_wake_prefix("Hey, P-mo.")
    assert not gate.has_wake_prefix("Bemo reload yourself")
    assert not gate.has_wake_prefix("BMO reload yourself")
    assert not gate.has_wake_prefix("P-mo reload yourself")
    assert gate.apply("Hey Bemo", now=102.0) == ""
    # A bare wake arms exactly one unprefixed follow-up (deliberate since v1.28; see VoiceSessionGate).
    assert gate.apply("what model are you using", now=103.0) == "what model are you using"
    assert gate.apply("Okay. Okay. Okay.", now=103.1) is None
    assert gate.apply("You", now=103.2) is None
    assert gate.apply("go to sleep", now=103.3) is None
    assert gate.apply("Hey Bemo, what model are you using", now=103.4) == "what model are you using"
    assert gate.apply("Hey Bemo, list commands", now=104.0) == "list commands"
    assert gate.is_active(now=193.9)
    assert not gate.is_active(now=194.1)
    assert gate.apply("unprefixed after timeout", now=195.0) is None
    assert gate.apply("Hey Beemo, go to sleep", now=196.0) == "go to sleep"
    gate.sleep()
    assert not gate.is_active(now=196.1)
    assert Path("VOICE_COMMANDS.md").read_text(encoding="utf-8") == markdown_catalog()
    catalog_forms = [item.syntax for item in VOICE_COMMANDS]
    assert "Hey Bemo, reboot yourself" in catalog_forms
    assert "Hey Bemo, reload yourself" in catalog_forms
    assert "Hey Bemo, shutdown yourself" in catalog_forms
    assert "Hey Bemo, voice status" in catalog_forms
    assert "Hey Bemo, test voice" in catalog_forms
    assert "Hey Bemo, text mode" in catalog_forms
    assert "Hey Bemo, face mode" in catalog_forms
    assert "Hey Bemo, TTS off" in catalog_forms
    assert "Hey Bemo, TTS on" in catalog_forms
    assert "Hey Bemo, interface status" in catalog_forms
    assert not any("start Bemo" in form for form in catalog_forms)

    # Interface state is session-local: every launch starts Face + TTS on.
    # Silent mode is valid only while Text mode is active.
    with tempfile.TemporaryDirectory(prefix="bemo-interface-test-") as tmp:
        settings_path = Path(tmp) / "interface.json"
        interface = InterfaceSettings(settings_path, default_display_mode="face", default_tts_enabled=True)
        assert interface.status() == {"display_mode": "face", "tts_enabled": True}
        assert interface.set_tts_enabled(False) is True  # rejected in Face mode
        assert interface.status() == {"display_mode": "face", "tts_enabled": True}
        assert interface.set_display_mode("text") == "text"
        assert interface.set_tts_enabled(False) is False
        assert interface.status() == {"display_mode": "text", "tts_enabled": False}
        assert interface.set_display_mode("face") == "face"
        assert interface.status() == {"display_mode": "face", "tts_enabled": True}
        # A new process/session ignores the previous runtime state and starts clean.
        interface.set_display_mode("text")
        interface.set_tts_enabled(False)
        reloaded = InterfaceSettings(settings_path, default_display_mode="face", default_tts_enabled=True)
        assert reloaded.status() == {"display_mode": "face", "tts_enabled": True}
        final = json.loads(settings_path.read_text(encoding="utf-8"))
        assert final["display_mode"] == "face" and final["tts_enabled"] is True
        assert final["startup_policy"] == "face-tts-on"
    assert is_standby_wake_phrase("Hey Bemo")
    assert is_standby_wake_phrase("Hey, Beemo")
    assert is_standby_wake_phrase("Hey Bemo reload yourself")
    assert is_standby_wake_phrase("Hey BMO.")
    assert is_standby_wake_phrase("Hey, BMO.")
    assert is_standby_wake_phrase("Hey beee mo")
    assert is_standby_wake_phrase("Hey, P-mo.")
    # Standby needs "Hey …" (2026-10-19: a bare "Bemo" in room speech started BMO unasked);
    # while BMO runs, the bare name still wakes him (VoiceSessionGate, VOICE_WAKE_ALIASES).
    assert not is_standby_wake_phrase("Bemo")
    assert not is_standby_wake_phrase("BMO")
    assert is_standby_wake_phrase("A Bemo")  # tiny.en's "Hey" (live 2026-10-19)
    assert VoiceSessionGate(wake_aliases=config.VOICE_WAKE_ALIASES).has_wake_prefix("BMO")
    assert not is_standby_wake_phrase("P-mo")
    assert not is_standby_wake_phrase("random Hey Bemo in the middle")
    assert config.STANDBY_WAKE_STT_MODEL == "base.en"  # measured 2026-10-19 (tools/compare_wake_models.py)
    assert config.DEVICE_INDEX is None
    assert config.STT_VAD_FILTER is False
    assert config.STANDBY_WAKE_VAD_FILTER is False

    assert config.DEFAULT_MODEL_PROFILE in config.MODEL_PROFILES
    assert config.OLLAMA_NUM_THREAD >= 1
    assert config.STT_CPU_THREADS >= 1
    assert config.TORCH_NUM_THREADS >= 1
    assert config.TTS_BACKEND == "piper"
    assert config.DEFAULT_DISPLAY_MODE == "face"
    assert config.DEFAULT_TTS_ENABLED is True
    assert config.STREAM_TTS is True
    assert 1 <= config.STREAM_FIRST_CHUNK_MIN_WORDS <= config.STREAM_FIRST_CHUNK_MAX_WORDS
    assert config.STREAM_FIRST_CHUNK_MAX_WORDS < config.STREAM_CHUNK_MIN_WORDS
    assert config.STREAM_CHUNK_MIN_WORDS <= config.STREAM_CHUNK_MAX_WORDS
    assert config.STREAM_FIRST_CHUNK_MIN_WORDS == 6
    assert config.STREAM_FIRST_CHUNK_MAX_WORDS == 10
    assert config.STREAM_CHUNK_MIN_WORDS == 20
    assert config.STREAM_CHUNK_MAX_WORDS == 30
    assert config.PIPER_FIRST_CHUNK_MIN_WORDS <= config.PIPER_FIRST_CHUNK_MAX_WORDS
    assert config.PIPER_CHUNK_MIN_WORDS <= config.PIPER_CHUNK_MAX_WORDS
    assert config.SUPERVISOR_PARALLEL_PRELOAD_ON_STANDBY_WAKE is True
    assert config.SUPERVISOR_WARM_MAIN_MODEL_ON_PRELOAD is False
    assert config.RUNTIME_WARM_ACTIVE_PROMPT_ON_STANDBY_WAKE is False  # updated 2026-10-19: this value was already so in the v1.28.7 baseline (legacy; WARM_PROMPT_AFTER_SWITCH)
    assert abs(config.PAUSE_THRESHOLD - 1.10) < 1e-9  # updated 2026-10-19: this value was already so in the v1.28.7 baseline
    assert config.DYNAMIC_ENERGY_THRESHOLD is False
    assert config.VOICE_STARTUP_LOADING_ANNOUNCEMENT == "Hi friend! Give me a couple minutes to full boot up and I'll let you know when Im ready!"
    assert config.VOICE_STARTUP_READY_ANNOUNCEMENT == "Hi friend I am all ready to talk to! All done loading"
    assert config.TTS_PCM_QUEUE_CHUNKS >= 2
    assert config.TTS_PCM_FRAMES_PER_BUFFER >= 256
    chat_source = Path("systems/chat.py").read_text(encoding="utf-8")
    assert '"stream": True' in chat_source
    assert "class _StreamingSpeechPipeline" in chat_source
    assert "bemo-stream-tts" in chat_source and "bemo-stream-audio" in chat_source
    assert "def _pop_speech_chunks(self, text, *, min_words=1, max_words=None, max_chunks=None)" in chat_source
    supervisor_source = Path("supervisor.py").read_text(encoding="utf-8")
    assert "bemo-standby-main-preload" in supervisor_source
    assert "Runtime prompt/tool prefix warmup complete" in chat_source
    assert "Startup readiness probe" in chat_source
    assert "Speech timing: capture=" in chat_source
    assert "TTS utterance {} first PCM" in chat_source
    assert "Live PCM audio stream opened" in chat_source
    assert "tempfile" not in chat_source
    # Chatterbox dropped on 2026-10-19 (installer decision C): Piper only.
    assert not Path("systems/voice_streaming.py").exists() and not Path("voice_setup.py").exists()
    assert "chatterbox" not in Path("install.sh").read_text(encoding="utf-8").lower()
    idle_source = Path("systems/desktop_idle.py").read_text(encoding="utf-8")
    assert "xprintidle" in idle_source
    assert config.INTERFACE_SETTINGS_PATH.name == "interface.json"
    assert config.PIPER_MODEL_PATH.suffix == ".onnx"
    assert config.TTS_SPOKEN_SELF_NAME == "Beemo"
    assert prepare_spoken_text("I am Bemo.") == "I am Beemo."
    assert prepare_spoken_text("Bemo\'s voice") == "Beemo\'s voice"
    assert prepare_spoken_text("BEMO and bemo") == "Beemo and Beemo"
    assert prepare_spoken_text("Bemotion") == "Bemotion"
    assert config.OLLAMA_NUM_CTX >= 512
    assert config.MAIN_MODEL_PROFILE in config.MODEL_PROFILES
    assert config.MODEL_PROFILES[config.MAIN_MODEL_PROFILE].get("keep_alive") == -1
    main_prompt = config.MODEL_PROFILES[config.MAIN_MODEL_PROFILE].get("system_prompt", "")
    # Prompts were trimmed to a 2200-character budget (tests/legacy); capabilities come from detection.
    assert "MAIN BRAIN PERSONALITY" in main_prompt and len(main_prompt) < 2200
    assert "FAST CODING" not in main_prompt
    assert "fast-coding" not in config.MODEL_PROFILES
    assert config.MODEL_PROFILES["coding"].get("qualification") == "locked"
    assert config.MODEL_PROFILES["engineering"].get("qualification") == "locked"
    assert "fast-vision" not in config.MODEL_PROFILES  # dropped on 2026-10-24
    assert config.MODEL_PROFILES["deep-vision"].get("qualification") == "locked"
    assert config.MODEL_PROFILES["deep-vision"].get("benchmark_model") == "InternVL3.5-30B-A3B"
    assert config.MODEL_PROFILES["engineering"].get("engineering_tools") is True
    assert config.MODEL_PROFILES["normal"]["num_predict"] == 1024
    assert config.MODEL_PROFILES["coding"]["num_predict"] == 4096
    assert config.MODEL_PROFILES["engineering"]["num_predict"] == 4096
    assert config.MODEL_PROFILES["deep-vision"]["num_predict"] == 4096
    assert "Route by the action required, not just the topic" in config.MODEL_PROFILES["normal"]["system_prompt"]
    assert "smart friend in the workshop" in config.MODEL_PROFILES["normal"]["system_prompt"]
    assert "lightly mischievous" in config.MODEL_PROFILES["normal"]["system_prompt"]
    assert (config.POLICY_DIR / "main_personality.md").exists()
    autostart_manager = AutostartManager(Path.cwd())
    autostart_unit = autostart_manager.render_unit()
    autostart_desktop = autostart_manager.render_desktop_entry()
    assert "ExecStart=/bin/bash" in autostart_unit and "start.sh" in autostart_unit
    assert "Restart=always" in autostart_unit
    assert "OnlyShowIn=XFCE;" in autostart_desktop
    assert "bemo-session-start.sh" in autostart_desktop
    lightdm_cfg = render_lightdm_autologin("bemo", session="xfce")
    assert "autologin-user=bemo" in lightdm_cfg
    assert "autologin-user-timeout=0" in lightdm_cfg
    assert "autologin-session=xfce" in lightdm_cfg
    merged_lightdm = update_lightdm_config_text(
        "[Seat:*]\ngreeter-hide-users=false\nautologin-user=olduser\n\n[LightDM]\nrun-directory=/run/lightdm\n",
        "bemo",
        session="xfce",
    )
    assert "greeter-hide-users=false" in merged_lightdm
    assert "autologin-user=bemo" in merged_lightdm and "autologin-user=olduser" not in merged_lightdm
    assert "[LightDM]" in merged_lightdm and "run-directory=/run/lightdm" in merged_lightdm
    assert "INSUFFICIENT DATA" in config.MODEL_PROFILES["engineering"]["system_prompt"]
    assert "verify actual changes, and test when possible" in config.MODEL_PROFILES["coding"]["system_prompt"]
    assert "photos, screenshots and technical images" in config.MODEL_PROFILES["deep-vision"]["system_prompt"]
    benchmark_notes = config.POLICY_DIR / "benchmark_notes"
    assert (benchmark_notes / "main_brain_detailed.md").exists()
    assert (benchmark_notes / "engineering_detailed.md").exists()
    assert (benchmark_notes / "heavy_coding_detailed.md").exists()
    assert (benchmark_notes / "fast_vision_detailed.md").exists()
    for profile in config.MODEL_PROFILES.values():
        prompt = profile.get("system_prompt", "")
        # The voice-command catalog lives in VOICE_COMMANDS.md (checked above), not in every prompt;
        # each prompt carries the detected capability line instead (portability plan P3).
        assert "Claim no others" in prompt
    assert config.MODEL_PROFILES["coding"].get("route_signals") is True
    assert config.MODEL_PROFILES["engineering"].get("route_signals") is True
    assert config.MODEL_ROUTE_TO_PROFILE["HEAVY_CODING"] == "coding"
    assert config.MODEL_ROUTE_TO_PROFILE["ENGINEERING"] == "engineering"
    assert config.MODEL_ROUTE_TO_PROFILE["FAST_VISION"] == "deep-vision"  # the one vision brain
    assert config.MODEL_ROUTE_TO_PROFILE["DEEP_TECHNICAL_VISION"] == "deep-vision"
    install_aliases = {spec.alias: spec.source for spec in INSTALL_MODEL_ROSTER}
    assert install_aliases[config.MODEL_PROFILES["normal"]["model"]] == "qwen3:30b-a3b-instruct-2507-q4_K_M"
    assert install_aliases[config.MODEL_PROFILES["engineering"]["model"]] == "qwen3:30b-a3b-thinking-2507-q4_K_M"
    assert install_aliases[config.MODEL_PROFILES["coding"]["model"]] == "qwen3-coder:30b-a3b-q4_K_M"
    assert "qwen3-vl-fast:30b" not in install_aliases
    assert install_aliases[config.MODEL_PROFILES["deep-vision"]["model"]] == "hf.co/bartowski/OpenGVLab_InternVL3_5-30B-A3B-GGUF:Q4_K_M"
    assert install_aliases[config.MODEL_PROFILES["emergency"]["model"]] == "qwen3:4b-instruct-2507-q4_K_M"
    assert parse_route_signal("ROUTE: ENGINEERING") == "ENGINEERING"
    assert parse_route_signal(" route: deep technical vision ") == "DEEP_TECHNICAL_VISION"
    assert parse_route_signal("I think ROUTE: ENGINEERING") is None
    tool_names = {item["function"]["name"] for item in engineering_tool_schemas()}
    assert {"calculate_python", "solve_math", "convert_units", "inspect_engineering_file",
            "plot_engineering_data", "inspect_cad", "lookup_technical_reference",
            "mechanical_calculator", "electrical_calculator", "thermal_calculator"} <= tool_names
    assert config.ENABLE_ENGINEERING_TOOLS
    assert config.ENABLE_GAME_SUBSYSTEM
    assert config.GAME_FRONTEND_WIDTH == 640 and config.GAME_FRONTEND_HEIGHT == 480
    assert "games" in config.SUPERVISOR_APPS
    assert "emulator" in config.SUPERVISOR_APPS["games"].get("aliases", ())
    assert "gamecube" in config.SUPERVISOR_APPS["games"].get("aliases", ())
    assert config.GAME_GAMECUBE_ROM_DIR.name == "gamecube"
    command_forms = {item.syntax for item in VOICE_COMMANDS}
    assert "Hey Bemo, import games from USB" in command_forms
    assert "Hey Bemo, rescan games" in command_forms
    assert "Hey Bemo, import project from USB" in command_forms
    assert "Hey Bemo, import files into current project" in command_forms
    assert "Hey Bemo, display status" in command_forms
    assert "Hey Bemo, test screen resolution <width> by <height>" in command_forms
    assert "Hey Bemo, test screen crop <pixels> pixels" in command_forms
    assert "Hey Bemo, approve display changes" in command_forms
    assert "Hey Bemo, reject display changes" in command_forms

    # CPU tuning is generic: topology supplies safe defaults and the empirical
    # benchmark may select a more efficient Ollama count without CPU model tables.
    four_core = profile_from_counts(cpu_name="anything", physical_cores=4, logical_cores=8, ram_gb=32)
    eight_core = profile_from_counts(cpu_name="anything else", physical_cores=8, logical_cores=16, ram_gb=32)
    measured = profile_from_counts(
        cpu_name="unknown future CPU", physical_cores=12, logical_cores=24, ram_gb=64, measured_ollama_threads=18
    )
    assert (four_core.ollama_threads, four_core.stt_threads, four_core.torch_threads) == (4, 3, 3)
    assert (eight_core.ollama_threads, eight_core.stt_threads, eight_core.torch_threads) == (8, 7, 7)
    assert (measured.ollama_threads, measured.stt_threads, measured.torch_threads) == (18, 11, 11)
    assert candidate_thread_counts(4, 8) == [4, 6, 8]
    assert candidate_thread_counts(8, 16) == [8, 12, 16]
    assert candidate_thread_counts(8, 8) == [4, 6, 8]

    # A tiny performance gain must not consume extra threads. Here 6 and 8
    # threads are within the 5% efficient window, so 6 wins. TTS slowdown can
    # also knock a nominally-fast candidate out of the balanced-performance knee.
    efficient_rows = [
        {"threads": 4, "generation_tps": 10.0, "prompt_tps": 40.0, "tts_overlap_ratio": 1.00},
        {"threads": 6, "generation_tps": 10.9, "prompt_tps": 42.0, "tts_overlap_ratio": 1.02},
        {"threads": 8, "generation_tps": 11.1, "prompt_tps": 42.5, "tts_overlap_ratio": 1.10},
    ]
    chosen = choose_efficient_result(efficient_rows)
    assert chosen["threads"] == 6, efficient_rows

    record = {
        "algorithm_version": AUTOTUNE_ALGORITHM_VERSION,
        "cpu_name": "test CPU",
        "physical_cores": 4,
        "logical_cores": 8,
        "chosen_ollama_threads": 6,
    }
    assert autotune_record_matches(record, cpu_name="test CPU", physical_cores=4, logical_cores=8)
    assert not autotune_record_matches(record, cpu_name="different CPU", physical_cores=4, logical_cores=8)

    detected = detect_hardware()
    assert detected.physical_cores >= 1 and detected.logical_cores >= detected.physical_cores
    assert 1 <= detected.ollama_threads <= detected.logical_cores
    assert detected.profile_name in {
        "automatic topology fallback (benchmark not yet applied)",
        "automatic measured efficiency benchmark",
    }
    assert voice_profile_signature()

    with tempfile.TemporaryDirectory(prefix="bemo-self-test-") as temp:
        root = Path(temp)

        # Display changes are previews until approved. A fake xrandr backend keeps
        # this deterministic/headless while exercising mode validation + rollback.
        class FakeXrandrResult:
            def __init__(self, stdout="", returncode=0):
                self.stdout = stdout
                self.stderr = ""
                self.returncode = returncode

        current_mode = {"value": (640, 480)}
        def fake_xrandr(args, **kwargs):
            if args[1:] == ["--props"]:
                w, h = current_mode["value"]
                star_640 = "*" if (w, h) == (640, 480) else " "
                star_800 = "*" if (w, h) == (800, 600) else " "
                return FakeXrandrResult(
                    f"HDMI-1 connected primary {w}x{h}+0+0\n"
                    f"    EDID:\n"
                    f"        00ffffffffffff00123456789abcdef0\n"
                    f"   640x480 60.00{star_640}+\n"
                    f"   800x600 60.00{star_800}\n"
                )
            if "--mode" in args:
                mode = args[args.index("--mode") + 1]
                mw, mh = (int(x) for x in mode.split("x", 1))
                current_mode["value"] = (mw, mh)
                return FakeXrandrResult()
            return FakeXrandrResult(returncode=1)

        dm = DisplayManager(root / "display" / "settings.json", fallback_width=1280, fallback_height=800, runner=fake_xrandr)
        assert dm.current_state().width == 640 and dm.current_state().height == 480
        preview = dm.test_resolution(800, 600)
        assert preview.get("ok") and dm.pending and dm.current_state().width == 800
        crop_preview = dm.test_crop("horizontal", 12)
        assert crop_preview.get("ok") and dm.current_state().crop_left == 12 and dm.current_state().crop_right == 12
        saved_display = dm.approve()
        assert saved_display.get("ok") and not dm.pending and (root / "display" / "settings.json").is_file()
        assert dm.test_crop("all", 20).get("ok")
        reverted = dm.reject()
        assert reverted.get("ok") and dm.current_state().crop_left == 12
        assert dm.test_crop("all", 18).get("ok")
        dm._pending_deadline = 0.0  # deterministic timeout-path exercise
        timed_out = dm.tick()
        assert timed_out and timed_out.get("timeout") and dm.current_state().crop_left == 12
        unsupported = dm.test_resolution(1024, 768)
        assert not unsupported.get("ok") and unsupported.get("reason") == "unsupported-mode"

        # GameCube-only library discovery/config is deterministic and confined
        # to the persistent Bemo game root. No emulator binary is required for
        # this offline architecture test.
        games = GameLibrary(root / "games", config.APP_ROOT)
        paths = games.ensure_layout()
        (games.gamecube_dir / "F-Zero GX (USA).rvz").write_bytes(b"test")
        (games.gamecube_dir / "Mario Kart Double Dash.iso").write_bytes(b"test")
        nested = games.gamecube_dir / "Favorites"
        nested.mkdir(parents=True)
        (nested / "Super Smash Bros Melee.gcm").write_bytes(b"test")
        found = games.scan()
        assert len(found) == 3
        game, candidates = games.find("F Zero GX")
        assert game and game.system == "gamecube" and game.filename.endswith(".rvz")
        game, candidates = games.find("Mario Kart Double Dash")
        assert game and game.system == "gamecube"
        systems_xml = (games.esde_custom_systems / "es_systems.xml").read_text(encoding="utf-8")
        assert "<loadExclusive/>" in systems_xml and "Nintendo GameCube" in systems_xml and "Dolphin" in systems_xml
        assert "arcade" not in systems_xml.lower() and "playstation" not in systems_xml.lower()
        assert Path(paths["gamecube_roms"]).is_dir() and Path(paths["dolphin_user"]).is_dir()

        # Command generation is also deterministic. /bin/echo stands in for
        # Dolphin so this test remains offline and emulator-independent.
        old_dolphin = os.environ.get("BEMO_DOLPHIN_BINARY")
        os.environ["BEMO_DOLPHIN_BINARY"] = "/bin/echo"
        try:
            cmd = games.command_for(game)
            assert "-b" in cmd and "-e" in cmd and "Dolphin.Display.Fullscreen=True" in cmd
            assert str(games.dolphin_user_dir) in cmd and str(Path(game.path)) in cmd
        finally:
            if old_dolphin is None:
                os.environ.pop("BEMO_DOLPHIN_BINARY", None)
            else:
                os.environ["BEMO_DOLPHIN_BINARY"] = old_dolphin

        # Removable-drive ROM import is deterministic, copy-only, SHA-256
        # verified, duplicate-safe, recursive, and ignores unrelated files.
        usb = root / "usb-games"
        (usb / "GameCube" / "Zelda").mkdir(parents=True)
        (usb / "GameCube" / "F-Zero GX (USA).rvz").write_bytes(b"test")  # exact duplicate
        (usb / "GameCube" / "Zelda" / "Wind Waker.rvz").write_bytes(b"wind-waker")
        (usb / "GameCube" / "Mario Kart Double Dash.iso").write_bytes(b"different-disc")  # path conflict
        (usb / "notes.txt").write_text("not a rom", encoding="utf-8")
        imported = games.import_from_roots([usb])
        assert imported.get("ok"), imported
        assert imported["discovered_count"] == 3
        assert imported["imported_count"] == 2
        assert imported["duplicate_count"] == 1
        assert imported["error_count"] == 0
        assert (games.gamecube_dir / "Zelda" / "Wind Waker.rvz").read_bytes() == b"wind-waker"
        conflict_files = list(games.gamecube_dir.glob("Mario Kart Double Dash (imported *).iso"))
        assert len(conflict_files) == 1 and conflict_files[0].read_bytes() == b"different-disc"
        index_path = games.config_dir / "rom-import-index.json"
        assert index_path.is_file() and "sha256" in json.loads(index_path.read_text(encoding="utf-8"))
        imported_again = games.import_from_roots([usb])
        assert imported_again.get("ok"), imported_again
        assert imported_again["imported_count"] == 0
        assert imported_again["duplicate_count"] == 3

        # The lifecycle supervisor protocol plus structured computer-control
        # broker works offline here. A fake Ollama backend verifies model
        # unload-before-load without requiring local model weights.
        fake_ollama = FakeOllama()
        computer = ComputerController(
            screenshot_dir=root / "screenshots",
            action_timeout_seconds=5,
            output_max_chars=4000,
            test_root=root,
        )
        supervisor_pm = make_project_manager(root / "supervisor-project-data", root / "supervisor-project-export")
        service = SupervisorService(
            socket_path=root / "supervisor.sock",
            state_path=root / "supervisor-state.json",
            app_config={},
            known_models=["main-test", "coder-test"],
            ollama_module=fake_ollama,
            computer_controller=computer,
            computer_state_path=root / "computer-control.json",
            pending_actions_path=root / "pending-actions.json",
            computer_action_log_path=root / "computer-actions.jsonl",
            project_manager=supervisor_pm,
        )
        service.start()
        client = SupervisorClient(root / "supervisor.sock", timeout=2)
        assert client.available()

        # A slow model transition must not block fast status polling.  The real
        # UI polls status every couple of seconds while a 30B preload can take
        # tens of seconds; sharing one lock used to create timed-out clients and
        # a BrokenPipe traceback storm.
        slow_service = SupervisorService(
            socket_path=root / "slow-supervisor.sock",
            state_path=root / "slow-supervisor-state.json",
            app_config={},
            known_models=["main-test"],
            ollama_module=SlowFakeOllama(delay=0.35),
            computer_controller=computer,
            computer_state_path=root / "slow-computer-control.json",
            pending_actions_path=root / "slow-pending-actions.json",
            computer_action_log_path=root / "slow-computer-actions.jsonl",
            project_manager=make_project_manager(root / "slow-supervisor-project-data", root / "slow-supervisor-project-export"),
        )
        slow_service.start()
        slow_client = SupervisorClient(root / "slow-supervisor.sock", timeout=2)
        result_box = {}
        transition = threading.Thread(
            target=lambda: result_box.setdefault("result", slow_client.switch_model("main-test", preload=True, keep_alive=-1, timeout=2)),
            daemon=True,
        )
        transition.start()
        time.sleep(0.05)
        status_started = time.monotonic()
        assert slow_client.status().get("ok")
        assert time.monotonic() - status_started < 0.20
        transition.join(timeout=2)
        assert result_box.get("result", {}).get("ok")
        slow_service.stop()

        switched = client.switch_model("main-test", preload=True, keep_alive=-1, timeout=2)
        assert switched.get("ok") and fake_ollama.loaded[-1] == ("main-test", -1)
        switched = client.switch_model("coder-test", preload=True, keep_alive="10m", timeout=2)
        assert switched.get("ok") and "main-test" in fake_ollama.unloaded
        assert fake_ollama.loaded[-1] == ("coder-test", "10m")
        supervisor_status = client.status()
        assert supervisor_status.get("active_model") == "coder-test"
        assert supervisor_status.get("voice_command_catalog_version") == COMMAND_CATALOG_VERSION
        assert supervisor_status.get("voice_command_count") == len(VOICE_COMMANDS)
        assert supervisor_status.get("voice_commands") == [item.syntax for item in VOICE_COMMANDS]
        assert supervisor_status.get("bemo_running") is False
        assert client.restart_bemo().get("ok") and service.restart_requested.is_set()
        service.restart_requested.clear()
        assert client.stop_bemo().get("ok") and service.stop_bemo_requested.is_set()
        service.stop_bemo_requested.clear()
        assert client.start_bemo(reason="test").get("ok") and service.start_bemo_requested.is_set()
        assert service.consume_start_reason() == "test"

        # The always-on Supervisor owns removable-media game import too, so the
        # operation remains deterministic instead of depending on an LLM shell copy.
        supervisor_games = GameLibrary(root / "supervisor-games", config.APP_ROOT)
        service.game_library = supervisor_games
        usb_supervisor = root / "usb-supervisor"
        usb_supervisor.mkdir()
        (usb_supervisor / "Luigi's Mansion.rvz").write_bytes(b"luigi")
        old_removable_roots = os.environ.get("BEMO_REMOVABLE_ROOTS")
        os.environ["BEMO_REMOVABLE_ROOTS"] = str(usb_supervisor)
        try:
            imported_via_supervisor = client.import_games_from_removable(timeout=5)
        finally:
            if old_removable_roots is None:
                os.environ.pop("BEMO_REMOVABLE_ROOTS", None)
            else:
                os.environ["BEMO_REMOVABLE_ROOTS"] = old_removable_roots
        assert imported_via_supervisor.get("ok"), imported_via_supervisor
        assert imported_via_supervisor.get("imported_count") == 1
        assert (supervisor_games.gamecube_dir / "Luigi's Mansion.rvz").is_file()
        rescanned = client.refresh_games()
        assert rescanned.get("ok") and rescanned.get("count") == 1

        # Project import is also Supervisor-owned. Multiple removable candidates
        # require an explicit selection; the chosen source is copied/verified,
        # its existing Git history survives, and the source remains untouched.
        usb_projects = root / "usb-projects"
        alpha = usb_projects / "Projects" / "Motor Controller"
        beta = usb_projects / "Projects" / "Red Light Mask"
        alpha.mkdir(parents=True)
        beta.mkdir(parents=True)
        (alpha / "controller.py").write_text("VALUE = 1\n", encoding="utf-8")
        (beta / "mask.txt").write_text("mask project\n", encoding="utf-8")
        subprocess.run(["git", "init"], cwd=alpha, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        subprocess.run(["git", "config", "user.name", "Source Test"], cwd=alpha, check=True)
        subprocess.run(["git", "config", "user.email", "source@test.local"], cwd=alpha, check=True)
        subprocess.run(["git", "add", "controller.py"], cwd=alpha, check=True)
        subprocess.run(["git", "commit", "-m", "source history"], cwd=alpha, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        source_head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=alpha, check=True, text=True, stdout=subprocess.PIPE).stdout.strip()
        os.environ["BEMO_REMOVABLE_ROOTS"] = str(usb_projects)
        try:
            ambiguous = client.import_project_from_removable(timeout=5)
            assert not ambiguous.get("ok") and ambiguous.get("reason") == "multiple-candidates"
            imported_project = client.import_project_from_removable("Motor Controller", timeout=5)
        finally:
            if old_removable_roots is None:
                os.environ.pop("BEMO_REMOVABLE_ROOTS", None)
            else:
                os.environ["BEMO_REMOVABLE_ROOTS"] = old_removable_roots
        assert imported_project.get("ok"), imported_project
        assert imported_project.get("preserved_git") is True
        imported_workspace = supervisor_pm.active_workspace
        assert imported_workspace and (imported_workspace / "controller.py").read_text(encoding="utf-8") == "VALUE = 1\n"
        imported_head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=imported_workspace, check=True, text=True, stdout=subprocess.PIPE).stdout.strip()
        assert imported_head == source_head
        assert (alpha / "controller.py").is_file()  # source drive remains untouched

        # Removable files can be merged into the current project without
        # overwriting collisions or importing another repository's .git folder.
        (imported_workspace / "notes.txt").write_text("keep original\n", encoding="utf-8")
        usb_files = root / "usb-project-files"
        usb_files.mkdir()
        (usb_files / "notes.txt").write_text("different notes\n", encoding="utf-8")
        (usb_files / "measurements.csv").write_text("x,y\n1,2\n", encoding="utf-8")
        (usb_files / ".git").mkdir()
        (usb_files / ".git" / "config").write_text("do not import\n", encoding="utf-8")
        os.environ["BEMO_REMOVABLE_ROOTS"] = str(usb_files)
        try:
            imported_files = client.import_files_into_project_from_removable(supervisor_pm.active_slug, timeout=5)
        finally:
            if old_removable_roots is None:
                os.environ.pop("BEMO_REMOVABLE_ROOTS", None)
            else:
                os.environ["BEMO_REMOVABLE_ROOTS"] = old_removable_roots
        assert imported_files.get("ok"), imported_files
        assert imported_files.get("imported_count") == 2, imported_files
        # Selected project files/folders can be exported through Supervisor to a
        # removable drive without moving the source or overwriting collisions.
        export_files_drive = root / "usb-file-export"
        export_files_drive.mkdir()
        os.environ["BEMO_REMOVABLE_ROOTS"] = str(export_files_drive)
        try:
            exported_file = client.export_project_items_to_removable(
                supervisor_pm.active_slug, ["controller.py"], timeout=5
            )
        finally:
            if old_removable_roots is None:
                os.environ.pop("BEMO_REMOVABLE_ROOTS", None)
            else:
                os.environ["BEMO_REMOVABLE_ROOTS"] = old_removable_roots
        assert exported_file.get("ok"), exported_file
        exported_controller = export_files_drive / "Bemo Files" / "controller.py"
        assert exported_controller.read_text(encoding="utf-8") == "VALUE = 1\n"
        assert (imported_workspace / "controller.py").is_file()
        assert imported_files.get("collision_count") == 1
        assert (imported_workspace / "notes.txt").read_text(encoding="utf-8") == "keep original\n"
        assert list(imported_workspace.glob("notes (imported *).txt"))
        assert (imported_workspace / "measurements.csv").is_file()

        # Every role reaches the same Supervisor-mediated logged-in-user computer
        # control layer. Ordinary actions execute immediately; destructive/high-
        # impact actions are queued until explicit confirmation.
        control_status = client.computer_status()
        assert control_status.get("ok") and control_status.get("enabled")
        sandbox_file = root / "pc" / "note.txt"
        write = client.computer_action(
            "write_text",
            {"path": str(sandbox_file), "content": "hello from bemo"},
            requester_role="ENGINEERING",
            task_id="test-task",
        )
        assert write.get("ok") and sandbox_file.read_text() == "hello from bemo"
        read = client.computer_action("read_text", {"path": str(sandbox_file)})
        assert read.get("ok") and "hello from bemo" in "\n".join(read.get("lines", []))
        overwrite = client.computer_action(
            "write_text", {"path": str(sandbox_file), "content": "updated"}
        )
        assert overwrite.get("confirmation_required") and sandbox_file.read_text() == "hello from bemo"
        confirmed = client.confirm_computer_action(overwrite.get("confirmation_id"), require_critical=False)
        assert confirmed.get("ok") and sandbox_file.read_text() == "updated"
        nested = root / "pc" / "folder"
        nested.mkdir(parents=True)
        (nested / "x.txt").write_text("x")
        delete_dir = client.computer_action("delete", {"path": str(nested), "recursive": True})
        assert delete_dir.get("confirmation_required") and nested.exists()
        client.cancel_computer_action(delete_dir.get("confirmation_id"))
        assert nested.exists()
        run = client.computer_action("run", {"argv": [__import__("sys").executable, "--version"]})
        assert run.get("ok") and run.get("returncode") == 0
        privileged = client.computer_action("run", {"argv": ["sudo", "echo", "no"]})
        assert not privileged.get("ok") and privileged.get("admin_required")
        inline = client.computer_action("run", {"argv": ["bash", "-c", "echo risky"]})
        assert inline.get("confirmation_required")
        client.cancel_computer_action(inline.get("confirmation_id"))
        admin = client.computer_action("admin_run", {"argv": ["echo", "admin-test"]})
        assert admin.get("confirmation_required") and admin.get("critical")
        normal_confirm = client.confirm_computer_action(admin.get("confirmation_id"), require_critical=False)
        assert normal_confirm.get("critical_confirmation_required")
        admin_confirm = client.confirm_computer_action(admin.get("confirmation_id"), require_critical=True)
        assert admin_confirm.get("ok") and admin_confirm.get("test_mode")
        client.set_computer_control(False)
        blocked = client.computer_action("list", {"path": str(root)})
        assert not blocked.get("ok")
        client.set_computer_control(True)
        assert (root / "computer-actions.jsonl").is_file()

        # Pending confirmations survive a Supervisor restart, so a specialist/UI
        # crash cannot silently discard or auto-approve a destructive request.
        persistent_file = root / "pc" / "persistent.txt"
        persistent_file.write_text("old")
        pending = client.computer_action(
            "write_text", {"path": str(persistent_file), "content": "new"}
        )
        pending_id = pending.get("confirmation_id")
        assert pending.get("confirmation_required") and pending_id
        service.stop()
        supervisor_pm = make_project_manager(root / "supervisor-project-data", root / "supervisor-project-export")
        service = SupervisorService(
            socket_path=root / "supervisor.sock",
            state_path=root / "supervisor-state.json",
            app_config={},
            known_models=["main-test", "coder-test"],
            ollama_module=fake_ollama,
            computer_controller=computer,
            computer_state_path=root / "computer-control.json",
            pending_actions_path=root / "pending-actions.json",
            computer_action_log_path=root / "computer-actions.jsonl",
        )
        service.start()
        client = SupervisorClient(root / "supervisor.sock", timeout=2)
        resumed = client.confirm_computer_action(pending_id, require_critical=False)
        assert resumed.get("ok") and persistent_file.read_text() == "new"
        service.stop()

        db = root / "memory" / "conversation.sqlite3"
        backups = root / "memory" / "backups"

        store = MemoryStore(db, backups)
        session = store.start_session(cpu_name="test", app_version="test")
        first = store.add_message(
            session_id=session,
            role="user",
            content="The flux capacitor lives in controller.py and uses a wake word.",
            project_slug="demo",
            model_profile="coding",
            model_name="test-model",
        )
        second = store.add_message(
            session_id=session,
            role="assistant",
            content="I remember the controller file.",
            project_slug="demo",
            model_profile="coding",
            model_name="test-model",
        )
        assert first and second
        assert store.integrity_check() == "ok"
        test_now = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)
        hits = store.search_messages(
            "flux controller",
            active_project_slug="demo",
            now=test_now,
        )
        assert hits and "flux capacitor" in hits[0]["content"].lower()
        assert hits[0]["memory_tier"] == "cold" and hits[0]["use_count"] == 0

        # Merely searching/scanning a memory must not reinforce it.
        unchanged = store.search_messages(
            "flux controller",
            active_project_slug="demo",
            now=test_now,
        )
        assert unchanged[0]["use_count"] == 0 and unchanged[0]["effective_heat"] == 0.0

        # One meaningful use adds heat but intentionally does not jump tiers.
        store.reinforce_messages([first], now=test_now)
        one_use = store.search_messages(
            "flux controller",
            active_project_slug="demo",
            now=test_now,
        )
        first_state = next(item for item in one_use if item["id"] == first)
        assert first_state["use_count"] == 1
        assert 0.9 <= first_state["effective_heat"] <= 1.1
        assert first_state["memory_tier"] == "cold"

        # Repeated meaningful use proportionally accumulates into warm/hot.
        for _ in range(2):
            store.reinforce_messages([first], now=test_now)
        warm = store.search_messages(
            "flux controller",
            active_project_slug="demo",
            now=test_now,
        )
        warm_state = next(item for item in warm if item["id"] == first)
        assert warm_state["memory_tier"] == "warm"
        for _ in range(6):
            store.reinforce_messages([first], now=test_now)
        hot = store.search_messages(
            "flux controller",
            active_project_slug="demo",
            now=test_now,
        )
        hot_state = next(item for item in hot if item["id"] == first)
        assert hot_state["memory_tier"] == "hot"

        # Heat cools continuously with time rather than crossing a destructive
        # archive cutoff. Relevance can still retrieve the now-cold memory.
        cooled = store.search_messages(
            "flux controller",
            active_project_slug="demo",
            now=test_now + timedelta(days=120),
        )
        cooled_state = next(item for item in cooled if item["id"] == first)
        assert cooled_state["memory_tier"] == "cold"
        assert "flux capacitor" in cooled_state["content"].lower()

        store.set_conversation_summary("Flux capacitor project context.", first)
        store.end_session(session)
        store.maybe_backup(interval_hours=0.000001, retain=2)
        store.close()

        # Reopen exactly as a reboot would; raw messages and summary must remain.
        reopened = MemoryStore(db, backups)
        recent = reopened.load_recent(12)
        summary, cursor = reopened.conversation_summary()
        assert len(recent) == 2 and cursor == first
        assert "flux capacitor" in summary.lower()
        reopened.close()

        # A pre-v1.3 archive migrates in place without losing existing messages.
        legacy_db = root / "memory" / "legacy-conversation.sqlite3"
        import sqlite3
        legacy = sqlite3.connect(str(legacy_db))
        legacy.executescript(
            """
            CREATE TABLE sessions (
                id TEXT PRIMARY KEY, started_at TEXT NOT NULL, ended_at TEXT,
                cpu_name TEXT, app_version TEXT
            );
            CREATE TABLE messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL, created_at TEXT NOT NULL,
                role TEXT NOT NULL, content TEXT NOT NULL, project_slug TEXT,
                model_profile TEXT, model_name TEXT
            );
            INSERT INTO sessions(id, started_at) VALUES('legacy', '2026-01-01T00:00:00+00:00');
            INSERT INTO messages(
                session_id, created_at, role, content, project_slug, model_profile, model_name
            ) VALUES(
                'legacy', '2026-01-01T00:00:00+00:00', 'user',
                'Legacy memory survives migration.', 'demo', 'normal', 'legacy-model'
            );
            """
        )
        legacy.commit()
        legacy.close()
        migrated = MemoryStore(legacy_db, root / "memory" / "legacy-backups")
        migrated_hits = migrated.search_messages("legacy memory", active_project_slug="demo")
        assert migrated_hits and "survives migration" in migrated_hits[0]["content"].lower()
        assert migrated_hits[0]["use_count"] == 0
        assert migrated.integrity_check() == "ok"
        migrated.close()

        export_root = root / "fake-usb"
        pm = make_project_manager(root, export_root)
        project = pm.create_project("Demo Project")
        assert project["slug"] == "demo-project"
        pm.write_workspace_file(
            "controller.py",
            "WAKE_WORD = 'Bemo'\n\ndef answer():\n    return 'hello'\n",
        )
        (pm.active_workspace / "reference.png").write_bytes(b"\x89PNG\r\n\x1a\nnot-a-real-image-but-an-asset")
        pm.append_project_note("Preserve the existing animation structure.")
        pm.index_project(force=True)
        search = pm.search_project("WAKE_WORD controller")
        assert search and search[0]["path"] == "controller.py"
        context = pm.context_for_query("where is the wake word")
        assert "controller.py" in context and len(context) <= 3600
        assets = pm.search_assets("reference")
        assert assets and assets[0]["kind"] == "image"

        old_fact = pm.set_project_fact("jackshaft diameter", "5/8 in", source="test")
        new_fact = pm.set_project_fact("jackshaft diameter", "3/4 in", source="test")
        current_facts = pm.project_facts()
        all_facts = pm.project_facts(include_superseded=True)
        assert any(f["id"] == new_fact["id"] and f["status"] == "current" for f in current_facts)
        assert any(f["id"] == old_fact["id"] and f["status"] == "superseded" for f in all_facts)
        assert "3/4 in" in pm.context_for_query("jackshaft diameter")
        assert "5/8 in" not in pm.current_facts_text()

        task_ledger = TaskLedger(root / "tasks")
        task = task_ledger.begin_task(
            "Analyze the mechanism then update code.",
            project_slug=pm.active_slug,
            current_role="ENGINEERING",
        )
        # A fresh task repeats the current user request and should not break the
        # cache-friendly prefix until it gains material handoff state.
        assert task_ledger.prompt_context() == ""
        task_ledger.checkpoint(
            role="ENGINEERING",
            summary="Failure mode identified.",
            decisions="Limit peak current.",
            remaining_work="Implement controller protection.",
            next_role="HEAVY_CODING",
        )
        task_ledger.handoff(from_role="ENGINEERING", to_role="HEAVY_CODING")
        assert "Failure mode identified" in task_ledger.context()
        assert "Failure mode identified" in task_ledger.prompt_context()
        assert task_ledger.active_task()["current_role"] == "HEAVY_CODING"
        task_ledger.complete(role="HEAVY_CODING", final_result="Protection implemented.")
        assert task_ledger.active_task() is None

        usb_image = export_root / "camera" / "motor.jpg"
        usb_image.parent.mkdir(parents=True, exist_ok=True)
        usb_image.write_bytes(b"fake-jpeg")
        locator = FileLocator(
            home_root=root / "home",
            removable_provider=lambda: [export_root],
            max_scan_files=1000,
            search_timeout_seconds=2,
        )
        found = locator.search("motor", scope="removable")
        assert found and found[0]["path"].endswith("motor.jpg")
        assert locator.validate_image(found[0]["path"]).name == "motor.jpg"

        pm.replace_workspace_text("controller.py", "'Bemo'", "'Wib'")
        assert "'Wib'" in pm.read_workspace_file("controller.py")
        try:
            pm.write_workspace_file("../escape.txt", "no")
            raise AssertionError("path traversal should have been rejected")
        except ValueError:
            pass

        snapshot = pm.snapshot("Bemo: self test change")
        assert snapshot.get("ok")
        check = pm.run_project_check("python-compile", timeout_seconds=30)
        assert "exit=0" in check

        # Direct selected-item export supports files + folders, verifies copies,
        # skips exact duplicates, and collision-renames different USB content.
        asset_dir = pm.active_workspace / "renders"
        asset_dir.mkdir(exist_ok=True)
        (asset_dir / "preview.txt").write_text("preview\n", encoding="utf-8")
        selected_export = pm.export_project_items_to_removable(["controller.py", "renders"])
        assert selected_export.get("ok"), selected_export
        assert (export_root / "Bemo Files" / "controller.py").is_file()
        assert (export_root / "Bemo Files" / "renders" / "preview.txt").is_file()
        selected_export_again = pm.export_project_items_to_removable(["controller.py", "renders"])
        assert selected_export_again.get("ok") and selected_export_again.get("duplicate_count") == 2
        (export_root / "Bemo Files" / "controller.py").write_text("different usb copy\n", encoding="utf-8")
        collision_export = pm.export_project_items_to_removable(["controller.py"])
        assert collision_export.get("ok") and collision_export.get("collision_count") == 1
        assert (export_root / "Bemo Files" / "controller (exported 2).py").is_file()

        exported = pm.export_active_project()
        assert exported.get("ok"), exported
        assert Path(exported["folder"]).is_dir()
        assert Path(exported["folder"], "SHA256SUMS.txt").is_file()
        assert Path(exported["zip"]).is_file()

        # A Bemo project export can be imported from removable storage as a full
        # project bundle: checksum verified, durable memory/facts preserved, stale
        # index rebuilt, and workspace Git history retained.
        restore_root = root / "restored-bemo-data"
        restored_pm = make_project_manager(restore_root, root / "restored-export")
        restored = restored_pm.import_project_from_removable([export_root], selection="Demo Project")
        assert restored.get("ok"), restored
        assert restored.get("bemo_bundle") and restored.get("source_manifest_verified")
        assert "animation structure" in restored_pm.project_memory().lower()
        assert restored_pm.active_workspace and (restored_pm.active_workspace / ".git").exists()
        assert (restored_pm.active_workspace / "controller.py").is_file()

        with tempfile.TemporaryDirectory(prefix="bemo-backup-drive-") as backup_temp:
            backup_drive = Path(backup_temp)
            full_backup = FullBackupManager(
                data_root=root,
                app_root=Path(__file__).resolve().parent,
                removable_provider=lambda: [backup_drive],
            )
            live_store = MemoryStore(root / "memory" / "conversation.sqlite3", root / "memory" / "backups")
            backup_result = full_backup.create(memory_store=live_store, create_zip=True)
            live_store.close()
            assert backup_result.get("ok"), backup_result
            assert Path(backup_result["folder"], "data", "memory", "conversation.sqlite3").is_file()
            assert Path(backup_result["folder"], "SHA256SUMS.txt").is_file()

        manager = AutostartManager(Path(__file__).resolve().parent)
        unit = manager.render_unit()
        desktop = manager.render_desktop_entry()
        assert "Restart=always" in unit and "start.sh" in unit
        assert "OnlyShowIn=XFCE;" in desktop and "bemo-session-start.sh" in desktop

        # Active project selection itself is durable across process recreation.
        pm2 = make_project_manager(root, export_root)
        assert pm2.active_slug == "demo-project"
        assert "animation structure" in pm2.project_memory().lower()

        # Engineering toolkit stays deterministic and bounded. These tests are
        # deliberately offline; public reference search is exercised manually.
        eng = EngineeringToolkit(root / "engineering", max_file_bytes=2_000_000, max_text_chars=5000)

        def engineering_json(tool_name: str, arguments: dict):
            raw = eng.execute(tool_name, arguments)
            if raw.startswith("Tool error:"):
                raise AssertionError(f"Engineering tool {tool_name} failed: {raw}")
            try:
                return json.loads(raw)
            except json.JSONDecodeError as exc:
                raise AssertionError(
                    f"Engineering tool {tool_name} returned non-JSON output: {raw!r}"
                ) from exc
        unit_result = engineering_json("convert_units", {"value": 1, "from_unit": "ft", "to_unit": "mm"})
        assert abs(unit_result["value"] - 304.8) < 1e-9
        numeric = engineering_json("calculate_python", {"operation": "expression", "expression": "sqrt(3**2+4**2)"})
        assert numeric["result"] == 5.0
        symbolic = engineering_json("solve_math", {"operation": "solve", "expression": "2*x+3=9", "variable": "x", "symbols": ["x"]})
        assert symbolic["solutions"] == ["3"]
        mech = engineering_json("mechanical_calculator", {"operation": "gear_ratio", "inputs": {"driver_teeth": 10, "driven_teeth": 50, "input_rpm": 3000}})
        assert mech["output_rpm"] == 600.0
        elec = engineering_json("electrical_calculator", {"operation": "ohms_law", "inputs": {"voltage_v": 12, "current_a": 5}})
        assert elec["power_w"] == 60.0 and elec["resistance_ohm"] == 2.4
        thermal = engineering_json("thermal_calculator", {"operation": "steady_state_rise", "inputs": {"power_w": 50, "thermal_resistance_k_per_w": 0.8, "ambient_c": 25}})
        assert thermal["estimated_temperature_c"] == 65.0
        csv_path = root / "engineering.csv"
        csv_path.write_text("rpm,torque\n1000,10\n2000,20\n", encoding="utf-8")
        inspected = engineering_json("inspect_engineering_file", {"path": str(csv_path)})
        assert inspected["rows"][1] == ["1000", "10"]
        plotted = engineering_json("plot_engineering_data", {"series": [{"x": [1, 2], "y": [2, 4], "label": "test"}], "filename": "self-test.png"})
        assert Path(plotted["path"]).is_file()

        # Mesh inspection verifies deterministic geometry without needing a GUI.
        import trimesh
        mesh_path = root / "box.stl"
        trimesh.creation.box(extents=[10, 20, 30]).export(mesh_path)
        cad = engineering_json("inspect_cad", {"path": str(mesh_path)})
        assert cad["watertight"] and len(cad["extents_native_units"]) == 3

        # Legacy Windows .scr support is application-owned and falls back to the
        # built-in CRT saver if Wine/files are unavailable. Test process lifecycle
        # with /bin/sh standing in for Wine so CI does not require Wine itself.
        saver_dir = root / "legacy-savers"
        saver_dir.mkdir(parents=True, exist_ok=True)
        fake_scr = saver_dir / "Pipes.scr"
        fake_scr.write_text("sleep 30\n", encoding="utf-8")
        saver_manager = WindowsScreensaverManager(
            saver_dir,
            removable_provider=lambda: [],
            wine_binary="/bin/sh",
            wine_prefix=root / "wine-prefix",
            rotate_seconds=60,
            startup_grace_seconds=0.25,
            search_removable=False,
            max_discovered=10,
            enabled=True,
        )
        discovered = saver_manager.discover()
        assert discovered and discovered[0].name == "Pipes.scr"
        started = saver_manager.tick()
        assert started.get("running")
        assert saver_manager.status().get("running")
        saver_manager.stop()
        assert not saver_manager.status().get("running")
        quick_scr = saver_dir / "Quick.scr"
        quick_scr.write_text("exit 0\n", encoding="utf-8")
        assert saver_manager.run_path(quick_scr)
        import time as _time
        _time.sleep(0.05)
        exit_state = saver_manager.tick()
        # Quick.scr is manually invoked after a healthy manager lifecycle; a
        # near-instant exit is now treated as startup failure, not fake activity.
        assert not exit_state.get("user_exit")

        # If desktop input has reset the X idle timer, an equally-fast exit is
        # a real user wake and must never quarantine a healthy saver.
        active_dir = root / "active-savers"
        active_dir.mkdir(parents=True, exist_ok=True)
        active_scr = active_dir / "ActiveWake.scr"
        active_scr.write_text("exit 0\n", encoding="utf-8")
        active_manager = WindowsScreensaverManager(
            active_dir,
            removable_provider=lambda: [],
            wine_binary="/bin/sh",
            wine_prefix=root / "active-wine-prefix",
            rotate_seconds=60,
            startup_grace_seconds=0.25,
            search_removable=False,
            max_discovered=10,
            enabled=True,
            desktop_idle_provider=lambda: 0.0,
            idle_threshold_seconds=180.0,
        )
        assert active_manager.tick().get("running")
        _time.sleep(0.05)
        active_state = active_manager.tick()
        assert active_state.get("user_exit")
        assert active_manager.status().get("quarantined_count") == 0

        crash_dir = root / "crash-savers"
        crash_dir.mkdir(parents=True, exist_ok=True)
        crash_scr = crash_dir / "Crash.scr"
        # Verify the launcher runs from the SCR directory: the script creates a
        # relative marker, then exits immediately and should be quarantined.
        crash_scr.write_text("pwd > cwd.txt\nexit 7\n", encoding="utf-8")
        crash_manager = WindowsScreensaverManager(
            crash_dir,
            removable_provider=lambda: [],
            wine_binary="/bin/sh",
            wine_prefix=root / "crash-wine-prefix",
            rotate_seconds=60,
            startup_grace_seconds=0.25,
            search_removable=False,
            max_discovered=10,
            enabled=True,
        )
        assert crash_manager.tick().get("running")
        _time.sleep(0.05)
        crash_state = crash_manager.tick()
        assert not crash_state.get("user_exit")
        assert crash_state.get("reason") == "no-healthy-scr-files"
        assert crash_manager.status().get("quarantined_count") == 1
        assert (crash_dir / "cwd.txt").is_file()

        # A saver that survives the startup grace and then exits on its own is
        # treated as the normal classic-SCR wake signal.
        wake_dir = root / "wake-savers"
        wake_dir.mkdir(parents=True, exist_ok=True)
        wake_scr = wake_dir / "Wake.scr"
        wake_scr.write_text("sleep 0.35\nexit 0\n", encoding="utf-8")
        wake_manager = WindowsScreensaverManager(
            wake_dir,
            removable_provider=lambda: [],
            wine_binary="/bin/sh",
            wine_prefix=root / "wake-wine-prefix",
            rotate_seconds=60,
            startup_grace_seconds=0.20,
            search_removable=False,
            max_discovered=10,
            enabled=True,
        )
        assert wake_manager.tick().get("running")
        _time.sleep(0.40)
        wake_state = wake_manager.tick()
        assert wake_state.get("user_exit")

        # The published-pack installer preserves the whole source subtree so
        # companion assets stay beside the .scr files, and it is idempotent.
        fake_pack = root / "published-pack" / "windows95"
        fake_pack.mkdir(parents=True, exist_ok=True)
        (fake_pack / "3DPipes.scr").write_bytes(b"MZ-fake-scr")
        (fake_pack / "textures").mkdir()
        (fake_pack / "textures" / "wall.bmp").write_bytes(b"BM-fake")
        fetched_dest = root / "screensavers" / "windows" / "legacy-pack"
        fetched = install_from_directory(fake_pack, fetched_dest, source_note="self-test")
        assert fetched.get("ok") and fetched.get("count") == 1
        assert (fetched_dest / "3DPipes.scr").is_file()
        assert (fetched_dest / "textures" / "wall.bmp").is_file()
        fetched_again = install_from_directory(fake_pack, fetched_dest, source_note="self-test")
        assert fetched_again.get("skipped") and fetched_again.get("count") == 1

        # CRT/model-loader configuration is intentionally application-owned so
        # it remains independent of the desktop environment and LLM availability.
        assert config.ENABLE_CRT_SCREENSAVER
        assert config.ENABLE_WINDOWS_SCR_SCREENSAVERS
        assert float(config.WINDOWS_SCREENSAVER_ROTATE_SECONDS) == 180.0
        assert float(config.CRT_SCREENSAVER_IDLE_SECONDS) == 240.0  # updated 2026-10-19: this value was already so in the v1.28.7 baseline
        assert float(config.WINDOWS_SCREENSAVER_STARTUP_GRACE_SECONDS) >= 0.25
        assert len(tuple(config.CRT_SCREENSAVER_MODES)) >= 2
        assert config.ENABLE_MODEL_SWITCH_LOADING_SCREEN
        assert float(config.MODEL_SWITCH_MIN_DISPLAY_SECONDS) >= 0
        assert config.MODEL_SWITCH_RETRO_PROGRESS
        assert int(config.MODEL_SWITCH_RETRO_PROGRESS_BLOCKS) >= 6
        assert 1 <= int(config.MODEL_SWITCH_RETRO_PROGRESS_LIT_BLOCKS) < int(config.MODEL_SWITCH_RETRO_PROGRESS_BLOCKS)
        assert float(config.MODEL_SWITCH_RETRO_PROGRESS_STEP_SECONDS) > 0
        renderer_source = (Path(__file__).resolve().parent / "systems" / "render.py").read_text(encoding="utf-8")
        chat_source = (Path(__file__).resolve().parent / "systems" / "chat.py").read_text(encoding="utf-8")
        assert "_render_screensaver" in renderer_source
        assert "_render_idle_backend" in renderer_source
        assert "windows_screensaver" in chat_source
        assert "_render_model_switch" in renderer_source
        assert "_render_retro_progress_bar" in renderer_source
        assert "_begin_model_switch_visual" in chat_source
        assert "_finish_model_switch_visual" in chat_source
        assert "engineering_tool_schemas" in chat_source
        assert "ENGINEERING_TOOL_NAMES" in chat_source
        assert "self.engineering_toolkit.execute" in chat_source

    print("Bemo persistence/project/supervisor self-test: PASS")
    for line in config.HARDWARE.as_lines():
        print(line)
    print(f"Persistent data root: {config.DATA_ROOT}")



if __name__ == "__main__":
    main()
