#!/usr/bin/env bash
# =============================================================================
# pgbackup.sh — Backup local PostgreSQL + PGVector (codebase-skill)
#
# Exécute un dump local de la base PostgreSQL/PGVector, vérifie son intégrité,
# et applique une rétention locale.
#
# La partie "upload distant" (rsync vers un serveur de backup via WireGuard)
# est isolée dans la fonction `upload_to_remote()` et DÉSACTIVÉE par défaut.
# Pour l'activer plus tard : passer --upload ou définir PG_BACKUP_UPLOAD=1.
#
# Usage:
#   ./pgbackup.sh                 # dump local uniquement
#   ./pgbackup.sh --upload        # dump local + transfert distant
#   ./pgbackup.sh --help
#
# Configuration (toutes optionnelles, valeurs par défaut cohérentes avec
# config.py / deploy.sh) :
#   CODEINDEX_DB_MODE     local | docker   (défaut: local)
#   CODEINDEX_DB_HOST     défaut: localhost
#   CODEINDEX_DB_PORT     défaut: 5432 (local) / 5433 (docker)
#   CODEINDEX_DB_NAME     défaut: codeindex (local) / codebase (docker)
#   CODEINDEX_DB_USER     défaut: codeindex (local) / postgres (docker)
#   PGPASSWORD / CODEINDEX_DB_PASSWORD  — mot de passe PostgreSQL
#
#   PG_BACKUP_DIR         Répertoire de dump local (défaut: /var/backups/pgvector)
#   PG_BACKUP_RETENTION_DAYS  Rétention locale en jours (défaut: 7)
#   PG_BACKUP_COMPRESS   Niveau de compression 0-9 (défaut: 9)
#   PG_BACKUP_UPLOAD      1 = activer l'upload distant (défaut: 0)
#   PG_BACKUP_REMOTE_HOST PG_BACKUP_REMOTE_USER PG_BACKUP_REMOTE_DIR
# =============================================================================
set -euo pipefail

# ----------------------------- Chargement .env (comme config.py) -----------
# Charge ~/.hermes/.env puis le .env du projet (sans écraser l'existant).
load_env_file() {
  local f="$1"
  [ -r "$f" ] || return 0
  while IFS= read -r line; do
    line="${line%%#*}"          # strip commentaires
    line="${line//\"/}"; line="${line//\'/}"  # strip quotes simples
    case "$line" in
      *=*) ;;
      *) continue ;;
    esac
    local key="${line%%=*}" val="${line#*=}"
    key="${key// /}"; val="${val# }"; val="${val% }"
    [ -n "$key" ] || continue
    [ -n "${!key-}" ] || export "$key=$val"
  done < "$f"
}
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
load_env_file "${HOME}/.hermes/.env"
load_env_file "${SCRIPT_DIR}/.env"
# PGPASSWORD alias pour la commodité (libpq lit PGPASSWORD)
[ -n "${PGPASSWORD:-}" ] || [ -z "${CODEINDEX_DB_PASSWORD:-}" ] || export PGPASSWORD="${CODEINDEX_DB_PASSWORD}"

# ----------------------------- Helpers --------------------------------------
log()  { printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"; }
err()  { printf '[%s] ERREUR: %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >&2; }
die()  { err "$*"; exit 1; }

# ----------------------------- Config: DB mode ------------------------------
DB_MODE="${CODEINDEX_DB_MODE:-local}"
case "${DB_MODE}" in
  local)
    DEFAULT_HOST="localhost"; DEFAULT_PORT="5432"
    DEFAULT_DB="codeindex";  DEFAULT_USER="codeindex"
    ;;
  docker)
    DEFAULT_HOST="localhost"; DEFAULT_PORT="5433"
    DEFAULT_DB="codebase";   DEFAULT_USER="postgres"
    ;;
  *)
    die "CODEINDEX_DB_MODE invalide: '${DB_MODE}' (attendu: local|docker)"
    ;;
esac

PGHOST="${CODEINDEX_DB_HOST:-${DEFAULT_HOST}}"
PGPORT="${CODEINDEX_DB_PORT:-${DEFAULT_PORT}}"
PGDATABASE="${CODEINDEX_DB_NAME:-${DEFAULT_DB}}"
PGUSER="${CODEINDEX_DB_USER:-${DEFAULT_USER}}"
# PGPASSWORD est lu directement par libpq (hérité de l'environnement)

export PGHOST PGPORT PGDATABASE PGUSER

# ----------------------------- Config: backup ------------------------------
BACKUP_DIR="${PG_BACKUP_DIR:-/var/backups/pgvector}"
RETENTION_DAYS="${PG_BACKUP_RETENTION_DAYS:-7}"
COMPRESS="${PG_BACKUP_COMPRESS:-9}"
DO_UPLOAD="${PG_BACKUP_UPLOAD:-0}"

