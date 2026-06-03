#!/bin/bash
# codebase-skill — Full deployment script
# Sets up everything from scratch: PostgreSQL DB, Python venv, dependencies.
#
# Usage:
#   bash deploy.sh                          # interactive (prompts for DB password)
#   bash deploy.sh <db_password>            # non-interactive (local PG)
#   bash deploy.sh --docker                # use Docker pgvectordb container
#   bash deploy.sh --docker <db_password>   # Docker + non-interactive
#
# Modes:
#   (default)   — Local PostgreSQL: creates user, DB, pgvector extension, tables.
#                 Requires PostgreSQL 15+ installed + sudo access.
#   --docker    — Docker PostgreSQL: clones/builds pgvectordb, runs via docker compose.
#                 Requires Docker + docker compose. Zero sudo needed.
#
# Password resolution order (never uses insecure defaults):
#   1. CODEINDEX_DB_PASSWORD env var (shell / ~/.hermes/.env / .env)
#   2. Password passed as CLI argument
#   3. Interactive prompt (local mode) or auto-generated random password (Docker mode)
#
# Prerequisites:
#   Local mode:  PostgreSQL 15+ running (sudo access for postgres user)
#   Docker mode: Docker + docker compose installed
#   Both modes:  Python 3.11+
#
# ModernBERT (default) auto-downloads from HuggingFace — no extra service needed.
# Optional: Ollama for nomic-embed-text (advanced — only if already running Ollama with GPU).
#
# After running this script, configure your agent's MCP server entry
# and run cbsetup to generate agent instruction files.
# See README.md for details.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# ─── Parse arguments ──────────────────────────────────────────────────
USE_DOCKER=false
CLI_PASSWORD=""

for arg in "$@"; do
    case "$arg" in
        --docker|-d) USE_DOCKER=true ;;
        *) CLI_PASSWORD="$arg" ;;
    esac
done

# ─── Color helpers ─────────────────────────────────────────────────────
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
RED='\033[0;31m'
NC='\033[0m'

step()   { echo -e "${CYAN}==>${NC} $1"; }
ok()     { echo -e "${GREEN}    ✓ $1${NC}"; }
warn()   { echo -e "${YELLOW}    ⚠ $1${NC}"; }
fail()   { echo -e "${RED}    ✗ $1${NC}"; }

