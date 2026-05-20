<div align="center">

# codebase-skill

**Semantic Code Search for AI Agents**

*Stop feeding entire repos to your context window. Search surgically instead.*

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org)
[![Tree-sitter](https://img.shields.io/badge/tree--sitter-0.21-green.svg)](https://tree-sitter.github.io)
[![pgvector](https://img.shields.io/badge/pgvector-0.6-orange.svg)](https://github.com/pgvector/pgvector)
[![Embeddings](https://img.shields.io/badge/embeddings-Ollama%20compatible-blueviolet.svg)](https://ollama.com)
[![MCP](https://img.shields.io/badge/MCP-stdio-black.svg)](https://modelcontextprotocol.io)
[![License: MIT](https://img.shields.io/badge/license-MIT-lightgrey.svg)](LICENSE)

</div>

---

## Why?

AI agents waste tokens reading entire files they don't need. A 500k-LOC repo becomes a 30-minute scroll fest. This skill turns your codebase into a **searchable vector database** — the agent asks a question, gets 8–15 precise chunks back, and skips the noise.

**Before:** "Let me read all 47 files in `src/auth/` to find how JWT validation works..."
**After:** One semantic query → the 3 functions that matter, with line numbers.

This isn't a search engine for humans. It's a **RAG backbone for AI agents** — exposed as MCP tools your agent calls like any other tool.

---

## How It Works

```
  ┌─────────────┐     ┌──────────────┐     ┌────────────────┐     ┌──────────┐
  │  Repository  │────▶│  Tree-sitter │────▶│  Embed service │────▶│  pgvector │
  │  (any lang)  │     │  chunking    │     │  nomic (768d)  │     │  storage  │
  └─────────────┘     └──────────────┘     └────────────────┘     └──────────┘
                                                                    │
  ┌─────────────┐     ┌──────────────┐     ┌────────────────┐        │
  │  Agent gets │◀────│  Ranked      │◀────│  Cosine        │◀───────┘
  │  relevant   │     │  chunks      │     │  similarity    │
  │  code only  │     │  + metadata  │     │  search        │
  └─────────────┘     └──────────────┘     └────────────────┘
```

**Parsing is structural, not naive.** Tree-sitter chunks by functions, classes, and methods — not by arbitrary line splits. Each chunk carries its symbol name, file path, and line numbers so the agent knows exactly where it landed.

**Incremental reindexing** — only changed files get re-parsed and re-embedded. Re-index a modified repo in seconds, not minutes.

---

## Benchmark: Context Loading

Real benchmark on a mid-size project (AIssistant: 502 chunks, 81 source files, ~1.2M chars). Eight representative queries, three strategies compared:

| Strategy | What it does | Avg time | Avg context tokens |
|----------|-------------|----------|-------------------|
| **Naive Traditional** | Grep keywords → read all matching files | 16ms | ~299K |
| **Smart Traditional** | Grep keywords → read top-5 files by hit count | 18ms | ~184K |
| **pgvector (this skill)** | Embed query → cosine similarity → top-10 chunks | 100ms | **~3K** |

### The numbers

```
Query                                      Naive Trad       Smart Trad       pgvector
                                            time/tokens      time/tokens      time/tokens
────────────────────────────────────────────────────────────────────────────────────────────
how does authentication work                19ms/302Ktok     17ms/213Ktok      92ms/3Ktok
send an email via gmail                     16ms/308Ktok     19ms/213Ktok     122ms/4Ktok
calendar event creation                     16ms/277Ktok     16ms/190Ktok      87ms/4Ktok
telegram bot message handler                15ms/285Ktok     18ms/210Ktok      95ms/3Ktok
error handling and retries                  15ms/304Ktok     17ms/200Ktok      91ms/1Ktok
how is the agent run loop structured        16ms/308Ktok     20ms/211Ktok     133ms/3Ktok
memory and context management               15ms/305Ktok     17ms/200Ktok      88ms/4Ktok
Google Workspace OAuth flow                16ms/303Ktok     17ms/36Ktok        89ms/2Ktok
```

### Takeaway

- **97× less context** than naive grep-all, **60× less** than smart top-5 file reading.
- Trade-off: ~100ms per query (embedding + vector search) vs ~17ms for local file reads.
- That 100ms buys you **semantically ranked, relevant chunks** instead of whole files full of noise.
- At current LLM pricing, 296K wasted tokens per query is the real cost — not the 80ms latency difference.

---

## Tech Stack

| Layer | Tech | Why |
|-------|------|-----|
| **Parsing** | [Tree-sitter](https://tree-sitter.github.io) + tree-sitter-languages | AST-aware chunking by function/class, not line splits. 25 languages. |
| **Embeddings** | [Ollama](https://ollama.com) / compatible API | Local-first, swap model/provider freely. Supports any Ollama-compatible endpoint (LM Studio, vLLM, cloud). |
| **Storage** | [PostgreSQL](https://postgresql.org) + [pgvector](https://github.com/pgvector/pgvector) | HNSW index for sub-ms cosine search. ACID, proven, no new infra. |
| **Agent Interface** | [MCP](https://modelcontextprotocol.io) (stdio) | Standard protocol — works with Hermes, Claude Code, Cursor, Pi, Codex, any MCP client. |
| **API** | [FastAPI](https://fastapi.tiangolo.com) | Optional HTTP endpoints. Same logic, REST access. |
| **CLI** | argparse | `cbsearch`, `cbcontext`, `cbstats` — terminal-first, scriptable. |

---

## 5 MCP Tools Your Agent Gets

| Tool | What it does | When to use |
|------|-------------|-------------|
| `search` | Semantic search with filters (language, file pattern, repo) | "Where is auth implemented?" "Find all database connection code" |
| `file_context` | A file's chunks + semantically related chunks from other files | Understanding a file and its dependencies without reading everything |
| `stats` | Chunk count, file count, language count | "Is this repo indexed?" "How big is the codebase?" |
| `reindex` | Refresh a repo: re-embed modified files + purge deleted file chunks | After pulling code changes, or when search seems stale |
| `list_projects` | List all indexed repos with last_indexed time | "What repos are tracked?" |

---

## Keeping the Index Fresh

Your codebase changes. The index needs to keep up.

### Incremental Reindex

Re-running `index` on an already-indexed repo only processes changes:

| Change | Handling |
|--------|----------|
| Modified file (mtime > last_indexed) | Re-parsed, old chunks deleted, new chunks embedded & stored |
| Deleted file | Chunks purged automatically (`orphan_chunks_purged` in stats) |
| New file | Parsed, embedded & stored normally |
| Unchanged file | Skipped (zero cost) |

```bash
# Incremental (fast — only changes)
.venv/bin/python3 cli.py index /path/to/repo

# Force full reindex
.venv/bin/python3 cli.py index /path/to/repo --force
```

Or via MCP: `mcp_codebase_reindex(repo_path="/path/to/repo")`

### Auto-Reindex Cron

A cron job runs `auto_reindex.py` every 4 hours, keeping all registered repos fresh automatically. It only reports when changes are detected.

```bash
# Manual run
.venv/bin/python3 auto_reindex.py

# Force full reindex of everything
.venv/bin/python3 auto_reindex.py --force
```

---

## 25 Languages

Python, JavaScript, TypeScript, TSX, JSX, Go, Rust, Java, C, C++, C#, Ruby, PHP, Swift, Kotlin, Scala, Lua, R, Bash, SQL, HTML, CSS, JSON, YAML, TOML, Markdown.

Don't see yours? Tree-sitter supports [many more](https://tree-sitter.github.io/tree-sitter/) — just add the grammar.

---

## Quick Start

### Prerequisites

- PostgreSQL 15+ (with sudo to create extensions)
- Python 3.11+
- An embedding service running (e.g. Ollama: `ollama serve` + `ollama pull nomic-embed-text`)

### Deploy

```bash
git clone <this-repo> /data/codebase-skill
bash deploy.sh <db_password>
```

That one command creates: DB user, database, pgvector extension, tables, Python venv + all dependencies, and runs a verification check.

### Configure your agent

**Hermes Agent** — `~/.hermes/config.yaml`:

```yaml
mcp_servers:
  codebase-skill:
    command: /data/codebase-skill/.venv/bin/python3
    args: ["/data/codebase-skill/mcp_server.py"]
    env:
      CODEINDEX_DB_PASSWORD: "${CODEINDEX_DB_PASSWORD}"
```

**Claude Code / Cursor / any MCP client:**

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

### Index & search

```bash
# Index a repo (first time: parses + embeds everything)
.venv/bin/python3 cli.py index /path/to/repo

# Search from terminal
.venv/bin/python3 cli.py search "authentication middleware"
./bin/cbsearch "database connection pool" --language python --top-k 5

# Or let your agent do it via MCP tools
# → mcp_codebase_search(query="authentication middleware")
```

---

## Configuration

| Variable | Default | Purpose |
|----------|---------|---------|
| `CODEINDEX_DB_HOST` | localhost | PostgreSQL host |
| `CODEINDEX_DB_PORT` | 5432 | PostgreSQL port |
| `CODEINDEX_DB_NAME` | codeindex | Database name |
| `CODEINDEX_DB_USER` | codeindex | DB user |
| `CODEINDEX_DB_PASSWORD` | **(required)** | DB password |
| `CODEINDEX_EMBED_MODEL` | nomic-embed-text | Embedding model name |
| `CODEINDEX_EMBED_API_BASE` | http://localhost:11434 | Embedding API base URL (Ollama-compatible) |
| `CODEINDEX_API_HOST` | 127.0.0.1 | API server host |
| `CODEINDEX_API_PORT` | 8900 | API server port |

---

## The "Aha" Moment

Context windows are expensive and finite. This skill turns a **read-everything** agent into a **search-then-read** agent:

| Scenario | Without codebase-skill | With codebase-skill |
|----------|----------------------|-------------------|
| "How does auth work?" | Read 47 files, 200k tokens | 1 query, 10 chunks, 5k tokens |
| Understanding a new file | Read it + guess dependencies | `file_context` finds related code automatically |
| "Where is X used?" | Grep, hope it's named consistently | Semantic search finds it even with different naming |
| Large monorepo | Not feasible — exceeds context | Sub-ms vector search, any size |

**Your agent stays fast, focused, and within budget.** That's the whole point.

---

## Project Structure

```
codebase-skill/
├── mcp_server.py      # MCP stdio server (3 tools)
├── config.py          # Env-based configuration
├── parser.py           # Tree-sitter chunking (25 languages)
├── embedder.py         # Embeddings via Ollama-compatible API + retry/backoff
├── indexer.py          # Repository walker + incremental reindex
├── search.py           # Cosine similarity search + filters
├── api.py              # FastAPI HTTP server (optional)
├── cli.py              # CLI interface
├── init_db.sql         # Database schema
├── deploy.sh           # Full one-command deployment
├── setup_db.sh         # DB-only setup (legacy)
├── auto_reindex.py     # Cron-friendly auto-reindex for all repos
├── requirements.txt    # Python dependencies
├── .env.example        # Environment variables template
├── SKILL.md            # Hermes Agent skill definition
├── README.md           # This file
├── PROGRESSION.md      # Development history
├── info-db.txt         # DB setup notes
└── bin/
    ├── cbsearch        # CLI: semantic search
    ├── cbcontext       # CLI: file context + related chunks
    └── cbstats         # CLI: indexing statistics
```

---

## License

MIT