#!/usr/bin/env python3
"""Empirically choose an efficient Ollama thread count for this Bemo host.

The benchmark deliberately looks for the performance knee instead of blindly
choosing the largest thread count. It tests a few topology-derived candidates,
measures prompt + generation throughput, and (when the Piper voice is available)
measures TTS latency while Ollama is generating. Candidates within 5% of the
best balanced score prefer the lower thread count, leaving CPU headroom for
speech, audio, pygame, and Linux.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import config
from hardware import (
    AUTOTUNE_ALGORITHM_VERSION,
    autotune_path,
    autotune_record_matches,
    read_autotune_record,
)


PERFORMANCE_WINDOW = 0.05
TTS_PENALTY_WEIGHT = 0.50
# v3: 48 tokens x 2 repeats let one lucky run pick 16 SMT threads on the
# 5700U test host, whose decode then swung between 5.7 and 14.7 tok/s.
# Longer runs, three repeats, and scoring the slowest repeat favour the
# speed the user actually hears.
BENCH_NUM_PREDICT = 160
BENCH_REPEATS = 3


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def candidate_thread_counts(physical: int, logical: int) -> list[int]:
    physical = max(1, int(physical))
    logical = max(physical, int(logical))
    if logical > physical:
        midpoint = int(round((physical + logical) / 2.0))
        candidates = [physical, midpoint, logical]
    else:
        candidates = [max(1, int(round(physical * 0.50))), max(1, int(round(physical * 0.75))), physical]
    return sorted({max(1, min(logical, int(v))) for v in candidates})


def _value(response: Any, name: str, default: Any = None) -> Any:
    try:
        value = getattr(response, name)
        if value is not None:
            return value
    except Exception:
        pass
    if isinstance(response, dict):
        return response.get(name, default)
    try:
        return response.get(name, default)
    except Exception:
        return default


def _content(response: Any) -> str:
    message = _value(response, "message", None)
    if message is not None:
        try:
            return str(message.content or "")
        except Exception:
            if isinstance(message, dict):
                return str(message.get("content", "") or "")
    return str(_value(response, "response", "") or "")


def _tps(count: Any, duration_ns: Any, fallback_count: int, wall_seconds: float) -> float:
    try:
        count_f = float(count)
        duration_f = float(duration_ns) / 1_000_000_000.0
        if count_f > 0 and duration_f > 0:
            return count_f / duration_f
    except (TypeError, ValueError, ZeroDivisionError):
        pass
    return float(max(1, fallback_count)) / max(0.001, float(wall_seconds))


def choose_efficient_result(results: list[dict], *, performance_window: float = PERFORMANCE_WINDOW) -> dict:
    """Return the lowest-thread result within the efficient balanced-score window."""
    usable = [r for r in results if float(r.get("generation_tps", 0.0)) > 0]
    if not usable:
        raise ValueError("No usable benchmark results")

    max_gen = max(float(r.get("generation_tps", 0.0)) for r in usable) or 1.0
    max_prompt = max(float(r.get("prompt_tps", 0.0)) for r in usable) or 1.0

    for result in usable:
        gen_ratio = float(result.get("generation_tps", 0.0)) / max_gen
        prompt_ratio = float(result.get("prompt_tps", 0.0)) / max_prompt
        raw = 0.80 * gen_ratio + 0.20 * prompt_ratio
        tts_ratio = result.get("tts_overlap_ratio")
        penalty = 0.0
        if tts_ratio is not None:
            penalty = max(0.0, float(tts_ratio) - 1.0)
        balanced = raw / (1.0 + TTS_PENALTY_WEIGHT * penalty)
        result["normalized_llm_score"] = raw
        result["balanced_score"] = balanced
        result["generation_tps_per_thread"] = float(result.get("generation_tps", 0.0)) / max(1, int(result["threads"]))

    best_score = max(float(r["balanced_score"]) for r in usable)
    floor = best_score * (1.0 - float(performance_window))
    eligible = [r for r in usable if float(r["balanced_score"]) >= floor]
    # This is the key efficiency policy: once performance is effectively tied,
    # keep the smaller CPU footprint rather than chasing a tiny benchmark win.
    return min(eligible, key=lambda r: (int(r["threads"]), -float(r["balanced_score"])))


def _installed_models() -> set[str]:
    import ollama

    response = ollama.list()
    models = _value(response, "models", []) or []
    found: set[str] = set()
    for item in models:
        name = _value(item, "model", None) or _value(item, "name", None)
        if name:
            found.add(str(name))
    return found


def _model_is_installed(model: str) -> bool:
    try:
        installed = _installed_models()
    except Exception:
        return False
    if model in installed:
        return True
    # Ollama may normalize a tag with :latest.
    return any(name == model or name.startswith(model + ":") for name in installed)


def _benchmark_prompt(nonce: str = "") -> str:
    # The nonce leads the prompt so Ollama cannot reuse a cached prefix;
    # v2 measured 3,786 "prompt tok/s" on repeats that were cache hits.
    context = f"Benchmark run {nonce}.\n" + (
        "Bemo is a local workshop assistant that helps with engineering, coding, files, "
        "hardware diagnostics, project memory, and practical problem solving. "
    ) * 8
    return (
        context
        + "\nBenchmark task: give a compact numbered list of practical workshop checks. "
        "Keep generating until the token limit; use short phrases and do not stop early."
    )


def _run_ollama_once(model: str, threads: int, nonce: str = "") -> dict:
    import ollama

    started = time.perf_counter()
    response = ollama.chat(
        model=model,
        stream=False,
        messages=[{"role": "user", "content": _benchmark_prompt(nonce)}],
        options={
            "num_ctx": min(int(config.OLLAMA_NUM_CTX), 2048),
            "num_predict": BENCH_NUM_PREDICT,
            "num_thread": int(threads),
            "temperature": 0.0,
        },
        keep_alive="10m",
    )
    wall = time.perf_counter() - started
    text = _content(response)
    words = max(1, len(text.split()))
    gen_tps = _tps(_value(response, "eval_count"), _value(response, "eval_duration"), words, wall)
    prompt_fallback = max(1, len(_benchmark_prompt().split()))
    prompt_tps = _tps(
        _value(response, "prompt_eval_count"),
        _value(response, "prompt_eval_duration"),
        prompt_fallback,
        wall,
    )
    return {
        "wall_seconds": wall,
        "generation_tps": gen_tps,
        "prompt_tps": prompt_tps,
        "eval_count": int(_value(response, "eval_count", words) or words),
        "prompt_eval_count": int(_value(response, "prompt_eval_count", prompt_fallback) or prompt_fallback),
    }


class TTSProbe:
    def __init__(self):
        self.tts = None
        self.baseline_seconds: float | None = None
        self.error: str | None = None

    def load(self) -> None:
        try:
            from systems.piper_streaming import PiperSpeechEngine
            self.tts = PiperSpeechEngine(config.PIPER_MODEL_PATH)
            # Warm the model once so model initialization is not counted.
            self._synthesize("Bemo hardware tuning warmup.")
            self.baseline_seconds = self._synthesize(
                "Bemo is checking speech responsiveness while choosing an efficient CPU setting."
            )
        except Exception as exc:
            self.error = f"{type(exc).__name__}: {exc}"
            self.tts = None
            self.baseline_seconds = None

    def _synthesize(self, text: str) -> float:
        if self.tts is None:
            raise RuntimeError("TTS probe is unavailable")
        started = time.perf_counter()
        for _ in self.tts.stream(text):
            pass
        return time.perf_counter() - started

    def overlap(self) -> float | None:
        if self.tts is None:
            return None
        return self._synthesize(
            "This sentence measures whether speech slows down while the local language model is working."
        )


def _run_candidate(model: str, threads: int, tts_probe: TTSProbe) -> dict:
    runs: list[dict] = []
    overlap_seconds: float | None = None

    for repeat in range(BENCH_REPEATS):
        result_box: dict[str, Any] = {}
        error_box: list[BaseException] = []

        def llm_job() -> None:
            try:
                result_box.update(_run_ollama_once(model, threads, f"{threads}-{repeat}-{time.time_ns()}"))
            except BaseException as exc:  # preserve original failure for main thread
                error_box.append(exc)

        worker = threading.Thread(target=llm_job, name=f"bemo-autotune-llm-{threads}", daemon=True)
        worker.start()
        # The model is preloaded before candidate testing, so a short delay is
        # enough to overlap TTS with actual generation rather than model loading.
        time.sleep(0.35)
        if repeat == 0 and tts_probe.tts is not None:
            try:
                overlap_seconds = tts_probe.overlap()
            except Exception as exc:
                tts_probe.error = f"{type(exc).__name__}: {exc}"
                tts_probe.tts = None
        worker.join()
        if error_box:
            raise error_box[0]
        runs.append(dict(result_box))

    # Score the slowest repeat: erratic decode is what makes speech stall.
    generation = min(float(r["generation_tps"]) for r in runs)
    generation_median = statistics.median(float(r["generation_tps"]) for r in runs)
    prompt = statistics.median(float(r["prompt_tps"]) for r in runs)
    wall = statistics.median(float(r["wall_seconds"]) for r in runs)
    baseline = tts_probe.baseline_seconds
    overlap_ratio = None
    if overlap_seconds is not None and baseline and baseline > 0:
        overlap_ratio = float(overlap_seconds) / float(baseline)

    return {
        "threads": int(threads),
        "generation_tps": generation,
        "generation_tps_median": generation_median,
        "prompt_tps": prompt,
        "median_wall_seconds": wall,
        "tts_overlap_seconds": overlap_seconds,
        "tts_baseline_seconds": baseline,
        "tts_overlap_ratio": overlap_ratio,
        "repeats": runs,
    }


def _preload(model: str, threads: int) -> None:
    import ollama

    ollama.generate(
        model=model,
        prompt="",
        stream=False,
        keep_alive="10m",
        options={"num_predict": 1, "num_thread": int(threads)},
    )
    # One tiny warmup makes the first measured run less sensitive to cold state.
    ollama.chat(
        model=model,
        stream=False,
        messages=[{"role": "user", "content": "Reply with exactly: ready"}],
        options={"num_ctx": 512, "num_predict": 4, "num_thread": int(threads), "temperature": 0.0},
        keep_alive="10m",
    )


def _record_is_current(record: dict | None) -> bool:
    hw = config.HARDWARE
    return (
        autotune_record_matches(
            record,
            cpu_name=hw.cpu_name,
            physical_cores=hw.physical_cores,
            logical_cores=hw.logical_cores,
        )
        and str(record.get("benchmark_model", "")) == str(config.MODEL_PROFILES[config.MAIN_MODEL_PROFILE]["model"])
        and str(record.get("tts_model", "")) == str(config.TTS_MODEL)
        and str(record.get("tts_profile_signature", "")) == __import__("systems.voice", fromlist=["voice_profile_signature"]).voice_profile_signature()
    )


def run_benchmark(*, force: bool = False, progress=None) -> dict:
    """progress(step, steps, label), when given, follows the stages (the install screen's bar)."""
    progress = progress or (lambda step, steps, label="": None)
    target = autotune_path()
    existing = read_autotune_record(target)
    if not force and _record_is_current(existing):
        return {"ok": True, "skipped": True, "reason": "current", "record": existing}

    hw = config.HARDWARE
    model = str(config.MODEL_PROFILES[config.MAIN_MODEL_PROFILE]["model"])
    if not _model_is_installed(model):
        return {
            "ok": False,
            "skipped": True,
            "reason": f"Main benchmark model is not installed: {model}",
        }

    candidates = candidate_thread_counts(hw.physical_cores, hw.logical_cores)
    print(f"CPU: {hw.cpu_name}")
    print(f"Topology: {hw.physical_cores} physical / {hw.logical_cores} logical")
    print(f"Benchmark model: {model}")
    print(f"Candidate Ollama thread counts: {', '.join(map(str, candidates))}")
    print("Policy: prefer the lower-thread candidate when balanced performance is within 5% of the best.")

    steps = len(candidates) + 2
    progress(0, steps, "loading the main brain")
    _preload(model, candidates[0])

    progress(1, steps, "checking the voice")
    tts_probe = TTSProbe()
    print(f"Loading TTS probe: {config.TTS_MODEL}")
    tts_probe.load()
    if tts_probe.baseline_seconds is not None:
        print(f"TTS baseline: {tts_probe.baseline_seconds:.3f}s")
    else:
        print(f"TTS overlap probe unavailable; using LLM efficiency only. {tts_probe.error or ''}".strip())

    results = []
    for index, threads in enumerate(candidates):
        progress(2 + index, steps, f"testing {threads} threads")
        print(f"Testing {threads} Ollama threads...")
        try:
            row = _run_candidate(model, threads, tts_probe)
        except Exception as exc:
            row = {
                "threads": int(threads),
                "generation_tps": 0.0,
                "prompt_tps": 0.0,
                "error": f"{type(exc).__name__}: {exc}",
            }
        results.append(row)
        if row.get("generation_tps", 0):
            tts_text = "n/a"
            if row.get("tts_overlap_ratio") is not None:
                tts_text = f"{float(row['tts_overlap_ratio']):.2f}x baseline"
            print(
                f"  gen={float(row['generation_tps']):.2f} t/s  "
                f"prompt={float(row['prompt_tps']):.2f} t/s  TTS={tts_text}"
            )
        else:
            print(f"  FAILED: {row.get('error', 'unknown benchmark failure')}")

    progress(steps, steps, "done")
    chosen = choose_efficient_result(results)
    record = {
        "algorithm_version": AUTOTUNE_ALGORITHM_VERSION,
        "created_at": _utc_now(),
        "cpu_name": hw.cpu_name,
        "physical_cores": int(hw.physical_cores),
        "logical_cores": int(hw.logical_cores),
        "ram_gb": float(hw.ram_gb),
        "benchmark_model": model,
        "tts_model": str(config.TTS_MODEL),
        "tts_profile_signature": __import__("systems.voice", fromlist=["voice_profile_signature"]).voice_profile_signature(),
        "tts_probe_error": tts_probe.error,
        "performance_window": PERFORMANCE_WINDOW,
        "tts_penalty_weight": TTS_PENALTY_WEIGHT,
        "chosen_ollama_threads": int(chosen["threads"]),
        "chosen_balanced_score": float(chosen["balanced_score"]),
        "results": results,
    }
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_suffix(target.suffix + ".tmp")
    temp.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temp.replace(target)
    print(f"Selected {record['chosen_ollama_threads']} Ollama threads.")
    print(f"Saved: {target}")
    return {"ok": True, "skipped": False, "record": record}


def print_status() -> int:
    record = read_autotune_record()
    hw = config.HARDWARE
    print(f"Autotune file: {autotune_path()}")
    if not record:
        print("Status: no empirical benchmark saved; topology fallback is active.")
        print(f"Fallback Ollama threads: {hw.physical_cores}")
        return 1
    current = _record_is_current(record)
    print(f"Status: {'current' if current else 'stale for this CPU/model'}")
    print(f"CPU: {record.get('cpu_name')}")
    print(f"Topology: {record.get('physical_cores')} physical / {record.get('logical_cores')} logical")
    print(f"Model: {record.get('benchmark_model')}")
    print(f"Chosen Ollama threads: {record.get('chosen_ollama_threads')}")
    for row in record.get("results", []):
        if not isinstance(row, dict):
            continue
        print(
            f"  {row.get('threads')} threads: gen={float(row.get('generation_tps', 0.0)):.2f} t/s, "
            f"prompt={float(row.get('prompt_tps', 0.0)):.2f} t/s, "
            f"balanced={float(row.get('balanced_score', 0.0)):.3f}"
        )
    return 0 if current else 2


def main() -> None:
    parser = argparse.ArgumentParser(description="Bemo empirical CPU thread tuner")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status", help="Show the saved hardware benchmark")
    run = sub.add_parser("run", help="Run the benchmark and save the efficient thread count")
    run.add_argument("--force", action="store_true", help="Benchmark even if a current result already exists")
    sub.add_parser("ensure", help="Run only when the saved benchmark is missing/stale")
    args = parser.parse_args()

    if args.command == "status":
        raise SystemExit(print_status())
    if args.command == "run":
        result = run_benchmark(force=bool(args.force))
    else:
        result = run_benchmark(force=False)

    if result.get("skipped") and result.get("reason") == "current":
        record = result.get("record") or {}
        print(f"Hardware benchmark is already current: {record.get('chosen_ollama_threads')} Ollama threads.")
        raise SystemExit(0)
    if not result.get("ok"):
        print(f"Hardware benchmark skipped: {result.get('reason', 'unknown reason')}")
        # Missing model is not an installer-fatal condition. A later rerun of
        # install.sh or an explicit `hardware_autotune.py run` can complete it.
        raise SystemExit(0)


if __name__ == "__main__":
    main()
