#!/bin/bash
# codebase-skill — Full deployment script
# Sets up everything from scratch: PostgreSQL DB, user, pgvector, tables, Python venv.
#
# Usage:
#   bash deploy.sh                  # interactive (prompts for DB password)
#   bash deploy.sh <db_password>    # non-interactive
#
# Prerequisites:
#   - PostgreSQL 15+ running (sudo access for postgres user)
#   - Python 3.11+
#
# ModernBERT (default) auto-downloads from HuggingFace — no extra service needed.
# Optional: Ollama for nomic-embed-text backend.
#
# After running this script, configure your agent's MCP server entry.
# See README.md for details.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DB_NAME="${CODEINDEX_DB_NAME:-codeindex}"
DB_USER="${CODEINDEX_DB_USER:-codeindex}"
DB_PASSWORD="${1:-${CODEINDEX_DB_PASSWORD:-}}"

if [ -z "$DB_PASSWORD" ]; then
    read -rsp "Enter password for DB user '$DB_USER': " DB_PASSWORD
    echo
fi

# ─── 1. PostgreSQL: create user + database + extension ─────────────────
echo "==> Setting up PostgreSQL..."

# Create user if not exists
sudo -u postgres psql -tc "SELECT 1 FROM pg_roles WHERE rolname='$DB_USER'" | grep -q 1 || \
    sudo -u postgres psql -c "CREATE USER $DB_USER WITH LOGIN PASSWORD '$DB_PASSWORD';"

# Create database if not exists
sudo -u postgres psql -lqt | cut -d\| -f1 | grep -qw "$DB_NAME" || \
    sudo -u postgres psql -c "CREATE DATABASE $DB_NAME OWNER $DB_USER;"

# Grant pgvector extension (requires superuser)
sudo -u postgres psql -d "$DB_NAME" -c "CREATE EXTENSION IF NOT EXISTS vector;"

# Create tables
PGPASSWORD="$DB_PASSWORD" psql -h localhost -U "$DB_USER" -d "$DB_NAME" -f "$SCRIPT_DIR/init_db.sql"

echo "==> PostgreSQL ready."

# ─── 2. Python venv + dependencies ─────────────────────────────────────
echo "==> Setting up Python venv..."

VENV_DIR="$SCRIPT_DIR/.venv"
if [ ! -d "$VENV_DIR" ]; then
    python3 -m venv "$VENV_DIR"
fi

"$VENV_DIR/bin/pip" install --quiet --upgrade pip
"$VENV_DIR/bin/pip" install --quiet -r "$SCRIPT_DIR/requirements.txt"

echo "==> Python venv ready: $VENV_DIR"

# ─── 3. Verify ─────────────────────────────────────────────────────────
echo "==> Verifying..."

# DB connection
PGPASSWORD="$DB_PASSWORD" psql -h localhost -U "$DB_USER" -d "$DB_NAME" -c "
    SELECT extname, extversion FROM pg_extension WHERE extname = 'vector';
" 2>/dev/null && echo "    pgvector: OK" || echo "    pgvector: FAILED"

# Embedding service (Ollama or compatible)
EMBED_API_BASE=${CODEINDEX_EMBED_API_BASE:-http://localhost:11434}
EMBED_MODEL=${CODEINDEX_EMBED_MODEL:-}

# Auto-detect embedding model based on hardware
if [ -z "$EMBED_MODEL" ]; then
    EMBED_MODEL=$("$VENV_DIR/bin/python3" "$SCRIPT_DIR/scripts/detect_model.py" 2>/dev/null | head -1 | sed 's/Recommended model: //')
    if [ -z "$EMBED_MODEL" ]; then
        EMBED_MODEL="modernbert-embed-base"  # sensible fallback (no Ollama needed)
    fi
fi
echo "    Embedding model: $EMBED_MODEL"
echo "    Run 'python scripts/detect_model.py --write-env' to persist this choice."

# For ModernBERT (sentence-transformers), no service check needed — model auto-downloads.
if [[ "$EMBED_MODEL" == nomic* ]]; then
    if curl -s "$EMBED_API_BASE/api/tags" | grep -q "$EMBED_MODEL" 2>/dev/null; then
        echo "    Embedding service ($EMBED_MODEL): OK"
    else
        echo "    WARNING: Embedding model '$EMBED_MODEL' not found at $EMBED_API_BASE. If using Ollama: ollama pull $EMBED_MODEL"
    fi
else
    echo "    Embedding model ($EMBED_MODEL): auto-downloads from HuggingFace (sentence-transformers)"
fi

# Python imports
"$VENV_DIR/bin/python3" -c "
import tree_sitter_languages; import psycopg; import pgvector; import mcp; import sentence_transformers;
print('    Python deps: OK')
" 2>/dev/null || echo '    Python deps: FAILED'

# MCP server smoke test
"$VENV_DIR/bin/python3" -c "
import sys; sys.path.insert(0, '$SCRIPT_DIR')
from mcp_server import app; print('    MCP server module: OK')
" 2>/dev/null || echo "    MCP server module: FAILED"

echo ""
echo "==> Deployment complete!"
echo ""
echo "Next steps:"
echo "  1. Add these env vars to your agent config:"
echo "     CODEINDEX_DB_PASSWORD=$DB_PASSWORD"
echo "  2. Configure MCP server (see README.md)"
echo "  3. Index your first repo:"
echo "     $VENV_DIR/bin/python3 $SCRIPT_DIR/cli.py index /path/to/repo"