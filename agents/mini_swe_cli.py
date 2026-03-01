"""
Docker entrypoint for MiniSweAgent.

Reads task JSON from stdin, runs the agent, prints RunResult JSON to stdout.
All logging goes to stderr to keep stdout clean for structured output.

Usage (inside container):
    echo '{"problem_statement": "...", "model": "gemini/gemini-3-flash-preview", ...}' \
        | python -m agents.mini_swe_cli
"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path

# Suppress litellm's stdout noise (colored banners, debug info)
os.environ["LITELLM_LOG"] = "ERROR"

from agents.mini_swe import MiniSweAgent
from floop_integration.cli import count_behaviors, learn_from_transcript
from floop_integration.inject import build_floop_preamble, get_floop_context

import litellm
litellm.suppress_debug_info = True

# All logging to stderr so stdout stays clean JSON
logging.basicConfig(
    stream=sys.stderr,
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)


def main() -> None:
    raw = sys.stdin.read()
    try:
        task = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.error("Invalid JSON on stdin: %s", exc)
        sys.exit(1)

    problem_statement = task.get("problem_statement", "")
    model = task.get("model", "gemini/gemini-3-flash-preview")
    timeout = task.get("timeout", 300)
    floop_enabled = task.get("floop_enabled", False)
    floop_store = task.get("floop_store")

    if not problem_statement:
        logger.error("Missing problem_statement in input")
        sys.exit(1)

    # Phase 1: Guaranteed floop active (before agent run)
    floop_context = None
    behavior_count_before = 0
    store_path = None
    if floop_enabled and floop_store:
        store_path = Path(floop_store)
        # Symlink global store → volume so pack-installed behaviors are visible
        global_floop = Path.home() / ".floop"
        local_floop = store_path / ".floop"
        if local_floop.exists() and not global_floop.exists():
            global_floop.symlink_to(local_floop)
        behavior_count_before = count_behaviors(store_path, task_type="bug-fix")
        floop_context = get_floop_context(store_path, task_type="bug-fix")
        logger.info(
            "Floop context: %d chars, %d behaviors before",
            len(floop_context) if floop_context else 0,
            behavior_count_before,
        )

    # Phase 2: Agent run (unchanged)
    agent = MiniSweAgent(model=model)
    result = agent.run(
        problem_statement=problem_statement,
        repo_dir=Path("/workspace"),
        floop_context=floop_context,
        timeout=timeout,
    )

    # Phase 3: Fallback floop learn (after agent run)
    if floop_enabled and store_path and result.transcript:
        behavior_count_after = count_behaviors(store_path, task_type="bug-fix")
        if behavior_count_after <= behavior_count_before:
            logger.info("Agent didn't learn — extracting insight from transcript")
            learn_from_transcript(
                store_path, result.transcript, model, task_type="bug-fix",
            )
        else:
            logger.info(
                "Agent learned %d behavior(s) organically",
                behavior_count_after - behavior_count_before,
            )

    # Output result as JSON to stdout
    print(json.dumps(result.to_dict()))


if __name__ == "__main__":
    main()
