"""Floop CLI subprocess wrapper for agent-agnostic behavior injection."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path


def get_active_behaviors(
    store_path: Path, context: str | None = None
) -> list[dict]:
    """
    Get active behaviors from floop store via CLI.

    Args:
        store_path: Path to floop behavior store
        context: Optional context string for activation filtering

    Returns:
        List of behavior dicts with keys like 'kind', 'content', 'tags'
    """
    cmd = ["floop", "active", "--json", "--root", str(store_path)]
    if context:
        cmd.extend(["--context", context])

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            return []
        data = json.loads(result.stdout)
        return data.get("active", data.get("behaviors", []))
    except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError):
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
