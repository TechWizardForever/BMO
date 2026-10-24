#!/usr/bin/env python3
"""On-device live validation for the finished Bemo machine.

Run after qualified Ollama tags are installed. It performs real text inference,
optionally sends a real image through both configured Vision profiles, and
checks the running Supervisor when available. Voice/microphone interaction still
requires the short manual checklist printed at the end.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import config
import ollama
from systems.supervisor import SupervisorClient


def content(response) -> str:
    try:
        return response.message.content or ""
    except AttributeError:
        return response.get("message", {}).get("content", "") or ""


def installed_names() -> list[str]:
    response = ollama.list()
    models = getattr(response, "models", None)
    if models is None and isinstance(response, dict):
        models = response.get("models", [])
    result = []
    for item in models or []:
        name = getattr(item, "model", None) or getattr(item, "name", None)
        if not name and isinstance(item, dict):
            name = item.get("model") or item.get("name")
        if name:
            result.append(str(name))
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", help="Path to an image used for the Deep Technical Vision live test")
    args = parser.parse_args()

    installed = installed_names()
    print("Installed Ollama models:", ", ".join(installed) or "none")
    for profile_name, profile in config.MODEL_PROFILES.items():
        model = profile["model"]
        print(f"\n[{profile_name}] {model}")
        response = ollama.chat(
            model=model,
            stream=False,
            messages=[{"role": "user", "content": "Reply with exactly: BEMO LIVE OK"}],
            options={"num_predict": 32, "temperature": 0.0, "num_thread": config.OLLAMA_NUM_THREAD},
            keep_alive=profile.get("keep_alive", config.OLLAMA_KEEP_ALIVE),
        )
        print(content(response).strip())

    if args.image:
        image = Path(args.image).expanduser().resolve()
        if not image.is_file():
            raise SystemExit(f"Image does not exist: {image}")
        for profile_name, prompt in (
            ("deep-vision", "Ground yourself in the supplied technical image. Report two clearly visible facts with their labels/units when present, and mark anything unclear instead of guessing."),
        ):
            vision = config.MODEL_PROFILES.get(profile_name)
            if not vision:
                raise SystemExit(f"{profile_name} profile is not configured")
            response = ollama.chat(
                model=vision["model"],
                stream=False,
                messages=[{
                    "role": "user",
                    "content": prompt,
                    "images": [str(image)],
                }],
                options={"num_predict": 256, "temperature": 0.0, "num_thread": config.OLLAMA_NUM_THREAD},
                keep_alive=vision.get("keep_alive", config.OLLAMA_KEEP_ALIVE),
            )
            print(f"\n[{profile_name} image test]")
            print(content(response).strip())

    client = SupervisorClient(config.SUPERVISOR_SOCKET_PATH, timeout=2)
    if client.available():
        print("\n[supervisor]", client.status())
        try:
            computer = client.computer_status()
            print("\n[computer control]", computer)
            if computer.get("enabled"):
                home_listing = client.computer_action("list", {"path": str(Path.home()), "limit": 5}, requester_role="LIVE_VALIDATION")
                print("[computer list-home]", home_listing)
                desktop = client.computer_action("desktop_status", {}, requester_role="LIVE_VALIDATION")
                print("[desktop backend]", desktop)
        except Exception as exc:
            print("[computer control] validation failed:", exc)
    else:
        print("\n[supervisor] not currently reachable; start Bemo normally before the final integrated run")

    print("\nManual final checks:")
    print("1. Speak a normal Main Brain question.")
    print("2. Trigger Main -> Engineering -> Coding handoff and confirm the task checkpoint carries through.")
    print("3. Insert a USB image, ask Bemo to inspect it, and verify real pixels reach Deep Technical Vision when routed.")
    print("4. Kill/restart Bemo mid-task and say 'resume task'.")
    print("5. Reboot/login and confirm the systemd user service starts Bemo.")
    print("6. Ask any model role to find/open/read a harmless local file and verify the Supervisor action result.")
    print("7. Ask Bemo to overwrite a harmless test file; verify it pauses and requires 'confirm computer action'.")
    print("8. If desktop control is desired, take a screenshot and test one harmless mouse/keyboard action in the target X11/XWayland session.")
    print("9. Insert a USB drive and verify find/copy/open/image handoff works across the removable mount.")
    print("10. Say 'backup Bemo to USB' and verify the removable-drive backup.")
    print("11. Give Vision a dense screenshot and verify crop/zoom or OCR is used rather than guessed text when needed.")
    print("12. Ask Main or Engineering for current CPU/RAM/sensor state and verify hardware_diagnostics returns live values.")
    print("13. Add one harmless local manual to the technical reference library, search it, and verify the answer names the document/page locator.")
    print("14. If bench hardware is connected, test read-only serial/SCPI/VESC telemetry and a short background data log.")


if __name__ == "__main__":
    main()
