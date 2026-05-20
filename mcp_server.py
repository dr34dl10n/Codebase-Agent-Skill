#!/usr/bin/env python3
"""MCP server for codebase-skill: exposes semantic code search tools.

Run as stdio MCP server. Hermes Agent connects to this via mcp_servers config.

Tools exposed:
  - search: Semantic search across indexed codebases
  - file_context: Get a file's chunks + related chunks
  - stats: Indexing statistics
  - reindex: Refresh an indexed repository (detects changes + deleted files)
  - list_projects: List all indexed repositories
"""

import json
import sys
from pathlib import Path

# Auto-load ~/.hermes/.env before config import
def _load_env_file(path: Path) -> None:
    if not path.is_file():
        return
    import os
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
            value = value[1:-1]
        if key and key not in os.environ:
            os.environ[key] = value

_load_env_file(Path.home() / ".hermes" / ".env")

import mcp.server.stdio as stdio_server
from mcp.server import Server
from mcp.types import (
    TextContent,
    Tool,
)

from config import AppConfig
from search import CodeSearcher
from indexer import CodeIndexer

app = Server("codebase-skill")

# Lazy factories — created per call to avoid stale connections
def _search() -> CodeSearcher:
    config = AppConfig()
    return CodeSearcher(config)

def _indexer() -> CodeIndexer:
    config = AppConfig()
    return CodeIndexer(config)


@app.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="search",
            description=(
                "Semantic search across indexed codebases. Returns relevant code chunks "
                "ranked by cosine similarity to the query. Use this BEFORE reading entire "
                "files or doing deep analysis — it finds the most relevant code in seconds. "
                "Supports filters: language, file pattern, repo path."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Natural language or code query to search for.",
                    },
                    "top_k": {
                        "type": "integer",
                        "description": "Maximum number of results (default: 10).",
                        "default": 10,
                    },
                    "language": {
                        "type": "string",
                        "description": "Filter by programming language (e.g. python, javascript).",
                    },
                    "file_pattern": {
                        "type": "string",
                        "description": "SQL LIKE pattern for file path (e.g. '%/auth%').",
                    },
                    "repo_path": {
                        "type": "string",
                        "description": "Restrict search to a specific repository path.",
                    },
                    "min_score": {
                        "type": "number",
                        "description": "Minimum cosine similarity 0-1 (default: 0.3).",
                        "default": 0.3,
                    },
                },
                "required": ["query"],
            },
        ),
        Tool(
            name="file_context",
            description=(
                "Get a file's code chunks plus semantically related chunks from other files. "
                "Use when you need to understand a specific file and its dependencies. "
                "The 'focus' parameter targets the related-chunk search."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Absolute path to the file.",
                    },
                    "focus": {
                        "type": "string",
                        "description": "Query to focus the related-chunk search.",
                    },
                    "top_k": {
                        "type": "integer",
                        "description": "Number of related chunks (default: 5).",
                        "default": 5,
                    },
                },
                "required": ["file_path"],
            },
        ),
        Tool(
            name="stats",
            description="Get indexing statistics: total chunks, files, languages. Optional repo_path filter.",
            inputSchema={
                "type": "object",
                "properties": {
                    "repo_path": {
                        "type": "string",
                        "description": "Optional repo path filter.",
                    },
                },
            },
        ),
        Tool(
            name="reindex",
            description=(
                "Refresh an indexed repository. Detects modified files (mtime-based), "
                "re-parses and re-embeds them, and purges chunks for files that were "
                "deleted from disk. Use force_reindex=true to re-index everything. "
                "Call this after pulling code changes or when search results seem stale."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "repo_path": {
                        "type": "string",
                        "description": "Absolute path to the repository root.",
                    },
                    "force_reindex": {
                        "type": "boolean",
                        "description": "If true, re-index all files regardless of mtime (default: false).",
                        "default": False,
                    },
                },
                "required": ["repo_path"],
            },
        ),
        Tool(
            name="list_projects",
            description="List all indexed repositories with their path, last indexed time, and chunk count.",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
    ]


@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    try:
        if name == "search":
            with _search() as searcher:
                results = searcher.semantic_search(
                    query=arguments["query"],
                    top_k=arguments.get("top_k", 10),
                    language=arguments.get("language"),
                    file_pattern=arguments.get("file_pattern"),
                    repo_path=arguments.get("repo_path"),
                    min_score=arguments.get("min_score", 0.3),
                )
            if not results:
                return [TextContent(type="text", text="No results found.")]
            lines = []
            for r in results:
                lines.append(f"{'='*60}")
                lines.append(f"  {r.symbol}  [{r.language}]  score={r.score}")
                lines.append(f"  {r.file_path}:{r.start_line}-{r.end_line}")
                lines.append(f"{'='*60}")
                content_lines = r.content.split("\n")
                for cl in content_lines[:25]:
                    lines.append(f"  {cl}")
                if len(content_lines) > 25:
                    lines.append(f"  ... ({len(content_lines) - 25} more lines)")
            return [TextContent(type="text", text="\n".join(lines))]

        elif name == "file_context":
            with _search() as searcher:
                ctx = searcher.get_file_context(
                    file_path=arguments["file_path"],
                    focus=arguments.get("focus"),
                    top_k=arguments.get("top_k", 5),
                )
            out_lines = [f"File: {ctx['file_path']}"]
            out_lines.append(f"Chunks in file: {len(ctx['file_chunks'])}")
            for chunk in ctx["file_chunks"]:
                out_lines.append(
                    f"  - {chunk['symbol']} (lines {chunk['start_line']}-{chunk['end_line']})"
                )
            if ctx["related_chunks"]:
                out_lines.append(f"\nRelated chunks ({len(ctx['related_chunks'])}):")
                for rc in ctx["related_chunks"]:
                    out_lines.append(f"  [{rc['score']:.3f}] {rc['file_path']}:{rc['symbol']}")
            return [TextContent(type="text", text="\n".join(out_lines))]

        elif name == "stats":
            with _search() as searcher:
                stats = searcher.get_stats(repo_path=arguments.get("repo_path"))
            return [TextContent(type="text", text=json.dumps(stats, indent=2))]

        elif name == "reindex":
            with _indexer() as indexer:
                result = indexer.index_repository(
                    repo_path=arguments["repo_path"],
                    force_reindex=arguments.get("force_reindex", False),
                )
            return [TextContent(type="text", text=json.dumps(result, indent=2, default=str))]

        elif name == "list_projects":
            with _indexer() as indexer:
                projects = indexer.list_projects()
            if not projects:
                return [TextContent(type="text", text="No indexed repositories.")]
            lines = []
            for p in projects:
                lines.append(f"  {p['path']}")
                lines.append(f"    chunks: {p['total_chunks']}  last_indexed: {p['last_indexed'] or 'never'}")
            return [TextContent(type="text", text="\n".join(lines))]

        else:
            return [TextContent(type="text", text=f"Unknown tool: {name}")]
    except Exception as e:
        return [TextContent(type="text", text=f"Error: {e}")]


async def main():
    async with stdio_server.stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())