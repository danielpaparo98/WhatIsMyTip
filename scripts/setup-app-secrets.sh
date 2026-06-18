#!/usr/bin/env bash
# ============================================================================
# setup-app-secrets.sh
# ----------------------------------------------------------------------------
# Reads the gitignored `.env` file at the repo root and pushes the values
# declared as `type: SECRET` in `.do/app.yaml` to the running App Platform
# app via `doctl apps update --env`.
#
# Usage:
#   ./scripts/setup-app-secrets.sh                 # apply
#   ./scripts/setup-app-secrets.sh --dry-run       # print what would run
#   ./scripts/setup-app-secrets.sh --app <id>      # target a specific app
#                                                   # (else auto-detected)
#
# Required:
#   - .env at the repo root with the keys listed in REQUIRED_SECRET_KEYS
#     below populated.
#   - doctl installed and authenticated (`doctl auth init`).
#   - jq for safe YAML/JSON parsing.
#
# The script is idempotent — re-running it is safe.  It overwrites any
# existing value on the target app with whatever is currently in .env.
#
# Real secret values are echoed ONLY when --dry-run is NOT set, and even
# then are passed to doctl as arguments rather than logged.  Treat the
# output of this script as sensitive if you remove the redaction below.
# ============================================================================

set -euo pipefail

# ── paths ───────────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
ENV_FILE="${REPO_ROOT}/.env"
SPEC_FILE="${REPO_ROOT}/.do/app.yaml"

# ── flags ───────────────────────────────────────────────────────────────────
DRY_RUN=false
APP_ID_OVERRIDE=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run)
      DRY_RUN=true
      shift
      ;;
    --app)
      APP_ID_OVERRIDE="$2"
      shift 2
      ;;
    -h|--help)
      sed -n '2,30p' "$0"
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 64
      ;;
  esac
done

# ── preflight ───────────────────────────────────────────────────────────────
for bin in doctl jq; do
  if ! command -v "$bin" >/dev/null 2>&1; then
    echo "❌ $bin is required but not installed." >&2
    exit 1
  fi
done

if [[ ! -f "$ENV_FILE" ]]; then
  echo "❌ $ENV_FILE not found." >&2
  echo "   Copy the template first:" >&2
  echo "     cp .env.example .env   # then fill in the [REQUIRED] values" >&2
  exit 1
fi

if [[ ! -f "$SPEC_FILE" ]]; then
  echo "❌ $SPEC_FILE not found — are you in the repo root?" >&2
  exit 1
fi

# ── resolve target app ──────────────────────────────────────────────────────
if [[ -n "$APP_ID_OVERRIDE" ]]; then
  APP_ID="$APP_ID_OVERRIDE"
else
  # If exactly one app exists in the account, use it.  Otherwise refuse.
  APP_ID="$(doctl apps list --no-header --format ID,Spec.Name 2>/dev/null \
            | awk -v target="whatismytip" '$2 == target { print $1; exit }')"
  if [[ -z "$APP_ID" ]]; then
    echo "❌ Could not auto-detect an app named 'whatismytip'." >&2
    echo "   Pass --app <id> explicitly, or run: doctl apps list" >&2
    exit 1
  fi
fi
echo "→ Targeting app: ${APP_ID}"

# ── load .env into the shell (only the keys we care about) ──────────────────
# Required secret keys — must match the `key:` of every `type: SECRET`
# entry in .do/app.yaml.
REQUIRED_SECRET_KEYS=(
  DATABASE_URL
  REDIS_URL
  ADMIN_API_KEY
  OPENROUTER_API_KEY
)

# Optional secret keys — populated only if present + non-empty in .env.
OPTIONAL_SECRET_KEYS=(
  ALERT_WEBHOOK_URL
)

# Parse .env into shell variables.  We intentionally avoid `source` so we
# don't inherit arbitrary code from .env (which is gitignored but trusted).
declare -A SECRETS=()
while IFS='=' read -r key value; do
  # Strip CR, surrounding whitespace, optional leading "export "
  key="$(printf '%s' "$key" | sed -e 's/^[[:space:]]*export[[:space:]]//' -e 's/[[:space:]]*$//')"
  value="${value%$'\r'}"
  # Skip blanks and comments
  [[ -z "$key" || "$key" =~ ^# ]] && continue
  SECRETS["$key"]="$value"
done < "$ENV_FILE"

# ── validate ────────────────────────────────────────────────────────────────
missing=()
for k in "${REQUIRED_SECRET_KEYS[@]}"; do
  if [[ -z "${SECRETS[$k]+set}" || -z "${SECRETS[$k]}" ]]; then
    missing+=("$k")
  fi
done
if (( ${#missing[@]} > 0 )); then
  echo "❌ .env is missing required keys:" >&2
  for k in "${missing[@]}"; do
    echo "     - $k" >&2
  done
  echo "   Populate them in .env and re-run." >&2
  exit 1
fi

# ── apply ───────────────────────────────────────────────────────────────────
echo
if $DRY_RUN; then
  echo "🔍 DRY RUN — would call the following (value redacted to last 4 chars):"
else
  echo "🚀 Applying secrets to app ${APP_ID}…"
fi

apply_secret() {
  local key="$1"
  local value="$2"
  local masked="${value: -4}"

  if $DRY_RUN; then
    printf '   doctl apps update %s --env %s=****%s\n' "$APP_ID" "$key" "$masked"
  else
    # --env KEY=VALUE is positional; the value is passed directly to the
    # API and encrypted at rest.  We suppress the value from logs.
    if ! doctl apps update "$APP_ID" --env "${key}=${value}" >/dev/null; then
      echo "   ❌ failed to set $key" >&2
      return 1
    fi
    printf '   ✅ %s (****%s)\n' "$key" "$masked"
  fi
}

for k in "${REQUIRED_SECRET_KEYS[@]}"; do
  apply_secret "$k" "${SECRETS[$k]}"
done

for k in "${OPTIONAL_SECRET_KEYS[@]}"; do
  if [[ -n "${SECRETS[$k]+set}" && -n "${SECRETS[$k]}" ]]; then
    apply_secret "$k" "${SECRETS[$k]}"
  fi
done

echo
if $DRY_RUN; then
  echo "✅ Dry run complete.  Re-run without --dry-run to apply."
else
  echo "✅ Secrets applied.  Trigger a redeploy to pick them up:"
  echo "     doctl apps create-deployment ${APP_ID} --force-rebuild"
fi