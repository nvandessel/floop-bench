"""
Worker pool for parallel task execution.

Each worker:
1. Gets a dedicated worktree (git worktree add)
2. Runs one task at a time in that worktree
3. Captures the diff, cleans up, moves to next task

The pool:
- N workers (default: 4, configurable via --workers)
- Each worker is a separate process (ProcessPoolExecutor)
- Each worker writes results to SQLite (safe via WAL mode)
- Cost guard checked before submitting each task
"""

from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

from agents.base import RunResult
from harness.config import ArmConfig
from harness.db import get_total_cost, save_run
from harness.runner import SandboxConfig, append_prediction, run_single_task


def _worker_task(
    instance: dict,
    arm: ArmConfig,
    base_dir: Path,
    transcript_dir: Path,
    prediction_dir: Path,
    timeout: int,
    sandbox: SandboxConfig | None = None,
) -> RunResult:
    """Run a single task in a worker process."""
    result = run_single_task(
        instance, arm, base_dir, transcript_dir, timeout, sandbox=sandbox,
    )
    save_run(result)
    append_prediction(result, prediction_dir / f"{arm.name}.jsonl")
    return result


def run_parallel(
    queue: list[tuple[dict, ArmConfig]],
    base_dir: Path,
    transcript_dir: Path,
    prediction_dir: Path,
    workers: int = 4,
    budget: float = 55.0,
    timeout: int = 300,
    on_complete=None,
    sandbox: SandboxConfig | None = None,
) -> list[RunResult]:
    """
    Run task queue across N parallel workers.

    Args:
        queue: List of (instance_dict, arm_config) pairs
        base_dir: Base directory for repo checkouts
        transcript_dir: Directory for transcripts
        prediction_dir: Directory for prediction JSONL files
        workers: Number of parallel workers
        budget: Maximum total cost before stopping
        timeout: Per-task timeout in seconds
        on_complete: Optional callback(result, index, total) for progress
        sandbox: Optional sandbox configuration for Docker execution

    Returns:
        List of RunResults
    """
    results = []
    total = len(queue)

    if workers <= 1:
        # Sequential execution
        for i, (instance, arm) in enumerate(queue):
            spent = get_total_cost()
            if spent >= budget:
                break
            result = _worker_task(
                instance, arm, base_dir, transcript_dir,
                prediction_dir, timeout, sandbox=sandbox,
            )
            results.append(result)
            if on_complete:
                on_complete(result, i, total)
        return results

    # Parallel execution
    with ProcessPoolExecutor(max_workers=workers) as executor:
        futures = {}
        for i, (instance, arm) in enumerate(queue):
            # Cost guard before submitting
            spent = get_total_cost()
            if spent >= budget:
                break
            future = executor.submit(
                _worker_task,
                instance, arm, base_dir, transcript_dir,
                prediction_dir, timeout, sandbox,
            )
            futures[future] = i

        for future in as_completed(futures):
            idx = futures[future]
            try:
                result = future.result()
                results.append(result)
                if on_complete:
                    on_complete(result, idx, total)
            except Exception as e:
                # run_single_task now catches exceptions internally and
                # returns error RunResults, so this should be rare
                print(f"Worker error (task {idx}): {e}")

    return results
