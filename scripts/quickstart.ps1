<#
.SYNOPSIS
  HELIOS one-command quickstart (Windows / PowerShell).

  Brings up the containerized stack, waits for it to be healthy, installs the
  SDK, seeds the demo fixture, and runs the live offline demo agent.

.EXAMPLE
  ./scripts/quickstart.ps1
#>
[CmdletBinding()]
param(
    [switch]$SkipSdkInstall,
    [int]$TimeoutSeconds = 120
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot

function Write-Step($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }

# 0. preflight
Write-Step "Checking prerequisites"
docker version --format '{{.Server.Version}}' | Out-Null
if ($LASTEXITCODE -ne 0) { throw "Docker does not appear to be running. Start Docker and retry." }
python --version | Out-Null
Write-Host "  Docker and Python found."

# 1. stack
Write-Step "Starting the HELIOS stack (docker compose up -d)"
Push-Location (Join-Path $root "deploy")
try { docker compose up -d } finally { Pop-Location }

# 2. wait for ClickHouse to answer (the seed depends on it)
Write-Step "Waiting for ClickHouse to become healthy"
$deadline = (Get-Date).AddSeconds($TimeoutSeconds)
do {
    Start-Sleep -Seconds 3
    $state = (docker inspect -f '{{.State.Health.Status}}' helios-clickhouse 2>$null)
    Write-Host "  clickhouse: $state"
} until ($state -eq "healthy" -or (Get-Date) -gt $deadline)
if ($state -ne "healthy") { throw "ClickHouse did not become healthy within $TimeoutSeconds s." }

# 3. SDK
if (-not $SkipSdkInstall) {
    Write-Step "Installing the HELIOS SDK (editable)"
    # pip writes progress/notices to stderr; don't let that abort the script.
    # Success is confirmed by the import check below, not pip's stderr.
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    & python -m pip install -e (Join-Path $root "sdk-python") --quiet 2>&1 | Out-Null
    $ErrorActionPreference = $prev
    python -c "import helios_sdk" 2>$null
    if ($LASTEXITCODE -ne 0) { throw "helios_sdk failed to import after install." }
    Write-Host "  helios_sdk installed."
}

# 4. seed the golden fixture
Write-Step "Seeding the demo fixture"
python (Join-Path $root "deploy/scripts/seed_demo.py") --reset

# 5. run the live demo agent
Write-Step "Running the live demo agent (offline, no external LLM)"
python (Join-Path $root "sdk-python/examples/refund_agent.py")

Write-Host "`nHELIOS is ready." -ForegroundColor Green
Write-Host "  Grafana   : http://localhost:3000 (HELIOS folder)"
Write-Host "  Flagship  : http://localhost:3000/d/helios-causal-path"
Write-Host "  Tutorial  : docs/tutorial-stale-memory.md"
