#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="/im"
BACKUP_DIR="${1:-}"
ENV_FILE="${ROOT_DIR}/.env"
COMPOSE="docker compose --env-file ${ENV_FILE} -f ${ROOT_DIR}/docker-compose.yml"
log() { echo "[restore] $*"; }
fail() { echo "[restore][ERROR] $*" >&2; exit 1; }

[[ -n "${BACKUP_DIR}" ]] || fail "Usage: bash scripts/restore.sh /im/backups/<timestamp>"
[[ -d "${BACKUP_DIR}" ]] || fail "Backup directory not found: ${BACKUP_DIR}"
[[ -f "${BACKUP_DIR}/humhub.sql" ]] || fail "humhub.sql not found in backup directory"
[[ -f "${BACKUP_DIR}/humhub-files.tar.gz" ]] || fail "humhub-files.tar.gz not found in backup directory"
# shellcheck disable=SC1090
source "${ENV_FILE}"

log "Restoring file data"
tar -xzf "${BACKUP_DIR}/humhub-files.tar.gz" -C "${ROOT_DIR}"

log "Ensuring database service is running"
${COMPOSE} up -d db
sleep 10

log "Restoring MariaDB dump"
cat "${BACKUP_DIR}/humhub.sql" | ${COMPOSE} exec -T db sh -c 'exec mariadb -uroot -p"$MARIADB_ROOT_PASSWORD" "$HUMHUB_DB_NAME"'

log "Restore completed. Restarting services"
${COMPOSE} up -d humhub humhub-worker adapter auth-broker nginx
