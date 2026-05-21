#!/usr/bin/env python3
"""cbsetup: Generate agent instruction files and MCP config for indexed repositories.

After indexing a repo with `cli.py index`, run `cbsetup <repo_path>` to:
  1. Create/update agent instruction files (AGENTS.md, CLAUDE.md, .cursorrules, etc.)
     telling any agent to use semantic search before reading files.
  2. Create/update MCP server configuration for agents that support MCP
     (Claude Code, Cursor, Cline, Windsurf, etc.)
  3. Create a `.pi-indexed` marker file with indexing metadata.

The instruction content is the same across all files — only the format differs
per agent convention.

Supported agent instruction files:
  - AGENTS.md        (Pi, Codex, many generic agents)
  - CLAUDE.md        (Claude Code)
  - .cursorrules     (Cursor)
  - .cursor/rules/   (Cursor, newer convention)
  - .windsurfrules   (Windsurf/Codeium)
  - .clinerules      (Cline)
  - .github/copilot-instructions.md  (GitHub Copilot)

Supported MCP configs:
  - .claude/settings.json  (Claude Code — local project settings)
  - .cursor/mcp.json       (Cursor)
  - .cline/mcp.json         (Cline)
  - .windsurf/mcp.json     (Windsurf)
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Instruction template — agent-agnostic
# ---------------------------------------------------------------------------

INSTRUCTION_BODY = """\
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
SKILL_DIR="{skill_dir}"
cd "$SKILL_DIR" && .venv/bin/python3 cli.py search "your query"
cd "$SKILL_DIR" && .venv/bin/python3 cli.py file-context /path/to/file.py --focus "class definition"
```

Or wrapper scripts:

```bash
{skill_dir}/bin/cbsearch "your query" --language python --top-k 5
{skill_dir}/bin/cbcontext /path/to/file.py --focus "class definition"
```
"""

# ---------------------------------------------------------------------------
# MCP server configuration — varies by agent
# ---------------------------------------------------------------------------

def mcp_server_config(skill_dir: str) -> dict:
    """MCP server configuration for codebase-skill (stdio transport)."""
    return {
        "mcpServers": {
            "codebase-skill": {
                "command": f"{skill_dir}/.venv/bin/python3",
                "args": [f"{skill_dir}/mcp_server.py"],
                "env": {
                    # Inherit DB and embedding config from the skill's .env
                    # If needed, override here
                }
            }
        }
    }


# ---------------------------------------------------------------------------
# Agent-specific file generators
# ---------------------------------------------------------------------------

def generate_instruction_content(skill_dir: str, repo_path: str, chunks: int) -> str:
    """Generate the full instruction content for agent instruction files."""
    return INSTRUCTION_BODY.format(skill_dir=skill_dir)


def write_instruction_file(path: Path, content: str, marker_comment: str) -> bool:
    """Write an instruction file, prepending a marker comment if creating new
    or updating an existing codebase-skill section.

    Returns True if the file was created/updated, False if skipped.
    """
    # Check if file already has our marker
    MARKER_START = "<!-- codebase-skill:begin -->"
    MARKER_END = "<!-- codebase-skill:end -->"
    SEP = "<!-- Do not edit between these markers — regenerate with cbsetup -->"

    full_section = f"{MARKER_START}\n{SEP}\n{content}\n{MARKER_END}"

    if path.exists():
        text = path.read_text()
        if MARKER_START in text:
            # Replace existing section
            start = text.index(MARKER_START)
            end = text.index(MARKER_END) + len(MARKER_END)
            new_text = text[:start] + full_section + text[end:]
            path.write_text(new_text)
            return True
        else:
            # Append to existing file
            new_text = text.rstrip() + "\n\n" + full_section + "\n"
            path.write_text(new_text)
            return True
    else:
        # For .cursorrules / .windsurfrules / .clinerules (no markdown comments)
        # we use a simpler section boundary
        path.parent.mkdir(parents=True, exist_ok=True)
        if marker_comment:
            path.write_text(f"{marker_comment}\n\n{content}\n")
        else:
            path.write_text(f"{content}\n")
        return True


def write_cursorrules(path: Path, content: str) -> bool:
    """Write .cursorrules — no HTML comments, use plain text markers."""
    MARKER_START = "# === codebase-skill:begin ==="
    MARKER_END = "# === codebase-skill:end ==="

    full_section = f"{MARKER_START}\n{content}\n{MARKER_END}"

    if path.exists():
        text = path.read_text()
        if MARKER_START in text:
            start = text.index(MARKER_START)
            end = text.index(MARKER_END) + len(MARKER_END)
            new_text = text[:start] + full_section + text[end:]
            path.write_text(new_text)
            return True
        else:
            new_text = text.rstrip() + "\n\n" + full_section + "\n"
            path.write_text(new_text)
            return True
    else:
        path.write_text(full_section + "\n")
        return True


def write_mcp_config(path: Path, config: dict) -> bool:
    """Write or merge MCP server config into an existing JSON file."""
    path.parent.mkdir(parents=True, exist_ok=True)

    if path.exists():
        try:
            existing = json.loads(path.read_text())
        except json.JSONDecodeError:
            existing = {}
    else:
        existing = {}

    # Deep-merge mcpServers
    if "mcpServers" not in existing:
        existing["mcpServers"] = {}

    existing["mcpServers"].update(config.get("mcpServers", {}))

    path.write_text(json.dumps(existing, indent=2) + "\n")
    return True


def write_pi_indexed(path: Path, repo_path: str, chunks: int, skill_dir: str) -> bool:
    """Write .pi-indexed marker file."""
    metadata = {
        "repo_path": str(repo_path),
        "last_indexed": datetime.now(timezone.utc).isoformat(),
        "total_chunks": chunks,
        "skill_dir": str(skill_dir),
        "mcp_command": f"{skill_dir}/.venv/bin/python3 {skill_dir}/mcp_server.py",
        "cli_command": f"cd {skill_dir} && .venv/bin/python3 cli.py",
    }
    path.write_text(json.dumps(metadata, indent=2) + "\n")
    return True


def write_cursor_rules_dir(path: Path, content: str) -> bool:
    """Write .cursor/rules/codebase-search.mdc (Cursor's newer convention)."""
    path.parent.mkdir(parents=True, exist_ok=True)

    mdc_content = f"""\
---
description: Semantic code search rules — use indexed search before reading files
globs:
alwaysApply: true
---

{content}
"""
    path.write_text(mdc_content)
    return True


# ---------------------------------------------------------------------------
# Main setup logic
# ---------------------------------------------------------------------------

AGENT_FILES = {
    # (relative_path, format, marker_comment)
    "agents_md":  ("AGENTS.md",                           "markdown", None),
    "claude_md":  ("CLAUDE.md",                            "markdown", None),
    "cursorrules": (".cursorrules",                         "plain",    None),
    "cursor_rules": (".cursor/rules/codebase-search.mdc",  "mdc",      None),
    "windsurfrules": (".windsurfrules",                    "plain",    None),
    "clinerules": (".clinerules",                           "plain",    None),
    "copilot":    (".github/copilot-instructions.md",       "markdown", None),
}

MCP_FILES = {
    # (agent_name, relative_path)
    "claude": (".claude/settings.json",     "Claude Code"),
    "cursor": (".cursor/mcp.json",           "Cursor"),
    "cline":  (".cline/mcp.json",           "Cline"),
    "windsurf": (".windsurf/mcp.json",      "Windsurf"),
}


def run_setup(repo_path: str, skill_dir: str, agents: list[str] | None = None, 
              mcp: list[str] | None = None, dry_run: bool = False) -> list[str]:
    """Generate all instruction and MCP config files."""
    repo = Path(repo_path).resolve()
    if not repo.is_dir():
        print(f"Error: {repo} is not a directory", file=sys.stderr)
        sys.exit(1)

    # Get indexing stats
    try:
        from config import AppConfig
        from indexer import CodeIndexer
        with CodeIndexer(AppConfig()) as idx:
            projects = idx.list_projects()
            matching = [p for p in projects if p["path"] == str(repo)]
            if not matching:
                print(f"Warning: {repo} is not in the index. Run `cli.py index {repo}` first.", file=sys.stderr)
                chunks = 0
            else:
                chunks = matching[0]["total_chunks"]
    except Exception as e:
        print(f"Warning: Could not query index: {e}", file=sys.stderr)
        chunks = 0

    skill_dir_resolved = str(Path(skill_dir).resolve())
    content = generate_instruction_content(skill_dir_resolved, str(repo), chunks)
    config = mcp_server_config(skill_dir_resolved)

    written = []

    # --- Agent instruction files ---
    for key, (rel_path, fmt, marker) in AGENT_FILES.items():
        if agents is not None and key not in agents:
            continue
        target = repo / rel_path
        if dry_run:
            print(f"  Would write: {target}")
            written.append(str(target))
            continue
        try:
            if fmt == "markdown":
                write_instruction_file(target, content, marker)
            elif fmt == "plain":
                write_cursorrules(target, content)
            elif fmt == "mdc":
                write_cursor_rules_dir(target, content)
            print(f"  ✓ Written: {target}")
            written.append(str(target))
        except Exception as e:
            print(f"  ✗ Error writing {target}: {e}", file=sys.stderr)

    # --- MCP config files ---
    for key, (rel_path, agent_name) in MCP_FILES.items():
        if mcp is not None and key not in mcp:
            continue
        target = repo / rel_path
        if dry_run:
            print(f"  Would write: {target}")
            written.append(str(target))
            continue
        try:
            write_mcp_config(target, config)
            print(f"  ✓ Written: {target}")
            written.append(str(target))
        except Exception as e:
            print(f"  ✗ Error writing {target}: {e}", file=sys.stderr)

    # --- .pi-indexed marker ---
    pi_indexed = repo / ".pi-indexed"
    if not dry_run:
        write_pi_indexed(pi_indexed, str(repo), chunks, skill_dir_resolved)
        print(f"  ✓ Written: {pi_indexed}")
    else:
        print(f"  Would write: {pi_indexed}")
    written.append(str(pi_indexed))

    return written


def main():
    parser = argparse.ArgumentParser(
        prog="cbsetup",
        description="Generate agent instruction files and MCP configs for an indexed repository.",
    )
    parser.add_argument("repo_path", help="Path to the indexed repository")
    parser.add_argument(
        "--skill-dir",
        default=os.path.dirname(os.path.abspath(__file__)),
        help="Path to the codebase-skill directory (default: this script's directory)",
    )
    parser.add_argument(
        "--agents",
        nargs="+",
        choices=list(AGENT_FILES.keys()),
        help="Which agent instruction files to generate (default: all)."
        " Choices: agents_md, claude_md, cursorrules, cursor_rules, windsurfrules, clinerules, copilot",
    )
    parser.add_argument(
        "--mcp",
        nargs="+",
        choices=list(MCP_FILES.keys()),
        help="Which MCP configs to generate (default: all)."
        " Choices: claude, cursor, cline, windsurf",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be written without writing",
    )
    parser.add_argument(
        "--instructions-only",
        action="store_true",
        help="Only write instruction files, skip MCP configs",
    )
    parser.add_argument(
        "--mcp-only",
        action="store_true",
        help="Only write MCP configs, skip instruction files",
    )

    args = parser.parse_args()

    agents = args.agents  # None if not specified (means all), list if specified
    mcp_agents = args.mcp  # None if not specified (means all), list if specified

    if args.instructions_only:
        mcp_agents = []
    if args.mcp_only:
        agents = []

    print(f"\n🔍 cbsetup — generating agent files for: {args.repo_path}")
    print(f"   Skill directory: {args.skill_dir}\n")

    written = run_setup(
        repo_path=args.repo_path,
        skill_dir=args.skill_dir,
        agents=agents,
        mcp=mcp_agents,
        dry_run=args.dry_run,
    )

    print(f"\n✅ Done. {len(written)} file(s) processed.")
    print("\nNext steps:")
    print("  1. Commit the generated files to version control (optional but recommended)")
    print("  2. Restart your agent to pick up the new instruction files and MCP config")
    print("  3. Verify with: list_projects (MCP tool) or cbstats (CLI)")


if __name__ == "__main__":
    main()