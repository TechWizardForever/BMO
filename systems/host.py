"""What computer BMO is on, and what he can do there (portability plan P1).

Read once at startup from the OS, environment and installed tools; everything
that needs to know asks here instead of probing on its own. The capability
report says plainly what works, what doesn't and why (with the package to
install), so BMO never claims or attempts something the computer can't do.
Linux only (decided 2026-10-19).
"""
from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

MIN_RAM_GIB = 27.5  # "28 GB" machines report ~28.2 GiB; BMO's models need it (a deliberate minimum)

# Tool -> Debian/Ubuntu package that provides it (named in "install X" advice).
PACKAGES = {
    "wmctrl": "wmctrl", "xdotool": "xdotool", "xrandr": "x11-xserver-utils", "pactl": "pulseaudio-utils",
    "xfconf-query": "xfconf", "gsettings": "libglib2.0-bin", "nmcli": "network-manager",
    "xprintidle": "xprintidle", "gtk-launch": "libgtk-3-bin", "pkexec": "policykit-1", "ollama": "ollama",
    "pyatspi": "python3-pyatspi at-spi2-core", "systemctl": "systemd", "lightdm": "lightdm",
}
TOOLS = ("wmctrl", "xdotool", "xrandr", "pactl", "xfconf-query", "gsettings", "nmcli", "xprintidle",
         "gtk-launch", "pkexec", "systemctl", "ollama", "lightdm", "openscad")


@dataclass(frozen=True)
class Host:
    os_name: str = "Linux"
    distro: str = ""            # PRETTY_NAME, e.g. "Linux Mint 22.3"
    distro_family: str = ""     # ID + ID_LIKE, e.g. "linuxmint ubuntu debian"
    session: str = ""           # x11 / wayland / tty / ""
    desktop: str = ""           # e.g. "XFCE", "GNOME", "KDE"
    init: str = ""              # systemd or ""
    package_manager: str = ""   # apt / dnf / pacman / zypper / ""
    tools: dict = field(default_factory=dict)  # name -> bool
    ram_gib: float = 0.0
    cpu: str = ""

    @property
    def x11(self) -> bool:
        return self.session == "x11"

    @property
    def wayland(self) -> bool:
        return self.session == "wayland"

    def has(self, tool: str) -> bool:
        return bool(self.tools.get(tool))

    @property
    def desktop_name(self) -> str:
        names = {"xfce": "Xfce", "gnome": "GNOME", "kde": "KDE Plasma", "x-cinnamon": "Cinnamon",
                 "cinnamon": "Cinnamon", "mate": "MATE", "lxqt": "LXQt", "budgie": "Budgie"}
        first = self.desktop.split(":")[0].lower()
        return names.get(first, self.desktop.split(":")[0] or "no desktop")

    def describe(self) -> str:
        where = f"{self.distro or self.os_name} with {self.desktop_name}"
        return where + (f" on {self.session.upper() if self.session == 'x11' else self.session.capitalize()}"
                        if self.session in ("x11", "wayland") else "")


def _os_release(text: str | None = None) -> dict:
    if text is None:
        try:
            text = Path("/etc/os-release").read_text(encoding="utf-8")
        except OSError:
            text = ""
    values = {}
    for line in text.splitlines():
        if "=" in line and not line.startswith("#"):
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip().strip('"')
    return values


