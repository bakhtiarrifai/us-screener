#!/usr/bin/env bash
# Daily wrapper for the US Stock Screener.
# Run by systemd timer / cron every weekday at 09:00 America/New_York
# (30 minutes before the US market opens at 09:30 ET).
#
# Example crontab (host timezone set to America/New_York):
#   0 9 * * 1-5 /home/bakhtiarrifai/projects/us-screener/run_screener.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Run everything in US Eastern Time so scheduling matches market hours
export TZ="${TZ:-America/New_York}"

# Prefer virtualenv python if available
if [ -f ".venv/bin/python" ]; then
    PYTHON=".venv/bin/python"
elif [ -f ".venv/Scripts/python.exe" ]; then
    PYTHON=".venv/Scripts/python.exe"
else
    PYTHON="python3"
fi

# Optional: ping Healthchecks.io start
if [ -n "${HEALTHCHECKS_URL:-}" ]; then
    curl -fsS -m 10 --retry 3 "${HEALTHCHECKS_URL}/start" >/dev/null 2>&1 || true
fi

# Load .env so run.py's fallback check sees credentials (it also loads it)
set -a
# shellcheck disable=SC1091
[ -f .env ] && . ./.env
set +a

# Run screener; single-line JSON is printed as the last stdout line
JSON_RESULT=$("$PYTHON" run.py 2>/dev/null | tail -1)

# Basic JSON validation
if ! echo "$JSON_RESULT" | python3 -c "import json,sys; json.load(sys.stdin)" >/dev/null 2>&1; then
    echo "Failed to produce valid JSON output from screener." >&2
    if [ -n "${HEALTHCHECKS_URL:-}" ]; then
        curl -fsS -m 10 --retry 3 "${HEALTHCHECKS_URL}/fail" >/dev/null 2>&1 || true
    fi
    exit 1
fi

# Ping success to Healthchecks.io
if [ -n "${HEALTHCHECKS_URL:-}" ]; then
    curl -fsS -m 10 --retry 3 "$HEALTHCHECKS_URL" >/dev/null 2>&1 || true
fi

echo "$JSON_RESULT"
