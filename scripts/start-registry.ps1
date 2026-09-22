$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $Root "a2a-server\.venv\Scripts\python.exe"

if (-not (Test-Path $Python)) {
  throw "Python environment is missing. Run .\scripts\setup.ps1 first."
}

$env:REGISTRY_HOST = if ($env:REGISTRY_HOST) { $env:REGISTRY_HOST } else { "127.0.0.1" }
$env:REGISTRY_PORT = if ($env:REGISTRY_PORT) { $env:REGISTRY_PORT } else { "4200" }
$env:REGISTRY_PUBLIC_URL = if ($env:REGISTRY_PUBLIC_URL) {
  $env:REGISTRY_PUBLIC_URL
} else {
  "http://$($env:REGISTRY_HOST):$($env:REGISTRY_PORT)"
}
$env:A2A_GATEWAY_PUBLIC_URL = if ($env:A2A_GATEWAY_PUBLIC_URL) {
  $env:A2A_GATEWAY_PUBLIC_URL
} else {
  "http://127.0.0.1:4101"
}

Push-Location (Join-Path $Root "registry-service")
try {
  & $Python -m registry_service.app
} finally {
  Pop-Location
}

