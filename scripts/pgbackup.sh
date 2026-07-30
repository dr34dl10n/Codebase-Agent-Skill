#!/usr/bin/env bash
# =============================================================================
# local-pgvector-backup.sh
# Backup PostgreSQL + PGVector exécuté localement sur le serveur DB
# puis poussé vers le serveur de backup via WireGuard
# =============================================================================

set -euo pipefail

# ----------------------------- Configuration ---------------------------------
# Emplacement temporaire local (sur le serveur PostgreSQL)
LOCAL_TMP_DIR="/var/tmp/pg_backup"
RETENTION_LOCAL_HOURS=24          # on ne garde le dump local que peu de temps

# Destination distante (via WireGuard)
REMOTE_HOST="10.10.0.100"         # IP WireGuard du serveur de backup
REMOTE_USER="backup"
REMOTE_DIR="/var/backups/postgresql"
RETENTION_DAYS=14

DATE=$(date +%Y%m%d_%H%M%S)
PGDATABASE="${PGDATABASE:-monapp}"
DUMP_NAME="\( {PGDATABASE}_pgvector_ \){DATE}.dump"
LOCAL_DUMP="\( {LOCAL_TMP_DIR}/ \){DUMP_NAME}"
LOG_FILE="\( {LOCAL_TMP_DIR}/backup_ \){DATE}.log"

# Utilisateur PostgreSQL local (idéalement un rôle dédié)
PGUSER="${PGUSER:-backup_user}"

# -----------------------------------------------------------------------------

mkdir -p "${LOCAL_TMP_DIR}"
exec > >(tee -a "${LOG_FILE}") 2>&1

echo "=== Début backup local PostgreSQL + PGVector - $(date) ==="
echo "Base : ${PGDATABASE}"

# ----------------------------- Vérifications ---------------------------------
echo "Vérification de l'extension vector..."
psql -U "\( {PGUSER}" -d " \){PGDATABASE}" -c \
  "SELECT extversion FROM pg_extension WHERE extname = 'vector';" || {
  echo "ERREUR : l'extension PGVector n'est pas installée."
  exit 1
}

# ----------------------------- Backup local ----------------------------------
echo "Lancement de pg_dump (format custom + compression)..."
pg_dump \
  -U "${PGUSER}" \
  --format=custom \
  --compress=9 \
  --verbose \
  --no-owner \
  --no-acl \
  --file="${LOCAL_DUMP}" \
  "${PGDATABASE}"

echo "Vérification de l'intégrité du dump..."
pg_restore --list "${LOCAL_DUMP}" > /dev/null

SIZE=\( (du -h " \){LOCAL_DUMP}" | cut -f1)
echo "Dump local créé : \( {LOCAL_DUMP} ( \){SIZE})"

# ----------------------------- Transfert vers le serveur de backup -----------
echo "Transfert vers \( {REMOTE_HOST}: \){REMOTE_DIR} via WireGuard..."
rsync -avz --progress \
  -e "ssh -o StrictHostKeyChecking=accept-new" \
  "${LOCAL_DUMP}" \
  "\( {REMOTE_USER}@ \){REMOTE_HOST}:${REMOTE_DIR}/"

echo "Transfert terminé."

# ----------------------------- Nettoyage local -------------------------------
echo "Nettoyage des dumps locaux de plus de ${RETENTION_LOCAL_HOURS}h..."
find "\( {LOCAL_TMP_DIR}" -name " \){PGDATABASE}_pgvector_*.dump" -mmin +$((RETENTION_LOCAL_HOURS * 60)) -delete
find "\( {LOCAL_TMP_DIR}" -name "backup_*.log" -mmin + \)((RETENTION_LOCAL_HOURS * 60)) -delete

# ----------------------------- Rotation distante (optionnel mais recommandé) -
# On peut laisser le serveur de backup gérer sa propre rotation,
# ou le faire depuis ici :
echo "Rotation distante (${RETENTION_DAYS} jours)..."
ssh "\( {REMOTE_USER}@ \){REMOTE_HOST}" \
  "find \( {REMOTE_DIR} -name ' \){PGDATABASE}_pgvector_*.dump' -mtime +${RETENTION_DAYS} -delete"

echo "=== Backup terminé avec succès - $(date) ==="
