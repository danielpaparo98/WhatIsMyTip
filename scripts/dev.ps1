# ──────────────────────────────────────────────────────────────────────
# WhatIsMyTip local development helper (PowerShell).
#
# Windows users: invoke this script from PowerShell (pwsh) at the
# project root:
#
#   .\scripts\dev.ps1 up
#   .\scripts\dev.ps1 logs api
#   .\scripts\dev.ps1 reset
#
# Subcommands (parity with scripts/dev.sh):
#   up [--logs]    Start the stack (default detached).  --logs follows.
#   down           Stop the stack (volumes preserved).
#   reset          Stop the stack AND delete the named volumes.
#   logs <svc...>  Tail logs for the given service(s).
#   ps             Show running containers.
#   shell [svc]    Open a shell in the given service (default: api).
#   psql           Open a psql shell against the dev database.
#   redis          Open a redis-cli shell against the dev cache.
#   config         Validate docker-compose.yml and print the resolved config.
#   help           Print this help text.
#
# Container runtime
# -----------------
#   Auto-detects docker first, then podman.  Override with
#   $env:WIMT_RUNTIME = "docker" (or "podman") if you have both.
# ──────────────────────────────────────────────────────────────────────

[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [string]$Command = "help"
)

$ErrorActionPreference = "Stop"

# ---- resolve paths -------------------------------------------------
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Resolve-Path (Join-Path $ScriptDir "..")
Set-Location $ProjectRoot

# ---- colour helpers ------------------------------------------------
function Write-Banner { param([string]$Message) Write-Host $Message -ForegroundColor Cyan }
function Write-Info   { param([string]$Message) Write-Host $Message -ForegroundColor Yellow }
function Write-Ok     { param([string]$Message) Write-Host $Message -ForegroundColor Green }
function Write-Err    { param([string]$Message) Write-Host $Message -ForegroundColor Red }

# ---- runtime detection ---------------------------------------------
function Resolve-Runtime {
    if ($env:WIMT_RUNTIME) { return $env:WIMT_RUNTIME }
    $docker = Get-Command docker -ErrorAction SilentlyContinue
    if ($docker) {
        try { docker info | Out-Null; return "docker" } catch { }
    }
    $podman = Get-Command podman -ErrorAction SilentlyContinue
    if ($podman) {
        try { podman info | Out-Null; return "podman" } catch { }
    }
    return $null
}

$Runtime = Resolve-Runtime
if (-not $Runtime) {
    Write-Err "Error: no running container runtime found."
    Write-Host "Install Docker Desktop or Podman, then start the daemon."
    Write-Host "Tip: set `$env:WIMT_RUNTIME = 'docker' (or 'podman') to override."
    exit 1
}

# ---- compose command ------------------------------------------------
# Compose command is represented as a single string (the executable)
# plus an optional list of extra args.  Keeping it simple avoids
# PowerShell's array-splatting quirks with single-element arrays.
function Resolve-ComposeCommand {
    if ($Runtime -eq "docker") {
        return @{ Cmd = "docker"; Extra = @("compose") }
    }
    # Prefer the real ``podman-compose`` (Python) tool over ``podman
    # compose`` because, on Windows, the latter is a thin wrapper that
    # falls back to ``docker-compose.exe`` and ends up driving the
    # Docker engine instead of Podman.  Using podman-compose directly
    # keeps the whole stack on the chosen runtime.
    $podmanCompose = Get-Command "podman-compose" -ErrorAction SilentlyContinue
    if ($podmanCompose) { return @{ Cmd = "podman-compose"; Extra = @() } }
    # podman compose plugin (wrapper that may delegate to docker-compose)
    try { podman help compose | Out-Null; return @{ Cmd = "podman"; Extra = @("compose") } } catch { }
    Write-Err "Error: podman found but no compose provider is available."
    Write-Host "Install with: uv tool install podman-compose  (or 'pipx install podman-compose')"
    exit 1
}

$Compose = Resolve-ComposeCommand

# Helper: invoke the resolved compose command with extra args.
function Invoke-Compose {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Args)
    $cmd = $Compose.Cmd
    $extra = $Compose.Extra
    if ($extra) {
        & $cmd @extra @Args
    } else {
        & $cmd @Args
    }
}

