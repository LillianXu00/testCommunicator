#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="/im"
log() { echo "[init] $*"; }

mkdir -p \
  "${ROOT_DIR}/scripts" \
  "${ROOT_DIR}/nginx/conf.d" \
  "${ROOT_DIR}/nginx/certs" \
  "${ROOT_DIR}/humhub/config" \
  "${ROOT_DIR}/humhub/modules" \
  "${ROOT_DIR}/humhub/uploads" \
  "${ROOT_DIR}/humhub/custom" \
  "${ROOT_DIR}/mariadb/data" \
  "${ROOT_DIR}/redis/data" \
  "${ROOT_DIR}/adapter/app" \
  "${ROOT_DIR}/adapter/tests" \
  "${ROOT_DIR}/auth-broker/app" \
  "${ROOT_DIR}/backups"

if [[ ! -f "${ROOT_DIR}/.env" ]]; then
  cp "${ROOT_DIR}/.env.example" "${ROOT_DIR}/.env"
  log "Created ${ROOT_DIR}/.env from template"
else
  log "${ROOT_DIR}/.env already exists, keeping it unchanged"
fi

if [[ ! -f "${ROOT_DIR}/nginx/certs/server.crt" ]]; then
  cat > "${ROOT_DIR}/nginx/certs/server.crt" <<'CRT'
-----BEGIN CERTIFICATE-----
PLACEHOLDER_CERTIFICATE_REPLACE_ME
-----END CERTIFICATE-----
CRT
  log "Created placeholder TLS certificate"
fi

if [[ ! -f "${ROOT_DIR}/nginx/certs/server.key" ]]; then
  cat > "${ROOT_DIR}/nginx/certs/server.key" <<'KEY'
-----BEGIN PRIVATE KEY-----
PLACEHOLDER_PRIVATE_KEY_REPLACE_ME
-----END PRIVATE KEY-----
KEY
  chmod 600 "${ROOT_DIR}/nginx/certs/server.key"
  log "Created placeholder TLS private key"
fi

chmod +x "${ROOT_DIR}/scripts/"*.sh || true
chown -R root:root "${ROOT_DIR}"

log "Initialization finished"
log "Please edit ${ROOT_DIR}/.env before running deploy.sh"
