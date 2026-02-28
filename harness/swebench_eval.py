"""
SWE-bench evaluation integration.

Invokes SWE-bench's Docker-based evaluation on prediction JSONL files,
then imports resolved/unresolved results back into SQLite.

Usage:
    uv run python -m harness.swebench_eval --arm haiku_bare
    uv run python -m harness.swebench_eval --arm haiku_bare --split train
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import click
from rich.console import Console

from harness.db import init_db, update_resolved

console = Console()

PREDICTIONS_DIR = Path("results/predictions")
EVAL_RESULTS_DIR = Path("results/eval_output")


def run_swebench_evaluation(
    predictions_path: Path,
    run_id: str,
    dataset_name: str = "princeton-nlp/SWE-bench_Verified",
    max_workers: int = 4,
) -> bool:
    """
    Invoke SWE-bench Docker evaluation on a predictions file.

    Returns True if evaluation completed successfully.
    """
    console.print(f"[cyan]Running SWE-bench evaluation: {run_id}[/cyan]")
    console.print(f"  Predictions: {predictions_path}")

    cmd = [
        "python", "-m", "swebench.harness.run_evaluation",
        "--dataset_name", dataset_name,
        "--predictions_path", str(predictions_path),
        "--max_workers", str(max_workers),
        "--run_id", run_id,
    ]

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=3600,
        )
        if result.returncode != 0:
            console.print(f"[red]SWE-bench evaluation failed:[/red]")
            console.print(result.stderr[-1000:] if result.stderr else "No stderr")
            return False
        console.print(f"[green]Evaluation completed: {run_id}[/green]")
        return True
    except subprocess.TimeoutExpired:
        console.print("[red]SWE-bench evaluation timed out (1hr)[/red]")
        return False
    except FileNotFoundError:
        console.print("[red]swebench not installed. Run: pip install swebench[/red]")
        return False


def import_swebench_results(arm: str, run_id: str) -> int:
    """
    Import SWE-bench evaluation results into SQLite.

    Looks for the SWE-bench output report and updates resolved status.
    Returns number of results imported.
    """
    # SWE-bench writes results to a predictable location
    # Look for the report file
    report_patterns = [
        Path(f"logs/run_evaluation/{run_id}/report.json"),
        EVAL_RESULTS_DIR / run_id / "report.json",
    ]

    report_path = None
    for p in report_patterns:
        if p.exists():
            report_path = p
            break

    if report_path is None:
        # Try to find it
        for p in Path(".").rglob(f"*{run_id}*report*.json"):
            report_path = p
            break

    if report_path is None:
        console.print(f"[yellow]No report found for {run_id}[/yellow]")
        console.print("Expected locations:")
        for p in report_patterns:
            console.print(f"  {p}")
        return 0

    console.print(f"[cyan]Importing results from: {report_path}[/cyan]")

    with open(report_path) as f:
        report = json.load(f)

    # Log structure for debugging
    top_keys = list(report.keys())[:10]
    console.print(f"  Report keys: {top_keys} ({len(report)} total entries)")

    count = 0
    # SWE-bench report format: dict of instance_id -> {"resolved": bool, ...}
    # or {"resolved": [...], "unresolved": [...]}
    if "resolved" in report and isinstance(report["resolved"], list):
        for instance_id in report.get("resolved", []):
            update_resolved(instance_id, arm, True)
            count += 1
        for instance_id in report.get("unresolved", []):
            update_resolved(instance_id, arm, False)
            count += 1
    else:
        for instance_id, result in report.items():
            if isinstance(result, dict) and "resolved" in result:
                update_resolved(instance_id, arm, result["resolved"])
                count += 1

    if count == 0:
        console.print(
            f"[yellow]Warning: zero results imported for {arm}. "
            f"Report structure may not match expected format. "
            f"Top-level keys: {top_keys}[/yellow]"
        )
    else:
        console.print(f"[green]Imported {count} results for {arm}[/green]")
    return count


@click.command()
@click.option("--arm", required=True, help="Arm name to evaluate")
@click.option("--split", default=None, help="Split name (train/eval) for run ID")
@click.option("--max-workers", default=4, help="Max parallel Docker workers")
def main(arm: str, split: str | None, max_workers: int):
    """Evaluate predictions for an arm using SWE-bench Docker."""
    init_db()

    predictions_path = PREDICTIONS_DIR / f"{arm}.jsonl"
    if not predictions_path.exists():
        console.print(f"[red]No predictions found: {predictions_path}[/red]")
        raise SystemExit(1)

    run_id = f"{arm}_{split}" if split else f"{arm}_eval"

    success = run_swebench_evaluation(
        predictions_path, run_id, max_workers=max_workers,
    )

    if success:
        import_swebench_results(arm, run_id)


if __name__ == "__main__":
    main()
