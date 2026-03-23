#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="/im"
ENV_FILE="${ROOT_DIR}/.env"
NEW_VERSION="${1:-}"
COMPOSE="docker compose --env-file ${ENV_FILE} -f ${ROOT_DIR}/docker-compose.yml"
log() { echo "[upgrade] $*"; }
fail() { echo "[upgrade][ERROR] $*" >&2; exit 1; }

[[ -n "${NEW_VERSION}" ]] || fail "Usage: bash scripts/upgrade_humhub.sh <new-version>"
[[ -f "${ENV_FILE}" ]] || fail ".env not found"

log "Creating safety backup before upgrade"
bash "${ROOT_DIR}/scripts/backup.sh"

log "Updating HUMHUB_VERSION in ${ENV_FILE} to ${NEW_VERSION}"
sed -i "s/^HUMHUB_VERSION=.*/HUMHUB_VERSION=${NEW_VERSION}/" "${ENV_FILE}"

log "Pulling new HumHub image"
${COMPOSE} pull humhub humhub-worker || true

log "Restarting HumHub services"
${COMPOSE} up -d humhub humhub-worker

cat <<MSG
[upgrade] Upgrade flow completed.

IMPORTANT MANUAL CHECKS:
1. Review HumHub release notes for ${NEW_VERSION}.
2. Verify REST / JWT SSO module compatibility.
3. Open HumHub admin UI and run any pending migrations.
4. Run bash /im/scripts/healthcheck.sh.
5. If anything breaks, restore from the latest backup under /im/backups.
MSG
