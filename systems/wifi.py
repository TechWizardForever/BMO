"""Internet for BMO's first boot (installer plan, milestone I3), through NetworkManager's own tools.

The Wi-Fi password never passes through BMO: he asks NetworkManager to connect without one, and
NetworkManager asks Mint's network tray (nm-applet) for it, which shows its own password window. The
owner types it there (2026-10-19). BMO only learns whether it connected.
"""
from __future__ import annotations

import os
import re
import shutil
import socket
import subprocess
import time
from dataclasses import dataclass

HOSTS = (("github.com", 443), ("registry.ollama.ai", 443), ("huggingface.co", 443))
_ENV = dict(os.environ, LC_ALL="C")


def online(hosts=HOSTS, connect=socket.create_connection) -> bool:
    """True when any of the places BMO downloads from answers."""
    for host, port in hosts:
        try:
            connect((host, port), timeout=5).close()
            return True
        except OSError:
            continue
    return False


def _split(line: str) -> list[str]:
    """nmcli -t fields: ':' separates, '\\:' is a colon inside a value."""
    return [part.replace("\\:", ":").replace("\\\\", "\\") for part in re.split(r"(?<!\\):", line)]


@dataclass(frozen=True)
class Network:
    ssid: str
    signal: int
    secured: bool


class Wifi:
    def __init__(self, run=subprocess.run, which=shutil.which, popen=subprocess.Popen):
        self._run, self._which, self._popen = run, which, popen

    def _nmcli(self, *args: str, timeout: float = 30):
        return self._run(["nmcli", *args], capture_output=True, text=True, timeout=timeout, env=_ENV, check=False)

    def device(self) -> str | None:
        """The Wi-Fi adapter's name, or None when there is none (or no NetworkManager)."""
        if not self._which("nmcli"):
            return None
        done = self._nmcli("-t", "-f", "DEVICE,TYPE", "device")
        for line in done.stdout.splitlines():
            fields = _split(line)
            if len(fields) >= 2 and fields[1] == "wifi":
                return fields[0]
        return None

    def connected(self, device: str) -> bool:
        done = self._nmcli("-t", "-f", "DEVICE,STATE", "device")
        return any(_split(line)[:2] == [device, "connected"] for line in done.stdout.splitlines())

    def networks(self, limit: int = 5) -> list[Network]:
        """Nearby networks with a name, strongest first, each name once."""
        # "--rescan yes" waits for a fresh scan; a plain rescan returns at once and the list was still
        # empty half a second later on this PC (2026-10-20). One more look if it is empty anyway.
        lines = []
        for attempt in range(2):
            done = self._nmcli("-t", "-f", "SSID,SIGNAL,SECURITY", "device", "wifi", "list", "--rescan", "yes",
                               timeout=40)
            lines = [line for line in done.stdout.splitlines() if line.strip()]
            if lines or attempt:
                break
            time.sleep(3)
        best: dict[str, Network] = {}
        for line in lines:
            fields = _split(line)
            if len(fields) < 3 or not fields[0].strip():
                continue
            try:
                signal = int(fields[1])
            except ValueError:
                signal = 0
            network = Network(fields[0], signal, fields[2].strip() not in ("", "--"))
            if network.ssid not in best or best[network.ssid].signal < signal:
                best[network.ssid] = network
        return sorted(best.values(), key=lambda n: -n.signal)[:limit]

    def ensure_password_window(self) -> None:
        """Mint's tray shows the password window; start it if it isn't running."""
        if self._which("nm-applet") and self._run(["pgrep", "-x", "nm-applet"], capture_output=True,
                                                  check=False).returncode != 0:
            self._popen(["nm-applet"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            time.sleep(2)

    def connect(self, ssid: str, wait_seconds: int = 180) -> tuple[bool, str]:
        """Connect without a password on the command line; NetworkManager asks the tray for it."""
        self.ensure_password_window()
        done = self._nmcli("-w", str(wait_seconds), "device", "wifi", "connect", ssid, timeout=wait_seconds + 15)
        message = (done.stderr or done.stdout or "").strip()
        return done.returncode == 0, message


_ORDINALS = {"one": 1, "first": 1, "two": 2, "second": 2, "three": 3, "third": 3, "four": 4, "fourth": 4,
             "five": 5, "fifth": 5, "to": 2, "too": 2, "for": 4, "won": 1}


def pick_network(text: str | None, networks: list[Network]) -> Network | None:
    """'number two' / 'the second one' / '2' / the network's name -> that network."""
    import difflib
    words = re.sub(r"[^a-z0-9 ]+", " ", str(text or "").lower()).split()
    if not words or not networks:
        return None
    spoken = " ".join(words)
    squashed = spoken.replace(" ", "")
    for network in networks:  # the name first: "Home 2" must not read as number two
        name = re.sub(r"[^a-z0-9]+", "", network.ssid.lower())
        if name and (name in squashed or difflib.SequenceMatcher(None, name, squashed).ratio() >= 0.8):
            return network
    if len(words) <= 4:
        for word in words:
            number = int(word) if word.isdigit() else _ORDINALS.get(word)
            if number and 1 <= number <= len(networks):
                return networks[number - 1]
    return None


def spoken_reason(message: str) -> str:
    """NetworkManager's error, said simply (it never contains the password)."""
    text = message.lower()
    if "secrets were required" in text or "no secrets" in text:
        return "The password wasn't entered, or it wasn't right."
    if "timeout" in text or "timed out" in text:
        return "It took too long to connect."
    if "no network with ssid" in text or "not found" in text:
        return "I couldn't find that network any more."
    return "It didn't connect."
