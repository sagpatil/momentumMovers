#!/usr/bin/env bash
# Run the full screener → dashboard flow locally (the CI flow minus commit/deploy).
#
#   ./scrape/run_local.sh          # pipeline → build → preview (prod bundle)
#   ./scrape/run_local.sh --dev    # pipeline → vite dev server (hot reload)
#   ./scrape/run_local.sh --no-fetch # skip the scrape, just (re)build the dashboard
#
# Secrets come from .env (ALPACA_* required; ANTHROPIC_API_KEY optional for the
# LLM catalyst fallback). No GitHub secrets needed locally.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

MODE="preview"
FETCH=1
for arg in "$@"; do
  case "$arg" in
    --dev) MODE="dev" ;;
    --no-fetch) FETCH=0 ;;
    *) echo "unknown flag: $arg" >&2; exit 1 ;;
  esac
done

# --- venv ---
if [[ -d .venv ]]; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
else
  echo "No .venv found. Create it: python3 -m venv .venv && pip install -r scrape/requirements-scrape.txt" >&2
  exit 1
fi

# --- 1. pipeline ---
if [[ "$FETCH" -eq 1 ]]; then
  echo "▶ Running screener pipeline (≈50-60s)…"
  python -m scrape.build_snapshot
else
  echo "▶ Skipping fetch (--no-fetch); using existing dashboard/public/data/latest.json"
fi

# --- 2. dashboard ---
cd dashboard
[[ -d node_modules ]] || { echo "▶ Installing dashboard deps…"; npm install; }

if [[ "$MODE" == "dev" ]]; then
  echo "▶ Starting Vite dev server (hot reload)…"
  exec npm run dev
else
  echo "▶ Building + serving production bundle…"
  npm run build
  exec npm run preview
fi
