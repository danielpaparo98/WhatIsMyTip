#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────
# scripts/make-data.sh — generate real AFL CSVs into ./data/
#
# Phase 7 wrapper around backend/scripts/scrape_to_csv.py.  Calls the
# scraper with sensible defaults and prints a summary table of files
# generated.
#
# Scope decision (2020 – 2025)
# ----------------------------
# 5 seasons was chosen as a compromise between:
#   * enough history for Elo / Form / HomeAdvantage / Value / Matchup
#     models to have stable predictions, and
#   * small enough that the full scrape fits in 10 – 30 minutes and
#     produces CSVs that are still easy to inspect / commit-as-sample.
#
# Bump the SEASONS env var to add more history:
#   SEASONS="2015 2016 2017 2018 2019 2020 2021 2022 2023 2024 2025" \
#     ./scripts/make-data.sh
#
# Or pass a single season:
#   SEASONS="2025" ./scripts/make-data.sh
#
# Windows / PowerShell users:  use scripts/make-data.ps1 instead.
# ──────────────────────────────────────────────────────────────────────

set -euo pipefail

# ---- locate script directory and project root ----------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

# ---- defaults --------------------------------------------------------
SEASONS="${SEASONS:-2020 2021 2022 2023 2024 2025}"
OUTPUT_DIR="${OUTPUT_DIR:-./data}"
LIMIT="${LIMIT:-0}"  # 0 = no limit; set to e.g. 3 for a quick smoke test

mkdir -p "$OUTPUT_DIR"

echo "=================================================================="
echo "WhatIsMyTip make-data"
echo "  seasons  = $SEASONS"
echo "  out dir  = $OUTPUT_DIR"
echo "  limit    = $LIMIT (per-season; 0 = all games)"
echo "=================================================================="
echo

# ---- run the scraper -------------------------------------------------
# `cd backend` so scrape_to_csv's relative sys.path tweaks work.
cd "$PROJECT_ROOT/backend"
uv run python scripts/scrape_to_csv.py \
    --season $SEASONS \
    --output-dir "../$OUTPUT_DIR" \
    --verbose
SCRAPE_EXIT=$?
cd "$PROJECT_ROOT"

# ---- summary ---------------------------------------------------------
echo
echo "=================================================================="
if [[ "$SCRAPE_EXIT" -ne 0 ]]; then
    echo "WARNING: scraper exited with code $SCRAPE_EXIT"
    echo "Some tables may be missing.  Check $OUTPUT_DIR/scrape.log."
fi
echo "Files in $OUTPUT_DIR:"
# Find CSV files in the data dir and its per-season subdirs
if command -v find >/dev/null 2>&1; then
    find "$OUTPUT_DIR" -maxdepth 2 -name "*.csv" -type f 2>/dev/null | sort | while read -r f; do
        rows=$(wc -l < "$f" 2>/dev/null || echo "?")
        # subtract 1 for the header line
        if [[ "$rows" =~ ^[0-9]+$ ]] && [[ "$rows" -gt 0 ]]; then
            rows=$((rows - 1))
        fi
        printf "  %-60s %5s rows\n" "${f#$PROJECT_ROOT/}" "$rows"
    done
fi
echo
echo "Next: bring up the stack — the init-data container will load"
echo "these CSVs into Postgres on first start."
echo "  ./scripts/dev.sh reset && ./scripts/dev.sh up --logs"
echo "=================================================================="

exit "$SCRAPE_EXIT"