# Remote (utilisé uniquement par upload_to_remote)
REMOTE_HOST="${PG_BACKUP_REMOTE_HOST:-10.10.0.100}"
REMOTE_USER="${PG_BACKUP_REMOTE_USER:-backup}"
REMOTE_DIR="${PG_BACKUP_REMOTE_DIR:-/var/backups/postgresql}"
REMOTE_RETENTION_DAYS="${PG_BACKUP_REMOTE_RETENTION_DAYS:-14}"

DATE="$(date '+%Y%m%d_%H%M%S')"
DUMP_NAME="${PGDATABASE}_pgvector_${DATE}.dump"
DUMP_PATH="${BACKUP_DIR}/${DUMP_NAME}"
LOG_PATH="${BACKUP_DIR}/backup_${DATE}.log"

# ----------------------------- Préflight ------------------------------------
require_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "Commande requise introuvable: $1"
}

require_cmd pg_dump
require_cmd pg_restore
require_cmd psql

usage() {
  sed -n '2,/^# =\{10,\}/p' "$0" | sed 's/^# \{0,1\}//'
  exit 0
}

# ----------------------------- Upload distant (fonction pour plus tard) -----
# Désactivée par défaut. Activée via --upload ou PG_BACKUP_UPLOAD=1.
upload_to_remote() {
  local dump="$1"
  require_cmd rsync
  [ -n "${REMOTE_HOST}" ] || die "REMOTE_HOST non configuré pour l'upload"
  log "Transfert vers ${REMOTE_USER}@${REMOTE_HOST}:${REMOTE_DIR} ..."
  rsync -az --progress \
    -e "ssh -o StrictHostKeyChecking=accept-new" \
    "${dump}" "${REMOTE_USER}@${REMOTE_HOST}:${REMOTE_DIR}/"
  log "Transfert terminé."

  log "Rotation distante (${REMOTE_RETENTION_DAYS} jours)..."
  ssh -o StrictHostKeyChecking=accept-new "${REMOTE_USER}@${REMOTE_HOST}" \
    "find '${REMOTE_DIR}' -name '${PGDATABASE}_pgvector_*.dump' -mtime +${REMOTE_RETENTION_DAYS} -delete" \
    || err "Rotation distante: échec (non fatal)"
}

# ----------------------------- Main -----------------------------------------
main() {
  # Parsing args
  while [ $# -gt 0 ]; do
    case "$1" in
      --upload) DO_UPLOAD=1 ;;
      --help|-h) usage ;;
      *) die "Option inconnue: $1 (voir --help)" ;;
    esac
    shift
  done

  mkdir -p "${BACKUP_DIR}"

  # Logging: stdout+stderr -> fichier + console
  exec > >(tee -a "${LOG_PATH}") 2>&1

  log "=== Backup local PostgreSQL + PGVector ==="
  log "Mode     : ${DB_MODE}"
  log "Hôte     : ${PGHOST}:${PGPORT}"
  log "Base     : ${PGDATABASE}"
  log "Utilisateur : ${PGUSER}"
  log "Sortie   : ${DUMP_PATH}"
  log "Upload   : $([ "${DO_UPLOAD}" = 1 ] && echo oui || echo non)"

  # Vérif mot de passe
  [ -n "${PGPASSWORD:-${CODEINDEX_DB_PASSWORD:-}}" ] \
    || die "Mot de passe PostgreSQL manquant (définir PGPASSWORD ou CODEINDEX_DB_PASSWORD)"

  # Vérif extension pgvector
  log "Vérification de l'extension vector..."
  if ! psql -d "${PGDATABASE}" -tAc \
      "SELECT extversion FROM pg_extension WHERE extname = 'vector';" \
      | grep -q .; then
    die "L'extension PGVector n'est pas installée sur '${PGDATABASE}'."
  fi

  # Dump (format custom = compression native pg_dump, + --compress)
  log "Lancement de pg_dump..."
  pg_dump \
    --format=custom \
    --compress="${COMPRESS}" \
    --no-owner \
    --no-acl \
    --verbose \
    --file="${DUMP_PATH}" \
    "${PGDATABASE}"

  # Vérification d'intégrité (lecture de la table des contents du dump)
  log "Vérification de l'intégrité du dump..."
  if ! pg_restore --list "${DUMP_PATH}" >/dev/null; then
    die "Dump corrompu: pg_restore --list a échoué."
  fi

  local size; size="$(du -h "${DUMP_PATH}" | cut -f1)"
  log "Dump local créé : ${DUMP_PATH} (${size})"

  # Upload (optionnel, fonction pour plus tard)
  if [ "${DO_UPLOAD}" = 1 ]; then
    upload_to_remote "${DUMP_PATH}"
  else
    log "Upload distant désactivé (passer --upload pour l'activer)."
  fi

  # Rétention locale
  log "Nettoyage local > ${RETENTION_DAYS} jours..."
  find "${BACKUP_DIR}" -name "${PGDATABASE}_pgvector_*.dump" -mtime +"${RETENTION_DAYS}" -delete
  find "${BACKUP_DIR}" -name "backup_*.log"            -mtime +"${RETENTION_DAYS}" -delete

  log "=== Backup terminé avec succès - $(date) ==="
}

main "$@"