#!/bin/bash
# Iterate until validate_harness.py passes all checks.
# Run from the floop-bench project root.

set -euo pipefail
MAX_ITERS=25
ITER=0

while [ $ITER -lt $MAX_ITERS ]; do
    ITER=$((ITER + 1))
    echo ""
    echo "══════════════════════════════════════"
    echo "  Ralph iteration $ITER / $MAX_ITERS"
    echo "══════════════════════════════════════"

    if uv run python -m scripts.validate_harness 2>&1 | tee /tmp/floop_validation.log; then
        echo ""
        echo "Harness ready after $ITER iterations!"
        exit 0
    fi

    echo ""
    echo "Feeding errors to Claude Code for fixing..."
    claude -p "You are building the floop-bench benchmark harness.

The validation script failed. Here is the output:

---
$(cat /tmp/floop_validation.log)
---

Context: Read SPEC.md in this project root for the full specification.

Instructions:
1. Focus on the FIRST failing check only
2. Read the relevant source files to understand the current state
3. Fix the issue
4. Do NOT re-run the validation — I will do that on the next iteration

Be precise. Make the minimal change needed to fix the failing check."

    sleep 3
done

echo "Did not converge after $MAX_ITERS iterations."
exit 1
