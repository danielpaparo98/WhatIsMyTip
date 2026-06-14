# get-fn-urls.ps1
# Print the exact NUXT_PUBLIC_*_FN_URL values to paste into .do/app.yaml.
#
# Usage (in PowerShell):
#   1. doctl auth init              # paste your DO API token
#   2. doctl serverless connect     # if you haven't bound a namespace yet
#   3. doctl serverless deploy ..\backend --env .env   # deploy the functions
#   4. .\backend\scripts\get-fn-urls.ps1
#   5. Copy the printed block into .do/app.yaml under static_sites[0].envs:

$ErrorActionPreference = 'Stop'

if (-not (Get-Command doctl -ErrorAction SilentlyContinue)) {
    Write-Host "ERROR: doctl not found in PATH" -ForegroundColor Red
    exit 1
}

try {
    doctl account get | Out-Null
} catch {
    Write-Host "ERROR: doctl not authenticated. Run: doctl auth init" -ForegroundColor Red
    exit 1
}

$status = doctl serverless status 2>&1 | Out-String
$apiHost = ([regex]::Match($status, "API host:\s+(\S+)")).Groups[1].Value
$namespace = ([regex]::Match($status, "namespace\s+'([^']+)'")).Groups[1].Value

if (-not $apiHost -or -not $namespace) {
    Write-Host "ERROR: Could not parse API host / namespace from 'doctl serverless status'" -ForegroundColor Red
    Write-Host "Output was:" -ForegroundColor Yellow
    Write-Host $status
    Write-Host ""
    Write-Host "Hint: have you run 'doctl serverless connect' yet?" -ForegroundColor Yellow
    exit 1
}

Write-Host ""
Write-Host "Namespace: $namespace" -ForegroundColor Green
Write-Host "API host: $apiHost" -ForegroundColor Green
Write-Host ""

# Map DO function name -> our per-function env var name
$map = [ordered]@{
    'api/games'    = 'NUXT_PUBLIC_GAMES_FN_URL'
    'api/tips'     = 'NUXT_PUBLIC_TIPS_FN_URL'
    'api/backtest' = 'NUXT_PUBLIC_BACKTEST_FN_URL'
    'api/admin'    = 'NUXT_PUBLIC_ADMIN_FN_URL'
}

# Pull the live function list (one line per function: "name url")
$functionsRaw = doctl serverless functions list --format Name,URL --no-header 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: 'doctl serverless functions list' failed:" -ForegroundColor Red
    Write-Host $functionsRaw
    exit 1
}

# Build a lookup "api/games" -> URL
$urlByName = @{}
foreach ($line in ($functionsRaw -split "`n")) {
    $cols = ($line -replace '\s+', ' ').Trim() -split ' ', 2
    if ($cols.Count -lt 2) { continue }
    $urlByName[$cols[0]] = $cols[1]
}

Write-Host "=========================================================="
Write-Host "Paste into .do/app.yaml under static_sites[0].envs:" -ForegroundColor Cyan
Write-Host "=========================================================="
foreach ($key in $map.Keys) {
    $var = $map[$key]
    if ($urlByName.ContainsKey($key)) {
        $url = $urlByName[$key]
        Write-Host "      - key: $var"
        Write-Host "        value: $url"
    } else {
        Write-Host "      - key: $var" -ForegroundColor Yellow
        Write-Host "        value: $apiHost/$namespace/$key   # not yet deployed" -ForegroundColor Yellow
    }
    Write-Host ""
}
Write-Host "=========================================================="
