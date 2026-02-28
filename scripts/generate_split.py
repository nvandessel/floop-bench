"""
Generate train/eval split from SWE-bench Verified.

Samples 50 tasks from the full dataset, stratifies by repo, and splits into:
- 30 train tasks (for floop behavior training)
- 20 eval tasks (for the benchmark)

Output: config/splits.json

Usage:
    uv run python -m scripts.generate_split
"""

from __future__ import annotations

import json
import random
from collections import defaultdict
from pathlib import Path

from datasets import load_dataset

SEED = 42
SAMPLE_SIZE = 50
TRAIN_SIZE = 30
EVAL_SIZE = 20
DATASET_NAME = "princeton-nlp/SWE-bench_Verified"
OUTPUT_PATH = Path("config/splits.json")


def generate_split():
    """Generate stratified train/eval split."""
    print(f"Loading {DATASET_NAME} dataset...")
    ds = load_dataset(DATASET_NAME, split="test")
    print(f"Loaded {len(ds)} tasks")

    # Sample 50 tasks from the full 500
    rng = random.Random(SEED)
    indices = list(range(len(ds)))
    rng.shuffle(indices)
    sampled_indices = sorted(indices[:SAMPLE_SIZE])
    ds = ds.select(sampled_indices)
    print(f"Sampled {len(ds)} tasks")

    if len(ds) < TRAIN_SIZE + EVAL_SIZE:
        print(
            f"Warning: dataset has {len(ds)} tasks, "
            f"expected {TRAIN_SIZE + EVAL_SIZE}"
        )

    # Group tasks by repo for stratification
    by_repo: dict[str, list[str]] = defaultdict(list)
    for item in ds:
        repo = item["repo"]
        instance_id = item["instance_id"]
        by_repo[repo].append(instance_id)

    print(f"Tasks span {len(by_repo)} repos:")
    for repo, ids in sorted(by_repo.items(), key=lambda x: -len(x[1])):
        print(f"  {repo}: {len(ids)} tasks")

    # Stratified split: for each repo, split proportionally (60/40)
    rng = random.Random(SEED)
    train_ids: list[str] = []
    eval_ids: list[str] = []

    for repo in sorted(by_repo.keys()):
        ids = sorted(by_repo[repo])
        rng.shuffle(ids)

        n = len(ids)
        if n == 1:
            train_ids.append(ids[0])
        else:
            n_train = max(1, round(n * TRAIN_SIZE / (TRAIN_SIZE + EVAL_SIZE)))
            n_train = min(n_train, n - 1)
            train_ids.extend(ids[:n_train])
            eval_ids.extend(ids[n_train:])

    # Rebalance to exact sizes
    rng.shuffle(train_ids)
    rng.shuffle(eval_ids)

    while len(train_ids) > TRAIN_SIZE and len(eval_ids) < EVAL_SIZE:
        eval_ids.append(train_ids.pop())
    while len(eval_ids) > EVAL_SIZE and len(train_ids) < TRAIN_SIZE:
        train_ids.append(eval_ids.pop())

    train_ids.sort()
    eval_ids.sort()

    print(f"\nSplit: {len(train_ids)} train / {len(eval_ids)} eval")

    overlap = set(train_ids) & set(eval_ids)
    assert not overlap, f"Split overlap: {overlap}"

    split_data = {
        "seed": SEED,
        "dataset": DATASET_NAME,
        "train": train_ids,
        "eval": eval_ids,
    }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(split_data, indent=2) + "\n")
    print(f"Written to {OUTPUT_PATH}")


if __name__ == "__main__":
    generate_split()
