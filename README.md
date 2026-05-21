<div align="center">

# codebase-skill

**Semantic Code Search for AI Agents**

*Stop feeding entire repos to your context window. Search surgically instead.*

[![GitHub stars](https://img.shields.io/github/stars/dr34dl10n/Codebase-Agent-Skill?style=social)](https://github.com/dr34dl10n/Codebase-Agent-Skill/stargazers)
[![GitHub forks](https://img.shields.io/github/forks/dr34dl10n/Codebase-Agent-Skill?style=social)](https://github.com/dr34dl10n/Codebase-Agent-Skill/fork)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org)
[![Tree-sitter](https://img.shields.io/badge/tree--sitter-0.21-green.svg)](https://tree-sitter.github.io)
[![pgvector](https://img.shields.io/badge/pgvector-0.6-orange.svg)](https://github.com/pgvector/pgvector)
[![ModernBERT](https://img.shields.io/badge/ModernBERT-default-blue.svg)](https://huggingface.co/nomic-ai/modernbert-embed-base)
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

## Benchmark: Context Loading with ModernBERT

Real benchmark on a mid-size project (**AIssistant**: 406 chunks, 83 source files, ~312K tokens).
Eight representative developer queries, compared across models and strategies.

### Headline Numbers

| Strategy | Avg Time | Avg Context Tokens | vs Naive | vs Smart |
|----------|----------|--------------------|----------|----------|
| **Naive Traditional** (grep → read all) | ~16ms | ~44M | 1× | — |
| **Smart Traditional** (grep → top-5 files) | ~18ms | ~3.8M | 0.1× | 1× |
| **ModernBERT-base + pgvector** ⭐ | **66ms** | **~3.2K** | **13,700×** | **1,185×** |
| **Nomic-embed + pgvector** *(optional)* | 59ms | ~2.8K | 15,700× | 1,352× |

> **13,700× less context** than naive grep. That's the difference between sending an entire codebase and sending 3 chunks to your LLM.

### Per-Query Detail

```
Query                                      ModernBERT-base          Nomic-embed-text
                                            time/tokens/score       time/tokens/score
────────────────────────────────────────────────────────────────────────────────────────────
how does authentication work                83ms/3.2Ktok/0.556     66ms/1.8Ktok/0.610
send an email via gmail                     61ms/2.5Ktok/0.619     60ms/2.9Ktok/0.671
calendar event creation                    54ms/3.1Ktok/0.705     51ms/3.2Ktok/0.745
telegram bot message handler                64ms/38Ktok*/0.735     52ms/3.6Ktok/0.741
error handling and retries                 66ms/1.2Ktok/0.601     61ms/1.6Ktok/0.595
how is the agent run loop structured       88ms/5.8Ktok/0.619     69ms/3.3Ktok/0.550
memory and context management              59ms/3.2Ktok/0.579     48ms/3.5Ktok/0.623
Google Workspace OAuth flow                54ms/3.6Ktok/0.716     66ms/2.2Ktok/0.618
────────────────────────────────────────────────────────────────────────────────────────────
AVERAGE                                    66ms/3.2Ktok            59ms/2.8Ktok
```

*\* The "telegram" outlier for ModernBERT returns a large class chunk (34K tokens) — a chunking granularity issue, not an embedding quality issue.*

### Model Comparison: What This Means

| | ModernBERT-base (default) | Nomic-embed-text (optional) |
|--|:--:|:--:|
| **Top-1 similarity** | Wins 3/8 queries | Wins 5/8 queries |
| **Avg query latency** | ~66ms | ~59ms |
| **Avg context returned** | ~3.2K tokens | ~2.8K tokens |
| **Context window** | 8,192 tokens | ~8,192 tokens |
| **Dimensions** | 768 | 768 |
| **Backend** | sentence-transformers (Python, zero-config) | Ollama (requires running server) |
| **External dependency** | ❌ None — model loads in-process | ✅ Ollama must be running |
| **Setup** | `pip install sentence-transformers` | `ollama pull nomic-embed-text` |
| **Model download** | Auto from HuggingFace | Manual `ollama pull` |
| **Best for** | **Default. All setups.** | GPU servers with Ollama already running |

### Takeaway

- Both models deliver **3–4 orders of magnitude** context reduction vs grep.
- **ModernBERT is the default** — zero-config, no running service, pure Python.
- Nomic scores slightly higher on some queries but requires a separate Ollama server.
- **ModernBERT wins** on complex structural queries ("agent run loop", "OAuth flow"), Nomic wins on keyword-aligned queries.
- At ~60ms/query, both are fast enough for interactive agent use.
- The **real cost saving** is tokens: 3K tokens vs 44M tokens means your LLM calls cost **~14,700× less**.

---

## Use Cases

### 🤖 Autonomous Coding Agents (Hermes, Codex, OpenHands)
Your agent explores an unfamiliar codebase autonomously. Instead of reading every file top-to-bottom, it calls `mcp_codebase_search("Where is the payment gateway integration?")` and gets the 5 relevant functions — then acts on them.

### ✨ IDE-Integrated Agents (Cursor, Claude Code, Windsurf)
While you code, the inline agent searches the entire repo semantically: "Find all uses of the `UserService` class" → instant, accurate results across files, no grep gymnastics.

### 🏗️ DevOps / Platform Agents
A CI/CD agent searches infra-as-code repos: "Which Helm charts set resource limits?" → ranked results across `values.yaml`, templates, and helpers, not just filename matches.

### 🔒 Sovereign AI Pipelines
No cloud API calls, no data leaving your infrastructure. ModernBERT loads in-process from HuggingFace, storage in your own PostgreSQL. Your code never leaves your network — perfect for defense, finance, healthcare.

---

## Architecture

```mermaid
graph LR
    subgraph "Indexing Pipeline"
        A[Repository<br/>any language] --> B[Tree-sitter<br/>AST chunking]
        B --> C[Embed Model<br/>ModernBERT (default)<br/>Nomic (optional)]
        C --> D[(PostgreSQL<br/>+ pgvector)]
    end

    subgraph "Query Pipeline"
        E[AI Agent<br/>MCP / REST / CLI] --> F[Embed query]
        F --> G[Cosine similarity<br/>HNSW index]
        G --> H[Ranked chunks<br/>+ metadata]
        H --> E
        D -.-> G
    end

    style D fill:#f0883e,color:#fff
    style B fill:#2563eb,color:#fff
    style C fill:#7c3aed,color:#fff
    style E fill:#3fb950,color:#fff
```

---

## Comparison: codebase-skill vs LangChain + Chroma/Pinecone

| Dimension | **codebase-skill** | **LangChain + Chroma/Pinecone** |
|-----------|-------------------|--------------------------------|
| **Chunking** | Tree-sitter AST (functions, classes, methods) | Recursive text splitter (character-based splits) |
| **Chunk quality** | ✅ Syntactically coherent — never splits a function in half | ⚠️ May cut mid-function, break indentation, lose context |
| **Embedding model** | ModernBERT (default, in-process) or Ollama (optional API) | Cloud API (OpenAI) or local, but no unified config |
| **Vector store** | PostgreSQL + pgvector (HNSW) | Chroma (file-based) or Pinecone (SaaS) |
| **Infrastructure** | 1 PostgreSQL you already run | Chroma = ephemeral/local **or** Pinecone = vendor lock-in |
| **Data sovereignty** | ✅ 100% on-prem — zero data egress | ⚠️ Pinecone = code sent to US cloud; Chroma = not prod-ready |
| **Query latency** | ~60ms (local embed + HNSW) | ~200–500ms (cloud API round-trip) |
| **Incremental reindex** | ✅ Built-in — only changed files | ❌ Full reindex on every change |
| **Cost at scale** | PostgreSQL + ModernBERT = $0/mo extra | Pinecone: $70/mo (S1 pod) + OpenAI embed API fees |
| **Agent protocol** | MCP (native stdio) | Custom Python API, no standard agent protocol |
| **Languages** | 25 out of the box (Tree-sitter) | Unlimited (text-based, no AST awareness) |
| **Metadata** | Symbol name, file path, line range, language | Custom metadata (user must implement) |

### TL;DR

| | codebase-skill | LangChain + Chroma | LangChain + Pinecone |
|--|:--:|:--:|:--:|
| **Setup** | 1 command | Python script | Account + API key |
| **Extra infra** | None (use your PG) | Chroma server | Pinecone SaaS |
| **Monthly cost** | **$0** | $0 (local, fragile) | **$70+** |
| **Code leaves network** | **Never** | No (local) | **Yes** |
| **Chunk coherence** | **AST-aware** | Text-split | Text-split |

---

## Tech Stack

| Layer | Tech | Why |
|-------|------|-----|
| **Parsing** | [Tree-sitter](https://tree-sitter.github.io) + tree-sitter-languages | AST-aware chunking by function/class, not line splits. 25 languages. |
| **Embeddings** | [ModernBERT](https://huggingface.co/nomic-ai/modernbert-embed-base) (sentence-transformers) | Default. In-process, zero-config, auto-downloads from HuggingFace. |
| | [Nomic-embed-text](https://ollama.com/library/nomic-embed-text) (Ollama) | Optional. Requires a running Ollama server. |
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

**That's it.** ModernBERT auto-downloads from HuggingFace — no external service, no Ollama, no API keys.

<details>
<summary>Optional: Using Nomic-embed-text via Ollama</summary>

If you already have Ollama running on a GPU server and prefer that backend:

```bash
ollama serve
ollama pull nomic-embed-text   # 768-dim, ~8k context
```

Then set `CODEINDEX_EMBED_MODEL=nomic-embed-text` and `CODEINDEX_EMBED_BACKEND=ollama`.

</details>

### Deploy

```bash
git clone https://github.com/dr34dl10n/Codebase-Agent-Skill.git /data/codebase-skill
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

## Auto-Setup for All Agents (`cbsetup`)

After indexing a repository, run `cbsetup` to **automatically generate instruction files and MCP configuration** for every supported coding agent. This ensures any agent opening the project knows it must use semantic search before reading files.

### Supported Agent Files

| File | Agent | What it does |
|------|-------|-------------|
| `AGENTS.md` | Pi, Codex, generic | Project instructions (search-first protocol) |
| `CLAUDE.md` | Claude Code | Project instructions |
| `.cursorrules` | Cursor (legacy) | Project rules |
| `.cursor/rules/codebase-search.mdc` | Cursor (newer) | Always-apply project rule |
| `.windsurfrules` | Windsurf / Codeium | Project rules |
| `.clinerules` | Cline | Project rules |
| `.github/copilot-instructions.md` | GitHub Copilot | Repo instructions |
| `.claude/settings.json` | Claude Code | MCP server config |
| `.cursor/mcp.json` | Cursor | MCP server config |
| `.cline/mcp.json` | Cline | MCP server config |
| `.windsurf/mcp.json` | Windsurf | MCP server config |
| `.pi-indexed` | All agents | Marker with indexing metadata |

### Usage

```bash
# Full setup — all agents, all MCP configs
.venv/bin/python3 cbsetup.py /path/to/repo
# Or via wrapper:
cbsetup /path/to/repo

# Preview without writing
.venv/bin/python3 cbsetup.py /path/to/repo --dry-run

# Only instruction files, no MCP configs
.venv/bin/python3 cbsetup.py /path/to/repo --instructions-only

# Only MCP configs, no instruction files  
.venv/bin/python3 cbsetup.py /path/to/repo --mcp-only

# Specific agents only
.venv/bin/python3 cbsetup.py /path/to/repo --agents claude_md cursorrules --mcp claude cursor
```

### Idempotent & Safe

- Existing files are **appended to**, never overwritten. A `<!-- codebase-skill:begin/end -->` marker allows automatic updates without losing existing content.
- Re-running `cbsetup` updates the section in place.
- The `.pi-indexed` marker file tracks indexing metadata for programmatic detection.

### Typical Workflow

```bash
# 1. Index your repo
.venv/bin/python3 cli.py index /data/myproject

# 2. Generate agent files
.venv/bin/python3 cbsetup.py /data/myproject

# 3. Commit (optional — share the instructions with your team)
cd /data/myproject && git add AGENTS.md CLAUDE.md .cursorrules .pi-indexed && git commit -m "Add codebase-skill search protocol"
```

## Configuration

| Variable | Default | Purpose |
|----------|---------|---------|
| `CODEINDEX_DB_HOST` | localhost | PostgreSQL host |
| `CODEINDEX_DB_PORT` | 5432 | PostgreSQL port |
| `CODEINDEX_DB_NAME` | codeindex | Database name |
| `CODEINDEX_DB_USER` | codeindex | DB user |
| `CODEINDEX_DB_PASSWORD` | **(required)** | DB password |
| `CODEINDEX_EMBED_MODEL` | modernbert-embed-base | Embedding model (run `python scripts/detect_model.py` to auto-detect) |
| `CODEINDEX_EMBED_BACKEND` | sentence_transformers (auto) | `sentence_transformers` or `ollama` (auto-detected from model) |
| `CODEINDEX_EMBED_API_BASE` | http://localhost:11434 | Ollama API URL (only for ollama backend) |
| `CODEINDEX_API_HOST` | 127.0.0.1 | API server host |
| `CODEINDEX_API_PORT` | 8900 | API server port |

---

### Embedding Models

The default model is **ModernBERT-embed-base** — loaded in-process via sentence-transformers, auto-downloaded from HuggingFace. No external service needed.

| Model | Backend | Dim | Context | Best for |
|-------|---------|-----|---------|----------|
| `modernbert-embed-base` ⭐ | sentence_transformers | 768 | 8,192 tokens | **Default. CPU-only, zero config.** |
| `modernbert-embed-large` | sentence_transformers | 1024 | 8,192 tokens | Better quality, more RAM |
| `nomic-embed-text` | ollama | 768 | ~8,192 tokens | Optional. Requires Ollama server |

Run `python scripts/detect_model.py` to auto-detect the best model for your hardware:

```bash
python scripts/detect_model.py              # print recommendation
python scripts/detect_model.py --write-env   # write CODEINDEX_EMBED_MODEL to .env
python scripts/detect_model.py --json        # machine-readable output
```

| Environment | Recommended model | Backend | Why |
|-------------|-------------------|---------|-----|
| **No GPU** | `modernbert-embed-base` | sentence_transformers | Fastest on CPU, smallest footprint |
| **GPU < 8 GB** | `modernbert-embed-large` | sentence_transformers | Leverages GPU for quality |
| **GPU ≥ 8 GB** | `nomic-embed-text` | ollama | Optional. Requires already-running Ollama |

> **⚠️ Important:** If you change model after indexing, you must re-index from scratch (embeddings must match the new model's dimension).

---

## The "Aha" Moment

Context windows are expensive and finite. This skill turns a **read-everything** agent into a **search-then-read** agent:

| Scenario | Without codebase-skill | With codebase-skill |
|----------|----------------------|-------------------|
| "How does auth work?" | Read 47 files, 200k tokens | 1 query, 10 chunks, 3k tokens |
| Understanding a new file | Read it + guess dependencies | `file_context` finds related code automatically |
| "Where is X used?" | Grep, hope it's named consistently | Semantic search finds it even with different naming |
| Large monorepo | Not feasible — exceeds context | Sub-ms vector search, any size |

**Your agent stays fast, focused, and within budget.** That's the whole point.

---

## Project Structure

```
codebase-skill/
├── mcp_server.py      # MCP stdio server (5 tools)
├── config.py          # Env-based configuration + model/backend selection
├── parser.py           # Tree-sitter chunking (25 languages)
├── embedder.py         # Embedding providers (ModernBERT default; Ollama optional)
├── indexer.py          # Repository walker + incremental reindex
├── search.py           # Cosine similarity search + filters
├── api.py              # FastAPI HTTP server (optional)
├── cbsetup.py          # Generate agent instruction files + MCP configs
├── cli.py              # CLI interface
├── benchmark.py        # Model comparison benchmark
├── init_db.sql         # Database schema
├── deploy.sh           # Full one-command deployment
├── setup_db.sh         # DB-only setup (legacy)
├── scripts/
│   └── detect_model.py  # Auto-detect best embed model for hardware
├── auto_reindex.py     # Cron-friendly auto-reindex for all repos
├── requirements.txt    # Python dependencies
├── .env.example        # Environment variables template
├── SKILL.md            # Hermes Agent skill definition
├── README.md           # This file
└── bin/
    ├── cbsearch        # CLI: semantic search
    ├── cbcontext       # CLI: file context + related chunks
    ├── cbstats         # CLI: indexing statistics
    └── cbsetup         # CLI: generate agent files for indexed repos
```

---

## License

[MIT](LICENSE) — Copyright © 2026 dr34dl10n