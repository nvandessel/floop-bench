"""Floop CLI subprocess wrapper for agent-agnostic behavior injection."""

from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)


def get_active_behaviors(
    store_path: Path, task_type: str | None = None
) -> list[dict]:
    """
    Get active behaviors from floop store via CLI.

    Args:
        store_path: Path to floop behavior store
        task_type: Optional task type for activation filtering (e.g. "bug-fix")

    Returns:
        List of behavior dicts with keys like 'kind', 'content', 'tags'
    """
    cmd = ["floop", "active", "--json", "--root", str(store_path)]
    if task_type:
        cmd.extend(["--task", task_type])

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            logger.warning(
                "floop active failed (exit %d): %s",
                result.returncode,
                result.stderr.strip() or result.stdout.strip(),
            )
            return []
        data = json.loads(result.stdout)
        if "error" in data:
            logger.warning("floop returned error: %s", data["error"])
            return []
        return data.get("active", data.get("behaviors", []))
    except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError) as exc:
        logger.warning("floop CLI unavailable or returned bad data: %s", exc)
        return []


def floop_available() -> bool:
    """Check if floop CLI is available on PATH."""
    try:
        result = subprocess.run(
            ["floop", "--version"], capture_output=True, text=True, timeout=5,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def init_store(store_path: Path) -> bool:
    """Initialize a floop behavior store if it doesn't exist."""
    if (store_path / ".floop").exists() or (store_path / "floop.db").exists():
        return True
    try:
        result = subprocess.run(
            ["floop", "init", "--root", str(store_path)],
            capture_output=True, text=True, timeout=10,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