# ---- usage ---------------------------------------------------------
function Show-Usage {
    @"
Usage: .\scripts\dev.ps1 <command> [args...]

Commands:
  up [--logs]    Start the stack (default: detached).  --logs follows logs.
  down           Stop the stack (volumes preserved).
  reset          Stop the stack AND delete the named volumes.
  logs <svc...>  Tail logs for the given service(s).
  ps             Show running containers.
  shell [svc]    Open a shell in the given service (default: api).
  psql           Open a psql shell against the dev database.
  redis          Open a redis-cli shell against the dev cache.
  config         Validate docker-compose.yml and print the resolved config.

Environment:
  WIMT_RUNTIME   Force runtime to 'docker' or 'podman'.
"@
}

# ---- subcommands ---------------------------------------------------
function Invoke-Up {
    param([string[]]$ExtraArgs)

    $composeDesc = if ($Compose.Extra.Count -gt 0) { $Compose.Cmd + " " + ($Compose.Extra -join " ") } else { $Compose.Cmd }
    Write-Banner "WhatIsMyTip dev  ·  runtime=$Runtime  ·  compose=$composeDesc"
    Write-Info "Starting stack..."

    Invoke-Compose up -d --build
    if ($LASTEXITCODE -ne 0) { throw "compose up failed." }

    Write-Ok "Stack up."
    Write-Host "  API:       http://localhost:8000  (Swagger UI at /docs)"
    Write-Host "  Frontend:  http://localhost:3000"
    Write-Host "  Postgres:  localhost:5432  (user: wimt, db: whatismytip, pw: wimt_dev_password)"
    Write-Host "  Redis:     localhost:6379"
    Write-Host ""

    if ($ExtraArgs -contains "--logs") {
        Invoke-Compose logs -f --tail=100
    }
}

function Invoke-Down {
    Write-Banner "WhatIsMyTip dev  ·  runtime=$Runtime"
    Invoke-Compose down
    Write-Ok "Stack stopped (volumes preserved)."
}

function Invoke-Reset {
    Write-Banner "WhatIsMyTip dev  ·  runtime=$Runtime"
    Write-Info "This will DELETE the database volume."
    $ans = Read-Host "Continue? [y/N]"
    if ($ans -notin @("y", "Y", "yes", "YES")) {
        Write-Host "Aborted."
        return
    }
    Invoke-Compose down -v
    Write-Ok "Stack stopped and volumes deleted."
}

function Invoke-Logs {
    param([string[]]$Services)
    Write-Banner "WhatIsMyTip dev  ·  runtime=$Runtime"
    if (-not $Services -or $Services.Count -eq 0) {
        Invoke-Compose logs -f --tail=100
    } else {
        Invoke-Compose logs -f --tail=100 @Services
    }
}

function Invoke-Ps {
    Write-Banner "WhatIsMyTip dev  ·  runtime=$Runtime"
    Invoke-Compose ps
}

function Invoke-Shell {
    param([string]$Service = "api")
    Write-Banner "WhatIsMyTip dev  ·  runtime=$Runtime"
    Invoke-Compose exec $Service /bin/bash
}

function Invoke-Psql {
    Write-Banner "WhatIsMyTip dev  ·  runtime=$Runtime"
    Invoke-Compose exec postgres psql -U wimt -d whatismytip
}

function Invoke-Redis {
    Write-Banner "WhatIsMyTip dev  ·  runtime=$Runtime"
    Invoke-Compose exec redis redis-cli
}

function Invoke-Config {
    Write-Banner "WhatIsMyTip dev  ·  runtime=$Runtime"
    Invoke-Compose config
}

# ---- main ----------------------------------------------------------
switch ($Command.ToLower()) {
    "up"        { Invoke-Up -ExtraArgs $args }
    "down"      { Invoke-Down }
    "reset"     { Invoke-Reset }
    "logs"      { Invoke-Logs -Services $args }
    "ps"        { Invoke-Ps }
    { @("shell", "sh") -contains $_ } { Invoke-Shell -Service $(if ($args) { $args[0] } else { "api" }) }
    "psql"      { Invoke-Psql }
    "redis"     { Invoke-Redis }
    { @("config", "validate") -contains $_ } { Invoke-Config }
    { @("-h", "--help", "help", "") -contains $_ } { Show-Usage }
    default {
        Write-Err "Unknown command: $Command"
        Show-Usage
        exit 1
    }
}
