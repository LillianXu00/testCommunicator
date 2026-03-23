#!/usr/bin/env bash
set -euo pipefail

log() { echo "[install-docker] $*"; }
fail() { echo "[install-docker][ERROR] $*" >&2; exit 1; }

if ! command -v yum >/dev/null 2>&1 && ! command -v dnf >/dev/null 2>&1; then
  fail "Neither yum nor dnf is available. This script targets Aliyun Linux / CentOS / RHEL compatible systems."
fi

PKG_TOOL="yum"
if command -v dnf >/dev/null 2>&1; then
  PKG_TOOL="dnf"
fi

log "Using package manager: ${PKG_TOOL}"
${PKG_TOOL} install -y yum-utils device-mapper-persistent-data lvm2 curl ca-certificates gnupg2

if ! ${PKG_TOOL} repolist all | grep -qi docker; then
  log "Configuring Docker CE repository"
  yum-config-manager --add-repo https://download.docker.com/linux/centos/docker-ce.repo || true
fi

log "Installing Docker Engine and Compose plugin"
${PKG_TOOL} install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

log "Enabling and starting Docker"
systemctl enable docker
systemctl restart docker

log "Verifying installation"
docker --version || fail "docker installation failed"
docker compose version || fail "docker compose plugin installation failed"

log "Docker installation completed successfully"
