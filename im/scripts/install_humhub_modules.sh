#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="/im"
ENV_FILE="${ROOT_DIR}/.env"
log() { echo "[modules] $*"; }
fail() { echo "[modules][ERROR] $*" >&2; exit 1; }

[[ -f "${ENV_FILE}" ]] || fail "${ENV_FILE} not found"
# shellcheck disable=SC1090
source "${ENV_FILE}"

mkdir -p "${ROOT_DIR}/humhub/modules"

if ! command -v unzip >/dev/null 2>&1; then
  if command -v yum >/dev/null 2>&1; then yum install -y unzip; elif command -v dnf >/dev/null 2>&1; then dnf install -y unzip; else fail "unzip is required"; fi
fi
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "${TMP_DIR}"' EXIT

fetch_module() {
  local name="$1"
  local version="$2"
  local url="$3"
  local dest="${ROOT_DIR}/humhub/modules/${name}"
  log "Downloading ${name} ${version}"
  curl -fsSL "${url}" -o "${TMP_DIR}/${name}.zip" || fail "Failed to download ${name} from ${url}"
  rm -rf "${dest}"
  mkdir -p "${dest}"
  unzip -oq "${TMP_DIR}/${name}.zip" -d "${TMP_DIR}/${name}"
  local extracted
  extracted="$(find "${TMP_DIR}/${name}" -mindepth 1 -maxdepth 1 -type d | head -n 1)"
  [[ -n "${extracted}" ]] || fail "Failed to extract ${name}"
  cp -a "${extracted}/." "${dest}/"
  log "Installed module into ${dest}"
}

REST_URL="https://download.humhub.com/downloads/module/rest/${HUMHUB_REST_MODULE_VERSION}.zip"
JWT_URL="https://github.com/humhub-contrib/jwt-sso/archive/refs/tags/${HUMHUB_JWT_SSO_MODULE_VERSION}.zip"

fetch_module "rest" "${HUMHUB_REST_MODULE_VERSION}" "${REST_URL}"
fetch_module "jwt-sso" "${HUMHUB_JWT_SSO_MODULE_VERSION}" "${JWT_URL}"

log "Module download completed. Enable the modules from HumHub admin UI if not auto-detected."
