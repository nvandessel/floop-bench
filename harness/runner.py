"""
Runner: sets up repos, runs agents on SWE-bench tasks, captures diffs.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import time
from pathlib import Path

from agents.base import Agent, RunResult
from floop_integration.inject import get_floop_context
from harness.config import ArmConfig, create_agent

logger = logging.getLogger(__name__)


def setup_repo(instance: dict, base_dir: Path) -> Path:
    """
    Create an isolated checkout for one task run.

    Uses bare clone + worktree for fast, isolated checkouts.
    """
    repo_slug = instance["repo"].replace("/", "__")
    bare_path = base_dir / "repos" / repo_slug

    if not bare_path.exists():
        bare_path.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            [
                "git", "clone", "--bare",
                f"https://github.com/{instance['repo']}.git",
                str(bare_path),
            ],
            check=True,
            capture_output=True,
        )

    task_dir = base_dir / "worktrees" / instance["instance_id"]
    if task_dir.exists():
        shutil.rmtree(task_dir)

    task_dir.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "git", "-C", str(bare_path), "worktree", "add",
            "--detach", str(task_dir), instance["base_commit"],
        ],
        check=True,
        capture_output=True,
    )

    return task_dir


def cleanup_repo(task_dir: Path, base_dir: Path | None = None) -> None:
    """Remove worktree after run."""
    shutil.rmtree(task_dir, ignore_errors=True)
    if base_dir:
        # Prune stale worktree references
        for bare in (base_dir / "repos").iterdir():
            if bare.is_dir():
                subprocess.run(
                    ["git", "-C", str(bare), "worktree", "prune"],
                    capture_output=True,
                )


def run_single_task(
    instance: dict,
    arm: ArmConfig,
    base_dir: Path,
    transcript_dir: Path,
    timeout: int = 300,
) -> RunResult:
    """
    Run one agent on one SWE-bench task.

    1. Checkout repo at base_commit
    2. Build floop context if arm has floop enabled
    3. Run agent
    4. Capture git diff
    5. Save transcript
    6. Cleanup
    """
    instance_id = instance["instance_id"]
    task_dir = None
    start = time.monotonic()

    try:
        # Setup repo
        task_dir = setup_repo(instance, base_dir)

        # Build floop context
        floop_context = None
        if arm.floop and arm.floop_store:
            floop_context = get_floop_context(
                Path(arm.floop_store), task_type="bug-fix"
            )

        # Create agent and run
        agent = create_agent(arm)
        result = agent.run(
            problem_statement=instance["problem_statement"],
            repo_dir=task_dir,
            floop_context=floop_context,
            timeout=timeout,
        )

        # Fill in instance and arm info
        result.instance_id = instance_id
        result.arm = arm.name

        # Capture diff against base commit (agent may have staged changes)
        try:
            diff = subprocess.run(
                ["git", "diff", instance["base_commit"]],
                capture_output=True,
                text=True,
                cwd=str(task_dir),
            )
            if diff.stdout:
                result.model_patch = diff.stdout
        except Exception as exc:
            logger.warning("Failed to capture diff for %s: %s", instance_id, exc)

    except Exception as exc:
        logger.error("Task %s/%s failed: %s", instance_id, arm.name, exc)
        result = RunResult(
            instance_id=instance_id,
            arm=arm.name,
            model_patch="",
            model=arm.model,
            floop_enabled=arm.floop,
            status="error",
            duration_seconds=time.monotonic() - start,
            input_tokens=0,
            output_tokens=0,
            cost_usd=0.0,
            error_message=str(exc),
        )

    # Save transcript
    transcript_dir.mkdir(parents=True, exist_ok=True)
    transcript_path = transcript_dir / f"{instance_id}_{arm.name}.json"
    transcript_path.write_text(json.dumps(result.to_dict(), indent=2))
    result.transcript_path = str(transcript_path)

    # Cleanup
    if task_dir:
        cleanup_repo(task_dir, base_dir)

    return result


def append_prediction(result: RunResult, prediction_path: Path | str) -> None:
    """Append a prediction to JSONL file in SWE-bench format."""
    path = Path(prediction_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as f:
        f.write(json.dumps(result.to_prediction()) + "\n")