def _pyatspi_available(python: str = "/usr/bin/python3") -> bool:
    if not (shutil.which(python) or os.path.exists(python)):
        return False
    try:
        return subprocess.run([python, "-c", "import pyatspi"], capture_output=True, timeout=10).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def detect_host(env=None, os_release_text=None, which=shutil.which, pyatspi=None, systemd_dir="/run/systemd/system",
                ram_gib=None, cpu=None) -> Host:
    """The running computer. Every input can be replaced for tests (fake hosts)."""
    env = os.environ if env is None else env
    release = _os_release(os_release_text)
    session = str(env.get("XDG_SESSION_TYPE") or "").lower()
    if not session:
        session = "wayland" if env.get("WAYLAND_DISPLAY") else "x11" if env.get("DISPLAY") else ""
    tools = {name: bool(which(name)) for name in TOOLS}
    tools["pyatspi"] = _pyatspi_available() if pyatspi is None else bool(pyatspi)
    manager = next((m for m in ("apt-get", "dnf", "pacman", "zypper") if which(m)), "")
    if ram_gib is None or cpu is None:
        try:
            from hardware import detect_hardware
            profile = detect_hardware()
            ram_gib = profile.ram_gb if ram_gib is None else ram_gib
            cpu = profile.cpu_name if cpu is None else cpu
        except Exception:
            ram_gib, cpu = ram_gib or 0.0, cpu or ""
    return Host(
        os_name="Linux", distro=release.get("PRETTY_NAME", ""),
        distro_family=" ".join(x for x in (release.get("ID", ""), release.get("ID_LIKE", "")) if x),
        session=session, desktop=str(env.get("XDG_CURRENT_DESKTOP") or env.get("DESKTOP_SESSION") or ""),
        init="systemd" if os.path.isdir(systemd_dir) else "", package_manager=manager.replace("-get", ""),
        tools=tools, ram_gib=float(ram_gib or 0.0), cpu=str(cpu or ""))


@dataclass(frozen=True)
class Capability:
    key: str
    spoken: str        # "open and close apps"
    ok: bool
    why_not: str = ""  # "it needs X11 (this desktop runs Wayland)" / "install wmctrl"
    partial: str = ""  # works, with a limit worth saying


def _install(host: Host, *tools: str) -> str:
    missing = [t for t in tools if not host.has(t)]
    if not missing:
        return ""
    packages = " ".join(dict.fromkeys(PACKAGES.get(t, t) for t in missing))
    return f"install {packages}" if host.package_manager == "apt" else f"it needs {', '.join(missing)}"


def capabilities(host: Host, *, vision_files: bool | None = None) -> list[Capability]:
    """What BMO can do here, in the order he'd say it."""
    caps = []

    def add(key, spoken, ok, why="", partial=""):
        caps.append(Capability(key, spoken, bool(ok), "" if ok else why, partial if ok else ""))

    enough_ram = host.ram_gib >= MIN_RAM_GIB
    add("models", "run my brain models", enough_ram and host.has("ollama"),
        (f"this computer has {host.ram_gib:.0f} GB of memory and I need 28" if not enough_ram
         else _install(host, "ollama")))
    gui = host.x11 or host.wayland
    no_x11 = "it needs an X11 desktop session" + (" (this one runs Wayland)" if host.wayland else "")
    # Launching works on Wayland too (gtk-launch); seeing windows needs X11 (P4, 2026-10-19).
    add("apps", "open, switch and close apps", (host.x11 and host.has("wmctrl")) or host.wayland,
        no_x11 if not host.x11 else _install(host, "wmctrl"),
        partial="but I can't see app windows here, so I can't check, switch or close them" if host.wayland else "")
    add("app_controls", "use buttons, menus and tabs in apps", gui and host.has("pyatspi"),
        "it needs a desktop session" if not gui else _install(host, "pyatspi"),
        partial="only in the window list I can see" if host.wayland else "")
    vision_ram = host.ram_gib >= MIN_RAM_GIB
    vision_ok = host.x11 and host.has("xdotool") and vision_ram and vision_files is not False
    add("click_by_sight", "click things by looking at the screen", vision_ok,
        no_x11 if not host.x11 else _install(host, "xdotool") or
        ("the vision helper's files aren't installed" if vision_files is False else "it needs 28 GB of memory"))
    add("screen", "change the screen resolution and fit it to the TV", host.x11 and host.has("xrandr"),
        "on Wayland the desktop's own display settings own the screen" if host.wayland else
        no_x11 if not host.x11 else _install(host, "xrandr"))
    add("sound", "change the volume and sound output", host.has("pactl"), _install(host, "pactl"))
    desktop = host.desktop.lower()
    xfce = "xfce" in desktop and host.has("xfconf-query")
    gnome = any(d in desktop for d in ("gnome", "budgie", "unity", "pantheon")) and host.has("gsettings")
    add("readability", "change text and pointer size", xfce or gnome,
        f"I don't know how to change that on {host.desktop_name} yet",
        partial="" if xfce else "but not hide the panel")
    # Without xprintidle the screensaver still works, timed from BMO's own window only.
    whole_desktop_idle = host.x11 and host.has("xprintidle")
    add("idle", "start the screensaver when nobody is using the computer", True,
        partial="" if whole_desktop_idle else
        "but I only notice activity in my own window" + (f"; {_install(host, 'xprintidle')} to fix that"
                                                          if host.x11 and _install(host, "xprintidle") else ""))
    add("appliance", "start by myself when the computer turns on", host.init == "systemd",
        "it needs systemd")
    add("cad", "make 3D-printable models", host.has("openscad"), _install(host, "openscad"))
    # Only what BMO can really do now: Wi-Fi by voice is planned (first boot), so it isn't listed yet.
    return caps


