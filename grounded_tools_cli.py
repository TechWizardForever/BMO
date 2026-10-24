#!/usr/bin/env python3
"""Smoke tests for Bemo v1.9 grounded tool suite."""
from __future__ import annotations

import argparse
import json
import tempfile
import time
from pathlib import Path

import config
from systems.diagnostics import HardwareDiagnostics
from systems.instruments import InstrumentManager
from systems.reference_library import TechnicalReferenceLibrary
from systems.telemetry import TelemetryLogger
from systems.vision_tools import VisionToolkit
from systems.engineering import EngineeringToolkit


def smoke() -> dict:
    root = Path(tempfile.mkdtemp(prefix="bemo-grounded-smoke-"))
    results = {}

    # Vision crop + OCR on known text.
    from PIL import Image, ImageDraw
    image_path = root / "ocr.png"
    image = Image.new("RGB", (900, 260), "white")
    draw = ImageDraw.Draw(image)
    draw.text((40, 80), "TDP 65.1 W   CORE 1397 MHz", fill="black")
    image.save(image_path)
    vision = VisionToolkit(root / "vision")
    crop = vision.crop_zoom({"path": str(image_path), "region": {"x": 20, "y": 50, "width": 700, "height": 130}, "scale": 2})
    results["vision_crop"] = crop
    try:
        results["vision_ocr"] = vision.ocr({"path": str(crop["path"]), "scale": 2, "psm": 6, "min_confidence": 0})
    except Exception as exc:
        results["vision_ocr"] = {"optional_error": f"{type(exc).__name__}: {exc}"}

    diagnostics = HardwareDiagnostics()
    results["diagnostics_overview"] = diagnostics.overview()

    # Local reference index.
    manual = root / "manual.txt"
    manual.write_text("Motor controller maximum battery current is 50 A. Phase current is 150 A.\n", encoding="utf-8")
    refs = TechnicalReferenceLibrary(root / "references")
    results["reference_add"] = refs.add(str(manual), "Smoke Manual")
    results["reference_search"] = refs.search("battery current")

    instruments = InstrumentManager(root / "instruments.json")
    results["instrument_profiles"] = instruments.profile_examples()
    results["vesc_packet_hex"] = instruments._vesc_packet(bytes([4])).hex()

    logger = TelemetryLogger(root / "telemetry", instruments)
    session = logger.start([{"name": "cpu", "kind": "system", "metric": "cpu_percent"}], interval_s=0.1, filename="smoke.csv")
    time.sleep(0.28)
    results["logger_stop"] = logger.stop(session["session_id"])
    results["logger_summary"] = logger.summarize(session["path"])
    results["logger_plot"] = logger.plot(session["path"], ["cpu"])

    # Advanced CAD on a simple deterministic STEP cube.
    try:
        import cadquery as cq
        step = root / "cube.step"
        cq.exporters.export(cq.Workplane("XY").box(20, 10, 6), str(step))
        eng = EngineeringToolkit(root / "eng")
        results["cad_inspect"] = eng.inspect_cad({"path": str(step)})
        results["cad_section"] = eng.inspect_cad({"path": str(step), "mode": "section", "plane_origin": [0,0,0], "plane_normal": [0,0,1]})
        results["cad_overhang"] = eng.inspect_cad({"path": str(step), "mode": "overhang", "build_direction": [0,0,1]})
        results["cad_draft"] = eng.inspect_cad({"path": str(step), "mode": "draft", "pull_direction": [0,0,1], "minimum_draft_deg": 1.0})
        results["cad_thickness"] = eng.inspect_cad({"path": str(step), "mode": "thickness", "sample_count": 60})
        results["cad_enclosed"] = eng.inspect_cad({"path": str(step), "mode": "enclosed_volume"})
        results["cad_mass"] = eng.inspect_cad({"path": str(step), "mode": "mass_properties", "density_kg_m3": 1200, "native_length_unit": "mm"})
        step2 = root / "cube2.step"
        cq.exporters.export(cq.Workplane("XY").box(21, 10, 6), str(step2))
        results["cad_compare"] = eng.inspect_cad({"path": str(step), "mode": "compare", "other_path": str(step2), "sample_count": 300})
    except Exception as exc:
        results["cad_advanced_error"] = f"{type(exc).__name__}: {exc}"

    return results


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["smoke"])
    args = parser.parse_args()
    if args.command == "smoke":
        print(json.dumps(smoke(), indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
