# ──────────────────────────────────────────────────────────────────────
# scripts/make-data.ps1 — generate real AFL CSVs into .\data\
#
# PowerShell equivalent of scripts/make-data.sh.  See the bash script
# for the full scope decision; the two stay in lockstep.
#
# Usage (from repo root):
#   .\scripts\make-data.ps1                       # default: 2020-2025
#   .\scripts\make-data.ps1 -Seasons 2024,2025    # explicit
#   .\scripts\make-data.ps1 -OutputDir .\my_data  # custom output
#   .\scripts\make-data.ps1 -Limit 3              # smoke test (3 games/season)
#
# Override via env vars too:
#   $env:SEASONS = "2024 2025"; .\scripts\make-data.ps1
# ──────────────────────────────────────────────────────────────────────

[CmdletBinding()]
param(
    [string]$Seasons = $env:SEASONS,
    [string]$OutputDir = $(if ($env:OUTPUT_DIR) { $env:OUTPUT_DIR } else { ".\data" }),
    [int]$Limit = $(if ($env:LIMIT) { [int]$env:LIMIT } else { 0 })
)

$ErrorActionPreference = "Stop"

# ---- defaults --------------------------------------------------------
if (-not $Seasons) {
    $Seasons = "2020 2021 2022 2023 2024 2025"
}

# ---- locate script directory and project root ----------------------
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Resolve-Path (Join-Path $ScriptDir "..")
Push-Location $ProjectRoot
try {
    New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null

    Write-Host "=================================================================="
    Write-Host "WhatIsMyTip make-data"
    Write-Host "  seasons   = $Seasons"
    Write-Host "  out dir   = $OutputDir"
    Write-Host "  limit     = $Limit (per-season; 0 = all games)"
    Write-Host "=================================================================="
    Write-Host ""

    # ---- run the scraper ---------------------------------------------
    Push-Location (Join-Path $ProjectRoot "backend")
    try {
        # Convert the single-space-separated string to a real argument array
        $SeasonArgs = $Seasons -split "\s+" | Where-Object { $_ }
        & uv run python scripts/scrape_to_csv.py `
            --season @SeasonArgs `
            --output-dir (Join-Path ".." $OutputDir) `
            --verbose
        $ScrapeExit = $LASTEXITCODE
    } finally {
        Pop-Location
    }

    # ---- summary -----------------------------------------------------
    Write-Host ""
    Write-Host "=================================================================="
    if ($ScrapeExit -ne 0) {
        Write-Host "WARNING: scraper exited with code $ScrapeExit"
        Write-Host "Some tables may be missing.  Check $OutputDir\scrape.log."
    }
    Write-Host "Files in $OutputDir :"
    Get-ChildItem -Path $OutputDir -Recurse -Filter "*.csv" -ErrorAction SilentlyContinue |
        ForEach-Object {
            $lineCount = (Get-Content $_.FullName -ErrorAction SilentlyContinue | Measure-Object -Line).Lines
            $rows = if ($lineCount -gt 0) { $lineCount - 1 } else { 0 }
            $relPath = $_.FullName.Substring($ProjectRoot.Path.Length + 1)
            Write-Host ("  {0,-60} {1,5} rows" -f $relPath, $rows)
        } | Sort-Object
    Write-Host ""
    Write-Host "Next: bring up the stack — the init-data container will load"
    Write-Host "these CSVs into Postgres on first start."
    Write-Host "  .\scripts\dev.ps1 reset"
    Write-Host "  .\scripts\dev.ps1 up -Up -Logs"
    Write-Host "=================================================================="

    exit $ScrapeExit
} finally {
    Pop-Location
}
