#!/usr/bin/env python3
"""CLI for codebase-skill: index and search code repositories.

Usage:
    codebase-skill index <repo_path> [--force] [--name NAME]
    codebase-skill search <query> [--top-k N] [--lang LANG] [--file PATTERN] [--repo PATH]
    codebase-skill file-context <file_path> [--focus QUERY] [--top-k N]
    codebase-skill stats [--repo PATH]
    codebase-skill remove <repo_path>
    codebase-skill serve [--host HOST] [--port PORT]
    codebase-skill init-db
"""

import argparse
import json
import sys

from config import AppConfig
from indexer import CodeIndexer
from search import CodeSearcher


def cmd_init_db(args):
    """Print the SQL commands to initialize the database."""
    from pathlib import Path
    sql = Path(__file__).parent / "init_db.sql"
    print(f"Run this SQL as a superuser (e.g. sudo -u postgres psql -d codeindex -f {sql}):\n")
    print(sql.read_text())


def cmd_index(args):
    config = AppConfig()
    with CodeIndexer(config) as indexer:
        stats = indexer.index_repository(
            repo_path=args.repo_path,
            force_reindex=args.force,
            project_name=args.name,
        )
    print(json.dumps(stats, indent=2))


def cmd_search(args):
    config = AppConfig()
    with CodeSearcher(config) as searcher:
        results = searcher.semantic_search(
            query=args.query,
            top_k=args.top_k,
            language=args.lang,
            file_pattern=args.file,
            repo_path=args.repo,
        )
    
    if not results:
        print("No results found.")
        return
    
    for r in results:
        print(f"\n{'='*60}")
        print(f"  {r.symbol}  [{r.language}]  score={r.score}")
        print(f"  {r.file_path}:{r.start_line}-{r.end_line}")
        print(f"{'='*60}")
        # Show first ~20 lines of content
        lines = r.content.split("\n")
        for line in lines[:20]:
            print(f"  {line}")
        if len(lines) > 20:
            print(f"  ... ({len(lines) - 20} more lines)")


def cmd_file_context(args):
    config = AppConfig()
    with CodeSearcher(config) as searcher:
        ctx = searcher.get_file_context(
            file_path=args.file_path,
            focus=args.focus,
            top_k=args.top_k,
        )
    
    print(f"File: {ctx['file_path']}")
    print(f"Chunks in file: {len(ctx['file_chunks'])}")
    for chunk in ctx["file_chunks"]:
        print(f"  - {chunk['symbol']} (lines {chunk['start_line']}-{chunk['end_line']})")
    
    if ctx["related_chunks"]:
        print(f"\nRelated chunks ({len(ctx['related_chunks'])}):")
        for rc in ctx["related_chunks"]:
            print(f"  [{rc['score']:.3f}] {rc['file_path']}:{rc['symbol']}")


def cmd_stats(args):
    config = AppConfig()
    with CodeSearcher(config) as searcher:
        stats = searcher.get_stats(repo_path=args.repo)
    print(json.dumps(stats, indent=2))


def cmd_remove(args):
    config = AppConfig()
    with CodeIndexer(config) as indexer:
        deleted = indexer.remove_repository(args.repo_path)
    print(f"Deleted {deleted} chunks for {args.repo_path}")


def cmd_serve(args):
    import uvicorn
    uvicorn.run(
        "api:app",
        host=args.host,
        port=args.port,
        log_level="info",
    )


def main():
    parser = argparse.ArgumentParser(
        prog="codebase-skill",
        description="Semantic code indexing and search via pgvector",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # init-db
    p_init = sub.add_parser("init-db", help="Print DB init SQL")
    p_init.set_defaults(func=cmd_init_db)

    # index
    p_idx = sub.add_parser("index", help="Index a repository")
    p_idx.add_argument("repo_path", help="Path to repository root")
    p_idx.add_argument("--force", action="store_true", help="Force full re-index")
    p_idx.add_argument("--name", help="Project name")
    p_idx.set_defaults(func=cmd_index)

    # search
    p_srch = sub.add_parser("search", help="Semantic search")
    p_srch.add_argument("query", help="Search query")
    p_srch.add_argument("--top-k", type=int, default=10)
    p_srch.add_argument("--lang", help="Filter by language")
    p_srch.add_argument("--file", help="File path LIKE pattern")
    p_srch.add_argument("--repo", help="Filter by repo path")
    p_srch.set_defaults(func=cmd_search)

    # file-context
    p_fc = sub.add_parser("file-context", help="File context + related chunks")
    p_fc.add_argument("file_path")
    p_fc.add_argument("--focus", help="Query to focus related search")
    p_fc.add_argument("--top-k", type=int, default=5)
    p_fc.set_defaults(func=cmd_file_context)

    # stats
    p_stats = sub.add_parser("stats", help="Indexing statistics")
    p_stats.add_argument("--repo", help="Filter by repo path")
    p_stats.set_defaults(func=cmd_stats)

    # remove
    p_rm = sub.add_parser("remove", help="Remove indexed repo")
    p_rm.add_argument("repo_path")
    p_rm.set_defaults(func=cmd_remove)

    # serve
    p_serve = sub.add_parser("serve", help="Start API server")
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=8900)
    p_serve.set_defaults(func=cmd_serve)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()