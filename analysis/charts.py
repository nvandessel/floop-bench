"""
Chart generation for floop-bench results.

Produces:
1. Grouped bar chart: resolve rate per arm with 95% CI error bars
2. Cost-performance scatter: cost vs resolve rate
3. Cost per resolved task: bar chart

Usage:
    uv run python -m analysis.charts
"""

from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

from harness.db import get_arm_stats, init_db

CHARTS_DIR = Path("results/charts")
ARM_COLORS = {
    "sonnet_bare": "#4A90D9",
    "haiku_bare": "#E8A838",
    "haiku_floop": "#50C878",
}
ARM_LABELS = {
    "sonnet_bare": "Sonnet (ceiling)",
    "haiku_bare": "Haiku (bare)",
    "haiku_floop": "Haiku + floop",
}


def _wilson_ci(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval for binomial proportion."""
    if n == 0:
        return 0.0, 0.0
    p = k / n
    denom = 1 + z**2 / n
    center = (p + z**2 / (2 * n)) / denom
    margin = z * np.sqrt(p * (1 - p) / n + z**2 / (4 * n**2)) / denom
    return max(0, center - margin), min(1, center + margin)


def resolve_rate_chart(arm_stats: list[dict]) -> None:
    """Grouped bar chart of resolve rates with CI error bars."""
    fig, ax = plt.subplots(figsize=(8, 5))

    arms = []
    rates = []
    ci_lows = []
    ci_highs = []

    for s in arm_stats:
        name = s["arm"]
        total = s["total"]
        resolved = s["resolved_count"] or 0
        rate = resolved / total if total > 0 else 0
        low, high = _wilson_ci(resolved, total)

        arms.append(ARM_LABELS.get(name, name))
        rates.append(rate * 100)
        ci_lows.append((rate - low) * 100)
        ci_highs.append((high - rate) * 100)

    colors = [ARM_COLORS.get(s["arm"], "#888888") for s in arm_stats]
    x = range(len(arms))

    bars = ax.bar(x, rates, color=colors, edgecolor="black", linewidth=0.5)
    ax.errorbar(
        x,
        rates,
        yerr=[ci_lows, ci_highs],
        fmt="none",
        color="black",
        capsize=5,
        linewidth=1.5,
    )

    ax.set_ylabel("Resolve Rate (%)")
    ax.set_title("SWE-bench Resolve Rates by Arm")
    ax.set_xticks(x)
    ax.set_xticklabels(arms)
    ax.set_ylim(0, 100)
    ax.grid(axis="y", alpha=0.3)

    # Add rate labels on bars
    for bar, rate in zip(bars, rates):
        ax.text(
            bar.get_x() + bar.get_width() / 2,
            bar.get_height() + 2,
            f"{rate:.1f}%",
            ha="center",
            va="bottom",
            fontweight="bold",
        )

    plt.tight_layout()
    fig.savefig(CHARTS_DIR / "resolve_rates.png", dpi=150)
    fig.savefig(CHARTS_DIR / "resolve_rates.svg")
    plt.close(fig)
    print(f"Saved: {CHARTS_DIR / 'resolve_rates.png'}")


def cost_scatter(arm_stats: list[dict]) -> None:
    """Cost vs resolve rate scatter plot."""
    fig, ax = plt.subplots(figsize=(8, 5))

    for s in arm_stats:
        name = s["arm"]
        total = s["total"]
        resolved = s["resolved_count"] or 0
        rate = resolved / total * 100 if total > 0 else 0
        avg_cost = s["avg_cost"] or 0

        color = ARM_COLORS.get(name, "#888888")
        label = ARM_LABELS.get(name, name)

        ax.scatter(
            avg_cost,
            rate,
            color=color,
            s=150,
            edgecolors="black",
            linewidth=0.5,
            zorder=5,
        )
        ax.annotate(
            label,
            (avg_cost, rate),
            textcoords="offset points",
            xytext=(10, 5),
            fontsize=9,
        )

    ax.set_xlabel("Average Cost per Task ($)")
    ax.set_ylabel("Resolve Rate (%)")
    ax.set_title("Cost vs Performance")
    ax.set_ylim(0, 100)
    ax.grid(alpha=0.3)

    plt.tight_layout()
    fig.savefig(CHARTS_DIR / "cost_scatter.png", dpi=150)
    fig.savefig(CHARTS_DIR / "cost_scatter.svg")
    plt.close(fig)
    print(f"Saved: {CHARTS_DIR / 'cost_scatter.png'}")


def cost_per_resolved(arm_stats: list[dict]) -> None:
    """Bar chart of cost per resolved task."""
    fig, ax = plt.subplots(figsize=(8, 5))

    arms = []
    costs = []

    for s in arm_stats:
        name = s["arm"]
        resolved = s["resolved_count"] or 0
        total_cost = s["total_cost"] or 0

        if resolved > 0:
            arms.append(ARM_LABELS.get(name, name))
            costs.append(total_cost / resolved)

    if not arms:
        print("No resolved tasks — skipping cost_per_resolved chart")
        plt.close(fig)
        return

    colors = [
        ARM_COLORS.get(s["arm"], "#888888")
        for s in arm_stats
        if (s["resolved_count"] or 0) > 0
    ]
    x = range(len(arms))

    bars = ax.bar(x, costs, color=colors, edgecolor="black", linewidth=0.5)
    ax.set_ylabel("Cost per Resolved Task ($)")
    ax.set_title("Cost Efficiency by Arm")
    ax.set_xticks(x)
    ax.set_xticklabels(arms)
    ax.grid(axis="y", alpha=0.3)

    for bar, cost in zip(bars, costs):
        ax.text(
            bar.get_x() + bar.get_width() / 2,
            bar.get_height() + 0.1,
            f"${cost:.2f}",
            ha="center",
            va="bottom",
            fontweight="bold",
        )

    plt.tight_layout()
    fig.savefig(CHARTS_DIR / "cost_per_resolved.png", dpi=150)
    fig.savefig(CHARTS_DIR / "cost_per_resolved.svg")
    plt.close(fig)
    print(f"Saved: {CHARTS_DIR / 'cost_per_resolved.png'}")


def generate_all_charts():
    """Generate all charts from current results."""
    init_db()
    CHARTS_DIR.mkdir(parents=True, exist_ok=True)

    arm_stats = get_arm_stats()
    if not arm_stats:
        print("No results to chart.")
        return

    resolve_rate_chart(arm_stats)
    cost_scatter(arm_stats)
    cost_per_resolved(arm_stats)
    print("All charts generated.")


if __name__ == "__main__":
    generate_all_charts()
