import config
from hardware import autotune_path, read_autotune_record

print("Bemo hardware auto-detection")
print("----------------------------")
for line in config.HARDWARE.as_lines():
    print(line)
print()
print("Effective runtime values")
print(f"Ollama threads: {config.OLLAMA_NUM_THREAD}")
print(f"Whisper threads: {config.STT_CPU_THREADS}")
print(f"Torch/TTS threads: {config.TORCH_NUM_THREADS}")
print(f"Ollama context: {config.OLLAMA_NUM_CTX}")
print(f"Default model profile: {config.DEFAULT_MODEL_PROFILE}")
print(f"Default model: {config.MODEL_PROFILES[config.DEFAULT_MODEL_PROFILE]['model']}")
print()
record = read_autotune_record()
print(f"Efficiency benchmark: {autotune_path()}")
if record:
    print(f"Saved winner: {record.get('chosen_ollama_threads')} Ollama threads")
    print(f"Benchmark model: {record.get('benchmark_model', 'unknown')}")
else:
    print("Saved winner: none yet (using topology fallback)")

print()
print("Hardware report (portability plan P3)")
print("-------------------------------------")
from systems import hardware_report
from systems.display import DisplayManager
from systems.sound import host_sound
report = hardware_report.collect(config, sound=host_sound(),
                                 display=DisplayManager(config.DISPLAY_SETTINGS_PATH))
for line in hardware_report.printed(report):
    print(line)
hardware_report.save(report, config.HARDWARE_PROFILE_PATH)
print(f"Saved: {config.HARDWARE_PROFILE_PATH}")
