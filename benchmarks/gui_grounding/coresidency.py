"""Can MAI-UI-2B (8-bit, llama.cpp) stay loaded next to BMO's Main brain?

Loads, in order: Main (Ollama, BMO's settings), a stand-in for BMO's own
process (Whisper + Piper + BMO code), and the MAI-UI server. After each step
it records available RAM, swap use and major page faults (disk paging).
Then, with everything loaded, it times grounding on a spread of targets and
a normal Main reply. Usage (BMO venv, BMO not running):
  BENCH_CTX=4096 .venv/bin/python -I benchmarks/gui_grounding/coresidency.py ~/BMO-models/bench
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ollama
import psutil
from PIL import Image
import run_bench as rb

ROOT = Path(__file__).resolve().parents[2]


def mem():
    vm, sw = psutil.virtual_memory(), psutil.swap_memory()
    with open("/proc/vmstat") as f:
        faults = next(int(l.split()[1]) for l in f if l.startswith("pgmajfault "))
    return {"available_gb": round(vm.available / 2**30, 2), "swap_used_gb": round(sw.used / 2**30, 2), "majfault": faults}


def log(step, extra=None):
    row = {"step": step, **mem(), **(extra or {})}
    print(json.dumps(row), flush=True)
    return row


def main_reply():
    t = time.monotonic()
    r = ollama.chat(model="qwen3-main:30b", keep_alive="30m",
                    options={"num_ctx": 8192, "num_thread": 12, "num_predict": 120, "temperature": 0.2},
                    messages=[{"role": "user", "content": "Give me three short tips for building a sturdy wooden shelf."}])
    return {"main_wall_s": round(time.monotonic() - t, 1),
            "main_decode_tok_s": round(r.eval_count / (r.eval_duration / 1e9), 1),
            "main_prefill_s": round(r.prompt_eval_duration / 1e9, 2)}


def main():
    bench = Path(sys.argv[1]).expanduser()
    data = json.loads((bench / "dataset.json").read_text())
    log("baseline (nothing loaded)")

    ollama.generate(model="qwen3-main:30b", prompt="", keep_alive="30m", options={"num_ctx": 8192, "num_thread": 12})
    log("Main loaded", main_reply())

    stand_in = subprocess.Popen([sys.executable, "-I", "-c", (
        "import sys; sys.path.insert(0, %r); import os; os.environ.setdefault('SDL_VIDEODRIVER','dummy');"
        "import config, systems.chat; from faster_whisper import WhisperModel;"
        "from systems.piper_streaming import PiperSpeechEngine;"
        "w=WhisperModel(config.STT_MODEL, device='cpu', compute_type='int8', cpu_threads=7);"
        "p=PiperSpeechEngine(config.PIPER_MODEL_PATH); print('ready', flush=True);"
        "import time; time.sleep(3600)") % str(ROOT)],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, env=dict(os.environ, BEMO_DATA_DIR=str(Path(os.environ.get("BENCH_SCRATCH", "/tmp")) / "bmo-standin"),
                     BEMO_PIPER_MODEL=str(Path("~/.bemo/voice/piper/en_US-bemo-best-v3.onnx").expanduser())))
    stand_in.stdout.readline()
    log("BMO stand-in loaded", {"bmo_process_gb": round(psutil.Process(stand_in.pid).memory_info().rss / 2**30, 2)})

    g = Path("~/BMO-models/gguf").expanduser()
    mai = rb.LlamaCppBackend(str(g / "mai-ui-2b-Q8_0.gguf"), str(g / "mmproj-mai-ui-2b-f16.gguf"), "mai")
    log("MAI-UI 8-bit loaded", {"mai_server_gb": round(psutil.Process(mai.proc.pid).memory_info().rss / 2**30, 2),
                                "ctx": os.environ.get("BENCH_CTX", "8192")})

    sx, sy = rb.SIZE[0] / data["screen"][0], rb.SIZE[1] / data["screen"][1]
    items = data["items"][::4]  # a spread across all apps
    hits, firsts, repeats, seen = 0, [], [], set()
    for item in items:
        img = Image.open(bench / item["image"]).convert("RGB").resize(rb.SIZE, Image.LANCZOS)
        t = time.monotonic()
        reply, resized = mai.ask(img, item["instruction"])
        s = time.monotonic() - t
        (repeats if item["image"] in seen else firsts).append(s)
        seen.add(item["image"])
        raw = rb.numbers(reply)
        x, y, w, h = item["bbox"]
        if raw:
            px, py = rb.to_pixels(raw, "norm999", resized)
            hits += x * sx <= px <= (x + w) * sx and y * sy <= py <= (y + h) * sy
    log("grounding with everything loaded", {
        "targets": len(items), "hits": hits,
        "first_screen_s": round(sorted(firsts)[len(firsts) // 2], 1) if firsts else None,
        "same_screen_s": round(sorted(repeats)[len(repeats) // 2], 1) if repeats else None,
        "mai_server_gb": round(psutil.Process(mai.proc.pid).memory_info().rss / 2**30, 2)})
    log("Main reply with MAI-UI loaded", main_reply())
    log("Main still resident?", {"ollama_ps": subprocess.run(["ollama", "ps"], capture_output=True, text=True).stdout.split("\n")[1][:60]})

    mai.close()
    stand_in.terminate()
    ollama.generate(model="qwen3-main:30b", prompt="", keep_alive=0)
    log("all unloaded")


if __name__ == "__main__":
    main()
