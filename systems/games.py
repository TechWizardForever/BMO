"""Controller-first GameCube-only game subsystem for Bemo.

ES-DE provides the fullscreen/controller library and standalone Dolphin performs
GameCube emulation. Discovery is confined to Bemo's configured GameCube ROM
directory and all launch commands are argv lists with no shell expansion.
"""
from __future__ import annotations

import difflib
import hashlib
import json
import logging
import os
import platform
import re
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass, asdict
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape

try:
    import psutil
except Exception:
    psutil = None


# Dolphin's documented GameCube dump formats. RVZ is the preferred compressed
# archival format; ISO/GCM are uncompressed disc images.
GAMECUBE_EXTENSIONS = {".iso", ".gcm", ".gcz", ".ciso", ".rvz", ".wia"}
DOLPHIN_FLATPAK_ID = "org.DolphinEmu.dolphin-emu"


def _slug_title(path: Path) -> str:
    text = path.stem
    text = re.sub(r"[._]+", " ", text)
    # Remove common trailing region/revision/disc tags while preserving the real
    # title. Voice resolution still also checks the exact filename stem.
    text = re.sub(r"(?:\s*(?:\([^)]*\)|\[[^]]*\]))+\s*$", "", text)
    # No-Intro names put the article last ("Legend of Zelda, The - The Wind Waker"); BMO says it the usual way.
    text = re.sub(r"^(.+?), (The|A|An)\b(?=\s*(?:-|$))", r"\2 \1", text)
    return " ".join(text.split()).strip() or path.stem


