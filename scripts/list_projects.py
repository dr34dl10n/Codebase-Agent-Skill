#!/data/codebase-skill/.venv/bin/python3
"""List all indexed projects in pgvector with their last indexation date.

Goes beyond the simple `projects` table by joining (via path prefix) with
`code_chunks`, so each row also shows the *real* chunk count, file count,
distinct languages, and the most recent chunk `updated_at` — useful to spot
drift between the recorded `last_indexed` and what's actually in the DB.

Usage:
  cbprojects                 # human-readable table (default)
  cbprojects --json          # machine-readable JSON
  cbprojects --repo PATH     # restrict to one project (path prefix match)

The query is a single LEFT JOIN with a prefix match
    code_chunks.file_path LIKE projects.path || '/%'
(the '/' avoids accidental cross-project prefix collisions, e.g.
/data/foo vs /data/foobar). It runs entirely server-side in Postgres.
"""
import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _load_env(path: Path):
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


_load_env(Path.home() / ".hermes" / ".env")

import psycopg  # noqa: E402
from config import AppConfig  # noqa: E402

# Single complex query — kept here so it lives next to its callers and can be
# tuned without grepping the indexer. Returns one row per project with:
#   path, last_indexed, declared_total_chunks, real_chunks,
#   real_files, distinct_languages, latest_chunk_updated_at
QUERY = """
SELECT
    p.path,
    p.last_indexed,
    p.total_chunks                          AS declared_total_chunks,
    COALESCE(c.real_chunks, 0)              AS real_chunks,
    COALESCE(c.real_files, 0)               AS real_files,
    COALESCE(c.real_languages, 0)           AS real_languages,
    c.latest_chunk_updated_at               AS latest_chunk_updated_at
FROM projects p
LEFT JOIN LATERAL (
    SELECT
        COUNT(*)                                                AS real_chunks,
        COUNT(DISTINCT file_path)                               AS real_files,
        COUNT(DISTINCT language) FILTER (WHERE language IS NOT NULL)
                                                                    AS real_languages,
        MAX(updated_at)                                         AS latest_chunk_updated_at
    FROM code_chunks
    WHERE file_path LIKE p.path || '/%'
) c ON true
ORDER BY p.last_indexed DESC NULLS LAST;
"""


def fetch(repo_filter: str | None = None) -> list[dict]:
    config = AppConfig()
    with psycopg.connect(config.db.dsn) as conn:
        with conn.cursor() as cur:
            if repo_filter:
                # Restrict to one project (path prefix match).
                cur.execute(
                    QUERY.replace(
                        "ORDER BY p.last_indexed DESC NULLS LAST;",
                        "WHERE p.path LIKE %s || '%%'\n"
                        "ORDER BY p.last_indexed DESC NULLS LAST;",
                    ),
                    (repo_filter.rstrip("/"),),
                )
            else:
                cur.execute(QUERY)
            cols = [d.name for d in cur.description]
            rows = cur.fetchall()
    out = []
    for r in rows:
        row = dict(zip(cols, r))
        # Serialize timestamps to ISO strings for JSON friendliness.
        for k in ("last_indexed", "latest_chunk_updated_at"):
            v = row.get(k)
            row[k] = v.isoformat() if v is not None else None
        out.append(row)
    return out


def _fmt(ts: str | None) -> str:
    return ts.replace("T", " ").split("+")[0] if ts else "never"


def print_table(rows: list[dict]) -> None:
    if not rows:
        print("No indexed projects found.")
        return
    # Column widths
    wpath = max(len(r["path"]) for r in rows)
    wpath = max(wpath, len("path"))
    hdr = f"{'path':<{wpath}}  {'last_indexed':<20}  {'chunks':>8}  {'files':>7}  {'langs':>6}  {'latest_chunk':<20}"
    print(hdr)
    print("-" * len(hdr))
    for r in rows:
        print(
            f"{r['path']:<{wpath}}  "
            f"{_fmt(r['last_indexed']):<20}  "
            f"{r['real_chunks']:>8}  "
            f"{r['real_files']:>7}  "
            f"{r['real_languages']:>6}  "
            f"{_fmt(r['latest_chunk_updated_at']):<20}"
        )
    print(f"\n{len(rows)} project(s).")
    # Drift warnings
    for r in rows:
        decl = r["declared_total_chunks"] or 0
        real = r["real_chunks"] or 0
        if decl != real:
            print(f"  ⚠ {r['path']}: declared chunks ({decl}) != real ({real}) — reindex needed")


def main():
    p = argparse.ArgumentParser(description="List indexed pgvector projects + last index date")
    p.add_argument("--json", action="store_true", help="Emit JSON instead of a table")
    p.add_argument("--repo", help="Restrict to projects whose path starts with this prefix")
    args = p.parse_args()

    rows = fetch(repo_filter=args.repo)
    if args.json:
        print(json.dumps(rows, indent=2))
    else:
        print_table(rows)


if __name__ == "__main__":
    main()