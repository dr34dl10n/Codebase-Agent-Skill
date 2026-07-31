#!/usr/bin/env bash
# =============================================================================
# pgrestore.sh — Restaurer un dump PostgreSQL + PGVector (codebase-skill)
#
# Symétrique de pgbackup.sh. Restaure un dump (format custom pg_dump) dans une
# base existante ou créée à la volée. Vérifie le dump avant restauration et
# l'extension vector après.
#
# Usage:
#   ./pgrestore.sh <dump.dump>                 # restaure dans la base courante
#   ./pgrestore.sh <dump.dump> --create        # crée la base (DROP + CREATE) avant
#   ./pgrestore.sh <dump.dump> --target-db autre_base
#   ./pgrestore.sh --latest                    # restaure le dump le plus récent
#   ./pgrestore.sh --help
#
# Sécurité: refus d'écraser sans --confirm (sauf --create qui assume l'intent).
# Note: --create et l'extension vector nécessitent un rôle avec privilège CREATEDB
#       (typiquement un superuser). Sans cela, restaurer dans une base existante.
#
# Configuration (voir pgbackup.sh) : CODEINDEX_DB_* partagés.
# =============================================================================

set -euo pipefail

# ----------------------------- Chargement .env (comme config.py) -----------
load_env_file() {
  local f="$1"
  [ -r "$f" ] || return 0
  while IFS= read -r line; do
    line="${line%%#*}"
    line="${line//\"/}"; line="${line//\'/}"
    case "$line" in *=*) ;; *) continue ;; esac
    local key="${line%%=*}" val="${line#*=}"
    key="${key// /}"; val="${val# }"; val="${val% }"
    [ -n "$key" ] || continue
    [ -n "${!key-}" ] || export "$key=$val"
  done < "$f"
}
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
load_env_file "${HOME}/.hermes/.env"
load_env_file "${SCRIPT_DIR}/.env"
[ -n "${PGPASSWORD:-}" ] || [ -z "${CODEINDEX_DB_PASSWORD:-}" ] || export PGPASSWORD="${CODEINDEX_DB_PASSWORD}"

# ----------------------------- Helpers --------------------------------------
log()  { printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"; }
err()  { printf '[%s] ERREUR: %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >&2; }
die()  { err "$*"; exit 1; }

# ----------------------------- Config: DB mode ------------------------------
DB_MODE="${CODEINDEX_DB_MODE:-local}"
case "${DB_MODE}" in
  local)  DEFAULT_HOST="localhost"; DEFAULT_PORT="5432"; DEFAULT_DB="codeindex"; DEFAULT_USER="codeindex" ;;
  docker) DEFAULT_HOST="localhost"; DEFAULT_PORT="5433"; DEFAULT_DB="codebase";  DEFAULT_USER="postgres"  ;;
  *) die "CODEINDEX_DB_MODE invalide: '${DB_MODE}' (attendu: local|docker)" ;;
esac

PGHOST="${CODEINDEX_DB_HOST:-${DEFAULT_HOST}}"
PGPORT="${CODEINDEX_DB_PORT:-${DEFAULT_PORT}}"
PGDATABASE="${CODEINDEX_DB_NAME:-${DEFAULT_DB}}"
PGUSER="${CODEINDEX_DB_USER:-${DEFAULT_USER}}"
export PGHOST PGPORT PGDATABASE PGUSER

BACKUP_DIR="${PG_BACKUP_DIR:-/var/backups/pgvector}"
CREATE_DB=0
CONFIRM=0
USE_LATEST=0
TARGET_DB=""
DUMP_FILE=""

# ----------------------------- Préflight ------------------------------------
require_cmd() { command -v "$1" >/dev/null 2>&1 || die "Commande requise introuvable: $1"; }
require_cmd psql
require_cmd pg_restore

usage() { sed -n '2,/^# =\{10,\}/p' "$0" | sed 's/^# \{0,1\}//'; exit 0; }

