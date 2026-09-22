$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$PluginRoot = Join-Path $Root "manager-a2a-plugin"
$ServerRoot = Join-Path $Root "a2a-server"
$RegistryRoot = Join-Path $Root "registry-service"
$VenvRoot = Join-Path $ServerRoot ".venv"
$VenvPython = Join-Path $VenvRoot "Scripts\python.exe"

Write-Host "Creating the Python A2A Registry and Gateway environment..."
if (-not (Test-Path $VenvPython)) {
  python -m venv $VenvRoot
  if ($LASTEXITCODE -ne 0) { throw "Could not create the Python virtual environment" }
}
& $VenvPython -m pip install --disable-pip-version-check -e "$ServerRoot[dev]" -e "$RegistryRoot[dev]"
if ($LASTEXITCODE -ne 0) { throw "Could not install the Python A2A Registry and Gateway" }

Write-Host "Installing and building the Manager A2A plugin..."
if (-not (Test-Path (Join-Path $PluginRoot "node_modules"))) {
  npm --prefix $PluginRoot ci --no-audit --no-fund
}
npm --prefix $PluginRoot run plugin:build
$pluginConfig = Get-Content -Raw (Join-Path $env:USERPROFILE ".openclaw\openclaw.json") | ConvertFrom-Json
if ($pluginConfig.plugins.entries.PSObject.Properties.Name -contains "manager-a2a-plugin") {
  Write-Host "Manager A2A plugin is already registered"
} else {
  openclaw plugins install --link $PluginRoot
  if ($LASTEXITCODE -ne 0) { throw "Could not install Manager A2A plugin" }
}

$agents = openclaw config get agents.list --json | ConvertFrom-Json
$mainIndex = -1
for ($index = 0; $index -lt $agents.Count; $index++) {
  if ($agents[$index].id -eq "main") {
    $mainIndex = $index
    break
  }
}
if ($mainIndex -lt 0) { throw "Existing OpenClaw main agent was not found" }
if (-not ($agents | Where-Object { $_.id -eq "planner" })) {
  throw "Existing OpenClaw planner agent was not found"
}

# The plugin tools are optional, so only main receives them.
$toolAllowList = '[\"a2a_discover\",\"a2a_send\"]'
openclaw config set "agents.list[$mainIndex].tools.alsoAllow" $toolAllowList --strict-json
if ($LASTEXITCODE -ne 0) { throw "Could not enable A2A tools for main" }

# The Python adapter calls planner through OpenClaw's Responses endpoint.
openclaw config set "gateway.http.endpoints.responses.enabled" "true" --strict-json
if ($LASTEXITCODE -ne 0) { throw "Could not enable the OpenResponses endpoint" }

$defaultWorkspace = Join-Path $env:USERPROFILE ".openclaw\workspace"
$mainWorkspace = if ($agents[$mainIndex].workspace) { $agents[$mainIndex].workspace } else { $defaultWorkspace }
$agentsFile = Join-Path $mainWorkspace "AGENTS.md"
$mainSkills = Join-Path $mainWorkspace "skills"
$evolutionSkillSource = Join-Path $Root "skill-evolution"
$evolutionSkillTarget = Join-Path $mainSkills "skill-evolution"
$null = New-Item -ItemType Directory -Force -Path $evolutionSkillTarget
Copy-Item -Path (Join-Path $evolutionSkillSource "*") -Destination $evolutionSkillTarget -Recurse -Force
Write-Host "Installed skill-evolution for main at $evolutionSkillTarget"
$addendum = Get-Content -Raw (Join-Path $Root "config\main-manager-addendum.md")
$current = Get-Content -Raw $agentsFile
if ($current -match "(?s)<!-- openclaw-a2a-manager-demo:start -->.*?<!-- openclaw-a2a-manager-demo:end -->") {
  $updated = [regex]::Replace(
    $current,
    "(?s)<!-- openclaw-a2a-manager-demo:start -->.*?<!-- openclaw-a2a-manager-demo:end -->",
    $addendum.Trim()
  )
  Set-Content -Path $agentsFile -Value $updated -Encoding utf8
  Write-Host "Updated A2A Manager guidance in $agentsFile"
} else {
  Add-Content -Path $agentsFile -Value ("`r`n" + $addendum) -Encoding utf8
  Write-Host "Added A2A Manager guidance to $agentsFile"
}

openclaw config validate
Write-Host "Setup complete. Restart the Gateway, then run: npm run bridge"
