# Bemo model profiles and qualification status

The active role files in this directory are deliberately compact model profiles: roughly four sentences covering role, key failure guardrail, and escalation/quality behavior. Main also receives the compact `main_personality.md` identity/personality block. Shared routing, handoff, project-memory, file, and computer-control rules live in `shared_handoff.md` so they are not repeated in every role profile.

The longer benchmark-derived policies used during qualification are preserved under `benchmark_notes/` for reference and future requalification, but they are not injected into every model call.

| Role | Profile | Benchmark model | Status | Active compact profile | Detailed qualification notes |
|---|---|---|---|---|---|
| Main Brain | `normal` | Qwen3-30B-A3B-Instruct-2507 | locked | `main_personality.md` + `main_brain.md` | `benchmark_notes/main_brain_detailed.md` |
| Emergency / Recovery Brain | `emergency` | Qwen3-4B-Instruct-2507 | fallback | `emergency.md` | none |
| Heavy Coding | `coding` | Qwen3-Coder-30B-A3B | locked | `heavy_coding.md` | `benchmark_notes/heavy_coding_detailed.md` |
| Engineering / Deep Reasoning | `engineering` | Qwen3-30B-A3B-Thinking-2507 | locked | `engineering.md` + `engineering_tools.md` | `benchmark_notes/engineering_detailed.md` |
| Fast / General Vision | none | Qwen3-VL-30B-A3B-Instruct | dropped 2026-10-24 | none | `benchmark_notes/fast_vision_detailed.md` |
| Fast Coding | none | Qwen2.5-Coder-3B | dropped | none | none |
| Deep Technical Vision | `deep-vision` | InternVL3.5-30B-A3B | locked | `deep_technical_vision.md` | `benchmark_notes/deep_technical_vision_detailed.md` |

Fast Vision was dropped on 2026-10-24 (19 GB, unused in a week; InternVL was better on dense technical images), so Deep Technical Vision now takes every image. Before that, Fast Vision passed its final ordinary-UI validation on a normal Amazon homepage screenshot. It accurately grounded the visible page structure, labels, products and prices without a material hallucination; measured CPU-only speed was 9.8 tok/s prompt processing and 5.4 tok/s generation.

Deep Technical Vision is now locked to InternVL3.5-30B-A3B. Its compact profile specifically guards against the two observed failure classes: shifted/misread dense labels and confusing live sensor readings with specifications or limits.

Engineering also receives `engineering_tools.md`, a short tool-use policy layered beside the compact role profile. It directs exact math, unit conversion, deeper CAD inspection, live hardware diagnostics, local manuals/datasheets, read-only bench telemetry, experiment logging/plots, and published specifications through deterministic tools rather than mental arithmetic or invented values. Deep Vision gets no tools (Ollama refuses them for InternVL), but must cross-check OCR against the actual pixels.

The Main Brain and escalation-capable specialists return a routing request only as an exact single line:

`ROUTE: ROLE_NAME`

The runtime consumes that signal locally. `systems/routing.py` accepts only a whole-response route signal, and `config.MODEL_ROUTE_TO_PROFILE` maps stable role labels to local model profiles. This allows models to be replaced later without rewriting routing policy.

Fast Coding was removed after repeated failures to verify its own executable code and claimed outputs, even after policy tightening. Tiny isolated coding stays on the Main Brain; substantive coding routes to Heavy Coding.

All active role prompts include `shared_handoff.md`. It defines the cross-role checkpoint/route protocol, project-fact supersession behavior, safe file/image/computer-control rules, and the v1.9 tool-escalation rule. A role should use deterministic tools instead of guessing; when the required capability belongs elsewhere it can call `request_capability_handoff`, which checkpoints the reason and routes to the owning specialist automatically.


### V1.12 prompt-prefix layout

The role/personality/system policy is an immutable first message. Slow-changing durable conversation summary follows it, then bounded prior conversation. Query-specific task handoff state, project retrieval, and older semantic-memory snippets are placed at the tail immediately before the live user request. This does not make Ollama permanently learn a policy; it keeps the largest practical prompt prefix byte-for-byte stable so Ollama can reuse its resident prompt/KV cache instead of forcing dynamic retrieval to invalidate the whole prefix every turn. Fresh task records are not injected until they contain material handoff/resume/checkpoint/asset state, avoiding a redundant per-turn cache break.
