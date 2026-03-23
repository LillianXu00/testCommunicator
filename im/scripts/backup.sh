#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="/im"
ENV_FILE="${ROOT_DIR}/.env"
COMPOSE="docker compose --env-file ${ENV_FILE} -f ${ROOT_DIR}/docker-compose.yml"
log() { echo "[backup] $*"; }
fail() { echo "[backup][ERROR] $*" >&2; exit 1; }

[[ -f "${ENV_FILE}" ]] || fail ".env not found"
# shellcheck disable=SC1090
source "${ENV_FILE}"

TIMESTAMP="$(date +%Y%m%d-%H%M%S)"
TARGET_DIR="${ROOT_DIR}/backups/${TIMESTAMP}"
mkdir -p "${TARGET_DIR}"

log "Dumping MariaDB"
${COMPOSE} exec -T db sh -c 'exec mariadb-dump -uroot -p"$MARIADB_ROOT_PASSWORD" "$HUMHUB_DB_NAME"' > "${TARGET_DIR}/humhub.sql"

log "Archiving application data"
tar -czf "${TARGET_DIR}/humhub-files.tar.gz" -C "${ROOT_DIR}" humhub/config humhub/modules humhub/uploads humhub/custom
cp "${ROOT_DIR}/.env" "${TARGET_DIR}/.env.backup"

log "Backup completed: ${TARGET_DIR}"
