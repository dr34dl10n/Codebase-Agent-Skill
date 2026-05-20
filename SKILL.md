---
name: codebase-skill
description: "Use when indexing or semantically searching a codebase. Tree-sitter parsing, pgvector storage, embedding service for RAG."
version: 1.2.0
author: Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [codebase, indexing, semantic-search, pgvector, tree-sitter, rag]
    related_skills: [writing-plans, github-pr-workflow]
---

# Codebase Skill — Semantic Code Indexing & Search

## Overview

Turn any code repository into a searchable knowledge base using Tree-sitter parsing, embedding vectors (Ollama-compatible API), and pgvector. Instead of loading entire files into context, search surgically for the chunks you need.

## When to Use

- Agent needs to understand a large codebase without reading every file
- User asks "where is X implemented?" or "how does Y work?"
- Need to find all functions/classes related to a concept
- Preparing context for code review or modification
- Don't use for: tiny repos (<10 files), non-code files only, or when full file reads are sufficient

## Architecture

```
Repository → Tree-sitter parser → Semantic chunks → Embed service → pgvector
                                                                    ↓
Query → Embed service → cosine similarity search → ranked chunks
```

Components:
- `parser.py` — Tree-sitter based chunking (by function/class, not naive splitting)
- `embedder.py` — Ollama-compatible embedding API (768-dim vectors, swap model/provider freely)
- `indexer.py` — Repository walker + incremental reindexing + orphan purge
- `search.py` — Cosine similarity search with filters
- `api.py` — FastAPI server (HTTP endpoints + MCP tool definitions)
- `cli.py` — CLI interface for terminal use
- `mcp_server.py` — MCP stdio server exposing 5 tools
- `auto_reindex.py` — Cron-friendly auto-reindex for all registered repos

## Deploy on Another Agent

### Full Deploy (one command)

```bash
bash deploy.sh <db_password>
```

Creates: PostgreSQL DB + user, pgvector extension, tables, Python venv + deps.

### MCP Configuration

**Hermes Agent** — add to `~/.hermes/config.yaml`:

```yaml
mcp_servers:
  codebase-skill:
    command: /data/codebase-skill/.venv/bin/python3
    args: ["/data/codebase-skill/mcp_server.py"]
    env:
      CODEINDEX_DB_PASSWORD: "${CODEINDEX_DB_PASSWORD}"
```

**Claude Code / Cursor / other MCP clients** — add to MCP settings JSON:

```json
{
  "mcpServers": {
    "codebase-skill": {
      "command": "/path/to/codebase-skill/.venv/bin/python3",
      "args": ["/path/to/codebase-skill/mcp_server.py"],
      "env": { "CODEINDEX_DB_PASSWORD": "your_password" }
    }
  }
}
```

**Pi Agent / Codex** — same MCP stdio protocol. Adjust `command` path to the venv python on that host.

### Environment Variables

Required: `CODEINDEX_DB_PASSWORD`. All others have defaults (see `.env.example`).

The MCP server auto-loads `~/.hermes/.env` on startup. When using Hermes, you STILL need the `env` block in the MCP config because Hermes filters subprocess env vars.

### Auto-Reindex (optional cron)

```bash
# Manual run
.venv/bin/python3 auto_reindex.py

# Force full reindex of all repos
.venv/bin/python3 auto_reindex.py --force

# Hermes cron (every 4h — already configured)
# See: cronjob action='list' → codebase-auto-reindex
```

### Index First Repo

```bash
.venv/bin/python3 cli.py index /path/to/repo
```

## Keeping the Index Fresh

### Incremental Reindex

When you re-run `index` or call `reindex` on an already-indexed repo:
1. **Modified files**: compared by `mtime` vs `last_indexed` — only changed files are re-parsed and re-embedded
2. **Deleted files**: chunks for files no longer on disk are automatically purged from the DB
3. Stats include `orphan_chunks_purged` count

```bash
# Via MCP
# mcp_codebase_reindex(repo_path="/data/AIssistant")
# mcp_codebase_reindex(repo_path="/data/AIssistant", force_reindex=true)

# Via CLI
.venv/bin/python3 cli.py index /data/AIssistant   # incremental (fast)
.venv/bin/python3 cli.py index /data/AIssistant --force  # full re-index
```

### Auto-Reindex Cron

A cron job (`codebase-auto-reindex`) runs `auto_reindex.py` every 4h, which:
- Iterates all registered projects
- Runs incremental reindex on each
- Purges orphans
- Reports only if changes were made

