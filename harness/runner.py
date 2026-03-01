"""
Runner: sets up repos, runs agents on SWE-bench tasks, captures diffs.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from agents.base import Agent, RunResult
from floop_integration.inject import get_floop_context
from harness.config import ArmConfig, create_agent

logger = logging.getLogger(__name__)

SANDBOX_IMAGE = "floop-sandbox"


def find_container_runtime() -> str | None:
    """Find podman or docker on PATH. Returns the command name or None."""
    for cmd in ("podman", "docker"):
        if shutil.which(cmd):
            return cmd
    return None


@dataclass
class SandboxConfig:
    """Configuration for Docker sandbox execution."""

    enabled: bool = True
    runtime: str = "podman"  # "podman" or "docker"
    image: str = SANDBOX_IMAGE
    floop_volume: str | None = None
    floop_volume_readonly: bool = False
    memory: str = "2g"
    cpus: int = 2
    pids_limit: int = 256
    env_vars: list[str] | None = None


def setup_repo(instance: dict, base_dir: Path) -> Path:
    """
    Create an isolated checkout for one task run.

    Uses bare clone + worktree for fast, isolated checkouts.
    """
    repo_slug = instance["repo"].replace("/", "__")
    bare_path = (base_dir / "repos" / repo_slug).resolve()

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

    task_dir = (base_dir / "worktrees" / instance["instance_id"]).resolve()
    if task_dir.exists():
        shutil.rmtree(task_dir)

    # Prune stale worktree references before adding
    subprocess.run(
        ["git", "-C", str(bare_path), "worktree", "prune"],
        capture_output=True,
    )

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


def _run_sandboxed(
    instance: dict,
    arm: ArmConfig,
    task_dir: Path,
    sandbox: SandboxConfig,
    timeout: int,
) -> RunResult:
    """Run agent inside a container."""
    cmd = [
        sandbox.runtime, "run", "--rm",
        # Security: drop all capabilities, add only what's needed
        "--cap-drop", "ALL",
        "--cap-add", "CHOWN",
        "--cap-add", "DAC_OVERRIDE",
        "--cap-add", "FOWNER",
        # Resource limits
        f"--memory={sandbox.memory}",
        f"--cpus={sandbox.cpus}",
        f"--pids-limit={sandbox.pids_limit}",
        # Bind mount worktree as /workspace
        "-v", f"{task_dir.resolve()}:/workspace",
    ]

    # Floop volume mount
    if sandbox.floop_volume:
        mode = "ro" if sandbox.floop_volume_readonly else "rw"
        cmd.extend(["-v", f"{sandbox.floop_volume}:/floop-store:{mode}"])

    # Forward API key env vars from host
    env_vars = sandbox.env_vars or [
        "GEMINI_API_KEY",
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
    ]
    for var in env_vars:
        cmd.extend(["-e", var])

    # Stdin mode
    cmd.append("-i")

    # Image
    cmd.append(sandbox.image)

    # Build input JSON
    input_data = {
        "problem_statement": instance["problem_statement"],
        "model": arm.model,
        "timeout": timeout,
        "floop_enabled": arm.floop,
    }
    if arm.floop and sandbox.floop_volume:
        input_data["floop_store"] = "/floop-store"

    logger.info(
        "Running sandboxed: %s (model=%s, floop=%s)",
        instance["instance_id"], arm.model, arm.floop,
    )

    try:
        proc = subprocess.run(
            cmd,
            input=json.dumps(input_data),
            capture_output=True,
            text=True,
            timeout=timeout + 60,  # extra margin for container startup
        )
    except subprocess.TimeoutExpired:
        return RunResult(
            instance_id="",
            arm="",
            model_patch="",
            model=arm.model,
            floop_enabled=arm.floop,
            status="timeout",
            duration_seconds=float(timeout),
            input_tokens=0,
            output_tokens=0,
            cost_usd=0.0,
            error_message="Docker container timed out",
        )

    if proc.stderr:
        logger.debug("Container stderr: %s", proc.stderr[:2000])

    if proc.returncode != 0:
        return RunResult(
            instance_id="",
            arm="",
            model_patch="",
            model=arm.model,
            floop_enabled=arm.floop,
            status="error",
            duration_seconds=0.0,
            input_tokens=0,
            output_tokens=0,
            cost_usd=0.0,
            error_message=f"Container exited {proc.returncode}: {proc.stderr[:500]}",
        )

    # Parse RunResult from stdout
    try:
        data = json.loads(proc.stdout)
        return RunResult(**data)
    except (json.JSONDecodeError, TypeError) as exc:
        return RunResult(
            instance_id="",
            arm="",
            model_patch="",
            model=arm.model,
            floop_enabled=arm.floop,
            status="error",
            duration_seconds=0.0,
            input_tokens=0,
            output_tokens=0,
            cost_usd=0.0,
            error_message=f"Failed to parse container output: {exc}. stdout: {proc.stdout[:500]}",
        )


def run_single_task(
    instance: dict,
    arm: ArmConfig,
    base_dir: Path,
    transcript_dir: Path,
    timeout: int = 300,
    sandbox: SandboxConfig | None = None,
) -> RunResult:
    """
    Run one agent on one SWE-bench task.

    1. Checkout repo at base_commit
    2. Run agent (sandboxed or direct)
    3. Capture git diff
    4. Save transcript
    5. Cleanup
    """
    instance_id = instance["instance_id"]
    task_dir = None
    start = time.monotonic()

    try:
        # Setup repo
        task_dir = setup_repo(instance, base_dir)

        if sandbox and sandbox.enabled:
            # Sandboxed execution via Docker
            result = _run_sandboxed(instance, arm, task_dir, sandbox, timeout)
        else:
            # Direct execution (no sandbox)
            floop_context = None
            if arm.floop and arm.floop_store:
                floop_context = get_floop_context(
                    Path(arm.floop_store), task_type="bug-fix"
                )

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
