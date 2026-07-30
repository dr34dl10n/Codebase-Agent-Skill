<!-- codebase-skill:begin -->
<!-- Do not edit between these markers — regenerate with cbsetup -->
## 🔍 Semantic Code Search (codebase-skill)

This repository is **indexed** in a semantic code search engine (pgvector).
You MUST use it before reading files or running grep/find/cat to explore the code.

### Mandatory Protocol

1. **BEFORE reading files**, use the search tools to locate relevant code:
   - `search` — semantic search by meaning (e.g. "authentication middleware")
   - `file_context` — a file's full verbatim content + its chunk map + related chunks from other files
   - `stats` — check what is indexed

2. **NEVER blindly read multiple files** when a targeted search suffices.
   Search first, then read only the specific sections you need.

3. **After making changes**, consider `reindex` to keep the index fresh.

### Token-Saving Workflow (read less, search more)

Every chunk returned by `search` is **verbatim source** with exact
`start_line`/`end_line` — it is the real code, not a summary. Use this to stay
inside your context budget:

- **A `search` hit is usually enough to act.** The chunk content IS the code.
   Don't `read` the file again just to re-fetch what the chunk already gave you.
- **Need a little surrounding context?** Read only the chunk's line range, not
   the whole file: `read(path, offset=start_line, limit=(end_line - start_line + N))`.
   Never `read(path)` a 1000-line file to look at one 30-line function.
- **Need a whole file + its dependencies?** One `file_context(path, focus=...)`
   call returns the full verbatim file content, the file's chunk map (with line
   ranges), AND semantically related chunks from *other* files. This replaces
   several `read` calls plus a separate dependency search — one round trip.
- **File missing from disk?** `file_context` still returns its content: the
   verbatim source is stored at index time as ground truth (`file_sources`),
   so a lost/deleted file is recoverable from the index alone.

Rule of thumb: **`search` → act on the chunk. `file_context` → understand a
file + its neighborhood. `read` → only when you must edit lines you haven't
seen yet, and even then read the narrowest range.**

### How to Call the Tools

If your agent supports MCP, the tools are available as MCP tools:

| MCP Tool | Parameters | Use When |
|----------|-----------|----------|
| `search` | query (required), top_k, language, file_pattern, repo_path, min_score | Locating code by meaning — returns verbatim chunks with line ranges |
| `file_context` | file_path (required), focus, top_k | One call: full file content + chunk map + related chunks (also recovers lost files) |
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
