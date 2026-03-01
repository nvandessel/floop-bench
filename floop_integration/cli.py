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


def count_behaviors(store_path: Path, task_type: str | None = None) -> int:
    """Count active behaviors in the floop store."""
    behaviors = get_active_behaviors(store_path, task_type=task_type)
    return len(behaviors)


def _compress_transcript(transcript: list[dict], max_chars: int = 4000) -> str:
    """Truncate transcript to fit in a cheap model's context window."""
    parts = []
    for msg in transcript:
        role = msg.get("role", "unknown")
        content = msg.get("content", "")
        parts.append(f"[{role}] {content}")
    full = "\n---\n".join(parts)
    if len(full) > max_chars:
        full = full[:max_chars] + "\n[...truncated]"
    return full


def _extract_insight(compressed: str, model: str) -> dict | None:
    """Call LLM to extract a generalizable insight from a transcript.

    Returns {"right": "...", "wrong": "..."} or None if nothing learned.
    """
    import litellm

    prompt = (
        "You are analyzing a software engineering agent's work transcript.\n"
        "Extract ONE generalizable insight the agent learned (or should have learned).\n\n"
        "Rules:\n"
        "- The insight must be transferable to other bug-fix tasks\n"
        "- Do NOT include instance-specific details (file paths, variable names, test names)\n"
        "- Focus on the pattern, not the specific fix\n"
        "- If the agent didn't learn anything useful, respond with exactly: NOTHING\n\n"
        "Respond with ONLY a JSON object:\n"
        '{"right": "what to do (the correct approach)", "wrong": "what not to do (the mistake)"}\n\n'
        f"Transcript:\n{compressed}"
    )

    try:
        response = litellm.completion(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=256,
            timeout=30,
        )
        text = response.choices[0].message.content or ""
        if "NOTHING" in text:
            return None
        # Extract JSON from response
        data = json.loads(text.strip().strip("`").strip())
        if "right" in data and "wrong" in data:
            return data
        return None
    except Exception as exc:
        logger.warning("Insight extraction failed: %s", exc)
        return None


def learn_from_transcript(
    store_path: Path,
    transcript: list[dict],
    model: str,
    task_type: str | None = None,
) -> bool:
    """Extract insight from transcript and teach it to floop.

    Returns True if a behavior was learned, False otherwise.
    """
    compressed = _compress_transcript(transcript)
    insight = _extract_insight(compressed, model)
    if not insight:
        logger.info("No insight extracted from transcript")
        return False

    cmd = [
        "floop", "learn",
        "--right", insight["right"],
        "--wrong", insight["wrong"],
        "--scope", "local",  # persist to --root store, not ephemeral ~/.floop
        "--root", str(store_path),
    ]
    if task_type:
        cmd.extend(["--task", task_type])

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            logger.info("Fallback learn succeeded: %s", insight["right"][:80])
            return True
        else:
            logger.warning(
                "floop learn failed (exit %d): %s",
                result.returncode, result.stderr.strip(),
            )
            return False
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        logger.warning("floop learn failed: %s", exc)
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