def spoken_report(host: Host, caps: list[Capability]) -> str:
    """Two or three short sentences for 'what can you do on this computer?'."""
    ok = {c.key for c in caps if c.ok}

    def join(items):
        return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]
    can = []
    if "models" in ok:
        can.append("run my brain models")
    if {"apps", "app_controls"} <= ok:
        can.append("open and use apps")
    elif "apps" in ok:
        can.append("open and close apps")
    elif "app_controls" in ok:
        can.append("use the buttons in apps")
    if "click_by_sight" in ok:
        can.append("click by looking")
    change = [word for key, word in (("screen", "the screen"), ("sound", "sound"), ("readability", "text size"))
              if key in ok]
    if change:
        can.append("change " + join(change))
    if "appliance" in ok:
        can.append("start by myself")
    text = f"This is {host.describe()}."
    if can:
        text += " Here I can " + (join(can) if len(can) < 3 else ", ".join(can[:-1]) + ", and " + can[-1]) + "."
    apps = next((c for c in caps if c.key == "apps" and c.ok and c.partial), None)
    if apps:
        text += " " + apps.partial.replace("but ", "", 1)[0].upper() + apps.partial.replace("but ", "", 1)[1:] + "."
    cannot = [c for c in caps if not c.ok]
    for c in cannot[:2]:  # one sentence each: two reasons in one sentence ran together (P4)
        text += f" I can't {c.spoken}, because {c.why_not}."
    if len(cannot) > 2:
        text += f" {len(cannot) - 2} more things are in the full report."
    limited = [c for c in caps if c.ok and c.partial and c.key == "idle"]
    if limited and not cannot:
        text += " One limit: for the screensaver " + limited[0].partial.replace("but ", "").replace("; ", ". To fix that, ").replace(" to fix that", "") + "."
    return text


def printed_report(host: Host, caps: list[Capability]) -> str:
    lines = [f"Computer: {host.describe()}", f"CPU: {host.cpu or 'unknown'}   Memory: {host.ram_gib:.1f} GiB",
             f"Init: {host.init or 'unknown'}   Packages: {host.package_manager or 'unknown'}", ""]
    for c in caps:
        mark = "yes " if c.ok else "NO  "
        detail = f" ({c.partial})" if c.partial else f": {c.why_not}" if not c.ok else ""
        lines.append(f"{mark} {c.spoken}{detail}")
    missing = sorted(t for t, ok in host.tools.items() if not ok)
    if missing:
        lines.append("")
        lines.append("Tools not found: " + ", ".join(missing))
    return "\n".join(lines)
