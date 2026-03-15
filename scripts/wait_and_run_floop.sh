#!/bin/bash
# Wait for Gemini TPM quota to recover, then run the floop arm.
# Usage: bash scripts/wait_and_run_floop.sh
set -euo pipefail

cd "$(dirname "$0")/.."
# Load API keys — see .env.example for format
source .env 2>/dev/null
export GEMINI_API_KEY

echo "Waiting for Gemini TPM quota to recover..."
echo "Will test every 5 minutes with a large prompt."
echo "Started at: $(date)"

ATTEMPT=0
while true; do
    ATTEMPT=$((ATTEMPT + 1))

    # Test with a prompt size similar to mini-SWE-agent (~3000 tokens)
    RESULT=$(uv run python -c "
import litellm
system_msg = 'You are a helpful assistant. ' * 200
user_msg = 'Consider this bug report: ' + 'Bug in validation logic. ' * 100 + '\nWhat file should I check?'
try:
    resp = litellm.completion(
        model='gemini/gemini-2.5-flash',
        messages=[{'role':'system','content':system_msg}, {'role':'user','content':user_msg}],
        temperature=0, drop_params=True
    )
    print('OK')
except Exception:
    print('RATE_LIMITED')
" 2>/dev/null)

    echo "[$(date '+%H:%M:%S')] Attempt $ATTEMPT: $RESULT"

    if [ "$RESULT" = "OK" ]; then
        echo ""
        echo "Quota recovered! Starting floop arm..."
        echo "Started at: $(date)"

        # Clean any prior floop output
        rm -rf results/mswea/floop
        docker ps --filter "name=minisweagent" -q | xargs -r docker stop 2>/dev/null || true

        # Run floop arm with 1 worker to stay under rate limits
        uv run python -m scripts.run_mswea run --arm floop --workers 1

        echo ""
        echo "Floop arm complete at: $(date)"
        echo "Next steps:"
        echo "  uv run python -m scripts.run_mswea import-results --arm floop"
        echo "  uv run python -m scripts.run_mswea evaluate --arm mswea_floop --max-workers 4"
        exit 0
    fi

    # Wait 5 minutes between checks
    sleep 300
done
