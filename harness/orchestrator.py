"""
Orchestrator: main entry point for running floop-bench experiments.

Modes:
  --phase smoke     2 tasks, haiku_bare only (validate harness)
  --phase train     30 train tasks, haiku_bare only (generate training data)
  --phase eval      20 eval tasks x 3 arms (the actual experiment)

Features:
  - Resume: skips (instance_id, arm) pairs already in results.db
  - Shuffled queue: interleaves tasks and arms to avoid ordering bias
  - Live progress: prints running pass rate per arm
  - Cost guard: halts if cumulative spend exceeds --budget

Usage:
    uv run python -m harness.orchestrator --phase smoke
"""

from __future__ import annotations

import random
import sys
from pathlib import Path

import click
from datasets import load_dataset
from rich.console import Console
from rich.table import Table

from harness.config import ArmConfig, load_arms, load_split
from harness.db import (
    get_arm_stats,
    get_total_cost,
    init_db,
    load_completed,
    save_run,
)
from harness.parallel import run_parallel
from harness.runner import append_prediction, run_single_task

console = Console()

BASE_DIR = Path("work")
TRANSCRIPT_DIR = Path("results/transcripts")
PREDICTION_DIR = Path("results/predictions")


DATASET_NAME = "princeton-nlp/SWE-bench_Verified"


def load_dataset_lookup() -> dict[str, dict]:
    """Load SWE-bench Verified and index by instance_id."""
    ds = load_dataset(DATASET_NAME, split="test")
    return {item["instance_id"]: item for item in ds}


def build_queue(
    task_ids: list[str],
    active_arms: list[ArmConfig],
    completed: set[tuple[str, str]],
) -> list[tuple[str, ArmConfig]]:
    """Build queue of (instance_id, arm) pairs, skipping completed."""
    queue = []
    for tid in task_ids:
        for arm in active_arms:
            if (tid, arm.name) not in completed:
                queue.append((tid, arm))
    random.seed(42)
    random.shuffle(queue)
    return queue


def print_summary() -> None:
    """Print summary table of results per arm."""
    stats = get_arm_stats()
    if not stats:
        console.print("[yellow]No results yet.[/yellow]")
        return

    table = Table(title="Results Summary")
    table.add_column("Arm")
    table.add_column("Total", justify="right")
    table.add_column("Completed", justify="right")
    table.add_column("Resolved", justify="right")
    table.add_column("Rate", justify="right")
    table.add_column("Avg Duration", justify="right")
    table.add_column("Total Cost", justify="right")

    for s in stats:
        total = s["total"]
        resolved = s["resolved_count"] or 0
        completed = s["completed"] or 0
        rate = f"{resolved / total * 100:.1f}%" if total > 0 else "N/A"
        avg_dur = f"{s['avg_duration']:.1f}s" if s["avg_duration"] else "N/A"
        cost = f"${s['total_cost']:.2f}" if s["total_cost"] else "$0.00"

        table.add_row(
            s["arm"], str(total), str(completed), str(resolved),
            rate, avg_dur, cost,
        )

    console.print(table)
    console.print(f"Total spend: ${get_total_cost():.2f}")


@click.command()
@click.option(
    "--phase",
    type=click.Choice(["smoke", "train", "eval"]),
    required=True,
    help="Experiment phase to run",
)
@click.option("--budget", default=55.0, help="Maximum total cost in USD")
@click.option("--workers", default=1, help="Number of parallel workers")
@click.option("--timeout", default=300, help="Per-task timeout in seconds")
def main(phase: str, budget: float, workers: int, timeout: int):
    """Run floop-bench experiments."""
    init_db()

    console.print(f"[bold]Phase: {phase}[/bold]")
    console.print(f"Budget: ${budget:.2f} | Workers: {workers} | Timeout: {timeout}s")

    # Load config
    arms = load_arms()
    split = load_split()
    dataset = load_dataset_lookup()

    # Select tasks and arms for this phase
    if phase == "smoke":
        task_ids = split["train"][:2]
        active_arms = [arms["haiku_bare"]]
    elif phase == "train":
        task_ids = split["train"]
        active_arms = [arms["haiku_bare"]]
    elif phase == "eval":
        task_ids = split["eval"]
        active_arms = [arms["sonnet_bare"], arms["haiku_bare"], arms["haiku_floop"]]
    else:
        sys.exit(f"Unknown phase: {phase}")

    # Build queue
    completed = load_completed()
    queue = build_queue(task_ids, active_arms, completed)

    if not queue:
        console.print("[green]All tasks already completed![/green]")
        print_summary()
        return

    console.print(f"[cyan]{len(queue)} runs queued[/cyan]")

    # Resolve instance dicts
    instance_queue = []
    for tid, arm in queue:
        if tid not in dataset:
            console.print(f"[red]Warning: {tid} not found in dataset, skipping[/red]")
            continue
        instance_queue.append((dataset[tid], arm))

    def on_complete(result, idx, total):
        status_color = "green" if result.status == "completed" else "red"
        console.print(
            f"  [{idx + 1}/{total}] {result.instance_id} / {result.arm} "
            f"[{status_color}]{result.status}[/{status_color}] "
            f"{result.duration_seconds:.0f}s ${result.cost_usd:.3f}"
        )

    if workers > 1:
        results = run_parallel(
            instance_queue, BASE_DIR, TRANSCRIPT_DIR, PREDICTION_DIR,
            workers=workers, budget=budget, timeout=timeout,
            on_complete=on_complete,
        )
    else:
        # Sequential
        results = []
        for i, (instance, arm) in enumerate(instance_queue):
            spent = get_total_cost()
            if spent >= budget:
                console.print(
                    f"[yellow]Budget exhausted (${spent:.2f} >= ${budget}). Stopping.[/yellow]"
                )
                break

            result = run_single_task(
                instance, arm, BASE_DIR, TRANSCRIPT_DIR, timeout,
            )
            save_run(result)
            append_prediction(result, PREDICTION_DIR / f"{arm.name}.jsonl")
            results.append(result)
            on_complete(result, i, len(instance_queue))

    console.print()
    print_summary()


if __name__ == "__main__":
    main()
