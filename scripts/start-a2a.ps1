$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $Root "a2a-server\.venv\Scripts\python.exe"

if (-not (Test-Path $Python)) { throw "Python environment is missing. Run .\scripts\setup.ps1 first." }
if (-not $env:OPENCLAW_GATEWAY_TOKEN) {
  $config = Get-Content -Raw (Join-Path $env:USERPROFILE ".openclaw\openclaw.json") | ConvertFrom-Json
  $env:OPENCLAW_GATEWAY_TOKEN = $config.gateway.auth.token
}
$env:OPENCLAW_GATEWAY_URL = if ($env:OPENCLAW_GATEWAY_URL) { $env:OPENCLAW_GATEWAY_URL } else { "http://127.0.0.1:18789" }
$env:A2A_REGISTRY_URL = if ($env:A2A_REGISTRY_URL) { $env:A2A_REGISTRY_URL } else { "http://127.0.0.1:4200" }
Push-Location (Join-Path $Root "a2a-server")
try {
  & $Python -m a2a_server.server
} finally {
  Pop-Location
}
