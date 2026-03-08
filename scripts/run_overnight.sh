#!/bin/bash
# Run 11: Overnight A/B test — floop-in-container + 80 tasks
#
# First run using floop-the-binary inside Docker containers.
# 60s delay between tasks to avoid Gemini TPM rate limits.
#
# Usage: nohup bash scripts/run_overnight.sh > run11.log 2>&1 &

set -uo pipefail

# Load API keys
source .env && export GEMINI_API_KEY

echo "=== Run 11: Overnight A/B test ==="
echo "Started: $(date)"
echo "Floop version: $(floop version 2>&1 || echo 'not installed on host')"
echo "Tasks: $(python -c 'import json; print(len(json.load(open("config/splits.json"))["eval"]))') eval tasks"
echo ""

# Run both arms — continue to second arm even if first fails
# Bare arm (no behaviors, no floop)
echo "=== Bare arm ==="
uv run python -m scripts.run_mswea run --arm bare --delay 60 --workers 1 || echo "WARNING: bare arm exited non-zero"

# Floop arm (floop binary in container, behaviors from store)
echo ""
echo "=== Floop arm ==="
uv run python -m scripts.run_mswea run --arm floop --delay 60 --workers 1 || echo "WARNING: floop arm exited non-zero"

# Import results to DB + JSONL
echo ""
echo "=== Importing results ==="
uv run python -m scripts.run_mswea import-results --arm bare
uv run python -m scripts.run_mswea import-results --arm floop

# SWE-bench Docker evaluation
echo ""
echo "=== SWE-bench evaluation ==="
uv run python -m scripts.run_mswea evaluate --arm mswea_bare --max-workers 4
uv run python -m scripts.run_mswea evaluate --arm mswea_floop --max-workers 4

# Statistical analysis
echo ""
echo "=== Analysis ==="
uv run python -m analysis.analyze

echo ""
echo "=== Complete: $(date) ==="
