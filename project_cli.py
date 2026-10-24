"""Offline command-line project manager for Bemo.

Useful for importing an existing folder before using the voice interface.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import config
from systems.projects import ProjectManager
from systems.games import discover_removable_drives


def manager() -> ProjectManager:
    return ProjectManager(
        data_root=config.DATA_ROOT,
        projects_root=config.PROJECTS_ROOT,
        index_extensions=config.PROJECT_INDEX_EXTENSIONS,
        exclude_dirs=config.PROJECT_EXCLUDE_DIRS,
        max_file_bytes=config.PROJECT_FILE_MAX_BYTES,
        context_char_budget=config.PROJECT_CONTEXT_CHAR_BUDGET,
        retrieval_chunks=config.PROJECT_RETRIEVAL_CHUNKS,
        reindex_interval_seconds=config.PROJECT_REINDEX_INTERVAL_SECONDS,
        auto_git=config.AUTO_GIT_SNAPSHOTS,
        git_name=config.GIT_AUTHOR_NAME,
        git_email=config.GIT_AUTHOR_EMAIL,
        export_root_override=config.EXPORT_ROOT_OVERRIDE,
        export_include_git=config.EXPORT_INCLUDE_GIT_HISTORY,
        export_create_zip=config.EXPORT_CREATE_ZIP,
        memory_history_retain=config.PROJECT_MEMORY_HISTORY_RETAIN,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Bemo local project manager")
    sub = parser.add_subparsers(dest="command", required=True)

    create = sub.add_parser("create", help="Create an empty project")
    create.add_argument("name")

    imp = sub.add_parser("import", help="Copy an existing folder into Bemo storage")
    imp.add_argument("name")
    imp.add_argument("source")

    imp_usb = sub.add_parser("import-usb", help="Discover, verify, and import a project from removable media")
    imp_usb.add_argument("name", nargs="?", default="", help="Optional project/folder name when more than one candidate exists")
    sub.add_parser("import-files-usb", help="Copy and verify removable-drive files into the active project")

    sub.add_parser("list", help="List projects")

    op = sub.add_parser("open", help="Set the active project")
    op.add_argument("name")

    sub.add_parser("close", help="Clear the active project")
    sub.add_parser("status", help="Show the active project")
    sub.add_parser("index", help="Force-refresh the active project index")

    note = sub.add_parser("remember", help="Append an explicit project memory note")
    note.add_argument("text")

    snap = sub.add_parser("snapshot", help="Create a local Git snapshot")
    snap.add_argument("message", nargs="?", default="Manual Bemo snapshot")

    export_files = sub.add_parser("export-files-usb", help="Copy and verify active-project files/folders to removable media")
    export_files.add_argument("selector", nargs="+", help="Project-relative path or unique file/folder name")

    sub.add_parser("export", help="Export active project to a removable drive")
    sub.add_parser("undo", help="Revert the latest Bemo-authored Git commit")

    assets = sub.add_parser("assets", help="List/search non-text and text project assets")
    assets.add_argument("query", nargs="?", default="")
    assets.add_argument("--kind", choices=("image", "cad", "document", "text", "file"))

    facts = sub.add_parser("facts", help="Show authoritative current project facts")

    fact = sub.add_parser("set-fact", help="Set/supersede one keyed project fact")
    fact.add_argument("key")
    fact.add_argument("value")
    fact.add_argument("--category", default="decision")

    args = parser.parse_args()
    pm = manager()

    if args.command == "create":
        print(pm.create_project(args.name))
    elif args.command == "import":
        print(pm.create_project(args.name, Path(args.source)))
    elif args.command == "import-usb":
        print(pm.import_project_from_removable(discover_removable_drives(), selection=args.name or None))
    elif args.command == "import-files-usb":
        print(pm.import_files_from_removable(discover_removable_drives(), slug=pm.active_slug))
    elif args.command == "list":
        projects = pm.list_projects()
        if not projects:
            print("No projects.")
        for item in projects:
            marker = "*" if item.get("active") else " "
            print(f"{marker} {item.get('name')} [{item.get('slug')}]")
    elif args.command == "open":
        print(pm.open_project(args.name))
    elif args.command == "close":
        pm.close_project()
        print("No active project.")
    elif args.command == "status":
        print(pm.active_metadata() or "No active project.")
    elif args.command == "index":
        print(pm.index_project(force=True))
    elif args.command == "remember":
        pm.append_project_note(args.text)
        print("Project note saved.")
    elif args.command == "snapshot":
        print(pm.snapshot(args.message))
    elif args.command == "export-files-usb":
        print(pm.export_project_items_to_removable(args.selector, slug=pm.active_slug))
    elif args.command == "export":
        print(pm.export_active_project())
    elif args.command == "undo":
        print(pm.undo_last_bemo_change())
    elif args.command == "assets":
        rows = pm.search_assets(args.query, kind=args.kind, limit=100) if args.query else pm.list_assets(kind=args.kind, limit=100)
        for row in rows:
            print(f"{row['kind']:8} {row['size']:>10}  {row['path']}")
    elif args.command == "facts":
        print(pm.current_facts_text() or "No current project facts.")
    elif args.command == "set-fact":
        print(pm.set_project_fact(args.key, args.value, category=args.category, source="cli"))


if __name__ == "__main__":
    main()
