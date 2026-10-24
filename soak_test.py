#!/usr/bin/env python3
"""Repeatable offline Bemo architecture soak test.

This does not require microphone, TTS, Ollama model weights, or network access.
It repeatedly exercises persistence, memory heat, task handoffs, project facts,
asset indexing, Git snapshots, removable-drive export, full verified backup,
and the presence of the shared computer-control implementation in backups.
Use live_validation.py on the finished Bemo machine for real-model/vision checks.
"""
from __future__ import annotations

import argparse
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import config
from systems.backup import FullBackupManager
from systems.persistence import MemoryStore
from systems.projects import ProjectManager
from systems.tasks import TaskLedger


def make_pm(root: Path, usb: Path) -> ProjectManager:
    return ProjectManager(
        data_root=root,
        projects_root=root / "projects",
        index_extensions=config.PROJECT_INDEX_EXTENSIONS,
        exclude_dirs=config.PROJECT_EXCLUDE_DIRS,
        max_file_bytes=config.PROJECT_FILE_MAX_BYTES,
        context_char_budget=config.PROJECT_CONTEXT_CHAR_BUDGET,
        retrieval_chunks=config.PROJECT_RETRIEVAL_CHUNKS,
        reindex_interval_seconds=0,
        auto_git=True,
        git_name="Bemo Soak",
        git_email="bemo-soak@local",
        export_root_override=usb,
        export_include_git=True,
        export_create_zip=False,
        memory_history_retain=config.PROJECT_MEMORY_HISTORY_RETAIN,
    )


def run_cycle(root: Path, usb: Path, cycle: int) -> None:
    store = MemoryStore(root / "memory" / "conversation.sqlite3", root / "memory" / "backups")
    session = store.start_session(cpu_name="soak", app_version=config.APP_VERSION)
    pm = make_pm(root, usb)
    if not pm.list_projects():
        pm.create_project("Soak Project")
    else:
        pm.open_project("Soak Project")

    pm.write_workspace_file("state.txt", f"cycle={cycle}\n")
    (pm.active_workspace / f"image-{cycle}.png").write_bytes(b"\x89PNG\r\n\x1a\nsoak")
    pm.index_project(force=True)
    assert pm.search_assets(f"image-{cycle}", kind="image")

    key = "qualified current cycle"
    pm.set_project_fact(key, str(cycle), source="soak")
    assert any(f["value"] == str(cycle) for f in pm.project_facts() if f["key"] == key)

    task_ledger = TaskLedger(root / "tasks")
    task = task_ledger.begin_task(
        f"Soak task {cycle}", project_slug=pm.active_slug, current_role="ENGINEERING"
    )
    task_ledger.checkpoint(
        role="ENGINEERING",
        summary=f"Engineering checkpoint {cycle}",
        remaining_work="Coding continuation",
        next_role="HEAVY_CODING",
    )
    task_ledger.handoff(from_role="ENGINEERING", to_role="HEAVY_CODING")
    assert "Engineering checkpoint" in task_ledger.context()
    task_ledger.complete(role="HEAVY_CODING", final_result="done")

    msg = store.add_message(
        session_id=session,
        role="user",
        content=f"soak durable memory cycle {cycle}",
        project_slug=pm.active_slug,
        model_profile="normal",
        model_name="soak-model",
    )
    now = datetime.now(timezone.utc)
    store.reinforce_messages([msg], now=now)
    hits = store.search_messages(
        f"soak durable memory cycle {cycle}", active_project_slug=pm.active_slug, now=now
    )
    assert hits and hits[0]["id"] == msg
    cooled = store.search_messages(
        f"soak durable memory cycle {cycle}",
        active_project_slug=pm.active_slug,
        now=now + timedelta(days=180),
    )
    assert cooled

    snap = pm.snapshot(f"Bemo: soak cycle {cycle}")
    assert snap.get("ok")
    store.end_session(session)
    store.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cycles", type=int, default=20)
    args = parser.parse_args()
    cycles = max(1, min(500, args.cycles))

    with tempfile.TemporaryDirectory(prefix="bemo-soak-data-") as data_temp, tempfile.TemporaryDirectory(prefix="bemo-soak-usb-") as usb_temp:
        root = Path(data_temp)
        usb = Path(usb_temp)
        for cycle in range(1, cycles + 1):
            run_cycle(root, usb, cycle)

        # Reboot-style reopen and verified export/backup at the end.
        pm = make_pm(root, usb)
        assert pm.active_slug == "soak-project"
        assert pm.project_facts()
        exported = pm.export_active_project()
        assert exported.get("ok"), exported

        store = MemoryStore(root / "memory" / "conversation.sqlite3", root / "memory" / "backups")
        assert store.integrity_check() == "ok"
        with tempfile.TemporaryDirectory(prefix="bemo-soak-backup-drive-") as backup_temp:
            manager = FullBackupManager(
                data_root=root,
                app_root=Path(__file__).resolve().parent,
                removable_provider=lambda: [Path(backup_temp)],
            )
            result = manager.create(memory_store=store, create_zip=False)
            assert result.get("ok"), result
            assert manager.verify_folder(result["folder"]).get("ok")
            assert (Path(result["folder"]) / "app" / "systems" / "tasks.py").is_file()
            assert (Path(result["folder"]) / "app" / "systems" / "computer.py").is_file()
            restore_root = root.parent / (root.name + "-restored")
            restored = manager.restore_data_to(result["folder"], restore_root)
            assert restored.get("ok"), restored
            restored_store = MemoryStore(
                restore_root / "memory" / "conversation.sqlite3",
                restore_root / "memory" / "backups",
            )
            assert restored_store.integrity_check() == "ok"
            restored_store.close()
            app_restore_root = root.parent / (root.name + "-app-restored")
            app_restored = manager.restore_app_to(result["folder"], app_restore_root)
            assert app_restored.get("ok"), app_restored
            assert (app_restore_root / "systems" / "tasks.py").is_file()
            assert (app_restore_root / "systems" / "computer.py").is_file()
            import shutil
            shutil.rmtree(restore_root, ignore_errors=True)
            shutil.rmtree(app_restore_root, ignore_errors=True)
        store.close()

    print(f"Bemo offline soak test: PASS ({cycles} cycles)")


if __name__ == "__main__":
    main()
