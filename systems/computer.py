"""Logged-in-user computer control for Bemo.

All actions are executed by the parent Supervisor, never directly by an LLM.
The controller intentionally runs with the permissions of the Bemo OS user.
Privileged actions use an explicit admin path (pkexec) after confirmation rather
than giving models permanent root access.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import time
from pathlib import Path
from typing import Any

import psutil

from systems.gui_control import (
    CHANGE, CRITICAL, NAVIGATE, action_summary, classify, compact_view, host_accessibility_backend,
    snapshot_changed, verify,
)


def _bemo_process_tree() -> set[int]:
    """This process, its parents (the terminal) and children (the BMO app under the Supervisor)."""
    pids = set()
    try:
        me = psutil.Process()
        pids.update(p.pid for p in [me, *me.parents(), *me.children(recursive=True)])
    except psutil.Error:
        pids.add(os.getpid())
    return pids


class ComputerControlError(RuntimeError):
    pass


class ComputerController:
    """Execute bounded computer actions as the logged-in user.

    ``test_root`` is only for offline tests; production leaves it ``None`` so
    normal OS permissions define what Bemo can read/write.
    """

    VIRTUAL_WRITE_ROOTS = (Path("/proc"), Path("/sys"), Path("/dev"))
    PRIVILEGE_WRAPPERS = {"sudo", "su", "doas", "pkexec"}
    DESTRUCTIVE_COMMANDS = {
        "rm", "rmdir", "shred", "wipefs", "mkfs", "mkfs.ext4", "mkfs.xfs",
        "fdisk", "sfdisk", "parted", "gdisk", "sgdisk", "dd",
        "shutdown", "reboot", "poweroff", "halt", "init",
        "chmod", "chown", "chgrp", "setfacl",
        "apt", "apt-get", "dnf", "yum", "pacman", "zypper", "rpm", "dpkg",
        "useradd", "userdel", "usermod", "groupadd", "groupdel", "passwd",
        "ufw", "iptables", "nft", "firewall-cmd",
        "cp", "mv", "install", "truncate", "tee", "ln", "unlink",
        "mount", "umount", "losetup", "kill", "pkill", "killall",
        "service", "crontab", "at", "nmcli", "hostnamectl", "timedatectl", "localectl",
    }
    INPUT_ACTIONS = {"type_text", "key_press", "hotkey", "mouse_click"}
    MAX_TYPE_TEXT_CHARS = 2000
    SHELLS = {"sh", "bash", "zsh", "fish", "dash", "ksh", "csh", "tcsh"}
    INTERPRETERS = {"python", "python3", "perl", "ruby", "node", "php", "lua", "awk", "gawk", "mawk", "nawk",
                    "tclsh", "rscript", "deno", "bun", "julia", "java", "dotnet", "wine", "wine64", "pypy3"}
    # Safety review 2026-10-19. Wrappers run another command, which is what gets assessed:
    # "env bash -c ..." and "nohup rm ..." used to run without a question.
    COMMAND_WRAPPERS = {"env", "nohup", "timeout", "nice", "ionice", "setsid", "stdbuf", "time", "unbuffer",
                        "systemd-inhibit", "dbus-run-session", "xvfb-run", "command", "exec"}
    # Run commands in ways too varied to unwrap: always asked.
    INDIRECT_RUNNERS = {"xargs", "watch", "flock", "taskset", "chrt", "strace", "ltrace", "systemd-run", "script",
                        "busybox", "parallel", "batch", "sg", "newgrp", "runuser", "chroot", "unshare", "nsenter",
                        "firejail", "bwrap", "proot", "expect", "gdb", "make", "ninja"}
    # Downloads and anything that sends data off this computer.
    NETWORK_TOOLS = {"curl", "wget", "aria2c", "scp", "sftp", "ssh", "rsync", "nc", "ncat", "netcat", "socat",
                     "telnet", "ftp", "lftp", "rclone", "yt-dlp", "youtube-dl"}
    PACKAGE_TOOLS = {"pip", "pip3", "pipx", "npm", "npx", "pnpm", "yarn", "cargo", "gem", "flatpak", "snap",
                     "conda", "mamba", "uv", "go"}
    PACKAGE_READ_ONLY = {"list", "show", "freeze", "search", "info", "version", "--version", "-v", "-V", "help",
                         "--help", "check", "outdated", "ls", "view", "env", "doctor"}
    # Keyboard/mouse tools would bypass the type_text/key_press approvals; secret stores hold passwords.
    ASKED_TOOLS = {"xdotool", "ydotool", "wtype", "xte", "xvkbd", "xkill", "secret-tool", "pass", "gpg", "gpg2",
                   "ssh-keygen", "ssh-add", "patch", "chattr", "trash-rm", "gnome-session-quit",
                   "xfce4-session-logout"}
    CRITICAL_TOOLS = {"trash-empty"}
    GIT_READ_ONLY = {"status", "log", "diff", "show", "rev-parse", "ls-files", "blame", "describe", "grep",
                     "shortlog", "--version", "version", "help"}
    SYSTEMCTL_READ_ONLY = {"status", "list-units", "list-unit-files", "list-timers", "is-active", "is-enabled",
                           "is-failed", "show", "cat", "list-dependencies", "--version"}
    # Home-relative places a file can make something run at login, or that hold keys.
    STARTUP_PATHS = (".config/autostart", ".config/systemd", ".local/share/systemd", ".config/environment.d",
                     ".local/bin", "bin", ".local/share/applications", ".config/xfce4/xfconf", ".config/dconf",
                     ".ssh", ".gnupg", ".bashrc", ".bash_profile", ".bash_login", ".bash_logout", ".bash_aliases",
                     ".profile", ".zshrc", ".zprofile", ".zshenv", ".xprofile", ".xsession", ".xsessionrc",
                     ".xinitrc", ".Xresources", ".pam_environment", ".config/fish/config.fish")
    # Home-relative places with private keys, passwords and browser logins: never read by a model.
    SECRET_PATHS = (".ssh", ".gnupg", ".password-store", ".local/share/keyrings", ".mozilla", ".thunderbird",
                    ".config/google-chrome", ".config/chromium", ".config/BraveSoftware", ".config/microsoft-edge",
                    ".config/vivaldi", ".config/opera", ".netrc", ".git-credentials", ".aws", ".config/gcloud",
                    ".docker/config.json", ".kube", ".config/gh", ".pgpass", ".my.cnf", ".config/rclone")

    def __init__(
        self,
        *,
        screenshot_dir: str | Path,
        action_timeout_seconds: float = 30.0,
        output_max_chars: int = 40_000,
        max_text_read_bytes: int = 2_000_000,
        protected_paths: list[str | Path] | tuple[str | Path, ...] = (),
        test_root: str | Path | None = None,
        guarded_paths: list[str | Path] | tuple[str | Path, ...] = (),
        python_executable: str | None = None,
        gui_backend=None,
        protected_pids=None,
    ):
        self.screenshot_dir = Path(screenshot_dir).expanduser()
        self.action_timeout_seconds = max(1.0, float(action_timeout_seconds))
        self.output_max_chars = max(1000, int(output_max_chars))
        self.max_text_read_bytes = max(4096, int(max_text_read_bytes))
        self.protected_paths = [Path(x).expanduser().resolve(strict=False) for x in protected_paths]
        self.test_root = Path(test_root).resolve() if test_root is not None else None
        # BMO's own code and data: changing them through computer_control is always asked (critical).
        self.guarded_paths = [Path(x).expanduser().resolve(strict=False) for x in guarded_paths]
        self.python_executable = python_executable
        self._gui_backend = gui_backend
        self._protected_pids = protected_pids or _bemo_process_tree

    def _gui(self):
        if self._gui_backend is None:
            from systems.adapters import accessibility
            self._gui_backend = accessibility() or False
        if not self._gui_backend:
            raise ComputerControlError("GUI control isn't available on this computer yet (no accessibility backend).")
        return self._gui_backend

    def _gui_assess(self, payload: dict) -> dict:
        try:
            pid = int(payload.get("pid"))
        except (TypeError, ValueError):
            return {"blocked": True, "summary": "GUI action needs the target app's pid."}
        if pid in self._protected_pids():
            return {"blocked": True, "summary": "I won't operate my own window or the terminal I'm running in."}
        control = dict(payload.get("expect") or {})
        action = str(payload.get("action") or "")
        tier = classify(control, action)
        if tier is None:
            return {"blocked": True, "summary": "BMO never types into password fields."}
        summary = action_summary(control, action, str(payload.get("app") or "the app"), payload.get("value"))
        if tier <= NAVIGATE:
            return {"confirmation_required": False, "tier": tier}
        return {"confirmation_required": True, "critical": tier == CRITICAL, "tier": tier, "summary": summary}

    def gui_windows(self, payload: dict) -> dict:
        return self._gui().windows()

    def _point_window(self, payload: dict):
        """The window of payload['pid'] that contains the point, or an error string."""
        from systems.gui_vision import window_regions
        try:
            pid, x, y = int(payload.get("pid")), int(payload.get("x")), int(payload.get("y"))
        except (TypeError, ValueError):
            return "a point click needs pid, x and y"
        if pid in self._protected_pids():
            return "I won't click in my own window or the terminal I'm running in."
        for window_id, (wx, wy, ww, wh), title in window_regions(pid):
            if wx <= x < wx + ww and wy <= y < wy + wh:
                return window_id, (wx, wy, ww, wh), title
        return f"that point is not inside {payload.get('app') or 'the app'}'s window"

    def gui_click_point(self, payload: dict) -> dict:
        """Milestone 4: click a point found by looking at the screen, then compare before/after.

        The pointer rests on the point before the 'before' image, so a hover
        highlight is not mistaken for the click working.
        """
        from systems.gui_vision import (
            active_window_id, capture, changed_pixels, signature, signature_distance, window_regions,
        )
        found = self._point_window(payload)
        if isinstance(found, str):
            raise ComputerControlError(found)
        window_id, region, _title = found
        x, y = int(payload["x"]), int(payload["y"])
        # A screen click lands on whatever is on top. Bench 2026-10-18: another
        # window came to the front during the ~35 s of looking and got the click.
        self._desktop().activate(window_id)
        time.sleep(0.4)
        if active_window_id() != int(window_id, 16):
            raise ComputerControlError("I couldn't bring that window to the front, so I didn't click")
        expected = payload.get("seen_signature")
        if expected:
            drift = signature_distance(signature(capture(region)), list(expected))
            if drift > float(payload.get("max_drift", 18.0)):
                raise ComputerControlError(f"the window looks different from when I looked (change {drift:.0f}), "
                                           "so I didn't click")
        self._input(lambda d: d.move(x, y))
        time.sleep(0.35)
        before = capture(region)
        self._input(lambda d: d.click(1))
        time.sleep(float(payload.get("settle", 0.9)))
        if not any(w[0] == window_id for w in window_regions(int(payload["pid"]))):
            return {"ok": True, "verified": True, "note": "the window closed"}
        after = capture(region)
        local = (x - region[0], y - region[1])
        near, whole = changed_pixels(before, after, around=local), changed_pixels(before, after)
        # Pixel counts, not shares of the window: a line of new text was only
        # ~0.25% of a 1280x942 window (bench 2026-10-18). The "before" image
        # already has the hover look, so a checkbox tick (~200 px) counts.
        verified = near >= 120 or whole >= 400
        note = (f"the screen changed ({whole} pixels, {near} near the click)" if verified
                else f"nothing on screen changed after the click ({whole} pixels)")
        return {"ok": True, "verified": verified, "note": note, "changed_pixels": whole, "changed_near": near}

    def gui_close(self, payload: dict) -> dict:
        """Ask an app's windows to close (like the title-bar X), then report what happened.

        Window decorations are not in the accessibility tree, so this goes via
        the window manager. An app that asks about unsaved work keeps its
        window: that is reported, never treated as closed.
        """
        pid = int(payload["pid"])
        if pid in self._protected_pids():
            raise ComputerControlError("I won't close my own window or the terminal I'm running in.")
        desk = self._desktop()
        ok, why = desk.available()
        if not ok:
            raise ComputerControlError(f"closing windows isn't possible here: {why}")
        ids = desk.window_ids(pid)
        if not ids:
            return {"ok": False, "error": "that app has no window to close"}
        for window_id in ids:
            desk.close(window_id)
        deadline = time.monotonic() + float(payload.get("wait", 3.0))
        while time.monotonic() < deadline:
            time.sleep(0.3)
            if not set(ids) & desk.all_window_ids():
                return {"ok": True, "verified": True, "note": "the window closed"}
        return {"ok": True, "verified": False,
                "note": "the window is still open; it may be asking about unsaved work (look at it again)"}

    def gui_snapshot(self, payload: dict) -> dict:
        snap = self._gui().snapshot(int(payload["pid"]), int(payload.get("limit", 400)))
        if snap.get("ok"):
            snap["compact"] = compact_view(snap.get("controls", []))
        return snap

    def gui_act(self, payload: dict) -> dict:
        """Act on one control, then report only what could be verified."""
        if self._gui_assess(payload).get("blocked"):
            raise ComputerControlError(self._gui_assess(payload).get("summary", "blocked"))
        backend, pid, action = self._gui(), int(payload["pid"]), str(payload["action"])
        before_snap = backend.snapshot(pid)
        result = backend.act(pid, str(payload["path"]), action, payload.get("expect"), payload.get("value"))
        if not result.get("ok"):
            raise ComputerControlError(result.get("error") or "the action failed")
        verified, note = verify(action, result["before"], result.get("after"), payload.get("value"))
        if verified is None:  # buttons/menu items: did anything visible change at all?
            after_snap = backend.snapshot(pid)
            changed = after_snap.get("ok") and before_snap.get("ok") and snapshot_changed(before_snap, after_snap)
            gone = not after_snap.get("ok")  # e.g. the window closed
            verified = bool(changed or gone)
            note = "the window closed" if gone else ("something changed" if changed else "nothing visible changed")
            if changed:
                old_texts = {c["path"]: c.get("text") for c in before_snap.get("controls", [])}
                changed_texts = [c["text"] for c in after_snap.get("controls", [])
                                 if c.get("text") and c.get("text") != old_texts.get(c["path"]) and len(c["text"]) <= 40]
                return {"ok": True, "verified": True, "note": note, "changed_texts": changed_texts[:2],
                        "control": result.get("after") or result["before"]}
        return {"ok": True, "verified": verified, "note": note, "control": result.get("after") or result["before"]}

    # ------------------------------------------------------------------
    # Path + command validation
    # ------------------------------------------------------------------
    def _path(self, value: str | Path | None, *, default_home: bool = False) -> Path:
        if value in (None, ""):
            if not default_home:
                raise ComputerControlError("A path is required.")
            path = Path.home()
        else:
            path = Path(str(value)).expanduser()
        try:
            # Resolve the parent but preserve the final path component. This
            # keeps normal file-manager semantics for symlinks: move/delete a
            # symlink acts on the link itself rather than silently targeting the
            # object it points to.
            if not path.is_absolute():
                path = Path.cwd() / path
            resolved = path.parent.resolve(strict=False) / path.name
        except OSError as exc:
            raise ComputerControlError(f"Invalid path: {path}") from exc
        if self.test_root is not None:
            try:
                resolved.relative_to(self.test_root)
            except ValueError as exc:
                raise ComputerControlError("Path is outside the self-test sandbox.") from exc
        return resolved

    def _check_write_target(self, path: Path) -> None:
        if self.test_root is not None:
            return
        try:
            candidate = path.resolve(strict=False)
        except OSError:
            candidate = path
        for protected in self.protected_paths:
            if candidate == protected:
                raise ComputerControlError(f"This file is owned by Bemo's computer-control safety layer and cannot be modified through computer_control: {path}")
        for root in self.VIRTUAL_WRITE_ROOTS:
            try:
                candidate.relative_to(root)
                raise ComputerControlError(f"Direct writes under {root} are blocked.")
            except ValueError:
                pass

    @staticmethod
    def _argv(payload: dict) -> list[str]:
        raw = payload.get("argv")
        if not isinstance(raw, list) or not raw:
            raise ComputerControlError("argv must be a non-empty list of program arguments.")
        argv = [str(item) for item in raw]
        if not argv[0].strip():
            raise ComputerControlError("Executable is empty.")
        return argv

    @staticmethod
    def _exe_name(argv: list[str]) -> str:
        return Path(argv[0]).name.lower()

    def _command_assessment(self, argv: list[str], *, admin: bool = False, cwd: str | None = None,
                            depth: int = 0) -> dict:
        exe = self._exe_name(argv)
        args = [str(x).lower() for x in argv[1:]]
        command = shlex.join(argv)
        if not admin and exe in self.COMMAND_WRAPPERS:
            inner = self._unwrap(argv)
            if inner is None or depth >= 4:
                return {"confirmation_required": True, "summary": "Run a command through " + exe + ": " + command}
            return self._command_assessment(inner, cwd=cwd, depth=depth + 1)
        if not admin and self._secret_args(argv, cwd):
            return {"confirmation_required": True, "critical": True,
                    "summary": "Run a command on private keys or passwords: " + command}
        if not admin:
            asked = self._extra_command_rules(exe, args, command)
            if asked:
                return asked
        if not admin and exe in self.PRIVILEGE_WRAPPERS:
            return {
                "blocked": True,
                "admin_required": True,
                "summary": "Privileged wrappers are not allowed through normal command execution. Use admin_run instead.",
            }
        if admin:
            return {
                "confirmation_required": True,
                "critical": True,
                "summary": "Run an administrator command through the OS authorization prompt: " + shlex.join(argv),
            }
        if exe in self.DESTRUCTIVE_COMMANDS:
            # systemctl --user is explicitly user-scoped and does not need the
            # destructive/system-service confirmation applied to systemctl.
            if exe == "systemctl" and "--user" in args:
                return {"confirmation_required": False}
            return {
                "confirmation_required": True,
                "critical": exe in {"dd", "wipefs", "fdisk", "sfdisk", "parted", "gdisk", "sgdisk"} or exe.startswith("mkfs"),
                "summary": "Run potentially destructive command: " + shlex.join(argv),
            }
        if exe == "systemctl" and "--user" in args:
            verb = next((a for a in args if not a.startswith("-")), "")
            if verb and verb not in self.SYSTEMCTL_READ_ONLY:
                # Enabling or stopping user services (BMO's own among them) is asked (review 2026-10-19).
                return {"confirmation_required": True, "summary": "Change a user service: " + shlex.join(argv)}
        if exe == "systemctl" and "--user" not in args:
            return {
                "confirmation_required": True,
                "summary": "Run a system service command: " + shlex.join(argv),
            }
        if exe in self.SHELLS:
            if not args or all(arg in {"--version", "-version"} for arg in args):
                return {"confirmation_required": False}
            return {
                "confirmation_required": True,
                "summary": "Run a shell/script command: " + shlex.join(argv),
            }
        if exe in self.INTERPRETERS or re.fullmatch(r"python[\d.]*|pypy[\d.]*", exe):
            safe_version = args and all(arg in {"--version", "-v", "-V"} for arg in argv[1:])
            if not safe_version and not (cwd is None and self._module_check(exe, argv)):
                return {
                    "confirmation_required": True,
                    "summary": "Run interpreter/script code: " + shlex.join(argv),
                }
        if exe in {"sed"} and any(arg == "-i" or arg.startswith(("-i", "--in-place")) for arg in args):
            return {
                "confirmation_required": True,
                "summary": "Run an in-place file modification command: " + shlex.join(argv),
            }
        # Executables addressed directly from common user-writable locations can
        # be arbitrary downloaded/generated code, so require one explicit user
        # approval rather than treating them like trusted system PATH commands.
        raw_exe = str(argv[0])
        if "/" in raw_exe:
            try:
                exe_path = Path(raw_exe).expanduser().resolve(strict=False)
                writable_roots = [Path.home(), Path("/tmp"), Path("/var/tmp"), Path("/mnt"), Path("/media"), Path("/run/media")]
                if any(exe_path == root or root in exe_path.parents for root in writable_roots):
                    return {
                        "confirmation_required": True,
                        "summary": "Run executable from a user-writable location: " + shlex.join(argv),
                    }
            except OSError:
                pass
        return {"confirmation_required": False}

    @staticmethod
    def _under(path: Path, roots) -> bool:
        return any(path == root or root in path.parents for root in roots)

    def _home_paths(self, relative) -> list[Path]:
        home = Path.home()
        return [home / item for item in relative]

    def _is_secret(self, path: Path) -> bool:
        return self.test_root is None and self._under(path, self._home_paths(self.SECRET_PATHS))

    def _is_startup_or_bemo(self, path: Path) -> bool:
        if self.test_root is not None:
            return False
        try:
            path = path.parent.resolve(strict=False) / path.name
        except OSError:
            pass
        return self._under(path, self._home_paths(self.STARTUP_PATHS)) or self._under(path, self.guarded_paths)

    def _guarded_write(self, path: Path, what: str) -> dict | None:
        if self._is_startup_or_bemo(path):
            return {"confirmation_required": True, "critical": True,
                    "summary": f"{what} a file that runs at login, holds keys, or is part of BMO: {path}"}
        return None

    def _secret_args(self, argv: list[str], cwd: str | None = None) -> bool:
        for arg in argv[1:]:
            for part in str(arg).split("="):
                if not part or not ("/" in part or part.startswith(("~", "."))):
                    continue
                candidate = Path(part).expanduser()
                if not candidate.is_absolute():
                    candidate = Path(cwd or Path.cwd()) / candidate
                if self._is_secret(Path(os.path.normpath(candidate))):
                    return True
        return False

    # Need a terminal to type into; run without one they just hang (live 2026-10-19: "run nano note.txt"
    # while trying to type into the text editor).
    TERMINAL_ONLY = {"nano", "pico", "vim", "vi", "nvim", "less", "more", "top", "htop", "man", "mc", "ranger",
                     "micro", "joe", "ne", "btop"}

    def _extra_command_rules(self, exe: str, args: list[str], command: str) -> dict | None:
        """Rules added by the 2026-10-19 safety review (each was free before)."""
        if exe in self.TERMINAL_ONLY:
            return {"blocked": True, "summary": f"{exe} needs a terminal window to type into, so it can't be run "
                                                "this way. To write text, use the text editor app with the gui tool."}
        def ask(why, critical=False):
            return {"confirmation_required": True, "critical": critical, "summary": f"{why}: {command}"}
        first = next((a for a in args if not a.startswith("-")), "")
        if exe in self.CRITICAL_TOOLS:
            return ask("Permanently empty the trash", critical=True)
        if exe in self.INDIRECT_RUNNERS:
            return ask("Run commands through " + exe)
        if exe in self.NETWORK_TOOLS:
            return ask("Download or send data over the network")
        if exe in self.PACKAGE_TOOLS and not (args and (args[0] in self.PACKAGE_READ_ONLY
                                                        or all(a in {"--version", "-v", "-V"} for a in args))):
            return ask("Install, run or change software packages")
        if exe in self.ASKED_TOOLS:
            return ask("Run " + exe)
        if exe == "git" and (any(a == "-c" or a.startswith("--config") or a.startswith("--exec-path") for a in args)
                             or first not in self.GIT_READ_ONLY):
            return ask("Run a git command that changes files or talks to a server")
        if exe == "find" and any(a in {"-delete", "-exec", "-execdir", "-ok", "-okdir", "-fprint", "-fprint0",
                                       "-fprintf", "-fls"} for a in args):
            return ask("Run find with delete or run options")
        mode = args[0] if args else ""
        # The mode only: "tar -xf a.tar test" was free because the file name "test" looked like -t.
        if exe in {"tar", "bsdtar"} and not ("--list" in args or mode == "--version" or (
                re.fullmatch(r"-?[a-z]*t[a-z]*", mode) and not re.search(r"[xcruad]", mode))):
            return ask("Unpack or create an archive")
        if exe in {"unzip"} and not any(a in {"-l", "-v", "-z", "-t"} for a in args):
            return ask("Unpack an archive")
        if exe in {"7z", "7za", "7zr"} and first not in {"l", "t", "i", "h"}:
            return ask("Unpack or create an archive")
        if exe == "gio" and first not in {"info", "list", "open", "mime", "tree", "cat", "help", "version"}:
            return ask("Change files with gio")
        if exe == "loginctl" and not (first.startswith(("show", "list")) or first in {"session-status", "user-status", ""}):
            return ask("Change the login session")
        if exe == "wmctrl" and not all(re.fullmatch(r"-[ldmpGx]+", a) for a in args):
            return ask("Change windows")
        if exe == "xrandr" and not all(a in {"-q", "--query", "--listmonitors", "--listactivemonitors", "--current",
                                             "--verbose", "--prop", "--version"} for a in args):
            return ask("Change the screen")
        return None

    @staticmethod
    def _unwrap(argv: list[str]) -> list[str] | None:
        """The command a wrapper runs ('env FOO=1 bash -c x' -> 'bash -c x'); None if unclear."""
        exe = Path(argv[0]).name.lower()
        rest = list(argv[1:])
        if exe == "env" and any(a in {"-S", "--split-string"} or a.startswith("-S") for a in rest):
            return None
        takes_value = {"timeout": {"-s", "--signal", "-k", "--kill-after"}, "nice": {"-n", "--adjustment"},
                       "ionice": {"-c", "-n", "-p", "--class", "--classdata"}, "stdbuf": {"-i", "-o", "-e"},
                       "env": {"-u", "--unset", "-C", "--chdir"}, "systemd-inhibit": {"--what", "--who", "--why", "--mode"},
                       "xvfb-run": {"-s", "--server-args", "-n", "--server-num", "-f", "--auth-file"}}.get(exe, set())
        while rest:
            arg = rest[0]
            if arg == "--":
                rest.pop(0)
                break
            if arg in takes_value:
                rest = rest[2:]
                continue
            if arg.startswith("-") or (exe == "env" and "=" in arg):
                rest.pop(0)
                continue
            if exe == "timeout" and re.fullmatch(r"[\d.]+[smhd]?", arg):
                rest.pop(0)
                continue
            break
        return rest or None

    _IMPORT_ONLY = re.compile(
        r"^\s*(?:(?:import [\w.]+(?: as \w+)?(?:\s*,\s*[\w.]+(?: as \w+)?)*|from [\w.]+ import [\w, ]+)\s*[;\n]\s*)*"
        r"(?:import [\w.]+(?: as \w+)?(?:\s*,\s*[\w.]+(?: as \w+)?)*|from [\w.]+ import [\w, ]+)"
        r"(?:\s*[;\n]\s*print\((?:'[^'\\]*'|\"[^\"\\]*\"|[\w.]+)\))*\s*;?\s*$")

    @classmethod
    def _module_check(cls, exe: str, argv: list[str]) -> bool:
        """'python3 -c "import pygame; print(pygame.__version__)"': only checks what is installed.

        Headless bench 2026-10-19: Coding's first step for a snake game was this check, it
        needed the owner's OK, and the turn ended without a program.
        """
        args = [a for a in argv[1:] if a != "-I"]
        return (exe.startswith("python") and len(args) == 2 and args[0] == "-c"
                and bool(cls._IMPORT_ONLY.match(args[1])))

    # ------------------------------------------------------------------
    # Risk/confirmation classification
    # ------------------------------------------------------------------
    def assess(self, action: str, payload: dict) -> dict:
        action = str(action or "").strip().lower()
        if action in {"status", "list", "processes", "desktop_status", "screenshot", "mkdir", "mouse_move", "scroll",
                      "gui_windows", "gui_snapshot"}:
            return {"confirmation_required": False}
        if action == "gui_act":
            return self._gui_assess(payload)
        if action in ("window_focus", "window_close"):
            # Replaces chat's raw "run wmctrl -ia/-ic" (P2): same tier, no question asked,
            # but never BMO's own windows.
            pid = next((w.pid for w in self._desktop().windows().values()
                        if w.window_id == str(payload.get("window_id"))), None)
            if pid in self._protected_pids() and action == "window_close":
                return {"blocked": True, "summary": "I won't close my own window or the terminal I'm running in."}
            return {"confirmation_required": False}
        if action == "gui_click_point":
            found = self._point_window(payload)
            if isinstance(found, str):
                return {"blocked": True, "summary": found}
            target = str(payload.get("target") or "a spot")[:80]
            # A guessed point is never free: at least an ordinary change (approved plan).
            tier = max(CHANGE, classify({"role": "push button", "name": target}, "click") or CHANGE)
            return {"confirmation_required": True, "critical": tier == CRITICAL, "tier": tier,
                    "summary": f"click {target} in {payload.get('app') or 'the app'} (found by looking at the screen)"}
        if action == "gui_close":
            assessed = self._gui_assess({"pid": payload.get("pid"), "action": "click", "app": payload.get("app"),
                                         "expect": {"role": "frame", "name": "window"}})
            if assessed.get("confirmation_required"):
                assessed["summary"] = f"close {payload.get('app') or 'the app'}"
            return assessed
        if action == "mouse_click" and time.monotonic() - getattr(self, "_last_mouse_move", -1e9) > 60:
            # Live 2026-10-18 the Main Brain asked to "left-click in Volume
            # Control" without ever pointing: a click wherever the pointer is.
            return {"blocked": True, "summary": "Refusing a blind click: move the mouse to a target first. "
                                                "For a named button or tab, BMO's own command is: press <name> in <app>."}
        if action in self.INPUT_ACTIONS:
            # Keyboard/click injection goes to whatever window has focus, and
            # typing a command into a terminal would bypass the run/launch
            # checks. Until a verified GUI controller exists, the owner
            # approves each one.
            if action == "type_text" and len(str(payload.get("text", ""))) > self.MAX_TYPE_TEXT_CHARS:
                return {"blocked": True, "summary": f"Refusing to type more than {self.MAX_TYPE_TEXT_CHARS} characters at once."}
            target = self._active_window_name() or "the focused window"
            detail = {
                "type_text": f"type {len(str(payload.get('text', '')))} characters",
                "key_press": f"press {payload.get('key', '')}",
                "hotkey": "press " + "+".join(str(k) for k in (payload.get("keys") or [])),
                "mouse_click": f"{payload.get('button', 'left')}-click",
            }[action]
            return {"confirmation_required": True, "summary": f"{detail} in {target}"}
        if action == "read_text":
            if self._is_secret(self._path(payload.get("path"))):
                return {"blocked": True, "summary": "I don't read private keys, passwords or browser logins."}
            return {"confirmation_required": False}
        if action == "open" and payload.get("path") and self._is_secret(self._path(payload.get("path"))):
            return {"confirmation_required": True, "critical": True,
                    "summary": f"Open private keys or passwords: {self._path(payload.get('path'))}"}
        if action == "open":
            return {"confirmation_required": False}
        if action == "write_text":
            path = self._path(payload.get("path"))
            guarded = self._guarded_write(path, "Write")
            if guarded:
                return guarded
            if path.exists():
                return {
                    "confirmation_required": True,
                    "summary": f"Overwrite existing file: {path}",
                }
            return {"confirmation_required": False}
        if action in {"copy", "move"}:
            destination = self._path(payload.get("destination"))
            source = self._path(payload.get("path"))
            guarded = self._guarded_write(destination, "Put") or (
                self._guarded_write(source, "Move") if action == "move" else None)
            if guarded:
                return guarded
            if self._is_secret(source):
                return {"confirmation_required": True, "critical": True,
                        "summary": f"{action.capitalize()} private keys or passwords: {source}"}
            if destination.exists():
                return {
                    "confirmation_required": True,
                    "summary": f"Replace an existing destination during {action}: {destination}",
                }
            return {"confirmation_required": False}
        if action == "delete":
            path = self._path(payload.get("path"))
            guarded = self._guarded_write(path, "Delete")
            if guarded:
                return guarded
            if path.is_dir() or bool(payload.get("recursive")):
                return {
                    "confirmation_required": True,
                    "summary": f"Recursively delete directory: {path}",
                }
            # One ordinary file goes to the desktop trash (recoverable) without
            # an extra prompt; without a trash backend it would be permanent.
            if self._trash_command():
                return {"confirmation_required": False}
            return {
                "confirmation_required": True,
                "summary": f"Permanently delete file (no trash available): {path}",
            }
        if action == "terminate":
            if bool(payload.get("force")):
                return {
                    "confirmation_required": True,
                    "summary": f"Force-kill process PID {payload.get('pid')}",
                }
            return {"confirmation_required": False}
        if action in {"run", "launch"}:
            return self._command_assessment(self._argv(payload), admin=False, cwd=payload.get("cwd"))
        if action == "admin_run":
            return self._command_assessment(self._argv(payload), admin=True)
        return {"blocked": True, "summary": f"Unknown computer action: {action}"}

    # ------------------------------------------------------------------
    # Core action execution
    # ------------------------------------------------------------------
    def execute(self, action: str, payload: dict) -> dict:
        action = str(action or "").strip().lower()
        if action == "status":
            return self.status()
        if action == "list":
            return self.list_directory(payload)
        if action == "read_text":
            return self.read_text(payload)
        if action == "write_text":
            return self.write_text(payload)
        if action == "mkdir":
            return self.mkdir(payload)
        if action == "copy":
            return self.copy(payload)
        if action == "move":
            return self.move(payload)
        if action == "delete":
            return self.delete(payload)
        if action == "open":
            return self.open_path(payload)
        if action == "launch":
            return self.launch(payload)
        if action == "run":
            return self.run(payload)
        if action == "admin_run":
            return self.admin_run(payload)
        if action == "processes":
            return self.processes(payload)
        if action == "terminate":
            return self.terminate(payload)
        if action == "screenshot":
            return self.screenshot(payload)
        if action == "desktop_status":
            return self.desktop_status()
        if action == "mouse_move":
            return self.mouse_move(payload)
        if action == "mouse_click":
            return self.mouse_click(payload)
        if action == "scroll":
            return self.scroll(payload)
        if action == "type_text":
            return self.type_text(payload)
        if action == "key_press":
            return self.key_press(payload)
        if action == "hotkey":
            return self.hotkey(payload)
        if action == "gui_windows":
            return self.gui_windows(payload)
        if action == "gui_snapshot":
            return self.gui_snapshot(payload)
        if action == "gui_act":
            return self.gui_act(payload)
        if action == "gui_close":
            return self.gui_close(payload)
        if action == "gui_click_point":
            return self.gui_click_point(payload)
        if action == "window_focus":
            self._desktop().activate(str(payload["window_id"]))
            return {"ok": True}
        if action == "window_close":
            self._desktop().close(str(payload["window_id"]))
            return {"ok": True}
        raise ComputerControlError(f"Unknown computer action: {action}")

    def status(self) -> dict:
        from systems.adapters import system
        return {
            "ok": True,
            "user": os.environ.get("USER") or os.environ.get("USERNAME") or "unknown",
            "home": str(Path.home()),
            "cwd": os.getcwd(),
            "display": os.environ.get("DISPLAY", ""),
            "wayland_display": os.environ.get("WAYLAND_DISPLAY", ""),
            "desktop": self.desktop_status(),
            "admin_backend": system().admin_name,
            "note": "Actions run with the logged-in Bemo user's permissions; permanent root access is not used.",
        }

    def list_directory(self, payload: dict) -> dict:
        path = self._path(payload.get("path"), default_home=True)
        if not path.is_dir():
            raise ComputerControlError(f"Not a directory: {path}")
        limit = max(1, min(1000, int(payload.get("limit", 200))))
        entries = []
        try:
            iterator = path.iterdir()
            for item in iterator:
                if len(entries) >= limit:
                    break
                try:
                    stat = item.lstat()
                    entries.append({
                        "name": item.name,
                        "path": str(item),
                        "type": "directory" if item.is_dir() else "symlink" if item.is_symlink() else "file",
                        "size": int(stat.st_size),
                        "modified_at": float(stat.st_mtime),
                    })
                except OSError:
                    continue
        except PermissionError as exc:
            raise ComputerControlError(f"Permission denied: {path}") from exc
        entries.sort(key=lambda x: (x["type"] != "directory", x["name"].lower()))
        return {"ok": True, "path": str(path), "entries": entries, "truncated": len(entries) >= limit}

    def read_text(self, payload: dict) -> dict:
        path = self._path(payload.get("path"))
        if not path.is_file():
            raise ComputerControlError(f"Not a regular file: {path}")
        max_bytes = min(self.max_text_read_bytes, max(1024, int(payload.get("max_bytes", self.max_text_read_bytes))))
        try:
            raw = path.read_bytes()[:max_bytes]
        except PermissionError as exc:
            raise ComputerControlError(f"Permission denied: {path}") from exc
        text = raw.decode("utf-8", errors="replace")
        lines = text.splitlines()
        start = max(1, int(payload.get("start_line", 1)))
        max_lines = max(1, min(5000, int(payload.get("max_lines", 400))))
        selected = lines[start - 1:start - 1 + max_lines]
        return {
            "ok": True,
            "path": str(path),
            "start_line": start,
            "lines": selected,
            "truncated": len(raw) >= max_bytes or start - 1 + len(selected) < len(lines),
        }

    def write_text(self, payload: dict) -> dict:
        path = self._path(payload.get("path"))
        self._check_write_target(path)
        content = str(payload.get("content", ""))
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + f".bemo-tmp-{os.getpid()}")
        try:
            tmp.write_text(content, encoding="utf-8")
            os.replace(tmp, path)
        finally:
            try:
                tmp.unlink()
            except FileNotFoundError:
                pass
        return {"ok": True, "path": str(path), "bytes": len(content.encode("utf-8"))}

    def mkdir(self, payload: dict) -> dict:
        path = self._path(payload.get("path"))
        self._check_write_target(path)
        path.mkdir(parents=bool(payload.get("parents", True)), exist_ok=bool(payload.get("exist_ok", True)))
        return {"ok": True, "path": str(path)}

    def copy(self, payload: dict) -> dict:
        source = self._path(payload.get("path"))
        destination = self._path(payload.get("destination"))
        self._check_write_target(destination)
        if not source.exists() and not source.is_symlink():
            raise ComputerControlError(f"Source does not exist: {source}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists() or destination.is_symlink():
            if destination.is_dir() and not destination.is_symlink():
                shutil.rmtree(destination)
            else:
                destination.unlink()
        if source.is_dir() and not source.is_symlink():
            shutil.copytree(source, destination, symlinks=True)
        else:
            shutil.copy2(source, destination, follow_symlinks=False)
        return {"ok": True, "source": str(source), "destination": str(destination)}

    def move(self, payload: dict) -> dict:
        source = self._path(payload.get("path"))
        destination = self._path(payload.get("destination"))
        self._check_write_target(source)
        self._check_write_target(destination)
        if not source.exists() and not source.is_symlink():
            raise ComputerControlError(f"Source does not exist: {source}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists() or destination.is_symlink():
            if destination.is_dir() and not destination.is_symlink():
                shutil.rmtree(destination)
            else:
                destination.unlink()
        shutil.move(str(source), str(destination))
        return {"ok": True, "source": str(source), "destination": str(destination)}

    def delete(self, payload: dict) -> dict:
        path = self._path(payload.get("path"))
        self._check_write_target(path)
        if not path.exists() and not path.is_symlink():
            # Not a success: Main read "already missing" as done (live 2026-10-20).
            raise ComputerControlError(f"Path does not exist: {path}; nothing was deleted")
        if path.is_dir() and not path.is_symlink():
            if not bool(payload.get("recursive")):
                raise ComputerControlError("Deleting a directory requires recursive=true and confirmation.")
            shutil.rmtree(path)
        else:
            trash = self._trash_command()
            if trash:
                completed = subprocess.run([*trash, str(path)], capture_output=True, text=True, timeout=30, check=False)
                if completed.returncode != 0:
                    raise ComputerControlError((completed.stderr or completed.stdout or "Moving to trash failed.").strip())
                return {"ok": True, "path": str(path), "trashed": True}
            path.unlink()
        return {"ok": True, "path": str(path), "deleted": True}

    @staticmethod
    def _trash_command() -> list[str] | None:
        from systems.adapters import system
        return system().trash_command()

    @classmethod
    def _active_window_name(cls) -> str:
        name = cls._desktop().active_window_name()
        return f"'{name[:60]}'" if name else ""

    def open_path(self, payload: dict) -> dict:
        path = self._path(payload.get("path"), default_home=True)
        if not path.exists():
            raise ComputerControlError(f"Path does not exist: {path}")
        from systems.adapters import system
        opener = system().open_command()
        if not opener:
            raise ComputerControlError("xdg-open is not installed.")
        proc = subprocess.Popen(
            [*opener, str(path)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            close_fds=True,
        )
        return {"ok": True, "path": str(path), "pid": proc.pid}

    def _resolve_program(self, argv: list[str]) -> list[str]:
        executable = argv[0]
        if executable in {"python", "python3"} and self.python_executable:
            # BMO's own Python (pygame, numpy, Pillow ...). Headless bench 2026-10-19: the system
            # python3 had no pygame, so Coding asked for an administrator "pip3 install pygame"
            # instead of writing the snake game, and the game it wrote could not have run.
            return [self.python_executable, *argv[1:]]
        if os.path.isabs(executable):
            if not Path(executable).exists():
                raise ComputerControlError(f"Executable not found: {executable}")
            return argv
        located = shutil.which(executable)
        if not located:
            raise ComputerControlError(f"Executable not found: {executable}")
        argv = list(argv)
        argv[0] = located
        return argv

    def _cwd(self, payload: dict) -> str | None:
        if not payload.get("cwd"):
            return None
        path = self._path(payload.get("cwd"))
        if not path.is_dir():
            raise ComputerControlError(f"Working directory is not a directory: {path}")
        return str(path)

    def launch(self, payload: dict) -> dict:
        argv = self._resolve_program(self._argv(payload))
        assessment = self._command_assessment(argv)
        if assessment.get("blocked"):
            raise ComputerControlError(assessment.get("summary", "Command is blocked."))
        proc = subprocess.Popen(
            argv,
            cwd=self._cwd(payload),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            close_fds=True,
        )
        return {"ok": True, "pid": proc.pid, "argv": argv}

    def run(self, payload: dict) -> dict:
        argv = self._resolve_program(self._argv(payload))
        if not payload.get("cwd") and self._module_check(Path(argv[0]).name.lower(), argv):
            # Isolated (-I): the free module check can't import a file someone just wrote next to
            # it (review 2026-10-19: write evil.py, then "python3 -c 'import evil'" ran it unasked).
            argv = [argv[0], "-I", *argv[1:]]
        assessment = self._command_assessment(argv, cwd=payload.get("cwd"))
        if assessment.get("blocked"):
            raise ComputerControlError(assessment.get("summary", "Command is blocked."))
        timeout = max(1.0, min(600.0, float(payload.get("timeout", self.action_timeout_seconds))))
        try:
            completed = subprocess.run(
                argv,
                cwd=self._cwd(payload),
                input=(str(payload.get("stdin")) if payload.get("stdin") is not None else None),
                text=True,
                capture_output=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise ComputerControlError(f"Command timed out after {timeout:.1f} seconds.") from exc
        stdout = (completed.stdout or "")[:self.output_max_chars]
        stderr = (completed.stderr or "")[:self.output_max_chars]
        return {
            "ok": completed.returncode == 0,
            "returncode": int(completed.returncode),
            "stdout": stdout,
            "stderr": stderr,
            "output_truncated": len(completed.stdout or "") > len(stdout) or len(completed.stderr or "") > len(stderr),
            "argv": argv,
        }

    def admin_run(self, payload: dict) -> dict:
        argv = self._argv(payload)
        if self.test_root is not None:
            # Never invoke real privilege escalation during self-tests.
            return {"ok": True, "test_mode": True, "would_run": argv}
        from systems.adapters import system
        wrapper = system().admin_wrapper()
        if not wrapper:
            raise ComputerControlError("pkexec is not installed; administrator action cannot be authorized safely.")
        timeout = max(5.0, min(900.0, float(payload.get("timeout", max(120.0, self.action_timeout_seconds)))))
        try:
            completed = subprocess.run(
                [*wrapper, *argv],
                cwd=self._cwd(payload),
                text=True,
                capture_output=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise ComputerControlError("Administrator command timed out or authorization was not completed.") from exc
        stdout = (completed.stdout or "")[:self.output_max_chars]
        stderr = (completed.stderr or "")[:self.output_max_chars]
        return {
            "ok": completed.returncode == 0,
            "returncode": int(completed.returncode),
            "stdout": stdout,
            "stderr": stderr,
            "argv": argv,
            "authorized_via": "pkexec",
        }

    def processes(self, payload: dict) -> dict:
        query = str(payload.get("query", "")).lower().strip()
        limit = max(1, min(500, int(payload.get("limit", 100))))
        rows = []
        for proc in psutil.process_iter(["pid", "name", "username", "cmdline", "status", "memory_info"]):
            try:
                info = proc.info
                cmdline = " ".join(info.get("cmdline") or [])
                hay = f"{info.get('name') or ''} {cmdline} {info.get('username') or ''}".lower()
                if query and query not in hay:
                    continue
                mem = info.get("memory_info")
                rows.append({
                    "pid": int(info["pid"]),
                    "name": info.get("name") or "",
                    "username": info.get("username") or "",
                    "status": info.get("status") or "",
                    "memory_rss": int(getattr(mem, "rss", 0) or 0),
                    "cmdline": cmdline[:1000],
                })
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        rows.sort(key=lambda x: x["memory_rss"], reverse=True)
        return {"ok": True, "processes": rows[:limit], "truncated": len(rows) > limit}

    def terminate(self, payload: dict) -> dict:
        pid = int(payload.get("pid"))
        force = bool(payload.get("force"))
        if pid in {0, 1, os.getpid()}:
            raise ComputerControlError("Refusing to terminate a protected process from this control layer.")
        try:
            proc = psutil.Process(pid)
            name = proc.name()
            if force:
                proc.kill()
            else:
                proc.terminate()
            return {"ok": True, "pid": pid, "name": name, "force": force}
        except psutil.NoSuchProcess:
            return {"ok": True, "pid": pid, "already_stopped": True}
        except psutil.AccessDenied as exc:
            raise ComputerControlError(f"Permission denied terminating PID {pid}.") from exc

    # ------------------------------------------------------------------
    # Desktop input/screenshot backends
    # ------------------------------------------------------------------
    @staticmethod
    def _desktop():
        from systems.adapters import desktop
        return desktop()

    def _input(self, action) -> dict:
        """Run one input action through the OS adapter; its 'not here' becomes a normal error."""
        from systems.adapters import Unavailable
        try:
            action(self._desktop())
        except Unavailable as exc:
            raise ComputerControlError(str(exc)) from exc
        return {"ok": True, "backend": "xdotool"}

    def _screenshot_backend(self) -> tuple[str | None, list[str] | None]:
        return self._desktop().screenshot_command()

    def _input_backend(self) -> str | None:
        return "xdotool" if self._desktop().input_available() else None

    def desktop_status(self) -> dict:
        shot, _ = self._screenshot_backend()
        return {
            "ok": True,
            "screenshot_backend": shot,
            "input_backend": self._input_backend(),
            "display": os.environ.get("DISPLAY", ""),
            "wayland_display": os.environ.get("WAYLAND_DISPLAY", ""),
            "input_note": (
                "xdotool is available for mouse/keyboard control."
                if self._input_backend()
                else "No supported desktop input backend detected; install/use xdotool in an X11/XWayland session for mouse/keyboard control."
            ),
        }

    def screenshot(self, payload: dict) -> dict:
        backend, command = self._screenshot_backend()
        if not backend or not command:
            raise ComputerControlError("No screenshot backend detected (grim, gnome-screenshot, scrot, or ImageMagick import).")
        if payload.get("path"):
            path = self._path(payload.get("path"))
            self._check_write_target(path)
        else:
            self.screenshot_dir.mkdir(parents=True, exist_ok=True)
            path = self.screenshot_dir / f"bemo-screen-{int(time.time() * 1000)}.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        argv = list(command)
        if backend == "gnome-screenshot":
            argv.append(str(path))
        else:
            argv.append(str(path))
        completed = subprocess.run(argv, capture_output=True, text=True, timeout=20, check=False)
        if completed.returncode != 0 or not path.is_file():
            raise ComputerControlError((completed.stderr or completed.stdout or "Screenshot failed.").strip())
        return {"ok": True, "path": str(path), "backend": backend}

    def mouse_move(self, payload: dict) -> dict:
        result = self._input(lambda d: d.move(int(payload.get("x")), int(payload.get("y"))))
        self._last_mouse_move = time.monotonic()
        result.update({"x": int(payload.get("x")), "y": int(payload.get("y"))})
        return result

    def mouse_click(self, payload: dict) -> dict:
        button_name = str(payload.get("button", "left")).lower()
        button = {"left": "1", "middle": "2", "right": "3"}.get(button_name)
        if not button:
            raise ComputerControlError("button must be left, middle, or right")
        clicks = max(1, min(10, int(payload.get("clicks", 1))))
        return self._input(lambda d: d.click(int(button), clicks))

    def scroll(self, payload: dict) -> dict:
        amount = int(payload.get("amount", 0))
        if amount == 0:
            return {"ok": True, "backend": self._input_backend(), "amount": 0}
        button = "4" if amount > 0 else "5"
        repeats = min(100, abs(amount))
        result = self._input(lambda d: d.click(int(button), repeats))
        result["amount"] = amount
        return result

    def type_text(self, payload: dict) -> dict:
        text = str(payload.get("text", ""))
        delay = max(0, min(1000, int(payload.get("delay_ms", 10))))
        result = self._input(lambda d: d.type_text(text, delay))
        result["characters"] = len(text)
        return result

    def key_press(self, payload: dict) -> dict:
        key = str(payload.get("key", "")).strip()
        if not key:
            raise ComputerControlError("key is required")
        return self._input(lambda d: d.key(key))

    def hotkey(self, payload: dict) -> dict:
        keys = payload.get("keys")
        if not isinstance(keys, list) or not keys:
            raise ComputerControlError("keys must be a non-empty list")
        combo = "+".join(str(k) for k in keys)
        return self._input(lambda d: d.key(combo))
