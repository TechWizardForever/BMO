#!/usr/bin/env bash
# BMO GUI controller: check what this PC has, and how to build the vision fallback.
#
# Downloads nothing. On a PC where the vision files are missing it prints the
# exact, pinned steps that produced them on the test PC (2026-10-18), so the
# owner can run them knowingly. Exit 0 = everything present and identical.
set -uo pipefail
DIR="${BEMO_GUI_VISION_DIR:-$HOME/.bemo/models/gui}"
HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd -- "$HERE/../.." && pwd)"
missing=0

check_file() {  # path, sha256, what
  if [ ! -f "$DIR/$1" ]; then echo "MISSING   $1 ($3)"; missing=1; return; fi
  if [ "$(sha256sum "$DIR/$1" | cut -d' ' -f1)" = "$2" ]; then echo "OK        $1 ($3)"
  else echo "DIFFERENT $1 ($3): not the tested build; BMO will still try it"; fi
}

echo "== Accessibility and input (needed for all GUI commands)"
for tool in wmctrl xdotool; do
  command -v "$tool" >/dev/null && echo "OK        $tool" || { echo "MISSING   $tool (sudo apt-get install $tool)"; missing=1; }
done
/usr/bin/python3 -c "import pyatspi" 2>/dev/null && echo "OK        pyatspi (system Python)" \
  || { echo "MISSING   pyatspi (sudo apt-get install python3-pyatspi at-spi2-core)"; missing=1; }
[ -n "${DISPLAY:-}" ] && echo "OK        X11 display $DISPLAY" || echo "NOTE      no DISPLAY here; run this from the desktop session"
[ "${XDG_SESSION_TYPE:-x11}" = "wayland" ] && echo "NOTE      Wayland: accessibility works, clicking by sight does not (X11 only)"

echo "== Vision fallback files in $DIR"
check_file mai-ui-2b-Q8_0.gguf 1074653577f93d3c3fc1576ea1a6a9cec4d33345556f0bd022a8268f663c94e0 "MAI-UI-2B, 8-bit, 1.8 GB"
check_file mmproj-mai-ui-2b-f16.gguf 8a6951c3f7708402c6a939e69293a1cf6769c80cb88b92a1f3f1de22bda6adce "vision projector, 0.8 GB"
check_file llama.cpp/llama-server 39dac4b405bb24c908b1e8310857c1a94e4cfba585777aea3b89fdd2400ea7c2 "llama.cpp b11389 server"

echo "== Memory"
python3 - <<'PY'
import os
total = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 2**30
need = float(os.environ.get("BEMO_GUI_VISION_MIN_TOTAL_GIB", "28"))
print(f"{'OK' if total >= need else 'TOO SMALL':9s} {total:.1f} GiB installed (vision fallback needs {need:.0f}; "
      "everything else in GUI control works without it)")
PY

if [ "$missing" = 0 ]; then echo "== Ready."; exit 0; fi
cat <<STEPS

== To build the vision fallback on this PC (about 4.4 GB of downloads; run these yourself)
L=\$HOME/BMO-models/llama.cpp; M=\$HOME/BMO-models; mkdir -p \$L "$DIR"
# 1. llama.cpp b11389 (CPU build + source for the converter); check the SHA-256 sums:
curl -L -o \$L/bin.tar.gz https://github.com/ggml-org/llama.cpp/releases/download/b11389/llama-b11389-bin-ubuntu-x64.tar.gz
#    6366c732bf2b3198b6263cc1954d3e24e344bd1c545561258c4dc13707b064d0
curl -L -o \$L/src.tar.gz https://github.com/ggml-org/llama.cpp/archive/refs/tags/b11389.tar.gz
#    (the test PC used llama.cpp-b11389-src.tar.gz, ee3bf1be27e9eca5c8ec0b83118e9cc61bfb4a820afa9e9ed7b13b3517339666)
mkdir -p \$L/bin \$L/src && tar -xzf \$L/bin.tar.gz -C \$L/bin --strip-components=1 && tar -xzf \$L/src.tar.gz -C \$L/src --strip-components=1
# 2. MAI-UI-2B official weights, pinned commit (Tongyi-MAI/MAI-UI-2B on Hugging Face, ~4.3 GB):
#    https://huggingface.co/Tongyi-MAI/MAI-UI-2B/tree/503050934809558c8dfd2ddedaf9621fa74ac2de -> \$M/MAI-UI-2B
# 3. Convert with BMO's Python (the shim stands in for sentencepiece, which Qwen-family models never use):
cd \$M && PYTHONPATH=\$L/src/gguf-py:$HERE $REPO/.venv/bin/python \$L/src/convert_hf_to_gguf.py MAI-UI-2B --outtype f16 --outfile mai-ui-2b-f16.gguf
PYTHONPATH=\$L/src/gguf-py:$HERE $REPO/.venv/bin/python \$L/src/convert_hf_to_gguf.py MAI-UI-2B --mmproj --outtype f16 --outfile "$DIR/mmproj-mai-ui-2b-f16.gguf"
LD_LIBRARY_PATH=\$L/bin \$L/bin/llama-quantize mai-ui-2b-f16.gguf "$DIR/mai-ui-2b-Q8_0.gguf" Q8_0
cp -a \$L/bin "$DIR/llama.cpp"
# 4. Run this script again.
STEPS
exit 1
