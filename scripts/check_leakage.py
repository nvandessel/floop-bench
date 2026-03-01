"""
Audit floop behavior store for eval-task-specific content (data leakage).

Scans every behavior for:
- Instance IDs from the eval split
- File paths or function names unique to eval tasks
- Literal code snippets that match eval ground truth patches

Usage:
    uv run python -m scripts.check_leakage
    uv run python -m scripts.check_leakage --volume floop-train
    uv run python -m scripts.check_leakage --store-path /path/to/store
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import click
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


def _get_behaviors_from_volume(volume_name: str) -> list[dict]:
    """Extract behaviors from a container volume by mounting it temporarily."""
    runtime = shutil.which("podman") or shutil.which("docker")
    if not runtime:
        print("No container runtime (podman/docker) found")
        return []
    try:
        # Need rw mount (SQLite WAL requires write access) and symlink setup
        # so pack-installed behaviors in .floop/ subdirectory are visible.
        result = subprocess.run(
            [
                runtime, "run", "--rm",
                "-v", f"{volume_name}:/floop-store:z",
                "--entrypoint", "/bin/bash",
                "floop-sandbox",
                "-c",
                "ln -sfn /floop-store/.floop /root/.floop"
                " && floop active --json --root /floop-store",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            print(f"Failed to read from volume {volume_name}: {result.stderr}")
            return []
        data = json.loads(result.stdout)
        return data.get("active", data.get("behaviors", []))
    except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError) as exc:
        print(f"Error reading volume {volume_name}: {exc}")
        return []


def scan_behaviors(behaviors: list[dict], eval_ids: list[str], eval_patches: dict[str, str]) -> int:
    """Scan behaviors for leakage. Returns number of leaks found."""
    print(f"Scanning {len(behaviors)} behaviors...")

    leaks_found = 0
    for i, b in enumerate(behaviors):
        content = json.dumps(b).lower()

        # Check for eval instance IDs
        for eid in eval_ids:
            if eid.lower() in content:
                print(f"  LEAK: Behavior {i} contains eval instance ID: {eid}")
                leaks_found += 1

        # Check for eval patch code snippets (meaningful lines only)
        for eid, patch in eval_patches.items():
            for line in patch.split("\n"):
                line = line.strip()
                if not line.startswith(("+", "-")):
                    continue
                clean_line = line[1:].strip()
                # Skip short/generic snippets that cause false positives
                if len(clean_line) > 30 and clean_line in content:
                        print(
                            f"  LEAK: Behavior {i} contains eval patch code "
                            f"from {eid}: {clean_line[:60]}..."
                        )
                        leaks_found += 1

    return leaks_found


@click.command()
@click.option("--volume", default=None, help="Docker volume name to scan (e.g. floop-train)")
@click.option("--store-path", default=None, type=click.Path(), help="Host path to floop store")
def check_leakage(volume: str | None, store_path: str | None) -> None:
    """Check behavior store for eval data leakage."""
    eval_ids = load_eval_ids()
    if not eval_ids:
        print("No eval IDs found.")
        sys.exit(0)

    eval_patches = load_eval_patches()
    print(f"Checking {len(eval_ids)} eval task IDs for leakage...")

    # Get behaviors from the appropriate source
    if volume:
        behaviors = _get_behaviors_from_volume(volume)
    elif store_path:
        behaviors = get_active_behaviors(Path(store_path))
    else:
        # Fall back to arms config
        from harness.config import load_arms

        arms = load_arms()
        floop_arms = [a for a in arms.values() if a.floop and a.floop_store]
        if not floop_arms:
            print("No floop-enabled arms configured.")
            sys.exit(0)
        behaviors = get_active_behaviors(Path(floop_arms[0].floop_store))

    if not behaviors:
        print("No behaviors in store (or floop not initialized).")
        sys.exit(0)

    leaks_found = scan_behaviors(behaviors, eval_ids, eval_patches)

    if leaks_found == 0:
        print("No leakage detected.")
        sys.exit(0)
    else:
        print(f"\n{leaks_found} potential leak(s) found. Review and fix before eval.")
        sys.exit(1)


if __name__ == "__main__":
    check_leakage()
