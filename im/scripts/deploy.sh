#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="/im"
COMPOSE="docker compose --env-file ${ROOT_DIR}/.env -f ${ROOT_DIR}/docker-compose.yml"
log() { echo "[deploy] $*"; }
fail() { echo "[deploy][ERROR] $*" >&2; exit 1; }

cd "${ROOT_DIR}"
[[ -f .env ]] || fail ".env not found. Run scripts/init.sh first."
# shellcheck disable=SC1091
source "${ROOT_DIR}/.env"
command -v docker >/dev/null 2>&1 || fail "docker is not installed"
docker compose version >/dev/null 2>&1 || fail "docker compose plugin is not installed"

bash "${ROOT_DIR}/scripts/init.sh"

log "Pulling/building images"
${COMPOSE} pull db redis humhub nginx || true
${COMPOSE} build adapter auth-broker

log "Starting core services"
${COMPOSE} up -d db redis humhub humhub-worker adapter auth-broker nginx

log "Waiting for database and redis"
sleep 10

log "Current service status"
${COMPOSE} ps

cat <<MSG
[deploy] Deployment finished.

Next steps:
1. Visit ${PUBLIC_BASE_URL:-http://localhost}/ and complete HumHub installation wizard if it is the first deployment.
2. Install REST + JWT SSO modules: bash /im/scripts/install_humhub_modules.sh
3. Configure a HumHub service-account API token and write it into /im/.env as HUMHUB_SERVICE_ACCOUNT_TOKEN.
4. Restart adapter after updating token: ${COMPOSE} restart adapter
5. Run health check: bash /im/scripts/healthcheck.sh
MSG
