"""GUI grounding benchmark: does a model click inside the right control?

Each backend gets the same 1280x720 screenshots and the same instruction
('Click the "Save" button.'); it must return one point. A hit is a point
inside the control's real bounding box (from AT-SPI, verified against the
pixels). Every model first answers four calibration questions (red dots at
known positions) so its coordinate convention is checked, not assumed.

Usage:
  .venv/bin/python benchmarks/gui_grounding/run_bench.py BACKEND BENCH_DIR [--limit N]
Backends: hf-mai-ui, hf-ui-tars, ollama:<model tag>
Results: BENCH_DIR/results-<backend>.jsonl and a summary line per run.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import resource
import time
from pathlib import Path

from PIL import Image, ImageDraw

SIZE = (1280, 720)

MAI_SYSTEM = """You are a GUI grounding agent.
## Task
Given a screenshot and the user's grounding instruction. Your task is to accurately locate a UI element based on the user's instructions.
First, you should carefully examine the screenshot and analyze the user's instructions,  translate the user's instruction into a effective reasoning process, and then provide the final coordinate.
## Output Format
Return a json object with a reasoning process in <grounding_think></grounding_think> tags, a [x,y] format coordinate within <answer></answer> XML tags:
<grounding_think>...</grounding_think>
<answer>
{"coordinate": [x,y]}
</answer>"""  # verbatim from Tongyi-MAI/MAI-UI src/prompt.py (MAI_MOBILE_SYS_PROMPT_GROUNDING)

TARS_PROMPT = "Output only the coordinate of one point in your response. What element matches the following task: {instruction}"

QWEN_PROMPT = ('{instruction}\nReply with only the click point for that element as JSON: '
               '{{"point_2d": [x, y]}}, using coordinates from 0 to 1000 relative to the image.')


def numbers(text: str) -> tuple[float, float] | None:
    """Last [x, y] / (x, y) / x y pair in the model's answer."""
    answer = re.search(r"<answer>(.*?)</answer>", text, re.S)
    body = answer.group(1) if answer else text
    pairs = re.findall(r"(-?\d+(?:\.\d+)?)\s*[, ]\s*(-?\d+(?:\.\d+)?)", body)
    return (float(pairs[-1][0]), float(pairs[-1][1])) if pairs else None


class HFBackend:
    """Reference transformers implementation on CPU (no torchvision needed)."""

    def __init__(self, path: str, kind: str):
        import torch
        from transformers import AutoImageProcessor, AutoModelForImageTextToText, AutoTokenizer
        torch.set_num_threads(int(os.environ.get("BENCH_THREADS", "12")))
        self.torch, self.kind = torch, kind
        self.ip = AutoImageProcessor.from_pretrained(path, use_fast=False)
        self.tok = AutoTokenizer.from_pretrained(path)
        dtype = torch.float32 if kind == "mai" else torch.bfloat16  # 7B fp32 would not fit in RAM
        self.model = AutoModelForImageTextToText.from_pretrained(path, dtype=dtype).eval()
        # The image-aware template belongs to the processor (bypassed here);
        # UI-TARS's tokenizer only carries a text template.
        self.template = None
        for name in ("chat_template.json", "chat_template.jinja"):
            f = Path(path) / name
            if f.is_file():
                raw = f.read_text()
                self.template = json.loads(raw)["chat_template"] if name.endswith(".json") else raw
                break

    def ask(self, image: Image.Image, instruction: str) -> tuple[str, tuple[int, int]]:
        if self.kind == "mai":
            msgs = [{"role": "system", "content": MAI_SYSTEM},
                    {"role": "user", "content": [{"type": "image"}, {"type": "text", "text": instruction}]}]
        else:
            msgs = [{"role": "user", "content": [{"type": "image"},
                                                 {"type": "text", "text": TARS_PROMPT.format(instruction=instruction)}]}]
        vis = self.ip(images=[image], return_tensors="pt")
        grid = vis["image_grid_thw"]
        n = int(grid.prod()) // (self.ip.merge_size ** 2)
        text = self.tok.apply_chat_template(msgs, add_generation_prompt=True, tokenize=False,
                                            chat_template=self.template)
        text = text.replace("<|image_pad|>", "<|image_pad|>" * n, 1)
        ids = self.tok([text], return_tensors="pt")
        with self.torch.inference_mode():
            out = self.model.generate(**ids, pixel_values=vis["pixel_values"], image_grid_thw=grid,
                                      max_new_tokens=256, do_sample=False)
        reply = self.tok.decode(out[0, ids["input_ids"].shape[1]:], skip_special_tokens=True)
        patch = self.ip.patch_size * self.ip.merge_size
        resized = (int(grid[0, 2]) * self.ip.patch_size, int(grid[0, 1]) * self.ip.patch_size)
        return reply, resized


