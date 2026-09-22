$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $Root "a2a-server\.venv\Scripts\python.exe"

if (-not (Test-Path $Python)) {
  throw "Python environment is missing. Run .\scripts\setup.ps1 first."
}
if (-not $env:A2A_BROKER_URL) {
  $env:A2A_BROKER_URL = "amqp://a2a:change-me-rabbitmq@127.0.0.1:5672/"
}
if (-not $env:A2A_REGISTRY_URL) {
  $env:A2A_REGISTRY_URL = "http://127.0.0.1:4200"
}

Push-Location (Join-Path $Root "a2a-server")
try {
  & $Python -m a2a_server.worker
} finally {
  Pop-Location
}
