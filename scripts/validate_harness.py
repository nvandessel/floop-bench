"""
Progressive smoke test for floop-bench harness.

Exits 0 when the harness is ready. Each check builds on the previous.

Usage:
    uv run python -m scripts.validate_harness
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path


def check(name: str, fn) -> bool:
    """Run a check and print result."""
    try:
        ok, detail = fn()
        status = "PASS" if ok else "FAIL"
        if not ok:
            print(f"  {status}  {name}: {detail}")
        else:
            print(f"  {status}  {name}")
        return ok
    except Exception as e:
        print(f"  FAIL  {name}: {e}")
        return False


def c_deps():
    import scipy  # noqa: F401

    return True, ""


def c_swebench():
    try:
        import swebench  # noqa: F401

        return True, ""
    except ImportError:
        return False, "swebench not installed. Run: uv add swebench"


def c_docker():
    for cmd in ["docker", "podman"]:
        r = subprocess.run([cmd, "info"], capture_output=True)
        if r.returncode == 0:
            return True, f"using {cmd}"
    return False, "Neither docker nor podman available"


def c_dataset():
    from datasets import load_dataset

    ds = load_dataset("princeton-nlp/SWE-bench_Verified", split="test")
    return len(ds) >= 50, f"Got {len(ds)} tasks"


def c_split():
    p = Path("config/splits.json")
    if not p.exists():
        return False, "Run: uv run python -m scripts.generate_split"
    data = json.loads(p.read_text())
    train = data.get("train", [])
    eval_ = data.get("eval", [])
    train_len = len(train)
    eval_len = len(eval_)
    if train_len != 30 or eval_len != 20:
        return False, f"Expected 30/20, got {train_len}/{eval_len}"
    overlap = set(train) & set(eval_)
    if overlap:
        return False, f"Train/eval overlap: {overlap}"
    train_dupes = len(train) - len(set(train))
    eval_dupes = len(eval_) - len(set(eval_))
    if train_dupes or eval_dupes:
        return False, f"Duplicates: {train_dupes} in train, {eval_dupes} in eval"
    return True, ""


def c_claude_cli():
    r = subprocess.run(["claude", "--version"], capture_output=True)
    return r.returncode == 0, "claude CLI not found"


def c_claude_api():
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return False, "ANTHROPIC_API_KEY not set"
    r = subprocess.run(
        [
            "claude",
            "-p",
            "Say OK",
            "--output-format",
            "json",
            "--model",
            "claude-haiku-4-5-20251001",
            "--max-turns",
            "1",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    return r.returncode == 0 and len(r.stdout) > 0, "API call failed"


def c_db():
    from harness.db import init_db

    init_db()
    db = sqlite3.connect("results/results.db")
    db.execute("SELECT count(*) FROM runs")
    return True, ""


def c_single_task():
    """Run one training task end-to-end. Slow (~2 min) and costs ~$0.20."""
    r = subprocess.run(
        [
            "uv",
            "run",
            "python",
            "-m",
            "harness.orchestrator",
            "--phase",
            "smoke",
        ],
        capture_output=True,
        text=True,
        timeout=600,
    )
    if r.returncode != 0:
        return False, r.stderr[-500:] if r.stderr else "Unknown error"
    db = sqlite3.connect("results/results.db")
    count = db.execute("SELECT count(*) FROM runs").fetchone()[0]
    return count > 0, f"{count} run(s) in DB"


def c_swebench_eval():
    """Verify SWE-bench can evaluate a prediction."""
    pred_files = list(Path("results/predictions").glob("*.jsonl"))
    if not pred_files:
        return False, "No predictions yet — run smoke phase first"
    r = subprocess.run(
        [
            "python",
            "-m",
            "swebench.harness.run_evaluation",
            "--predictions_path",
            str(pred_files[0]),
            "--max_workers",
            "1",
            "--run_id",
            "validate_check",
        ],
        capture_output=True,
        text=True,
        timeout=600,
    )
    return r.returncode == 0, r.stderr[-500:] if r.stderr else ""


def c_floop():
    """Check floop CLI is available."""
    r = subprocess.run(
        ["floop", "--version"],
        capture_output=True,
        text=True,
        timeout=5,
    )
    return r.returncode == 0, "floop CLI not found"


def c_floop_store():
    """Check that floop store is initialized for floop-enabled arms."""
    from harness.config import load_arms

    arms = load_arms()
    for name, arm in arms.items():
        if arm.floop and arm.floop_store:
            store = Path(arm.floop_store)
            if not (store / ".floop").exists() and not (store / "floop.db").exists():
                return (
                    False,
                    f"Floop store not initialized for arm '{name}' at {store}. Run: floop init --root {store}",
                )
    return True, ""


def main():
    checks = [
        ("Python dependencies", c_deps),
        ("SWE-bench installed", c_swebench),
        ("Docker running", c_docker),
        ("Dataset loads", c_dataset),
        ("Train/eval split exists", c_split),
        ("Claude Code CLI available", c_claude_cli),
        ("SQLite DB works", c_db),
        ("Floop CLI available", c_floop),
        ("Floop store initialized", c_floop_store),
    ]

    passed = sum(check(name, fn) for name, fn in checks)
    total = len(checks)

    if passed == total:
        print(f"\nAll {total} checks passed! Harness is ready.")
        sys.exit(0)
    else:
        print(f"\n{passed}/{total} passed. Fix the first failing check and re-run.")
        sys.exit(1)


if __name__ == "__main__":
    main()
