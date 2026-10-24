# Deep Technical Vision qualification notes

Locked model: InternVL3.5-30B-A3B.

Why it was selected: direct CPU-only tests compared Qwen3-VL-30B-A3B-Thinking, InternVL3.5-30B-A3B, and Mistral Small 3.1 24B on dense technical imagery. Qwen reasoned well but repeatedly mis-grounded CAD/diagram features and tiny labels. Mistral failed catastrophically on the HWiNFO test by hallucinating a different modern PC and nonexistent temperatures/power/utilization. InternVL was materially better on dense exploded-diagram OCR and much faster in prompt processing, so it became the best practical candidate despite remaining grounding weaknesses.

Observed InternVL failure modes to guard against:
- Dense legends can still suffer a skipped line that shifts several following callout identities.
- Similar small words can be confused (for example pivot/pilot), and nearby gear labels can be swapped.
- On diagnostic screens it once misread a 65.1 W CPU TDP specification as a temperature and treated a reference clock as a current clock, which led to an unsupported throttling diagnosis.
- Mechanical reasoning is useful once the visual facts are correct, but it can compound an early OCR/label mistake.

Policy consequence: ground labels/values/units/columns/geometry first, cross-check important pairings, distinguish live readings from specs/limits, mark unclear details uncertain or request a crop, and only then perform engineering reasoning. Never diagnose from a value whose label/unit/state is uncertain.

Representative CPU-only results on the user's test machine:
- Exploded gearbox/clutch diagram: 20.8 tok/s prompt, 6.1 tok/s generation. Much better legend grounding than Qwen, with localized OCR errors around several adjacent entries.
- HWiNFO technical screen: 19.1 tok/s prompt, 6.5 tok/s generation. Fast, but exposed the TDP-vs-temperature/current-vs-spec grounding failure described above.
