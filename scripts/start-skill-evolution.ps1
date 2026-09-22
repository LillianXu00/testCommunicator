$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $Root "a2a-server\.venv\Scripts\python.exe"

if (-not (Test-Path $Python)) {
  throw "Python environment is missing. Run .\scripts\setup.ps1 first."
}

Push-Location (Join-Path $Root "skill-evolution-service")
try {
  & $Python -m skill_evolution @args
} finally {
  Pop-Location
}