class OllamaBackend:
    def __init__(self, tag: str):
        import ollama
        self.ollama, self.tag = ollama, tag

    def ask(self, image: Image.Image, instruction: str) -> tuple[str, tuple[int, int]]:
        path = "/tmp/bmo-bench-frame.png"
        image.save(path)
        r = self.ollama.chat(model=self.tag, keep_alive="15m", options={"temperature": 0, "num_predict": 128},
                             messages=[{"role": "user", "content": QWEN_PROMPT.format(instruction=instruction),
                                        "images": [path]}])
        return r.message.content, SIZE


class LlamaCppBackend:
    """llama-server (llama.cpp) with a 4-bit GGUF + vision projector: how BMO would run it."""

    def __init__(self, model: str, mmproj: str, kind: str, port: int = 8091):
        import subprocess
        import urllib.request
        self.kind, self.port, self.urllib = kind, port, urllib.request
        root = Path("~/BMO-models/llama.cpp/bin").expanduser()
        env = dict(os.environ, LD_LIBRARY_PATH=str(root))
        self.proc = subprocess.Popen(
            [str(root / "llama-server"), "-m", model, "--mmproj", mmproj, "--port", str(port),
             "--threads", os.environ.get("BENCH_THREADS", "12"), "--ctx-size", os.environ.get("BENCH_CTX", "8192"), "--temp", "0",
             "--no-warmup"],
            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = time.monotonic() + 300
        while time.monotonic() < deadline:
            try:
                self.urllib.urlopen(f"http://127.0.0.1:{port}/health", timeout=2)
                break
            except OSError:
                time.sleep(1)
        else:
            raise SystemExit("llama-server did not start")

    def ask(self, image: Image.Image, instruction: str) -> tuple[str, tuple[int, int]]:
        import base64
        import io
        buf = io.BytesIO()
        image.save(buf, format="PNG")
        url = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
        text = TARS_PROMPT.format(instruction=instruction) if self.kind == "tars" else instruction
        messages = [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": url}},
                                                 {"type": "text", "text": text}]}]
        if self.kind == "mai":
            messages.insert(0, {"role": "system", "content": MAI_SYSTEM})
        body = json.dumps({"messages": messages, "temperature": 0, "max_tokens": 256}).encode()
        req = self.urllib.Request(f"http://127.0.0.1:{self.port}/v1/chat/completions", data=body,
                                  headers={"Content-Type": "application/json"})
        with self.urllib.urlopen(req, timeout=900) as r:
            reply = json.loads(r.read())["choices"][0]["message"]["content"]
        return reply, SIZE

    def close(self):
        self.proc.terminate()


FIT = {"kx": 1.0, "ky": 1.0}


def to_pixels(raw: tuple[float, float], convention: str, resized: tuple[int, int]) -> tuple[float, float]:
    x, y = raw
    if convention == "fitted":  # scale fitted on the calibration dots (unknown resize)
        return x * FIT["kx"], y * FIT["ky"]
    if convention == "norm999":
        return x / 999 * SIZE[0], y / 999 * SIZE[1]
    if convention == "norm1000":
        return x / 1000 * SIZE[0], y / 1000 * SIZE[1]
    if convention == "resized":  # absolute pixels in the model's resized input
        return x / resized[0] * SIZE[0], y / resized[1] * SIZE[1]
    return x, y  # "pixels": already in the 1280x720 frame


