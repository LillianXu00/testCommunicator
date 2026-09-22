@echo off
setlocal EnableExtensions EnableDelayedExpansion

rem Always run from the project directory that contains this script.
cd /d "%~dp0"

rem An explicit first argument overrides automatic IP detection.
rem Example: start-network.cmd 192.168.1.10
set "LAN_IP=%~1"

if not defined LAN_IP (
    for /f "tokens=4" %%I in ('route print -4 ^| findstr /r /c:"^[ ]*0\.0\.0\.0[ ]*0\.0\.0\.0"') do (
        if not defined LAN_IP set "LAN_IP=%%I"
    )
)

if not defined LAN_IP (
    echo [ERROR] Unable to detect the IPv4 address used by the default route.
    echo Run "ipconfig", choose a reachable IPv4 address, and retry:
    echo.
    echo     start-network.cmd 192.168.1.10
    echo.
    exit /b 1
)

if not exist "package.json" (
    echo [ERROR] package.json was not found in:
    echo     %CD%
    exit /b 1
)

where npm >nul 2>&1
if errorlevel 1 (
    echo [ERROR] npm is not available on PATH.
    exit /b 1
)

set "REGISTRY_HOST=0.0.0.0"
set "REGISTRY_PORT=4200"
set "REGISTRY_PUBLIC_URL=http://!LAN_IP!:4200"

set "A2A_HOST=0.0.0.0"
set "A2A_PORT=4101"
set "A2A_PUBLIC_URL=http://!LAN_IP!:4101"
set "A2A_GATEWAY_PUBLIC_URL=http://!LAN_IP!:4101"

rem Local development defaults: RabbitMQ runs in Docker and exposes AMQP on
rem the host. Values supplied by the caller take precedence over these defaults.
if not defined A2A_BROKER_URL set "A2A_BROKER_URL=amqp://guest:guest@127.0.0.1:5672/"
if not defined A2A_QUEUE_PREFIX set "A2A_QUEUE_PREFIX=a2a.task"
if not defined A2A_QUEUE_PARTITIONS set "A2A_QUEUE_PARTITIONS=8"
if not defined A2A_QUEUE_RESULT_TIMEOUT_SECONDS set "A2A_QUEUE_RESULT_TIMEOUT_SECONDS=1800"
if not defined A2A_WORKER_LEASE_SECONDS set "A2A_WORKER_LEASE_SECONDS=2400"

rem Local Memos defaults. Values supplied by the caller still take precedence.
rem Replace the MEMOS_TOKEN placeholder below with the local Memos access token.
if not defined MEMOS_BASE_URL set "MEMOS_BASE_URL=https://memos.memtensor.cn/api/openmem/v1"
if not defined MEMOS_TOKEN set "MEMOS_TOKEN=mpg-wphKWNQjYEAo/gvaKy7R2Xxxi189uXGPIBs12Y7N"
if not defined MEMOS_TIMEOUT_SECONDS set "MEMOS_TIMEOUT_SECONDS=30"
if not defined MEMOS_USER_ID set "MEMOS_USER_ID=openclaw-user"
if not defined MEMOS_APP_ID set "MEMOS_APP_ID=a2a-coordination"
if not defined MEMOS_ALLOW_PUBLIC set "MEMOS_ALLOW_PUBLIC=0"
if not defined MEMOS_ASYNC_MODE set "MEMOS_ASYNC_MODE=0"
if not defined AGENT_EXPERIENCE_ENABLED set "AGENT_EXPERIENCE_ENABLED=1"
if not defined SKILL_EVOLUTION_ENABLED set "SKILL_EVOLUTION_ENABLED=1"

rem Skill evolution uses SkillHub's native HTTP API directly from Python.
if not defined SKILLHUB_REGISTRY set "SKILLHUB_REGISTRY=http://127.0.0.1:8080"
if not defined SKILLHUB_API_TOKEN if defined SKILLHUB_TOKEN set "SKILLHUB_API_TOKEN=!SKILLHUB_TOKEN!"
if not defined SKILLHUB_API_TOKEN set "SKILLHUB_API_TOKEN=sk_k6tpUbhej5YQL238sWGAM1zciZ7Bipfqx1mcKCAwEhA"
if not defined SKILLHUB_TIMEOUT_SECONDS set "SKILLHUB_TIMEOUT_SECONDS=300"

echo.
echo Starting A2A coordination services
echo ----------------------------------
echo LAN IP:       !LAN_IP!
echo Registry UI:  !REGISTRY_PUBLIC_URL!/registry-ui/
echo Registry API: !REGISTRY_PUBLIC_URL!
echo A2A Gateway:  !A2A_PUBLIC_URL!
echo Task queue:   RabbitMQ on 127.0.0.1:5672 ^(!A2A_QUEUE_PARTITIONS! partitions^)
echo Memos API:    !MEMOS_BASE_URL!
if defined SKILLHUB_REGISTRY echo SkillHub API: !SKILLHUB_REGISTRY!
if "!AGENT_EXPERIENCE_ENABLED!"=="1" echo Experience:   Enabled ^(asynchronous Agent reflection to Memos^)
if "!SKILL_EVOLUTION_ENABLED!"=="1" echo Skill upkeep: Enabled ^(Manager + Memos + SkillHub^)
echo.
echo Press Ctrl+C to stop all local coordination services.
echo.

call npm run bridge
set "EXIT_CODE=!ERRORLEVEL!"

echo.
if not "!EXIT_CODE!"=="0" (
    echo Services exited with code !EXIT_CODE!.
) else (
    echo Services stopped.
)

exit /b !EXIT_CODE!
