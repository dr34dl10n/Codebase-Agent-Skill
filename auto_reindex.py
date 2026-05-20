#!/usr/bin/env python3
"""Auto-reindex all registered repositories.

Called by cron. For each project in the database, runs incremental reindex
(purge deleted files + re-embed modified files). Only reports if changes were made.

Usage:
    .venv/bin/python3 auto_reindex.py
    .venv/bin/python3 auto_reindex.py --force   # force full reindex of everything
"""

import json
import sys
import os
from pathlib import Path

# Auto-load env (same as mcp_server.py / config.py)
def _load_env_file(path: Path) -> None:
    if not path.is_file():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
            value = value[1:-1]
        if key and key not in os.environ:
            os.environ[key] = value

_load_env_file(Path.home() / ".hermes" / ".env")

sys.path.insert(0, str(Path(__file__).parent))

from config import AppConfig
from indexer import CodeIndexer


def main():
    force = "--force" in sys.argv
    config = AppConfig()

    with CodeIndexer(config) as indexer:
        projects = indexer.list_projects()

    if not projects:
        print("No indexed repositories. Nothing to do.")
        return

    results = []
    for proj in projects:
        repo_path = proj["path"]
        if not Path(repo_path).is_dir():
            results.append(f"SKIP {repo_path} (directory gone)")
            continue

        with CodeIndexer(config) as indexer:
            stats = indexer.index_repository(
                repo_path=repo_path,
                force_reindex=force,
            )

        if stats["files_processed"] > 0 or stats["orphan_chunks_purged"] > 0:
            results.append(
                f"OK {repo_path}: {stats['files_processed']} files, "
                f"{stats['chunks_stored']} stored, "
                f"{stats['orphan_chunks_purged']} orphans purged "
                f"({stats['elapsed_seconds']}s)"
            )
        else:
            results.append(f"OK {repo_path}: up to date")

    # Print summary — cron will deliver this
    print(f"Auto-reindex report ({'force' if force else 'incremental'}):")
    for r in results:
        print(f"  {r}")


if __name__ == "__main__":
    main()