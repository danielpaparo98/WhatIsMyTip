# ──────────────────────────────────────────────────────────────────────
# setup-app-secrets.ps1
#
# PowerShell mirror of scripts/setup-app-secrets.sh.  Reads the
# gitignored `.env` at the repo root and pushes the values declared as
# `type: SECRET` in `.do/app.yaml` to the running App Platform app
# via `doctl apps update --env`.
#
# Usage (from PowerShell at the repo root):
#
#   .\scripts\setup-app-secrets.ps1                 # apply
#   .\scripts\setup-app-secrets.ps1 -DryRun         # print what would run
#   .\scripts\setup-app-secrets.ps1 -AppId <id>     # target a specific app
#
# Required:
#   - .env at the repo root with the keys listed in $RequiredSecretKeys
#     populated.
#   - doctl installed and authenticated.
#
# Idempotent.  Re-running it is safe and overwrites prior values.
# ──────────────────────────────────────────────────────────────────────

[CmdletBinding()]
param(
    [switch]$DryRun,
    [string]$AppId
)

$ErrorActionPreference = "Stop"

# ---- paths ---------------------------------------------------------
$ScriptDir   = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Resolve-Path (Join-Path $ScriptDir "..")
$EnvFile     = Join-Path $ProjectRoot ".env"
$SpecFile    = Join-Path $ProjectRoot ".do/app.yaml"

# ---- preflight -----------------------------------------------------
foreach ($bin in @("doctl")) {
    if (-not (Get-Command $bin -ErrorAction SilentlyContinue)) {
        Write-Error "$bin is required but not installed."
        exit 1
    }
}

if (-not (Test-Path $EnvFile)) {
    Write-Error "$EnvFile not found.  Copy the template first: cp .env.example .env"
    exit 1
}
if (-not (Test-Path $SpecFile)) {
    Write-Error "$SpecFile not found — are you in the repo root?"
    exit 1
}

# ---- resolve target app --------------------------------------------
if ([string]::IsNullOrEmpty($AppId)) {
    # Auto-detect: first app whose Spec.Name == "whatismytip"
    $match = doctl apps list --no-header --format ID,Spec.Name |
        Where-Object { $_ -match "^(?<id>\S+)\s+whatismytip\s*$" } |
        Select-Object -First 1
    if ($null -eq $match) {
        Write-Error "Could not auto-detect an app named 'whatismytip'.  Pass -AppId <id> explicitly."
        exit 1
    }
    $AppId = $Matches.id
}
Write-Host "→ Targeting app: $AppId"

# ---- load .env -----------------------------------------------------
$RequiredSecretKeys = @(
    "DATABASE_URL"
    "REDIS_URL"
    "ADMIN_API_KEY"
    "OPENROUTER_API_KEY"
)
$OptionalSecretKeys = @(
    "ALERT_WEBHOOK_URL"
)

# Parse .env into a hashtable.  Do NOT dot-source — .env may contain
# arbitrary (untrusted) shell that we don't want to evaluate.
$envMap = @{}
Get-Content $EnvFile | ForEach-Object {
    $line = $_.TrimEnd("`r")
    if ($line -match '^\s*#' -or [string]::IsNullOrWhiteSpace($line)) { return }
    if ($line -match '^(?<key>[A-Za-z_][A-Za-z0-9_]*)\s*=\s*(?<value>.*)$') {
        $k = $Matches.key
        $v = $Matches.value
        $envMap[$k] = $v
    }
}

# ---- validate ------------------------------------------------------
$missing = @()
foreach ($k in $RequiredSecretKeys) {
    if (-not $envMap.ContainsKey($k) -or [string]::IsNullOrEmpty($envMap[$k])) {
        $missing += $k
    }
}
if ($missing.Count -gt 0) {
    Write-Error ".env is missing required keys:`n  $($missing -join "`n  ")"
    exit 1
}

# ---- helpers -------------------------------------------------------
function Apply-Secret {
    param([string]$Key, [string]$Value)
    $masked = if ($Value.Length -ge 4) { $Value.Substring($Value.Length - 4) } else { "****" }

    if ($DryRun) {
        Write-Host "   doctl apps update $AppId --env $Key=****$masked"
    } else {
        $out = doctl apps update $AppId --env "${Key}=${Value}" 2>&1
        if ($LASTEXITCODE -ne 0) {
            Write-Error "   failed to set $Key : $out"
            return $false
        }
        Write-Host "   ✅ $Key (****$masked)"
    }
    return $true
}

# ---- apply ---------------------------------------------------------
Write-Host ""
if ($DryRun) {
    Write-Host "🔍 DRY RUN — would call the following (value redacted to last 4 chars):"
} else {
    Write-Host "🚀 Applying secrets to app $AppId…"
}

foreach ($k in $RequiredSecretKeys) {
    Apply-Secret -Key $k -Value $envMap[$k] | Out-Null
}
foreach ($k in $OptionalSecretKeys) {
    if ($envMap.ContainsKey($k) -and -not [string]::IsNullOrEmpty($envMap[$k])) {
        Apply-Secret -Key $k -Value $envMap[$k] | Out-Null
    }
}

Write-Host ""
if ($DryRun) {
    Write-Host "✅ Dry run complete.  Re-run without -DryRun to apply."
} else {
    Write-Host "✅ Secrets applied.  Trigger a redeploy to pick them up:"
    Write-Host "     doctl apps create-deployment $AppId --force-rebuild"
}