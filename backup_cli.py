#!/usr/bin/env python3
"""Create a verified full Bemo backup on the first writable removable drive."""
import config
from systems.backup import FullBackupManager
from systems.persistence import MemoryStore
from systems.projects import ProjectManager


def main():
    store = MemoryStore(config.MEMORY_DB_PATH, backup_dir=config.MEMORY_BACKUP_DIR)
    pm = ProjectManager(
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
    try:
        manager = FullBackupManager(data_root=config.DATA_ROOT, app_root=config.APP_ROOT, removable_provider=pm.removable_drives)
        print(manager.create(memory_store=store, create_zip=True))
    finally:
        store.close()


if __name__ == "__main__":
    main()
