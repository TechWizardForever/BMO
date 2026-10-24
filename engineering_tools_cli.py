#!/usr/bin/env python3
"""Small CLI for validating Bemo's deterministic engineering toolkit."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import config
from systems.engineering import EngineeringToolkit


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["smoke", "call"])
    parser.add_argument("tool", nargs="?")
    parser.add_argument("json_args", nargs="?")
    args = parser.parse_args()
    toolkit = EngineeringToolkit(
        config.ENGINEERING_OUTPUT_ROOT,
        max_file_bytes=config.ENGINEERING_FILE_MAX_BYTES,
        max_text_chars=config.ENGINEERING_TEXT_MAX_CHARS,
    )
    if args.command == "smoke":
        checks = {
            "unit": toolkit.execute("convert_units", {"value": 1, "from_unit": "ft", "to_unit": "mm"}),
            "numeric": toolkit.execute("calculate_python", {"operation": "expression", "expression": "sqrt(3**2+4**2)"}),
            "symbolic": toolkit.execute("solve_math", {"operation": "solve", "expression": "2*x+3=9", "variable": "x", "symbols": ["x"]}),
            "mechanical": toolkit.execute("mechanical_calculator", {"operation": "gear_ratio", "inputs": {"driver_teeth": 10, "driven_teeth": 50, "input_rpm": 3000}}),
            "electrical": toolkit.execute("electrical_calculator", {"operation": "ohms_law", "inputs": {"voltage_v": 12, "current_a": 5}}),
            "thermal": toolkit.execute("thermal_calculator", {"operation": "steady_state_rise", "inputs": {"power_w": 50, "thermal_resistance_k_per_w": 0.8, "ambient_c": 25}}),
        }
        print(json.dumps(checks, indent=2))
        return 0
    if not args.tool or args.json_args is None:
        parser.error("call requires TOOL and JSON_ARGS")
    print(toolkit.execute(args.tool, json.loads(args.json_args)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
