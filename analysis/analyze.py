"""
Statistical analysis of floop-bench results.

Primary metric: Resolve rate (% of eval tasks where patch passes tests)
Primary comparison: haiku_floop vs haiku_bare
Headline number: Gap closure = (haiku_floop - haiku_bare) / (sonnet_bare - haiku_bare)

With 20 eval tasks:
- McNemar's test for paired binary outcomes
- Bootstrap 95% CIs (10,000 resamples) on rates and gap closure
- Cohen's h for effect size

Usage:
    uv run python -m analysis.analyze
"""

from __future__ import annotations

import math

import numpy as np
from rich.console import Console
from rich.table import Table
from scipy import stats as sp_stats

from harness.db import get_arm_stats, get_runs, init_db

console = Console()


def compute_gap_closure(
    sonnet_rate: float, haiku_rate: float, floop_rate: float
) -> float | None:
    """
    How much of the Sonnet-Haiku gap does floop close?
    Returns fraction in [0, 1] (or >1 if floop+haiku beats Sonnet).
    """
    gap = sonnet_rate - haiku_rate
    if gap <= 0:
        return None
    return (floop_rate - haiku_rate) / gap


def bootstrap_ci(
    data: np.ndarray,
    stat_fn,
    n_boot: int = 10000,
    ci: float = 0.95,
    seed: int = 42,
) -> tuple[float, float]:
    """Bootstrap confidence interval for any statistic."""
    rng = np.random.RandomState(seed)
    boot_stats = []
    for _ in range(n_boot):
        sample = rng.choice(data, size=len(data), replace=True)
        boot_stats.append(stat_fn(sample))
    lower = np.percentile(boot_stats, (1 - ci) / 2 * 100)
    upper = np.percentile(boot_stats, (1 + ci) / 2 * 100)
    return float(lower), float(upper)


def cohens_h(p1: float, p2: float) -> float:
    """Cohen's h effect size for comparing two proportions."""
    return 2 * math.asin(math.sqrt(p1)) - 2 * math.asin(math.sqrt(p2))


def mcnemar_test(
    outcomes_a: list[bool], outcomes_b: list[bool]
) -> tuple[float, float]:
    """
    McNemar's test for paired binary outcomes.
    Returns (chi2, p_value).
    """
    assert len(outcomes_a) == len(outcomes_b)
    # b = A solves, B doesn't; c = B solves, A doesn't
    b = sum(a and not bb for a, bb in zip(outcomes_a, outcomes_b))
    c = sum(not a and bb for a, bb in zip(outcomes_a, outcomes_b))

    if b + c == 0:
        return 0.0, 1.0

    # McNemar's with continuity correction
    chi2 = (abs(b - c) - 1) ** 2 / (b + c)
    p_value = 1 - sp_stats.chi2.cdf(chi2, df=1)
    return float(chi2), float(p_value)


def analyze():
    """Run full analysis on benchmark results."""
    init_db()

    arm_stats = get_arm_stats()
    if not arm_stats:
        console.print("[yellow]No results to analyze.[/yellow]")
        return

    # Per-arm resolve rates with CIs
    table = Table(title="Resolve Rates")
    table.add_column("Arm")
    table.add_column("Resolved / Total", justify="right")
    table.add_column("Rate", justify="right")
    table.add_column("95% CI", justify="right")
    table.add_column("Avg Cost", justify="right")
    table.add_column("Total Cost", justify="right")

    rates = {}
    for s in arm_stats:
        total = s["total"]
        resolved = s["resolved_count"] or 0
        rate = resolved / total if total > 0 else 0.0
        rates[s["arm"]] = rate

        # Bootstrap CI on resolve rate
        outcomes = np.array([1] * resolved + [0] * (total - resolved))
        if len(outcomes) > 0:
            ci_low, ci_high = bootstrap_ci(outcomes, np.mean)
            ci_str = f"[{ci_low:.1%}, {ci_high:.1%}]"
        else:
            ci_str = "N/A"

        avg_cost = f"${s['avg_cost']:.3f}" if s["avg_cost"] else "N/A"
        total_cost = f"${s['total_cost']:.2f}" if s["total_cost"] else "$0.00"

        table.add_row(
            s["arm"],
            f"{resolved}/{total}",
            f"{rate:.1%}",
            ci_str,
            avg_cost,
            total_cost,
        )

    console.print(table)

    # Gap closure
    if "sonnet_bare" in rates and "haiku_bare" in rates and "haiku_floop" in rates:
        gap_closure = compute_gap_closure(
            rates["sonnet_bare"], rates["haiku_bare"], rates["haiku_floop"]
        )

        console.print()
        console.print("[bold]Gap Closure Analysis[/bold]")
        console.print(f"  Sonnet bare:  {rates['sonnet_bare']:.1%}")
        console.print(f"  Haiku bare:   {rates['haiku_bare']:.1%}")
        console.print(f"  Haiku floop:  {rates['haiku_floop']:.1%}")

        if gap_closure is not None:
            console.print(f"  [bold cyan]Gap closure: {gap_closure:.1%}[/bold cyan]")
        else:
            console.print("  [yellow]No gap to close (Haiku >= Sonnet)[/yellow]")

        # McNemar's test: haiku_floop vs haiku_bare
        haiku_runs = {r["instance_id"]: r for r in get_runs("haiku_bare")}
        floop_runs = {r["instance_id"]: r for r in get_runs("haiku_floop")}
        common = set(haiku_runs) & set(floop_runs)

        if common:
            haiku_outcomes = [bool(haiku_runs[tid].get("resolved")) for tid in sorted(common)]
            floop_outcomes = [bool(floop_runs[tid].get("resolved")) for tid in sorted(common)]
            chi2, p_val = mcnemar_test(floop_outcomes, haiku_outcomes)

            console.print(f"\n  McNemar's test (floop vs bare):")
            console.print(f"    chi2 = {chi2:.3f}, p = {p_val:.4f}")

            h = cohens_h(rates["haiku_floop"], rates["haiku_bare"])
            console.print(f"    Cohen's h = {h:.3f}")

    # Cost efficiency
    console.print()
    cost_table = Table(title="Cost Efficiency")
    cost_table.add_column("Arm")
    cost_table.add_column("Cost/Resolved", justify="right")
    cost_table.add_column("Cost/Task", justify="right")

    for s in arm_stats:
        resolved = s["resolved_count"] or 0
        total_cost = s["total_cost"] or 0
        cost_per_resolved = f"${total_cost / resolved:.2f}" if resolved > 0 else "N/A"
        cost_per_task = f"${total_cost / s['total']:.3f}" if s["total"] > 0 else "N/A"

        cost_table.add_row(s["arm"], cost_per_resolved, cost_per_task)

    console.print(cost_table)


if __name__ == "__main__":
    analyze()
