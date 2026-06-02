## 🔍 Semantic Code Search (codebase-skill)

This repository is **indexed** in a semantic code search engine (pgvector).
You MUST use it before reading files or running grep/find/cat to explore the code.

### Mandatory Protocol

1. **BEFORE reading files**, use the search tools to locate relevant code:
   - `search` — semantic search by meaning (e.g. "authentication middleware")
   - `file_context` — a file's chunks + related chunks from other files
   - `stats` — check what is indexed

2. **NEVER blindly read multiple files** when a targeted search suffices.
   Search first, then read only the specific sections you need.

3. **After making changes**, consider `reindex` to keep the index fresh.

### How to Call the Tools

If your agent supports MCP, the tools are available as MCP tools:

| MCP Tool | Parameters | Use When |
|----------|-----------|----------|
| `search` | query (required), top_k, language, file_pattern, repo_path, min_score | Locating code by meaning |
| `file_context` | file_path (required), focus, top_k | Understanding a file + its dependencies |
| `stats` | repo_path (optional) | Checking index coverage |
| `reindex` | repo_path (required), force_reindex | After code changes |
| `list_projects` | (none) | Listing indexed repos |

Otherwise, use the CLI:

```bash
SKILL_DIR="/data/codebase-skill"
cd "$SKILL_DIR" && .venv/bin/python3 cli.py search "your query"
cd "$SKILL_DIR" && .venv/bin/python3 cli.py file-context /path/to/file.py --focus "class definition"
```

Or wrapper scripts:

```bash
/data/codebase-skill/bin/cbsearch "your query" --language python --top-k 5
/data/codebase-skill/bin/cbcontext /path/to/file.py --focus "class definition"
```

<!-- codebase-skill:begin -->
<!-- Do not edit between these markers — regenerate with cbsetup -->
## 🔍 Semantic Code Search (codebase-skill)

This repository is **indexed** in a semantic code search engine (pgvector).
You MUST use it before reading files or running grep/find/cat to explore the code.

### Mandatory Protocol

1. **BEFORE reading files**, use the search tools to locate relevant code:
   - `search` — semantic search by meaning (e.g. "authentication middleware")
   - `file_context` — a file's chunks + related chunks from other files
   - `stats` — check what is indexed

2. **NEVER blindly read multiple files** when a targeted search suffices.
   Search first, then read only the specific sections you need.

3. **After making changes**, consider `reindex` to keep the index fresh.

### How to Call the Tools

If your agent supports MCP, the tools are available as MCP tools:

| MCP Tool | Parameters | Use When |
|----------|-----------|----------|
| `search` | query (required), top_k, language, file_pattern, repo_path, min_score | Locating code by meaning |
| `file_context` | file_path (required), focus, top_k | Understanding a file + its dependencies |
| `stats` | repo_path (optional) | Checking index coverage |
| `reindex` | repo_path (required), force_reindex | After code changes |
| `list_projects` | (none) | Listing indexed repos |

Otherwise, use the CLI:

```bash
SKILL_DIR="/data/codebase-skill"
cd "$SKILL_DIR" && .venv/bin/python3 cli.py search "your query"
cd "$SKILL_DIR" && .venv/bin/python3 cli.py file-context /path/to/file.py --focus "class definition"
```

Or wrapper scripts:

```bash
/data/codebase-skill/bin/cbsearch "your query" --language python --top-k 5
/data/codebase-skill/bin/cbcontext /path/to/file.py --focus "class definition"
```

<!-- codebase-skill:end -->
