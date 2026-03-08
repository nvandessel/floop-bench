"""
Wrapper for mini-SWE-agent: run arms, import results, evaluate.

Bridges mini-SWE-agent's SWE-bench runner with floop-bench's
existing eval/analysis pipeline (results.db, JSONL predictions,
SWE-bench evaluation, analysis/analyze.py).

Usage:
    uv run python -m scripts.run_mswea run --arm bare
    uv run python -m scripts.run_mswea run --arm floop --workers 2
    uv run python -m scripts.run_mswea import-results --arm bare
    uv run python -m scripts.run_mswea evaluate --arm mswea_bare
    uv run python -m scripts.run_mswea smoke
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import click
import minisweagent
from rich.console import Console

from agents.base import RunResult
from harness.db import init_db, save_run
from harness.swebench_eval import (
    import_swebench_results,
    run_swebench_evaluation,
)

console = Console()

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = PROJECT_ROOT / "config"
RESULTS_DIR = PROJECT_ROOT / "results"
PREDICTIONS_DIR = RESULTS_DIR / "predictions"
MSWEA_OUTPUT_DIR = RESULTS_DIR / "mswea"

# Built-in swebench_xml.yaml from mini-swe-agent package — must be passed as
# the base config before our overrides, since -c replaces (not extends).
# We use the XML variant (text-based action parsing via action_regex) instead
# of the tool-call variant because Gemini sometimes returns empty choices
# when using native tool calls.
SWEBENCH_BASE_CONFIG = (
    Path(minisweagent.__file__).parent / "config" / "benchmarks" / "swebench_xml.yaml"
)


def _find_container_runtime() -> str:
    """Auto-detect container runtime: prefer podman, fall back to docker."""
    for cmd in ["podman", "docker"]:
        if shutil.which(cmd):
            return cmd
    console.print("[red]No container runtime found (need docker or podman)[/red]")
    sys.exit(1)


def _load_eval_ids() -> list[str]:
    """Load eval instance IDs from splits.json."""
    splits_path = CONFIG_DIR / "splits.json"
    with open(splits_path) as f:
        data = json.load(f)
    return data["eval"]


def _build_filter_regex(instance_ids: list[str]) -> str:
    """Build regex filter for mini-extra swebench --filter."""
    escaped = [re.escape(iid) for iid in instance_ids]
    return "^(" + "|".join(escaped) + ")$"


def _arm_name(arm: str) -> str:
    """Canonical arm name for DB/predictions."""
    return f"mswea_{arm}"


def _mswea_output_dir(arm: str) -> Path:
    return MSWEA_OUTPUT_DIR / arm


@click.group()
def cli():
    """mini-SWE-agent wrapper for floop-bench."""
    pass


@cli.command()
@click.option("--arm", required=True, help="Arm name (matches config/<arm>.yaml)")
@click.option("--workers", default=1, help="Parallel workers")
@click.option(
    "--filter-ids",
    default=None,
    help="Comma-separated instance IDs (default: all eval IDs from splits.json)",
)
@click.option("--cost-limit", default=3.0, help="Per-instance cost limit in USD")
def run(arm: str, workers: int, filter_ids: str | None, cost_limit: float):
    """Run mini-SWE-agent on eval tasks for an arm."""
    container_rt = _find_container_runtime()

    if filter_ids:
        ids = [i.strip() for i in filter_ids.split(",")]
    else:
        ids = _load_eval_ids()

    filter_re = _build_filter_regex(ids)
    # Try config/<arm>.yaml first, then config/mswea_<arm>.yaml for backwards compat
    config_path = CONFIG_DIR / f"{arm}.yaml"
    if not config_path.exists():
        config_path = CONFIG_DIR / f"mswea_{arm}.yaml"
    output_dir = _mswea_output_dir(arm)
    output_dir.mkdir(parents=True, exist_ok=True)

    console.print(f"[cyan]Running mini-SWE-agent: arm={arm}, {len(ids)} tasks[/cyan]")
    console.print(f"  Config: {config_path}")
    console.print(f"  Output: {output_dir}")
    console.print(f"  Container runtime: {container_rt}")
    console.print(f"  Workers: {workers}")

    cmd = [
        "mini-extra", "swebench",
        "--subset", "verified",
        "--split", "test",
        "--filter", filter_re,
        "-c", str(SWEBENCH_BASE_CONFIG),
        "-c", str(config_path),
        "-c", f"agent.cost_limit={cost_limit}",
        "-o", str(output_dir),
        "--workers", str(workers),
    ]

    # Set container runtime if not docker (mini-SWE-agent defaults to docker)
    if container_rt != "docker":
        cmd.extend(["-c", f"environment.executable={container_rt}"])

    console.print(f"\n  Command: {' '.join(cmd[:6])} ... [truncated]")

    result = subprocess.run(cmd, cwd=str(PROJECT_ROOT))

    if result.returncode != 0:
        console.print(f"[red]mini-SWE-agent exited with code {result.returncode}[/red]")
        sys.exit(result.returncode)

    console.print(f"[green]Run complete for arm={arm}[/green]")
    console.print(f"Output at: {output_dir}")


@cli.command("import-results")
@click.option("--arm", required=True, help="Arm name (must match output dir in results/mswea/<arm>/)")
def import_results(arm: str):
    """Convert mini-SWE-agent output to floop-bench DB + JSONL."""
    init_db()

    output_dir = _mswea_output_dir(arm)
    preds_path = output_dir / "preds.json"
    arm_name = _arm_name(arm)

    if not preds_path.exists():
        console.print(f"[red]No preds.json found at {preds_path}[/red]")
        console.print("Run the 'run' command first.")
        sys.exit(1)

    # Load predictions
    with open(preds_path) as f:
        preds = json.load(f)

    console.print(f"[cyan]Importing {len(preds)} predictions for arm={arm_name}[/cyan]")

    # Write JSONL for SWE-bench eval
    PREDICTIONS_DIR.mkdir(parents=True, exist_ok=True)
    jsonl_path = PREDICTIONS_DIR / f"{arm_name}.jsonl"
    with open(jsonl_path, "w") as f:
        for instance_id, pred in preds.items():
            line = {
                "instance_id": instance_id,
                "model_name_or_path": pred.get("model_name_or_path", "mini-swe-agent"),
                "model_patch": pred.get("model_patch", ""),
            }
            f.write(json.dumps(line) + "\n")

    console.print(f"  Wrote {len(preds)} predictions to {jsonl_path}")

    # Parse trajectories and import to DB
    imported = 0
    total_cost = 0.0
    for instance_id, pred in preds.items():
        traj_path = output_dir / instance_id / f"{instance_id}.traj.json"

        cost_usd = 0.0
        duration_seconds = 0.0
        status = "completed"
        input_tokens = 0
        output_tokens = 0
        transcript_path = None

        if traj_path.exists():
            with open(traj_path) as f:
                traj = json.load(f)

            transcript_path = str(traj_path)
            info = traj.get("info", {})
            model_stats = info.get("model_stats", {})
            cost_usd = model_stats.get("instance_cost", 0.0)

            # Map exit status
            exit_status = info.get("exit_status", "unknown")
            if exit_status in ("Submitted", "submitted"):
                status = "completed"
            elif exit_status in ("LimitsExceeded", "limits_exceeded"):
                status = "timeout"
            else:
                status = "error"

            # Compute duration from message timestamps
            messages = traj.get("messages", [])
            timestamps = []
            for msg in messages:
                extra = msg.get("extra", {})
                ts = extra.get("timestamp")
                if ts is not None:
                    timestamps.append(float(ts))
            if len(timestamps) >= 2:
                duration_seconds = timestamps[-1] - timestamps[0]

            # Extract token counts from messages if available
            for msg in messages:
                extra = msg.get("extra", {})
                response = extra.get("response", {})
                usage = response.get("usage", {}) if isinstance(response, dict) else {}
                input_tokens += usage.get("prompt_tokens", 0)
                output_tokens += usage.get("completion_tokens", 0)

        # Detect model from trajectory or prediction metadata
        model_name = pred.get("model_name_or_path", "unknown")

        result = RunResult(
            instance_id=instance_id,
            arm=arm_name,
            model_patch=pred.get("model_patch", ""),
            model=model_name,
            floop_enabled=("floop" in arm),
            status=status,
            duration_seconds=duration_seconds,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost_usd,
            transcript_path=transcript_path,
        )
        save_run(result)
        imported += 1
        total_cost += cost_usd

    console.print(f"[green]Imported {imported} runs to results.db[/green]")
    console.print(f"  Total cost: ${total_cost:.2f}")


@cli.command()
@click.option("--arm", default=None, help="Specific arm to evaluate (default: all mswea_* arms)")
@click.option("--max-workers", default=4, help="Max parallel Docker workers for eval")
def evaluate(arm: str | None, max_workers: int):
    """Run SWE-bench evaluation on imported predictions."""
    init_db()

    if arm:
        arms = [arm]
    else:
        # Find all mswea prediction files
        arms = [
            p.stem for p in PREDICTIONS_DIR.glob("mswea_*.jsonl")
        ]

    if not arms:
        console.print("[red]No mswea prediction files found.[/red]")
        console.print("Run 'import-results' first.")
        sys.exit(1)

    for arm_name in arms:
        pred_path = PREDICTIONS_DIR / f"{arm_name}.jsonl"
        if not pred_path.exists():
            console.print(f"[yellow]Skipping {arm_name}: no predictions file[/yellow]")
            continue

        run_id = f"{arm_name}_eval"
        console.print(f"\n[cyan]Evaluating {arm_name}...[/cyan]")

        success = run_swebench_evaluation(
            pred_path, run_id, max_workers=max_workers,
        )
        if success:
            import_swebench_results(arm_name, run_id)
        else:
            console.print(f"[red]Evaluation failed for {arm_name}[/red]")


@cli.command()
@click.option("--instance", default="django__django-16485", help="Instance ID for smoke test")
@click.option("--config", "config_name", default="mswea_bare", help="Config name (without .yaml)")
def smoke(instance: str, config_name: str):
    """Run a single-task smoke test to validate setup."""
    container_rt = _find_container_runtime()
    config_path = CONFIG_DIR / f"{config_name}.yaml"
    output_dir = MSWEA_OUTPUT_DIR / "smoke"
    output_dir.mkdir(parents=True, exist_ok=True)

    console.print(f"[cyan]Smoke test: {instance}[/cyan]")
    console.print(f"  Container runtime: {container_rt}")

    cmd = [
        "mini-extra", "swebench",
        "--subset", "verified",
        "--split", "test",
        "--filter", f"^{re.escape(instance)}$",
        "-c", str(SWEBENCH_BASE_CONFIG),
        "-c", str(config_path),
        "-c", "agent.cost_limit=1.0",
        "-o", str(output_dir),
        "--workers", "1",
    ]

    if container_rt != "docker":
        cmd.extend(["-c", f"environment.executable={container_rt}"])

    result = subprocess.run(cmd, cwd=str(PROJECT_ROOT))

    if result.returncode != 0:
        console.print(f"[red]Smoke test failed (exit {result.returncode})[/red]")
        sys.exit(result.returncode)

    # Validate output
    preds_path = output_dir / "preds.json"
    if not preds_path.exists():
        console.print("[red]No preds.json produced[/red]")
        sys.exit(1)

    with open(preds_path) as f:
        preds = json.load(f)

    if instance not in preds:
        console.print(f"[red]{instance} not in preds.json[/red]")
        console.print(f"  Keys: {list(preds.keys())}")
        sys.exit(1)

    patch = preds[instance].get("model_patch", "")
    has_patch = bool(patch.strip())

    console.print(f"[green]Smoke test passed![/green]")
    console.print(f"  Patch generated: {'yes' if has_patch else 'no'}")
    if has_patch:
        console.print(f"  Patch size: {len(patch)} chars")

    # Check trajectory for cost
    traj_path = output_dir / instance / f"{instance}.traj.json"
    if traj_path.exists():
        with open(traj_path) as f:
            traj = json.load(f)
        cost = traj.get("info", {}).get("model_stats", {}).get("instance_cost", 0)
        console.print(f"  Cost: ${cost:.4f}")


if __name__ == "__main__":
    cli()
