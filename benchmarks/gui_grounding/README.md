# GUI grounding benchmark

Measures whether a model clicks inside the right control on this desktop.

- `capture.py` (system Python, needs `pyatspi`): opens GTK apps maximized, takes a
  screenshot, and reads exact control boxes from the accessibility tree. LibreOffice
  is excluded (its accessibility boxes are ~50 px off); xfce4-taskmanager exposes none.
- `run_bench.py` (BMO venv): runs one backend over the set at 1280x720 after a
  red-dot calibration that checks the model's coordinate convention.

Data and results live outside the repo in `~/BMO-models/bench/` (screenshots of the
owner's desktop). Model weights: `~/BMO-models/` (official Hugging Face releases,
pinned commits, fetched by `~/BMO-models/download.sh`).

Backends: `hf-mai-ui`, `hf-ui-tars` (transformers on CPU: reference accuracy, slow),
`ollama:<tag>` (deployment runtime, e.g. `ollama:qwen3-vl-fast:30b`).