# ----------------------------- Main -----------------------------------------
main() {
  while [ $# -gt 0 ]; do
    case "$1" in
      --create)      CREATE_DB=1 ;;
      --confirm)     CONFIRM=1 ;;
      --latest)      USE_LATEST=1 ;;
      --target-db)   shift; TARGET_DB="${1:-}" ; [ -n "$TARGET_DB" ] || die "--target-db nécessite une valeur" ;;
      --target-db=*) TARGET_DB="${1#*=}" ;;
      --help|-h)     usage ;;
      -*) die "Option inconnue: $1 (voir --help)" ;;
      *)  if [ -z "$DUMP_FILE" ] && [ "$USE_LATEST" != 1 ]; then DUMP_FILE="$1"; else die "Argument inattendu: $1"; fi ;;
    esac
    shift
  done

  [ -n "${PGPASSWORD:-${CODEINDEX_DB_PASSWORD:-}}" ] \
    || die "Mot de passe PostgreSQL manquant (définir PGPASSWORD ou CODEINDEX_DB_PASSWORD)"

  # Résolution du dump
  if [ "$USE_LATEST" = 1 ]; then
    DUMP_FILE="$(find "${BACKUP_DIR}" -maxdepth 1 -name "${PGDATABASE}_pgvector_*.dump" -printf '%T@ %p\n' 2>/dev/null \
                 | sort -rn | head -1 | cut -d' ' -f2-)"
    [ -n "$DUMP_FILE" ] || die "Aucun dump trouvé dans ${BACKUP_DIR} pour ${PGDATABASE}"
  fi
  [ -n "$DUMP_FILE" ] || die "Aucun dump spécifié. Usage: $0 <dump.dump> | --latest"
  [ -r "$DUMP_FILE" ] || die "Dump introuvable/illisible: $DUMP_FILE"

  # Base cible
  local db="$PGDATABASE"
  [ -z "$TARGET_DB" ] || db="$TARGET_DB"

  log "=== Restauration PostgreSQL + PGVector ==="
  log "Dump     : $DUMP_FILE"
  log "Base cible : $db (hôte ${PGHOST}:${PGPORT}, user ${PGUSER})"
  log "Créer base : $([ "$CREATE_DB" = 1 ] && echo oui || echo non)"

  # Vérification d'intégrité du dump AVANT toute action destructive
  log "Vérification de l'intégrité du dump..."
  if ! pg_restore --list "$DUMP_FILE" >/dev/null; then
    die "Dump corrompu: pg_restore --list a échoué."
  fi

  # Garde-fou: si pas --create et base == base par défaut, exiger --confirm
  if [ "$CREATE_DB" != 1 ] && [ "$CONFIRM" != 1 ]; then
    die "Restauration en place détectée (base existante '$db'). Ajoutez --confirm pour confirmer l'écrasement, ou --create pour recréer la base."
  fi

  # (Re)création de la base si demandé
  if [ "$CREATE_DB" = 1 ]; then
    log "Recréation de la base '$db'..."
    psql -d postgres -v ON_ERROR_STOP=1 <<SQL
SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '${db}';
DROP DATABASE IF EXISTS "${db}";
CREATE DATABASE "${db}";
SQL
    # L'extension vector nécessite un superuser normalement; on tente
    psql -d "$db" -v ON_ERROR_STOP=1 -c "CREATE EXTENSION IF NOT EXISTS vector;" \
      || err "Extension vector: création impossible (rôle non-superuser?). À installer manuellement."
  fi

  # Restauration (no-owner/no-acl pour portabilité entre environnements)
  log "Restauration en cours..."
  pg_restore \
    --no-owner \
    --no-acl \
    --dbname="$db" \
    --exit-on-error \
    "$DUMP_FILE"

  # Post-check: extension vector présente dans la base restaurée
  log "Vérification de l'extension vector dans '$db'..."
  if psql -d "$db" -tAc "SELECT extversion FROM pg_extension WHERE extname = 'vector';" | grep -q .; then
    log "Extension vector OK ($(psql -d "$db" -tAc "SELECT extversion FROM pg_extension WHERE extname = 'vector';"))."
  else
    err "Extension vector absente après restauration — vérifiez manuellement."
  fi

  log "=== Restauration terminée - $(date) ==="
}

main "$@"