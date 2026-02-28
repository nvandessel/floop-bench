"""
Estimate cost for upcoming experiment phases based on prior run data.

Usage:
    uv run python -m scripts.estimate_cost
"""

from __future__ import annotations

from rich.console import Console
from rich.table import Table

from harness.db import get_arm_stats, get_runs, init_db

console = Console()


def estimate_cost():
    """Estimate remaining cost based on historical data."""
    init_db()

    runs = get_runs()
    if not runs:
        console.print("[yellow]No runs yet — using default cost estimates.[/yellow]")
        _print_default_estimates()
        return

    # Compute per-arm average cost
    arm_costs: dict[str, list[float]] = {}
    for r in runs:
        arm = r["arm"]
        cost = r["cost_usd"] or 0
        arm_costs.setdefault(arm, []).append(cost)

    table = Table(title="Cost Estimates Based on Historical Data")
    table.add_column("Phase")
    table.add_column("Arm")
    table.add_column("Tasks", justify="right")
    table.add_column("Avg Cost/Task", justify="right")
    table.add_column("Est. Total", justify="right")

    total = 0.0
    phases = [
        ("train", "haiku_bare", 30),
        ("eval", "haiku_bare", 20),
        ("eval", "haiku_floop", 20),
        ("eval", "sonnet_bare", 20),
    ]

    for phase, arm, n_tasks in phases:
        if arm in arm_costs:
            avg = sum(arm_costs[arm]) / len(arm_costs[arm])
        else:
            # Default estimates
            avg = 0.20 if "haiku" in arm else 1.00

        est = avg * n_tasks
        total += est
        table.add_row(phase, arm, str(n_tasks), f"${avg:.3f}", f"${est:.2f}")

    console.print(table)
    console.print(f"\n[bold]Estimated total: ${total:.2f}[/bold]")


def _print_default_estimates():
    """Print default cost estimates without historical data."""
    table = Table(title="Default Cost Estimates")
    table.add_column("Phase")
    table.add_column("Tasks", justify="right")
    table.add_column("Est. Cost", justify="right")

    rows = [
        ("Smoke (Haiku)", "2", "$0.40"),
        ("Train (Haiku)", "30", "$6.00"),
        ("Eval: haiku_bare", "20", "$4.00"),
        ("Eval: haiku_floop", "20", "$5.00"),
        ("Eval: sonnet_bare", "20", "$20.00"),
        ("Buffer", "", "$5.00"),
    ]

    for row in rows:
        table.add_row(*row)

    console.print(table)
    console.print("\n[bold]Estimated total: ~$40[/bold]")


if __name__ == "__main__":
    estimate_cost()