def _norm(text: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", str(text).lower()).split())


def _sha256_file(path: Path, chunk_size: int = 4 * 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def discover_removable_drives() -> list[Path]:
    """Return mounted removable-media roots suitable for GameCube import.

    Bemo's appliance target is Linux Mint/Xfce, where desktop-mounted USB media
    normally appears under /media/<user>/ or /run/media/<user>/.  The optional
    environment override is useful for recovery/testing and accepts os.pathsep-
    separated absolute paths.
    """
    override = os.environ.get("BEMO_REMOVABLE_ROOTS", "").strip()
    candidates: list[Path] = []
    if override:
        # The override replaces the real drives (2026-10-23: with a USB stick plugged in, self_test's import
        # also took the stick's 96 files into its throwaway project).
        candidates.extend(Path(item.strip()).expanduser() for item in override.split(os.pathsep) if item.strip())

    if psutil is not None and not override:
        try:
            for part in psutil.disk_partitions(all=False):
                mount = Path(part.mountpoint)
                text = str(mount)
                if text.startswith("/media/") or text.startswith("/run/media/"):
                    candidates.append(mount)
        except Exception:
            pass

    # Desktop mounts can occasionally be absent from psutil's filtered list, so
    # also inspect the normal user-media parents. This never scans the system
    # root itself; only immediate mounted children become source roots.
    user = os.environ.get("USER", "").strip()
    parents = [Path("/media") / user, Path("/run/media") / user] if user and not override else []
    for parent in parents:
        try:
            if parent.is_dir():
                candidates.extend(child for child in parent.iterdir() if child.is_dir())
        except OSError:
            pass

    unique: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        key = str(resolved)
        if key in seen or not resolved.is_dir() or not os.access(resolved, os.R_OK):
            continue
        seen.add(key)
        unique.append(resolved)
    return unique


FLATHUB_REPO = "https://dl.flathub.org/repo/flathub.flatpakrepo"


def dolphin_available(run=subprocess.run) -> bool:
    """A native Dolphin, or the Flatpak (for this account or the whole PC)."""
    if shutil.which("dolphin-emu") or shutil.which("dolphin"):
        return True
    flatpak = shutil.which("flatpak")
    if not flatpak:
        return False
    try:
        return run([flatpak, "info", DOLPHIN_FLATPAK_ID], capture_output=True, timeout=30).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def install_dolphin_flatpak(data_root: Path, run=subprocess.run) -> None:
    """Dolphin from Flathub for this account (no password), with its save folder inside BMO's data, as install.sh
    does. Raises on failure. Used by the first-boot downloader (systems/downloads.py, 2026-10-24)."""
    flatpak = shutil.which("flatpak")
    if not flatpak:
        raise RuntimeError("flatpak is not installed")
    for argv in ([flatpak, "remote-add", "--user", "--if-not-exists", "flathub", FLATHUB_REPO],
                 [flatpak, "install", "--user", "--noninteractive", "-y", "flathub", DOLPHIN_FLATPAK_ID]):
        done = run(argv, capture_output=True, text=True, timeout=7200)
        if done.returncode != 0:
            raise RuntimeError((done.stderr or done.stdout or "flatpak failed")[-200:])
    # The Flatpak sees the home folder read-only; saves and settings go in BMO's own Dolphin folder.
    saves = Path(data_root) / "games" / "dolphin-user"
    saves.mkdir(parents=True, exist_ok=True)
    run([flatpak, "override", "--user", f"--filesystem={saves}", DOLPHIN_FLATPAK_ID],
        capture_output=True, text=True, timeout=60)


@dataclass(frozen=True)
class GameEntry:
    title: str
    system: str
    path: str
    filename: str

    def public(self) -> dict:
        return asdict(self)


class GameError(RuntimeError):
    pass


class GameLibrary:
    def __init__(self, game_root: str | Path, app_root: str | Path):
        self.root = Path(game_root).expanduser().resolve()
        self.app_root = Path(app_root).resolve()
        self.rom_root = self.root / "roms"
        self.gamecube_dir = self.rom_root / "gamecube"
        self.artwork_dir = self.root / "artwork"
        self.config_dir = self.root / "config"
        self.bin_dir = self.root / "bin"
        # A dedicated Dolphin user directory keeps controller settings, memory
        # cards, save states and per-game settings inside Bemo's persistent data.
        self.dolphin_user_dir = self.root / "dolphin-user"
        self.esde_home = self.root / "esde-home"
        self.esde_appdata = self.esde_home / "ES-DE"
        self.esde_settings_dir = self.esde_appdata / "settings"
        self.esde_custom_systems = self.esde_appdata / "custom_systems"
        self.esde_appimage = self.bin_dir / "ES-DE.AppImage"
        self.launcher = self.app_root / "game_launcher.sh"

    # ------------------------------------------------------------------
    # Files/layout/configuration
    # ------------------------------------------------------------------
    def ensure_layout(self) -> dict:
        for path in (
            self.gamecube_dir,
            self.artwork_dir,
            self.config_dir,
            self.bin_dir,
            self.dolphin_user_dir,
            self.esde_settings_dir,
            self.esde_custom_systems,
        ):
            path.mkdir(parents=True, exist_ok=True)
        self._write_esde_settings()
        self._write_esde_systems()
        return self.paths()

    def paths(self) -> dict:
        return {
            "root": str(self.root),
            "gamecube_roms": str(self.gamecube_dir),
            "dolphin_user": str(self.dolphin_user_dir),
            "artwork": str(self.artwork_dir),
            "esde_appimage": str(self.esde_appimage),
        }

    def _write_esde_settings(self) -> None:
        settings = (
            f'<string name="ROMDirectory" value="{xml_escape(str(self.rom_root))}" />\n'
            f'<string name="MediaDirectory" value="{xml_escape(str(self.artwork_dir))}" />\n'
        )
        path = self.esde_settings_dir / "es_settings.xml"
        if not path.exists():
            path.write_text(settings, encoding="utf-8")
        else:
            text = path.read_text(encoding="utf-8", errors="replace")
            if "ROMDirectory" not in text:
                path.write_text(settings, encoding="utf-8")

    def _write_esde_systems(self) -> None:
        launcher = xml_escape(str(self.launcher))
        rom_root = xml_escape(str(self.rom_root))
        xml = f'''<?xml version="1.0"?>
<systemList>
  <loadExclusive/>
  <system>
    <name>gc</name>
    <fullname>Nintendo GameCube</fullname>
    <path>{rom_root}/gamecube</path>
    <extension>.iso .ISO .gcm .GCM .gcz .GCZ .ciso .CISO .rvz .RVZ .wia .WIA</extension>
    <command label="Dolphin">{launcher} launch-file --system gamecube --rom %ROM%</command>
    <platform>gc</platform>
    <theme>gc</theme>
  </system>
</systemList>
'''
        (self.esde_custom_systems / "es_systems.xml").write_text(xml, encoding="utf-8")

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------
    def scan(self, system: str | None = None) -> list[GameEntry]:
        self.ensure_layout()
        if system not in (None, "gamecube"):
            return []
        out = self._scan_dir(self.gamecube_dir, "gamecube", GAMECUBE_EXTENSIONS)
        return sorted(out, key=lambda g: (_norm(g.title), g.filename.lower()))

    def _scan_dir(self, root: Path, system: str, extensions: set[str]) -> list[GameEntry]:
        if not root.exists():
            return []
        items: list[GameEntry] = []
        for path in root.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in extensions:
                continue
            items.append(GameEntry(_slug_title(path), system, str(path.resolve()), path.name))
        return items

    def find(self, query: str, system: str | None = None) -> tuple[GameEntry | None, list[GameEntry]]:
        query_norm = _norm(query)
        games = self.scan(system)
        if not query_norm:
            return None, []
        exact = [g for g in games if _norm(g.title) == query_norm or _norm(Path(g.filename).stem) == query_norm]
        if len(exact) == 1:
            return exact[0], exact
        if len(exact) > 1:
            return None, exact[:8]
        scored = []
        for g in games:
            title = _norm(g.title)
            ratio = difflib.SequenceMatcher(None, query_norm, title).ratio()
            if query_norm in title:
                ratio += 0.30
            scored.append((ratio, g))
        scored.sort(key=lambda x: x[0], reverse=True)
        candidates = [g for score, g in scored[:8] if score >= 0.48]
        if not candidates:
            return None, []
        if len(candidates) == 1 or (scored[0][0] >= 0.82 and (len(scored) < 2 or scored[0][0] - scored[1][0] >= 0.12)):
            return candidates[0], candidates
        return None, candidates

    # ------------------------------------------------------------------
    # Removable-drive ROM import
    # ------------------------------------------------------------------
    @staticmethod
    def _relative_import_path(path: Path, source_root: Path) -> Path:
        rel = path.relative_to(source_root)
        parts = list(rel.parts)
        # Strip an outer GameCube/GC folder wherever it appears on the removable
        # drive, while preserving folders beneath it (for example Zelda/...).
        for index, part in enumerate(parts[:-1]):
            if _norm(part) in {"gamecube", "game cube", "gc"}:
                tail = parts[index + 1:]
                return Path(*tail) if tail else Path(path.name)
        return rel

    @staticmethod
    def _unique_destination(path: Path) -> Path:
        if not path.exists():
            return path
        for index in range(2, 10000):
            candidate = path.with_name(f"{path.stem} (imported {index}){path.suffix}")
            if not candidate.exists():
                return candidate
        raise GameError(f"Could not choose a unique destination for {path.name}")

    def _load_import_index(self) -> dict[str, dict]:
        path = self.config_dir / "rom-import-index.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            rows = data.get("sha256", {}) if isinstance(data, dict) else {}
            return rows if isinstance(rows, dict) else {}
        except Exception:
            return {}

    def _save_import_index(self, rows: dict[str, dict]) -> None:
        path = self.config_dir / "rom-import-index.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"sha256": rows}, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(tmp, path)

    def import_from_roots(self, source_roots: list[str | Path]) -> dict:
        """Copy supported GameCube images from removable roots into Bemo.

        Imports are copy-only: the source USB is left unchanged. Every completed
        copy is SHA-256 verified before its temporary file is atomically renamed.
        A persistent verified-hash index makes rerunning an interrupted/repeated
        import idempotent without re-hashing an entire library of fixed-size ISOs.
        """
        self.ensure_layout()
        roots: list[Path] = []
        seen_roots: set[str] = set()
        for raw in source_roots:
            try:
                root = Path(raw).expanduser().resolve()
            except OSError:
                continue
            key = str(root)
            if key in seen_roots or not root.is_dir() or not os.access(root, os.R_OK):
                continue
            seen_roots.add(key)
            roots.append(root)

        if not roots:
            return {
                "ok": False, "reason": "no-removable-drive",
                "error": "No readable removable drive was found.",
                "source_roots": [], "discovered_count": 0, "imported_count": 0,
                "duplicate_count": 0, "error_count": 0,
            }

        import_index = self._load_import_index()
        # Drop stale index entries. The hash remains trusted only while the exact
        # indexed destination still exists at the recorded size.
        for sha, row in list(import_index.items()):
            try:
                indexed = Path(str(row.get("path", ""))).expanduser()
                stat = indexed.stat()
                if (
                    not indexed.is_file()
                    or stat.st_size != int(row.get("bytes", -1))
                    or stat.st_mtime_ns != int(row.get("mtime_ns", -1))
                ):
                    import_index.pop(sha, None)
            except Exception:
                import_index.pop(sha, None)

        candidates: list[tuple[Path, Path]] = []
        seen_sources: set[str] = set()
        library_root = self.gamecube_dir.resolve()
        for root in roots:
            for path in root.rglob("*"):
                try:
                    if path.is_symlink() or not path.is_file() or path.suffix.lower() not in GAMECUBE_EXTENSIONS:
                        continue
                    resolved = path.resolve()
                    try:
                        resolved.relative_to(library_root)
                        continue
                    except ValueError:
                        pass
                    key = str(resolved)
                    if key in seen_sources:
                        continue
                    seen_sources.add(key)
                    candidates.append((root, resolved))
                except OSError:
                    continue
        candidates.sort(key=lambda pair: str(pair[1]).lower())

        imported: list[dict] = []
        duplicates: list[dict] = []
        errors: list[dict] = []
        renamed_conflicts = 0

        for root, source in candidates:
            partial: Path | None = None
            try:
                size = source.stat().st_size
                source_hash = _sha256_file(source)

                indexed = import_index.get(source_hash)
                if indexed:
                    existing = Path(str(indexed.get("path", "")))
                    if existing.is_file() and existing.stat().st_size == size:
                        duplicates.append({
                            "source": str(source),
                            "existing": str(existing),
                            "sha256": source_hash,
                        })
                        continue

                rel = self._relative_import_path(source, root)
                destination = self.gamecube_dir / rel
                destination.parent.mkdir(parents=True, exist_ok=True)
                if destination.exists():
                    if destination.is_file() and destination.stat().st_size == size and _sha256_file(destination) == source_hash:
                        import_index[source_hash] = {
                            "path": str(destination), "bytes": size,
                            "mtime_ns": destination.stat().st_mtime_ns,
                        }
                        duplicates.append({
                            "source": str(source),
                            "existing": str(destination),
                            "sha256": source_hash,
                        })
                        continue
                    destination = self._unique_destination(destination)
                    renamed_conflicts += 1

                partial = destination.with_name(destination.name + ".bemo-import-part")
                partial.unlink(missing_ok=True)
                with open(source, "rb") as src, open(partial, "wb") as dst:
                    shutil.copyfileobj(src, dst, length=4 * 1024 * 1024)
                    dst.flush()
                    os.fsync(dst.fileno())
                if partial.stat().st_size != size:
                    raise GameError("copied size does not match source")
                copied_hash = _sha256_file(partial)
                if copied_hash != source_hash:
                    raise GameError("SHA-256 verification failed after copy")
                os.replace(partial, destination)
                partial = None
                try:
                    shutil.copystat(source, destination)
                except OSError:
                    pass
                import_index[source_hash] = {
                    "path": str(destination), "bytes": size,
                    "mtime_ns": destination.stat().st_mtime_ns,
                }
                self._save_import_index(import_index)
                imported.append({
                    "source": str(source),
                    "destination": str(destination),
                    "bytes": size,
                    "sha256": source_hash,
                    "title": _slug_title(destination),
                })
            except Exception as exc:
                if partial is not None:
                    try:
                        partial.unlink(missing_ok=True)
                    except OSError:
                        pass
                errors.append({"source": str(source), "error": str(exc)})

        self._save_import_index(import_index)
        library_games = self.scan("gamecube")
        return {
            "ok": not errors,
            "source_roots": [str(root) for root in roots],
            "discovered_count": len(candidates),
            "imported_count": len(imported),
            "duplicate_count": len(duplicates),
            "error_count": len(errors),
            "renamed_conflict_count": renamed_conflicts,
            "library_count": len(library_games),
            "imported": imported,
            "duplicates": duplicates,
            "errors": errors,
        }

    # ------------------------------------------------------------------
    # Dolphin discovery and launch commands
    # ------------------------------------------------------------------
    def _flatpak_dolphin_installed(self) -> bool:
        flatpak = shutil.which("flatpak")
        if not flatpak:
            return False
        try:
            result = subprocess.run(
                [flatpak, "info", DOLPHIN_FLATPAK_ID],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
                check=False,
            )
            return result.returncode == 0
        except (OSError, subprocess.SubprocessError):
            return False

    def dolphin_prefix(self) -> list[str] | None:
        # Advanced users can point Bemo at a custom/native Dolphin command.
        # The value is tokenized into argv; it is never passed through a shell.
        override = os.environ.get("BEMO_DOLPHIN_BINARY", "").strip()
        if override:
            parts = shlex.split(override)
            if parts:
                first = Path(parts[0]).expanduser()
                found = str(first.resolve()) if first.is_file() else shutil.which(parts[0])
                if found:
                    return [found, *parts[1:]]

        for name in ("dolphin-emu", "dolphin"):
            found = shutil.which(name)
            if found:
                return [found]

        if self._flatpak_dolphin_installed():
            return [shutil.which("flatpak") or "flatpak", "run", DOLPHIN_FLATPAK_ID]
        return None

    def _picture_shape(self) -> list[str]:
        """Stretch to fill on a CRT behind a converter that squeezes 16:9 into 4:3 (systems/display.py); the game's
        own shape everywhere else."""
        try:
            import config
            from systems.display import games_should_stretch, saved_for_connected
            stretch = games_should_stretch(saved_for_connected(config.DISPLAY_SETTINGS_PATH))
        except Exception:
            logging.getLogger("bemo-games").exception("Could not read the screen's settings")
            stretch = False
        logging.getLogger("bemo-games").info("Game picture: %s", "stretched to fill (squeezed CRT)" if stretch
                                             else "the game's own shape")
        return ["-C", f"Graphics.Settings.AspectRatio={3 if stretch else 0}"]  # Dolphin: 0 auto, 3 stretch

    def command_for(self, game: GameEntry, emulator: str | None = None) -> list[str]:
        del emulator  # GameCube-only: Dolphin is the sole emulator by design.
        self.ensure_layout()
        rom = Path(game.path).resolve()
        try:
            rom.relative_to(self.gamecube_dir)
        except ValueError as exc:
            raise GameError("Refusing to launch a game outside Bemo's GameCube library") from exc
        if not rom.is_file():
            raise GameError(f"Game image does not exist: {rom}")
        if rom.suffix.lower() not in GAMECUBE_EXTENSIONS:
            raise GameError(f"Unsupported GameCube image type: {rom.suffix}")
        if game.system != "gamecube":
            raise GameError(f"Unsupported system: {game.system}")

        dolphin = self.dolphin_prefix()
        if not dolphin:
            raise GameError(
                "Dolphin is not installed. Install dolphin-emu or the "
                f"{DOLPHIN_FLATPAK_ID} Flatpak, then rerun game_launcher.py doctor."
            )

        # -u isolates all Dolphin settings/saves under ~/.bemo/games.
        # -b closes Dolphin when emulation stops. The CLI config layer ("Dolphin." is Dolphin.ini) forces
        # fullscreen without permanently rewriting the user's Dolphin settings.
        # Gamepads plugged in now get players 1-4 (systems/gamepads.py); Dolphin's log names the devices it
        # really found, to compare with SDL's names here.
        from systems import gamepads
        ports = gamepads.prepare(self.dolphin_user_dir / "Config", self.config_dir / "gamepads.json",
                                 gamepads.connected())
        return [
            *dolphin,
            "-u", str(self.dolphin_user_dir),
            "-b",
            "-C", "Dolphin.Display.Fullscreen=True",
            "-C", "Dolphin.Interface.ConfirmStop=False",  # BMO closing the game must not wait on "are you sure?"
            *ports,
            *self._picture_shape(),
            "-C", "Logger.Logs.CONTROLLERINTERFACE=True",
            "-C", "Logger.Options.WriteToFile=True",
            "-e", str(rom),
        ]

    def frontend_binary(self) -> Path | str | None:
        override = os.environ.get("BEMO_ESDE_BINARY")
        if override:
            p = Path(override).expanduser()
            if p.is_file():
                return p.resolve()
            found = shutil.which(override)
            if found:
                return found
        if self.esde_appimage.is_file():
            return self.esde_appimage
        return shutil.which("es-de") or shutil.which("emulationstation")

    def frontend_command(self, width: int = 640, height: int = 480) -> list[str]:
        self.ensure_layout()
        binary = self.frontend_binary()
        if not binary:
            raise GameError("ES-DE is not installed; run game_launcher.py setup --fetch-esde")
        return [
            str(binary),
            "--home", str(self.esde_home),
            "--resolution", str(int(width)), str(int(height)),
            "--fullscreen-padding", "1",
            "--no-splash",
        ]

    # ------------------------------------------------------------------
    # Setup/download/health
    # ------------------------------------------------------------------
    def fetch_esde(self, metadata_url: str) -> dict:
        self.ensure_layout()
        import requests

        response = requests.get(metadata_url, timeout=30)
        response.raise_for_status()
        data = response.json()
        stable = data.get("stable") or {}
        machine = platform.machine().lower()
        wanted = "LinuxAArch64AppImage" if machine in {"aarch64", "arm64"} else "LinuxAppImage"
        package = next((p for p in stable.get("packages", []) if p.get("name") == wanted), None)
        if not package or not package.get("url"):
            raise GameError(f"No ES-DE AppImage metadata found for {machine}")
        tmp = self.esde_appimage.with_suffix(".download")
        existing = tmp.stat().st_size if tmp.exists() else 0
        headers = {"Accept-Encoding": "identity"}
        if existing:
            headers["Range"] = f"bytes={existing}-"

        with requests.get(package["url"], stream=True, timeout=(20, 90), headers=headers) as r:
            # A proper resume returns 206. If the host ignores Range and returns
            # 200, restart this one file rather than appending duplicate bytes.
            if existing and r.status_code == 206:
                mode = "ab"
            elif existing and r.status_code == 416:
                mode = None  # likely already complete; checksum below decides
            else:
                r.raise_for_status()
                mode = "wb"
            if mode is not None:
                with open(tmp, mode) as handle:
                    for chunk in r.iter_content(chunk_size=1024 * 1024):
                        if chunk:
                            handle.write(chunk)

        # Hash the complete partial/final file so resumed downloads are verified
        # exactly the same way as a one-shot download.
        if not tmp.is_file():
            raise GameError("ES-DE download did not produce a file")
        md5 = hashlib.md5()
        with open(tmp, "rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                md5.update(chunk)
        expected = str(package.get("md5") or "").lower()
        if expected and md5.hexdigest().lower() != expected:
            # Keep a network-interrupted partial, but discard a fully received
            # corrupt/wrong file after checksum failure so the next run is clean.
            tmp.unlink(missing_ok=True)
            raise GameError("ES-DE download checksum did not match release metadata")
        os.replace(tmp, self.esde_appimage)
        self.esde_appimage.chmod(0o755)
        return {"ok": True, "version": stable.get("version"), "path": str(self.esde_appimage), "md5": md5.hexdigest()}

    def doctor(self) -> dict:
        self.ensure_layout()
        games = self.scan()
        dolphin = self.dolphin_prefix()
        frontend = self.frontend_binary()
        return {
            "ok": bool(frontend and dolphin),
            "frontend": str(frontend or ""),
            "dolphin_command": dolphin or [],
            "game_counts": {
                "gamecube": len(games),
                "total": len(games),
            },
            "paths": self.paths(),
            "voice_close_command": "Hey Bemo, close current game.",
            "notes": [
                "GameCube BIOS/IPL is not required for normal Dolphin game launching.",
                "Controller mapping is stored in Bemo's dedicated Dolphin user folder after configuration.",
            ],
        }
