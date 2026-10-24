#!/usr/bin/python3
"""Build a labelled GUI-grounding test set from real apps on this desktop.

Each app is opened maximized, screenshotted, and its visible named controls
are read from the accessibility tree (AT-SPI) to get exact bounding boxes, so
answers are ground truth rather than hand-drawn. Nothing is clicked or typed;
each window is closed afterwards. Uses the system Python (pyatspi).

Usage: /usr/bin/python3 capture.py OUT_DIR [--per-app 8]
"""
import argparse
import json
import random
import subprocess
import time
from pathlib import Path

import pyatspi

APPS = [  # (desktop id, accessibility name substring, window class substring)
    ("org.gnome.Calculator", "calculator", "gnome-calculator"),
    ("thunar", "thunar", "thunar"),
    ("org.x.editor", "xed", "xed"),
    ("xfce-settings-manager", "xfce4-settings-manager", "xfce4-settings-manager"),
    ("pavucontrol", "pavucontrol", "pavucontrol"),
    ("gucharmap", "gucharmap", "gucharmap"),
    ("org.gnome.baobab", "baobab", "baobab"),
]
# Excluded after checking boxes against pixels (2026-10-18): LibreOffice reports
# accessibility coordinates ~50 px off, and xfce4-taskmanager exposes no controls.
ROLE_NOUN = {
    "push button": "button", "toggle button": "toggle button", "check box": "checkbox",
    "radio button": "option", "menu": "menu", "page tab": "tab", "combo box": "drop-down",
}


def windows():
    out = subprocess.run(["wmctrl", "-lx"], capture_output=True, text=True).stdout
    return {line.split()[0]: line.split()[2].lower() for line in out.splitlines() if len(line.split()) > 2}


def wait_new_window(before, cls, timeout=40):
    end = time.time() + timeout
    while time.time() < end:
        for wid, wclass in windows().items():
            if wid not in before and cls in wclass:
                return wid
        time.sleep(0.5)
    return None


def controls(app_node, screen_w, screen_h):
    found = []

    def walk(node, depth=0):
        if node is None or depth > 40 or len(found) > 2000:
            return
        try:
            role, name = node.getRoleName(), (node.name or "").strip()
            states = node.getState()
            if (role in ROLE_NOUN and name and len(name) <= 40
                    and states.contains(pyatspi.STATE_SHOWING) and states.contains(pyatspi.STATE_VISIBLE)):
                e = node.queryComponent().getExtents(pyatspi.DESKTOP_COORDS)
                # (0,0) is AT-SPI's placeholder for titlebar buttons drawn by
                # the app itself (Character Map); those boxes are not real.
                if e.width >= 8 and e.height >= 8 and e.x >= 0 and e.y >= 0 and (e.x, e.y) != (0, 0) \
                        and e.x + e.width <= screen_w and e.y + e.height <= screen_h:
                    found.append({"name": name, "role": role, "bbox": [e.x, e.y, e.width, e.height]})
        except Exception:
            pass
        for i in range(node.childCount):
            try:
                walk(node.getChildAtIndex(i), depth + 1)
            except Exception:
                pass

    walk(app_node)
    return found


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--per-app", type=int, default=8)
    args = ap.parse_args()
    out = Path(args.out).expanduser()
    out.mkdir(parents=True, exist_ok=True)
    dims = subprocess.run(["xdotool", "getdisplaygeometry"], capture_output=True, text=True).stdout.split()
    screen_w, screen_h = int(dims[0]), int(dims[1])
    rng = random.Random(1288)
    dataset = []
    for desktop_id, a11y_name, cls in APPS:
        before = windows()
        subprocess.Popen(["gtk-launch", desktop_id], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        wid = wait_new_window(before, cls)
        if not wid:
            print(f"skip {desktop_id}: no window")
            continue
        subprocess.run(["wmctrl", "-ir", wid, "-b", "add,maximized_vert,maximized_horz"])
        subprocess.run(["wmctrl", "-ia", wid])
        time.sleep(3.0)
        image = out / f"{desktop_id}.png"
        subprocess.run(["scrot", "-o", str(image)])
        desk = pyatspi.Registry.getDesktop(0)
        app = None
        for i in range(desk.childCount):
            node = desk.getChildAtIndex(i)
            if node is not None and a11y_name in (node.name or "").lower():
                app = node
        found = controls(app, screen_w, screen_h) if app else []
        counts = {}
        for c in found:
            counts[c["name"].lower()] = counts.get(c["name"].lower(), 0) + 1
        unique = [c for c in found if counts[c["name"].lower()] == 1]  # unambiguous targets only
        picked = rng.sample(unique, min(args.per_app, len(unique)))
        for c in picked:
            dataset.append({
                "id": f"{desktop_id}:{len(dataset)}", "app": desktop_id, "image": image.name,
                "instruction": f'Click the "{c["name"]}" {ROLE_NOUN[c["role"]]}.',
                "name": c["name"], "role": c["role"], "bbox": c["bbox"],
            })
        print(f"{desktop_id}: {len(found)} controls, {len(unique)} unique, picked {len(picked)}")
        subprocess.run(["wmctrl", "-ic", wid])
        time.sleep(2.0)
    (out / "dataset.json").write_text(json.dumps({"screen": [screen_w, screen_h], "items": dataset}, indent=1))
    print(f"{len(dataset)} targets -> {out / 'dataset.json'}")


if __name__ == "__main__":
    main()