# ─── Pre-load existing passwords ──────────────────────────────────────
# Load from ~/.hermes/.env if it exists (same logic as config.py)
_load_env() {
    local f="$1"
    [ ! -f "$f" ] && return
    while IFS='=' read -r key value; do
        key="$(echo "$key" | xargs)"
        value="$(echo "$value" | xargs)"
        # Strip surrounding quotes
        if [ ${#value} -ge 2 ] && [ "${value:0:1}" = "${value: -1:1}" ]; then
            case "${value:0:1}" in
                '"'|"'") value="${value:1:${#value}-2}" ;;
            esac
        fi
        # Only set if not already in environment (no overwrite)
        if [ -n "$key" ] && [ -z "${!key:-}" ]; then
            export "$key=$value"
        fi
    done < <(grep -v '^\s*#' "$f" | grep -v '^\s*$')
}

_load_env "$HOME/.hermes/.env"
_load_env "$SCRIPT_DIR/.env"

# ─── Password resolution ──────────────────────────────────────────────
# Priority: env var > CLI arg > prompt/generate
# NEVER default to "postgres" or any hardcoded password.

_resolve_db_password() {
    # 1. Already in environment (CODEINDEX_DB_PASSWORD from .hermes/.env or .env)
    if [ -n "${CODEINDEX_DB_PASSWORD:-}" ]; then
        echo "${CODEINDEX_DB_PASSWORD}"
        return
    fi
    # 2. Passed on CLI
    if [ -n "$CLI_PASSWORD" ]; then
        echo "$CLI_PASSWORD"
        return
    fi
    # 3. No password found — generate one
    local generated
    generated="$(openssl rand -base64 24 | tr -d '/+=' | head -c 32)"
    echo "$generated"
}

# ══════════════════════════════════════════════════════════════════════════
# MODE: Docker PostgreSQL (pgvectordb)
# ══════════════════════════════════════════════════════════════════════════
deploy_docker() {
    DOCKER_REPO_DIR="${CODEINDEX_DOCKER_REPO:-/data/docker-pgvectordb}"
    DOCKER_DB_USER="${CODEINDEX_DB_USER:-postgres}"
    DOCKER_DB_NAME="${CODEINDEX_DB_NAME:-codebase}"
    DOCKER_DB_PORT="${CODEINDEX_DB_PORT:-5433}"
    DOCKER_DB_PASSWORD="$(_resolve_db_password)"

    # Tell the user if we generated a new password
    if [ -z "${CODEINDEX_DB_PASSWORD:-}" ] && [ -z "$CLI_PASSWORD" ]; then
        warn "No existing password found — a random one was generated."
        echo "    Generated password is stored in $SCRIPT_DIR/.env"
        echo "    IMPORTANT: Keep this file secure and back it up."
    else
        ok "Reusing existing password from ${CODEINDEX_DB_PASSWORD:+env}${CLI_PASSWORD:+CLI arg}"
    fi

    step "Deploying PostgreSQL + pgvector via Docker..."

    # Check Docker is available
    if ! command -v docker &>/dev/null; then
        fail "Docker is not installed. Install it first: https://docs.docker.com/get-docker/"
        exit 1
    fi
    if ! docker compose version &>/dev/null 2>&1; then
        fail "docker compose is not available. Install the compose plugin."
        exit 1
    fi
    ok "Docker + compose found"

    # Clone or find the pgvectordb repo
    if [ ! -d "$DOCKER_REPO_DIR" ]; then
        step "Cloning pgvectordb repository..."
        git clone https://github.com/dr34dl10n/pgvectordb.git "$DOCKER_REPO_DIR"
        ok "Cloned to $DOCKER_REPO_DIR"
    else
        ok "pgvectordb repo found at $DOCKER_REPO_DIR"
    fi

    # Copy the codebase-skill schema into the Docker initdb directory
    # (pgvector extension is already created by 01_enable_pgvector.sql)
    DOCKER_INITDB="$DOCKER_REPO_DIR/initdb"
    step "Adding codebase-skill schema to Docker init scripts..."
    cp "$SCRIPT_DIR/init_db.sql" "$DOCKER_INITDB/02_codebase_skill_schema.sql"
    ok "Schema copied to $DOCKER_INITDB/02_codebase_skill_schema.sql"

    # Check if the container is already running
    if docker ps --format '{{.Names}}' | grep -q 'pgvectordb'; then
        ok "pgvectordb container is already running"
    else
        step "Building and starting pgvectordb container..."
        cd "$DOCKER_REPO_DIR"

        # Pass credentials via environment to docker compose (not hardcoded in compose file)
        export POSTGRES_USER="$DOCKER_DB_USER"
        export POSTGRES_PASSWORD="$DOCKER_DB_PASSWORD"
        export POSTGRES_DB="$DOCKER_DB_NAME"

        docker compose up -d --build
        ok "Container started"
    fi

    # Wait for PostgreSQL to be ready
    step "Waiting for PostgreSQL to be ready..."
    MAX_RETRIES=30
    RETRY=0
    while [ $RETRY -lt $MAX_RETRIES ]; do
        if docker exec pgvectordb pg_isready -U "$DOCKER_DB_USER" &>/dev/null; then
            ok "PostgreSQL is ready"
            break
        fi
        RETRY=$((RETRY + 1))
        sleep 1
    done
    if [ $RETRY -eq $MAX_RETRIES ]; then
        fail "PostgreSQL did not become ready in time"
        docker logs pgvectordb --tail 20
        exit 1
    fi

    # Verify pgvector and tables
    step "Verifying database setup..."
    docker exec pgvectordb psql -U "$DOCKER_DB_USER" -d "$DOCKER_DB_NAME" -c \
        "SELECT extname, extversion FROM pg_extension WHERE extname = 'vector';" 2>/dev/null && ok "pgvector: OK" || { fail "pgvector extension not found"; exit 1; }

    docker exec pgvectordb psql -U "$DOCKER_DB_USER" -d "$DOCKER_DB_NAME" -c \
        "SELECT COUNT(*) AS tables FROM information_schema.tables WHERE table_schema = 'public' AND table_name IN ('code_chunks', 'projects');" 2>/dev/null && ok "Tables: OK" || { fail "Tables not created"; exit 1; }

    # Store Docker-specific config in .env (with restrictive permissions)
    step "Writing .env configuration..."
    cat > "$SCRIPT_DIR/.env" <<EOF
# Database (Docker pgvectordb)
CODEINDEX_DB_HOST=localhost
CODEINDEX_DB_PORT=${DOCKER_DB_PORT}
CODEINDEX_DB_NAME=${DOCKER_DB_NAME}
CODEINDEX_DB_USER=${DOCKER_DB_USER}
CODEINDEX_DB_PASSWORD=${DOCKER_DB_PASSWORD}
CODEINDEX_DB_MODE=docker

# pgvectordb repo path (for docker compose commands)
CODEINDEX_DOCKER_REPO=${DOCKER_REPO_DIR}
EOF
    chmod 600 "$SCRIPT_DIR/.env"
    ok ".env written (mode 600 — owner read/write only)"

    echo ""
    echo -e "${GREEN}════════════════════════════════════════════════════════${NC}"
    echo -e "${GREEN}  Docker PostgreSQL is ready!${NC}"
    echo -e "${GREEN}════════════════════════════════════════════════════════${NC}"
    echo ""
    echo "  Connection: postgresql://${DOCKER_DB_USER}:***@localhost:${DOCKER_DB_PORT}/${DOCKER_DB_NAME}"
    echo "  Container:  pgvectordb (port ${DOCKER_DB_PORT} → 5432)"
    echo "  Password:   stored in $SCRIPT_DIR/.env"
    echo ""
    echo "  Manage:     cd ${DOCKER_REPO_DIR} && docker compose up -d   # start"
    echo "             cd ${DOCKER_REPO_DIR} && docker compose down       # stop"
    echo "             cd ${DOCKER_REPO_DIR} && docker compose down -v     # stop + delete data"
    echo ""
}

# ══════════════════════════════════════════════════════════════════════════
# MODE: Local PostgreSQL (classic)
# ══════════════════════════════════════════════════════════════════════════
deploy_local() {
    DB_NAME="${CODEINDEX_DB_NAME:-codeindex}"
    DB_USER="${CODEINDEX_DB_USER:-codeindex}"

    # Resolve password: env > CLI > interactive prompt
    if [ -n "${CODEINDEX_DB_PASSWORD:-}" ]; then
        DB_PASSWORD="$CODEINDEX_DB_PASSWORD"
        ok "Reusing existing password from environment"
    elif [ -n "$CLI_PASSWORD" ]; then
        DB_PASSWORD="$CLI_PASSWORD"
    else
        read -rsp "Enter password for DB user '$DB_USER': " DB_PASSWORD
        echo
        if [ -z "$DB_PASSWORD" ]; then
            fail "Password cannot be empty"
            exit 1
        fi
    fi

    step "Setting up local PostgreSQL..."

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

    ok "PostgreSQL ready"

    # Store config in .env (with restrictive permissions)
    step "Writing .env configuration..."
    cat > "$SCRIPT_DIR/.env" <<EOF
# Database (local PostgreSQL)
CODEINDEX_DB_HOST=localhost
CODEINDEX_DB_PORT=5432
CODEINDEX_DB_NAME=${DB_NAME}
CODEINDEX_DB_USER=${DB_USER}
CODEINDEX_DB_PASSWORD=${DB_PASSWORD}
CODEINDEX_DB_MODE=local
EOF
    chmod 600 "$SCRIPT_DIR/.env"
    ok ".env written (mode 600 — owner read/write only)"
}

# ══════════════════════════════════════════════════════════════════════════
# COMMON: Python venv + dependencies + verification
# ══════════════════════════════════════════════════════════════════════════
setup_python() {
    step "Setting up Python venv..."

    VENV_DIR="$SCRIPT_DIR/.venv"
    if [ ! -d "$VENV_DIR" ]; then
        python3 -m venv "$VENV_DIR"
    fi

    "$VENV_DIR/bin/pip" install --quiet --upgrade pip
    "$VENV_DIR/bin/pip" install --quiet -r "$SCRIPT_DIR/requirements.txt"

    ok "Python venv ready: $VENV_DIR"
}

verify() {
    step "Verifying..."

    # Source .env for verification (written by deploy_docker or deploy_local)
    if [ -f "$SCRIPT_DIR/.env" ]; then
        set -a
        source "$SCRIPT_DIR/.env"
        set +a
    fi

    # DB connection — adapt command for docker vs local
    if [ "${CODEINDEX_DB_MODE:-local}" = "docker" ]; then
        docker exec pgvectordb psql -U "$CODEINDEX_DB_USER" -d "$CODEINDEX_DB_NAME" -c \
            "SELECT extname, extversion FROM pg_extension WHERE extname = 'vector';" 2>/dev/null && ok "pgvector: OK" || fail "pgvector: FAILED"
    else
        PGPASSWORD="$CODEINDEX_DB_PASSWORD" psql -h localhost -U "$CODEINDEX_DB_USER" -d "$CODEINDEX_DB_NAME" -c \
            "SELECT extname, extversion FROM pg_extension WHERE extname = 'vector';" 2>/dev/null && ok "pgvector: OK" || fail "pgvector: FAILED"
    fi

    # Embedding model
    EMBED_MODEL=${CODEINDEX_EMBED_MODEL:-}
    if [ -z "$EMBED_MODEL" ]; then
        EMBED_MODEL=$("$VENV_DIR/bin/python3" "$SCRIPT_DIR/scripts/detect_model.py" 2>/dev/null | head -1 | sed 's/Recommended model: //')
        if [ -z "$EMBED_MODEL" ]; then
            EMBED_MODEL="modernbert-embed-base"
        fi
    fi
    echo "    Embedding model: $EMBED_MODEL"
    echo "    Run 'python scripts/detect_model.py --write-env' to persist this choice."

    if [[ "$EMBED_MODEL" == nomic* ]]; then
        EMBED_API_BASE=${CODEINDEX_EMBED_API_BASE:-http://localhost:11434}
        if curl -s "$EMBED_API_BASE/api/tags" | grep -q "$EMBED_MODEL" 2>/dev/null; then
            ok "Embedding service ($EMBED_MODEL): OK"
        else
            warn "Embedding model '$EMBED_MODEL' not found at $EMBED_API_BASE. If using Ollama: ollama pull $EMBED_MODEL"
        fi
    else
        echo "    Embedding model ($EMBED_MODEL): auto-downloads from HuggingFace (sentence-transformers)"
    fi

    # Python imports
    "$VENV_DIR/bin/python3" -c "
import tree_sitter_languages; import psycopg; import pgvector; import mcp; import sentence_transformers;
print('    Python deps: OK')
" 2>/dev/null || fail "Python deps: FAILED"

    # MCP server smoke test
    "$VENV_DIR/bin/python3" -c "
import sys; sys.path.insert(0, '$SCRIPT_DIR')
from mcp_server import app; print('    MCP server module: OK')
" 2>/dev/null || warn "MCP server module: check needed"
}

print_next_steps() {
    # Source from .env to get all vars including password
    if [ -f "$SCRIPT_DIR/.env" ]; then
        set -a
        source "$SCRIPT_DIR/.env"
        set +a
    fi

    echo ""
    echo -e "${GREEN}════════════════════════════════════════════════════════${NC}"
    echo -e "${GREEN}  ✓ Deployment complete!${NC}"
    echo -e "${GREEN}════════════════════════════════════════════════════════${NC}"
    echo ""
    echo "  DB Mode:    ${CODEINDEX_DB_MODE:-local}"
    echo "  Connection: postgresql://${CODEINDEX_DB_USER}:***@${CODEINDEX_DB_HOST}:${CODEINDEX_DB_PORT}/${CODEINDEX_DB_NAME}"
    echo "  Password:   stored in $SCRIPT_DIR/.env (mode 600)"
    echo ""
    echo "Next steps:"
    echo "  1. The password is stored in $SCRIPT_DIR/.env — keep this file secure."
    echo "     Your agent reads it automatically via config.py."
    if [ "${CODEINDEX_DB_MODE:-local}" = "docker" ]; then
        echo ""
        echo "  Docker commands:"
        echo "     cd ${CODEINDEX_DOCKER_REPO} && docker compose up -d     # start DB"
        echo "     cd ${CODEINDEX_DOCKER_REPO} && docker compose logs -f  # view logs"
        echo "     cd ${CODEINDEX_DOCKER_REPO} && docker compose down      # stop DB"
    fi
    echo ""
    echo "  2. Run cbsetup to auto-configure your agent:"
    echo "     $VENV_DIR/bin/python3 $SCRIPT_DIR/cbsetup.py /path/to/repo"
    echo ""
    echo "  3. Index your first repo:"
    echo "     $VENV_DIR/bin/python3 $SCRIPT_DIR/cli.py index /path/to/repo"
    echo ""
}

# ══════════════════════════════════════════════════════════════════════════
# Main
# ══════════════════════════════════════════════════════════════════════════

echo ""
echo -e "${CYAN}🔍 codebase-skill — Deployment${NC}"
echo ""

if $USE_DOCKER; then
    echo "  Mode: Docker (pgvectordb container)"
    deploy_docker
else
    echo "  Mode: Local PostgreSQL (use --docker for Docker mode)"
    deploy_local
fi

echo ""
setup_python
echo ""
verify
print_next_steps