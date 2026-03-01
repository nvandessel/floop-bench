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

    # Build floop context if enabled
    floop_context = None
    if floop_enabled and floop_store:
        store_path = Path(floop_store)
        floop_context = get_floop_context(store_path, task_type="bug-fix")
        logger.info(
            "Floop context: %d chars",
            len(floop_context) if floop_context else 0,
        )

    agent = MiniSweAgent(model=model)
    result = agent.run(
        problem_statement=problem_statement,
        repo_dir=Path("/workspace"),
        floop_context=floop_context,
        timeout=timeout,
    )

    # Output result as JSON to stdout
    print(json.dumps(result.to_dict()))


if __name__ == "__main__":
    main()
