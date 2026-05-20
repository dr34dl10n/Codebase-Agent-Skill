#!/bin/bash
# Database initialization script (legacy — prefer deploy.sh)
# Run as a user with sudo access:
#   CODEINDEX_DB_PASSWORD=<pass> bash setup_db.sh
#
# For full deployment (DB + venv + deps), use deploy.sh instead.

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DB_PASSWORD="${CODEINDEX_DB_PASSWORD:-}"

if [ -z "$DB_PASSWORD" ]; then
    echo "ERROR: Set CODEINDEX_DB_PASSWORD env var before running."
    echo "  CODEINDEX_DB_PASSWORD=<pass> bash setup_db.sh"
    exit 1
fi

echo "Creating pgvector extension and tables..."
sudo -u postgres psql -d codeindex -f "$SCRIPT_DIR/init_db.sql"

echo ""
echo "Verifying setup..."
PGPASSWORD="$DB_PASSWORD" psql -h localhost -U codeindex -d codeindex -c "
    SELECT extname, extversion FROM pg_extension WHERE extname = 'vector';
    SELECT COUNT(*) AS tables FROM information_schema.tables 
        WHERE table_schema = 'public' AND table_name IN ('code_chunks', 'projects');
"

echo ""
echo "Done! Database is ready for codebase-skill."