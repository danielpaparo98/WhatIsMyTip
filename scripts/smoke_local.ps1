# ──────────────────────────────────────────────────────────────────────
# Smoke test for the local Docker stack configuration (PowerShell).
#
# Windows / PowerShell equivalent of scripts/smoke_local.sh.
# Validates docker-compose.yml without bringing the stack up.
#
# Usage
# -----
#   .\scripts\smoke_local.ps1                 # validate only
#   .\scripts\smoke_local.ps1 -Up             # also bring the stack up
#   .\scripts\smoke_local.ps1 -Up -Health     # also curl /health
# ──────────────────────────────────────────────────────────────────────

[CmdletBinding()]
param(
    [switch]$Up,
    [switch]$Health
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Resolve-Path (Join-Path $ScriptDir "..")
Set-Location $ProjectRoot

function Write-Banner { param([string]$Message) Write-Host $Message -ForegroundColor Cyan }
function Write-Info   { param([string]$Message) Write-Host $Message -ForegroundColor Yellow }
function Write-Ok     { param([string]$Message) Write-Host $Message -ForegroundColor Green }
function Write-Err    { param([string]$Message) Write-Host $Message -ForegroundColor Red }

# --- runtime detection ----------------------------------------------
$Runtime = $env:WIMT_RUNTIME
if (-not $Runtime) {
    $docker = Get-Command docker -ErrorAction SilentlyContinue
    if ($docker) {
        try { docker info | Out-Null; $Runtime = "docker" } catch { }
    }
    if (-not $Runtime) {
        $podman = Get-Command podman -ErrorAction SilentlyContinue
        if ($podman) {
            try { podman info | Out-Null; $Runtime = "podman" } catch { }
        }
    }
}

if (-not $Runtime) {
    Write-Info "No running container runtime found."
    Write-Info "Falling back to YAML syntax check only."
    try {
        Add-Type -AssemblyName "System.Collections.ObjectModel" -ErrorAction SilentlyContinue
    } catch { }
    # Use python if available
    $python = Get-Command python -ErrorAction SilentlyContinue
    if (-not $python) { $python = Get-Command python3 -ErrorAction SilentlyContinue }
    if ($python) {
        $yamlCheck = @'
import yaml, sys
d = yaml.safe_load(open("docker-compose.yml"))
print("OK: docker-compose.yml is valid YAML")
print("  services:", list(d.get("services", {}).keys()))
sys.exit(0)
'@
        & $python.Source -c $yamlCheck
        if ($LASTEXITCODE -ne 0) { throw "YAML validation failed" }
        exit 0
    }
    Write-Err "Cannot validate YAML without Python."
    exit 1
}

# --- compose command ------------------------------------------------
if ($Runtime -eq "docker") {
    $Compose = @("docker", "compose")
} else {
    try { podman help compose | Out-Null; $Compose = @("podman", "compose") }
    catch {
        $podmanCompose = Get-Command podman-compose -ErrorAction SilentlyContinue
        if ($podmanCompose) { $Compose = @("podman-compose") }
        else { Write-Err "podman found but no compose plugin."; exit 1 }
    }
}

Write-Banner "Smoke test  ·  runtime=$Runtime  ·  compose=$($Compose -join ' ')"
Write-Host ""

# --- 1) validate compose file ---------------------------------------
Write-Host "[1/3] Validating docker-compose.yml ..."
& $Compose[0] $Compose[1..($Compose.Count - 1)] config --quiet
if ($LASTEXITCODE -ne 0) {
    Write-Err "FAIL: docker compose config reported an error."
    & $Compose[0] $Compose[1..($Compose.Count - 1)] config
    exit 1
}
Write-Host "  OK" -ForegroundColor Green

# --- 2) optionally bring the stack up -------------------------------
if ($Up) {
    Write-Host ""
    Write-Host "[2/3] Bringing stack up ..."
    & $Compose[0] $Compose[1..($Compose.Count - 1)] up -d
    if ($LASTEXITCODE -ne 0) {
        Write-Err "FAIL: docker compose up failed."
        exit 2
    }
    Write-Host "  OK" -ForegroundColor Green
} else {
    Write-Host ""
    Write-Host "[2/3] Skipping stack up (use -Up to bring the stack up)"
}

# --- 3) optionally check /health ------------------------------------
if ($Health) {
    Write-Host ""
    Write-Host "[3/3] Checking API health ..."
    $url = "http://localhost:8000/health"
    try {
        $resp = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 5
        $code = [int]$resp.StatusCode
    } catch {
        $code = 0
    }
    if ($code -eq 200) {
        Write-Host "  OK" -ForegroundColor Green " ($url returned 200)"
    } else {
        Write-Host "  FAIL" -ForegroundColor Red ": $url returned $code (expected 200)"
        exit 3
    }
} else {
    Write-Host ""
    Write-Host "[3/3] Skipping /health check (use -Health to check)"
}

Write-Host ""
Write-Host "All smoke checks passed." -ForegroundColor Green
