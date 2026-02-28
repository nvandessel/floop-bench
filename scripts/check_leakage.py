"""
Audit floop behavior store for eval-task-specific content (data leakage).

Scans every behavior for:
- Instance IDs from the eval split
- File paths or function names unique to eval tasks
- Literal code snippets that match eval ground truth patches

Usage:
    uv run python -m scripts.check_leakage
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from datasets import load_dataset

from floop_integration.cli import get_active_behaviors


def load_eval_ids() -> list[str]:
    """Load eval task IDs from splits.json."""
    split_path = Path("config/splits.json")
    if not split_path.exists():
        print("No splits.json found — generate split first")
        return []
    data = json.loads(split_path.read_text())
    return data.get("eval", [])


def load_eval_patches() -> dict[str, str]:
    """Load ground truth patches for eval tasks."""
    eval_ids = set(load_eval_ids())
    if not eval_ids:
        return {}

    ds = load_dataset("princeton-nlp/SWE-bench_Verified", split="test")
    patches = {}
    for item in ds:
        if item["instance_id"] in eval_ids:
            patches[item["instance_id"]] = item.get("patch", "")
    return patches


def check_leakage():
    """Check behavior store for eval data leakage."""
    eval_ids = load_eval_ids()
    if not eval_ids:
        print("No eval IDs found.")
        return

    eval_patches = load_eval_patches()
    print(f"Checking {len(eval_ids)} eval task IDs for leakage...")

    store_path = Path("behaviors/store")
    behaviors = get_active_behaviors(store_path)

    if not behaviors:
        print("No behaviors in store (or floop not initialized).")
        return

    print(f"Scanning {len(behaviors)} behaviors...")

    leaks_found = 0
    for i, b in enumerate(behaviors):
        content = json.dumps(b).lower()

        # Check for eval instance IDs
        for eid in eval_ids:
            if eid.lower() in content:
                print(f"  LEAK: Behavior {i} contains eval instance ID: {eid}")
                leaks_found += 1

        # Check for eval patch code snippets (lines > 20 chars)
        for eid, patch in eval_patches.items():
            for line in patch.split("\n"):
                line = line.strip()
                if len(line) > 20 and line.startswith(("+", "-")):
                    clean_line = line[1:].strip()
                    if clean_line and clean_line in content:
                        print(
                            f"  LEAK: Behavior {i} contains eval patch code "
                            f"from {eid}: {clean_line[:60]}..."
                        )
                        leaks_found += 1

    if leaks_found == 0:
        print("No leakage detected.")
    else:
        print(f"\n{leaks_found} potential leak(s) found. Review and fix before eval.")


if __name__ == "__main__":
    check_leakage()
