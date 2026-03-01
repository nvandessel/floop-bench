"""
Orchestrator: main entry point for running floop-bench experiments.

Modes:
  --phase smoke     2 tasks, 1 arm (validate harness)
  --phase train     30 train tasks, floor arm only (generate training data)
  --phase eval      20 eval tasks x 3 arms (the actual experiment)

Features:
  - Resume: skips (instance_id, arm) pairs already in results.db
  - Shuffled queue: interleaves tasks and arms to avoid ordering bias
  - Live progress: prints running pass rate per arm
  - Cost guard: halts if cumulative spend exceeds --budget
  - Docker sandbox: runs agents in isolated containers (default ON)

Usage:
    uv run python -m harness.orchestrator --phase smoke
    uv run python -m harness.orchestrator --phase smoke --arm gemini_flash_bare
    uv run python -m harness.orchestrator --phase smoke --no-sandbox
"""

from __future__ import annotations

import random
import subprocess
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
from harness.runner import (
    SANDBOX_IMAGE,
    SandboxConfig,
    append_prediction,
    find_container_runtime,
    run_single_task,
)

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


# ---------------------------------------------------------------------------
# Docker helpers
# ---------------------------------------------------------------------------

def _image_exists(runtime: str, image: str = SANDBOX_IMAGE) -> bool:
    """Check if the sandbox image exists locally."""
    try:
        result = subprocess.run(
            [runtime, "image", "inspect", image],
            capture_output=True,
            timeout=10,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _build_image(runtime: str, image: str = SANDBOX_IMAGE) -> bool:
    """Build the sandbox image from the project Dockerfile."""
    console.print(f"[cyan]Building sandbox image '{image}' ({runtime})...[/cyan]")
    try:
        result = subprocess.run(
            [runtime, "build", "-t", image, "."],
            timeout=600,
        )
        if result.returncode == 0:
            console.print(f"[green]Image '{image}' built successfully.[/green]")
            return True
        else:
            console.print(f"[red]Failed to build image '{image}'.[/red]")
            return False
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        console.print(f"[red]Failed to build image: {exc}[/red]")
        return False


def _ensure_volume(runtime: str, name: str) -> bool:
    """Create a container volume if it doesn't exist."""
    try:
        result = subprocess.run(
            [runtime, "volume", "create", name],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _init_floop_in_volume(runtime: str, volume_name: str, image: str = SANDBOX_IMAGE) -> bool:
    """Run `floop init` inside a temporary container with the volume mounted."""
    try:
        result = subprocess.run(
            [
                runtime, "run", "--rm",
                "-v", f"{volume_name}:/floop-store",
                image,
                "floop", "init", "--root", "/floop-store",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _run_leakage_audit(volume_name: str) -> bool:
    """Run leakage audit against a Docker volume. Returns True if clean."""
    console.print(f"[cyan]Running leakage audit on volume '{volume_name}'...[/cyan]")
    try:
        result = subprocess.run(
            [
                sys.executable, "-m", "scripts.check_leakage",
                "--volume", volume_name,
            ],
            timeout=120,
        )
        if result.returncode == 0:
            console.print("[green]Leakage audit passed.[/green]")
            return True
        else:
            console.print("[red]Leakage audit FAILED. Eval blocked.[/red]")
            return False
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        console.print(f"[red]Leakage audit error: {exc}[/red]")
        return False


def _setup_sandbox(
    phase: str,
    active_arms: list[ArmConfig],
    no_sandbox: bool,
) -> SandboxConfig | None:
    """
    Set up Docker sandbox configuration.

    Returns SandboxConfig if sandbox is enabled, None otherwise.
    Handles Docker availability check, image build, and volume lifecycle.
    """
    if no_sandbox:
        console.print("[yellow]Sandbox: disabled (--no-sandbox)[/yellow]")
        return None

    runtime = find_container_runtime()
    if not runtime:
        console.print(
            "[yellow]Sandbox: disabled (no container runtime found). "
            "Install podman or docker, or use --no-sandbox.[/yellow]"
        )
        return None

    # Auto-build image if missing
    if not _image_exists(runtime):
        if not _build_image(runtime):
            console.print(
                "[yellow]Sandbox: disabled (image build failed). "
                "Fix Dockerfile or use --no-sandbox.[/yellow]"
            )
            return None

    # Determine if any active arm uses floop
    has_floop = any(arm.floop for arm in active_arms)

    floop_volume = None
    floop_readonly = False

    if has_floop:
        volume_name = f"floop-{phase}"
        if not _ensure_volume(runtime, volume_name):
            console.print(f"[red]Failed to create volume '{volume_name}'[/red]")
            return None

        if phase == "train" or phase == "smoke":
            # Initialize floop store in the volume
            _init_floop_in_volume(runtime, volume_name)
            floop_volume = volume_name
            floop_readonly = False
        elif phase == "eval":
            # Run leakage audit before allowing eval
            train_volume = "floop-train"
            if not _run_leakage_audit(train_volume):
                sys.exit("Eval aborted: leakage audit failed.")
            floop_volume = train_volume
            floop_readonly = True

    console.print(
        f"[green]Sandbox: enabled ({runtime})[/green]"
        + (f" | Volume: {floop_volume} ({'ro' if floop_readonly else 'rw'})" if floop_volume else "")
    )

    return SandboxConfig(
        enabled=True,
        runtime=runtime,
        floop_volume=floop_volume,
        floop_volume_readonly=floop_readonly,
    )


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
@click.option("--arm", "arm_names", multiple=True, help="Arm(s) to run (repeatable). Defaults depend on phase.")
@click.option("--no-sandbox", is_flag=True, default=False, help="Disable Docker sandbox (run agents directly on host)")
def main(phase: str, budget: float, workers: int, timeout: int, arm_names: tuple[str, ...], no_sandbox: bool):
    """Run floop-bench experiments."""
    init_db()

    console.print(f"[bold]Phase: {phase}[/bold]")
    console.print(f"Budget: ${budget:.2f} | Workers: {workers} | Timeout: {timeout}s")

    # Load config
    arms = load_arms()
    split = load_split()
    dataset = load_dataset_lookup()

    # Resolve --arm overrides
    if arm_names:
        for name in arm_names:
            if name not in arms:
                sys.exit(f"Unknown arm: {name}. Available: {list(arms.keys())}")
        selected_arms = [arms[name] for name in arm_names]
    else:
        selected_arms = None  # use phase defaults

    # Select tasks and arms for this phase
    all_arm_names = list(arms.keys())
    if phase == "smoke":
        task_ids = split["train"][:2]
        active_arms = selected_arms or [arms[all_arm_names[0]]]
    elif phase == "train":
        task_ids = split["train"]
        active_arms = selected_arms or [arms[all_arm_names[0]]]
    elif phase == "eval":
        task_ids = split["eval"]
        active_arms = selected_arms or [arms[a] for a in all_arm_names]
    else:
        sys.exit(f"Unknown phase: {phase}")

    # Setup sandbox
    sandbox = _setup_sandbox(phase, active_arms, no_sandbox)

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
            on_complete=on_complete, sandbox=sandbox,
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
                sandbox=sandbox,
            )
            save_run(result)
            append_prediction(result, PREDICTION_DIR / f"{arm.name}.jsonl")
            results.append(result)
            on_complete(result, i, len(instance_queue))

    console.print()
    print_summary()


if __name__ == "__main__":
    main()
