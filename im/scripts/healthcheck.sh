#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="/im"
ENV_FILE="${ROOT_DIR}/.env"
COMPOSE="docker compose --env-file ${ENV_FILE} -f ${ROOT_DIR}/docker-compose.yml"
log() { echo "[healthcheck] $*"; }
fail() { echo "[healthcheck][ERROR] $*" >&2; exit 1; }

[[ -f "${ENV_FILE}" ]] || fail ".env not found"
# shellcheck disable=SC1090
source "${ENV_FILE}"

log "Checking docker compose service status"
${COMPOSE} ps

check_url() {
  local name="$1"
  local url="$2"
  if curl -fsS --max-time 10 "$url" >/dev/null; then
    log "OK: ${name} reachable at ${url}"
  else
    fail "${name} is not reachable at ${url}"
  fi
}

check_url "nginx" "http://127.0.0.1:${NGINX_HTTP_PORT}/healthz"
check_url "adapter" "http://127.0.0.1:${NGINX_HTTP_PORT}/api/adapter/healthz"

if ${COMPOSE} exec -T db sh -c 'mariadb-admin ping -h 127.0.0.1 -uroot -p"$MARIADB_ROOT_PASSWORD" --silent' >/dev/null; then
  log "OK: database reachable"
else
  fail "database unreachable"
fi

if ${COMPOSE} exec -T redis sh -c 'redis-cli -a "$REDIS_PASSWORD" ping | grep -q PONG' >/dev/null; then
  log "OK: redis reachable"
else
  fail "redis unreachable"
fi

log "Health check completed"