## MCP Tools

| Tool | Parameters | Description |
|------|-----------|-------------|
| `search` | query, top_k, language, file_pattern, repo_path, min_score | Semantic search across indexed codebases |
| `file_context` | file_path, focus, top_k | File's chunks + related chunks |
| `stats` | repo_path? | Indexing statistics |
| `reindex` | repo_path, force_reindex | Refresh repo: detect changes + purge deleted files |
| `list_projects` | (none) | List all indexed repositories |

## Search Filters

- `language` — Filter by programming language (python, javascript, etc.)
- `file_pattern` — SQL LIKE pattern (e.g. `%/auth%`)
- `repo_path` — Restrict to one repository
- `min_score` — Minimum cosine similarity (0-1, default 0.3)

## Configuration

Environment variables (or defaults in `config.py`):

| Variable | Default | Purpose |
|----------|---------|---------|
| `CODEINDEX_DB_HOST` | localhost | PostgreSQL host |
| `CODEINDEX_DB_PORT` | 5432 | PostgreSQL port |
| `CODEINDEX_DB_NAME` | codeindex | Database name |
| `CODEINDEX_DB_USER` | codeindex | DB user |
| `CODEINDEX_DB_PASSWORD` | (required) | DB password |
| `CODEINDEX_EMBED_MODEL` | nomic-embed-text | Embedding model name |
| `CODEINDEX_EMBED_API_BASE` | http://localhost:11434 | Embedding API base URL (Ollama-compatible) |
| `CODEINDEX_API_HOST` | 127.0.0.1 | API server host |
| `CODEINDEX_API_PORT` | 8900 | API server port |

## Supported Languages (25)

Python, JavaScript, TypeScript, TSX, JSX, Go, Rust, Java, C, C++, C#, Ruby, PHP, Swift, Kotlin, Scala, Lua, R, Bash, SQL, HTML, CSS, JSON, YAML, TOML, Markdown.

## Common Pitfalls

1. **pgvector extension missing.** Must be created by a superuser: `CREATE EXTENSION IF NOT EXISTS vector;`. The `deploy.sh` script does this but requires sudo.

2. **tree-sitter version mismatch.** Use `tree-sitter<0.22` with `tree-sitter-languages>=1.10`. Newer tree-sitter has incompatible API.

3. **Embedding service not running or model missing.** If using Ollama: verify `ollama list | grep nomic-embed-text`. Pull if needed: `ollama pull nomic-embed-text`.

4. **Large repos take time to embed.** First index of a 10k-file repo may take 10-30 min. Incremental reindex is fast (only changed files).

5. **Zero vectors on embedding failure.** If the embedding service is down, embeddings become zero vectors. Search still works but returns random results. Check logs.

6. **Module-level chunks may capture decorator lines.** For Python, `@dataclass` decorators before classes appear in both module and definition chunks if overlap detection fails.

7. **File path in chunks is absolute.** Searching with relative paths won't match. Always use absolute paths or LIKE patterns.

8. **Hermes MCP env filtering.** Hermes filters subprocess env vars. You MUST include the `env` block in the mcp_servers config to pass `CODEINDEX_DB_PASSWORD` through, even though `mcp_server.py` auto-loads `~/.hermes/.env`.

9. **Embedding service 500 intermittents.** The `/api/embeddings` endpoint can return 500 sporadically (common with Ollama). The embedder retries 3x with exponential backoff. Expect slower indexing on large repos. Never run two index operations simultaneously.

10. **tree-sitter FutureWarning.** tree-sitter 0.21.x emits FutureWarning (no impact, compatibility with tree-sitter-languages).

11. **Deleted file chunks are stale.** Orphan chunks for files removed from disk are purged during reindex. If you never reindex, they persist. The auto-reindex cron handles this automatically.

## Verification Checklist

- [ ] pgvector extension installed: `SELECT * FROM pg_extension WHERE extname = 'vector';`
- [ ] Embedding service running: `curl $CODEINDEX_EMBED_API_BASE/api/tags`
- [ ] Embedding model available (if Ollama): `ollama list | grep nomic`
- [ ] Index works: `.venv/bin/python3 cli.py index /some/repo`
- [ ] Search returns results: `.venv/bin/python3 cli.py search "test"`
- [ ] MCP tools appear in agent: check tool list
- [ ] Auto-reindex cron active: `cronjob action='list'`
- [ ] Orphan purge works: delete a file, reindex, check stats for `orphan_chunks_purged > 0`