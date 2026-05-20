# codebase-skill — Semantic Code Indexing & Search

Turn any code repository into a searchable knowledge base. Tree-sitter parsing, Ollama embeddings (nomic-embed-text), pgvector storage.

Instead of loading entire files into context, search surgically for the chunks you need — functions, classes, or concepts.

## Architecture

```
Repository → Tree-sitter parser → Semantic chunks → Ollama embed → pgvector
                                                                    ↓
Query → Ollama embed → cosine similarity search → ranked chunks
```

## Components

| File | Purpose |
|------|---------|
| `config.py` | Configuration via env vars (DB, Ollama, API) |
| `parser.py` | Tree-sitter chunking by function/class (25 languages) |
| `embedder.py` | Ollama nomic-embed-text, 768-dim, retry+backoff |
| `indexer.py` | Repository walker + incremental reindexing |
| `search.py` | Cosine similarity search with filters |
| `mcp_server.py` | MCP stdio server (3 tools: search, file_context, stats) |
| `api.py` | FastAPI HTTP server (optional) |
| `cli.py` | CLI interface |
| `init_db.sql` | Database schema (tables, indexes, upsert function) |
| `deploy.sh` | Full deployment script |
| `bin/cbsearch` | CLI shortcut: semantic search |
| `bin/cbcontext` | CLI shortcut: file context + related chunks |
| `bin/cbstats` | CLI shortcut: indexing statistics |

## Quick Deploy

### Prerequisites

- PostgreSQL 15+ with pgvector extension available
- Python 3.11+
- Ollama running with nomic-embed-text model

### 1. Run deploy.sh

```bash
bash deploy.sh              # interactive (prompts for DB password)
bash deploy.sh <password>    # non-interactive
```

This creates: DB user, database, pgvector extension, tables, Python venv + dependencies.

### 2. Configure environment

Set these variables in your agent's environment (e.g. `~/.hermes/.env`):

```bash
CODEINDEX_DB_PASSWORD=your_password   # required
# All others have sensible defaults — see .env.example
```

### 3. Configure MCP server

#### Hermes Agent

Add to `~/.hermes/config.yaml`:

```yaml
mcp_servers:
  codebase-skill:
    command: /data/codebase-skill/.venv/bin/python3
    args: ["/data/codebase-skill/mcp_server.py"]
    env:
      CODEINDEX_DB_PASSWORD: "${CODEINDEX_DB_PASSWORD}"
```

**Important:** Hermes filters subprocess env vars. You need the `env` block to pass `CODEINDEX_DB_PASSWORD` through. The MCP server auto-loads `~/.hermes/.env`, but the filter prevents inherited env. The `env` block ensures the variable reaches the subprocess.

#### Other MCP clients (Claude Code, Cursor, etc.)

```json
{
  "mcpServers": {
    "codebase-skill": {
      "command": "/path/to/codebase-skill/.venv/bin/python3",
      "args": ["/path/to/codebase-skill/mcp_server.py"],
      "env": {
        "CODEINDEX_DB_PASSWORD": "your_password"
      }
    }
  }
}
```

### 4. Index your first repo

```bash
cd /data/codebase-skill
.venv/bin/python3 cli.py index /path/to/repo
```

Or via API:
```bash
curl -X POST http://localhost:8900/index \
  -H "Content-Type: application/json" \
  -d '{"repo_path": "/path/to/repo"}'
```

### 5. Verify

```bash
# CLI
.venv/bin/python3 cli.py search "authentication middleware"

# MCP tools (from your agent)
# mcp_codebase_search(query="authentication middleware")
# mcp_codebase_file_context(file_path="/path/to/file.py")
# mcp_codebase_stats()
```

## MCP Tools

| Tool | Description |
|------|-------------|
| `search` | Semantic search across indexed codebases. Filters: language, file_pattern, repo_path, min_score |
| `file_context` | Get a file's chunks + semantically related chunks from other files |
| `stats` | Indexing statistics: total chunks, files, languages |

## API Endpoints (optional — run `cli.py serve`)

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/index` | POST | Index a repository |
| `/search` | POST | Semantic search |
| `/file-context` | POST | File + related chunks |
| `/stats` | GET | Indexing statistics |
| `/repository` | DELETE | Remove indexed repo |
| `/health` | GET | Health check |

## Configuration

| Variable | Default | Purpose |
|----------|---------|---------|
| `CODEINDEX_DB_HOST` | localhost | PostgreSQL host |
| `CODEINDEX_DB_PORT` | 5432 | PostgreSQL port |
| `CODEINDEX_DB_NAME` | codeindex | Database name |
| `CODEINDEX_DB_USER` | codeindex | DB user |
| `CODEINDEX_DB_PASSWORD` | (required) | DB password |
| `CODEINDEX_EMBED_MODEL` | nomic-embed-text | Ollama embedding model |
| `CODEINDEX_OLLAMA_BASE` | http://localhost:11434 | Ollama API base |
| `CODEINDEX_API_HOST` | 127.0.0.1 | API server host |
| `CODEINDEX_API_PORT` | 8900 | API server port |

## Supported Languages (25)

Python, JavaScript, TypeScript, TSX, JSX, Go, Rust, Java, C, C++, C#, Ruby, PHP, Swift, Kotlin, Scala, Lua, R, Bash, SQL, HTML, CSS, JSON, YAML, TOML, Markdown.

## Troubleshooting

| Issue | Fix |
|-------|-----|
| pgvector extension missing | `sudo -u postgres psql -d codeindex -c 'CREATE EXTENSION IF NOT EXISTS vector;'` |
| tree-sitter version conflict | Use `tree-sitter<0.22` + `tree-sitter-languages>=1.10` |
| Ollama not running | `curl http://localhost:11434/api/tags` |
| nomic-embed-text missing | `ollama pull nomic-embed-text` |
| Zero vectors (Ollama down) | Check logs, search returns random results |
| MCP tools not appearing | Check `env` block in MCP config — Hermes filters subprocess env |
| DB connection refused | Verify PostgreSQL running, check `CODEINDEX_DB_*` vars |

## Migration from Another Host

```bash
# 1. Copy the directory
rsync -av --exclude='.venv' --exclude='venv' --exclude='__pycache__' \
    /data/codebase-skill/ user@newhost:/data/codebase-skill/

# 2. On the new host
cd /data/codebase-skill
bash deploy.sh <password>

# 3. Re-index your repos (data is in PostgreSQL, not in files)
.venv/bin/python3 cli.py index /path/to/repo
```

## License

MIT