def calibrate(backend) -> tuple[str, float]:
    """Pick the coordinate convention that best explains clicks on known dots."""
    dots = [(160, 120), (1100, 150), (300, 600), (1000, 560)]
    raws, resized = [], SIZE
    for i, (cx, cy) in enumerate(dots):
        img = Image.new("RGB", SIZE, (235, 235, 235))
        ImageDraw.Draw(img).ellipse([cx - 22, cy - 22, cx + 22, cy + 22], fill=(220, 30, 30))
        reply, resized = backend.ask(img, 'Click the red circle.')
        raws.append(numbers(reply))
    pairs = [(raw, dot) for raw, dot in zip(raws, dots) if raw and raw[0] > 0 and raw[1] > 0]
    if pairs:  # least-squares scale through the origin, per axis
        FIT["kx"] = sum(r[0] * d[0] for r, d in pairs) / sum(r[0] ** 2 for r, _ in pairs)
        FIT["ky"] = sum(r[1] * d[1] for r, d in pairs) / sum(r[1] ** 2 for r, _ in pairs)
    best, best_err = "pixels", float("inf")
    for conv in ("pixels", "resized", "norm999", "norm1000", "fitted"):
        errs = []
        for raw, (cx, cy) in zip(raws, dots):
            if raw is None:
                errs.append(1e4)
                continue
            px, py = to_pixels(raw, conv, resized)
            errs.append(((px - cx) ** 2 + (py - cy) ** 2) ** 0.5)
        err = sum(errs) / len(errs)
        if err < best_err:
            best, best_err = conv, err
    return best, best_err


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("backend")
    ap.add_argument("bench")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()
    bench = Path(args.bench).expanduser()
    data = json.loads((bench / "dataset.json").read_text())
    sx, sy = SIZE[0] / data["screen"][0], SIZE[1] / data["screen"][1]
    items = data["items"][: args.limit or None]

    if args.backend == "hf-mai-ui":
        backend = HFBackend(str(Path("~/BMO-models/MAI-UI-2B").expanduser()), "mai")
    elif args.backend == "hf-ui-tars":
        backend = HFBackend(str(Path("~/BMO-models/UI-TARS-1.5-7B").expanduser()), "tars")
    elif args.backend.startswith("llamacpp-"):
        # llamacpp-mai, llamacpp-mai-q8, llamacpp-tars
        kind = "mai" if "mai" in args.backend else "tars"
        stem = {"mai": "mai-ui-2b", "tars": "ui-tars-1.5-7b"}[kind]
        quant = "Q8_0" if args.backend.endswith("-q8") else "Q4_K_M"
        g = Path("~/BMO-models/gguf").expanduser()
        backend = LlamaCppBackend(str(g / f"{stem}-{quant}.gguf"), str(g / f"mmproj-{stem}-f16.gguf"), kind)
    elif args.backend.startswith("ollama:"):
        backend = OllamaBackend(args.backend.split(":", 1)[1])  # "ollama:qwen3-vl-fast:30b" -> "qwen3-vl-fast:30b"
    else:
        raise SystemExit(f"unknown backend {args.backend}")

    convention, cal_err = calibrate(backend)
    print(f"calibration: convention={convention} mean error={cal_err:.0f}px", flush=True)
    safe = args.backend.replace(":", "_").replace("/", "_")
    out = (bench / f"results-{safe}.jsonl").open("w")
    images: dict[str, Image.Image] = {}
    hits, times = 0, []
    for item in items:
        if item["image"] not in images:
            images[item["image"]] = Image.open(bench / item["image"]).convert("RGB").resize(SIZE, Image.LANCZOS)
        x, y, w, h = item["bbox"]
        box = (x * sx, y * sy, (x + w) * sx, (y + h) * sy)
        t = time.monotonic()
        reply, resized = backend.ask(images[item["image"]], item["instruction"])
        seconds = time.monotonic() - t
        raw = numbers(reply)
        point = to_pixels(raw, convention, resized) if raw else None
        hit = bool(point and box[0] <= point[0] <= box[2] and box[1] <= point[1] <= box[3])
        hits += hit
        times.append(seconds)
        row = {"id": item["id"], "instruction": item["instruction"], "hit": hit, "point": point,
               "box": box, "seconds": round(seconds, 1), "reply": reply[-300:]}
        out.write(json.dumps(row) + "\n")
        out.flush()
        print(f"{'HIT ' if hit else 'miss'} {seconds:5.1f}s {item['instruction']}", flush=True)
    peak_gb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024 / 1024
    if isinstance(backend, LlamaCppBackend):
        import psutil
        peak_gb = psutil.Process(backend.proc.pid).memory_info().rss / 1024 ** 3  # server RSS at the end
        backend.close()
    summary = {"backend": args.backend, "convention": convention, "calibration_error_px": round(cal_err),
               "fit": FIT if convention == "fitted" else None,
               "targets": len(items), "hits": hits, "accuracy": round(hits / max(1, len(items)), 3),
               "median_seconds": sorted(times)[len(times) // 2] if times else None,
               "peak_process_gb": round(peak_gb, 1)}
    (bench / f"summary-{safe}.json").write_text(json.dumps(summary, indent=1))
    print("SUMMARY", json.dumps(summary), flush=True)


if __name__ == "__main__":
    main()
