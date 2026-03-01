"""
Mini SWE-agent: a minimal coding agent using litellm.

Loop:
1. Send problem statement + repo context to model
2. Model responds with bash commands in ```bash blocks
3. Execute commands via subprocess
4. Feed output back to model
5. Repeat until model outputs SUBMIT or step/cost limit reached

The runner captures the git diff after the agent finishes.
"""

from __future__ import annotations

import logging
import re
import subprocess
import time
from pathlib import Path

import litellm

from agents.base import RunResult

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a software engineer fixing a bug in a repository.

You have access to a bash shell. To run commands, write them in a ```bash code block.
You will see the output and can run more commands.

Rules:
- Do NOT modify or add test files
- Only edit existing source files
- Keep changes minimal
- When you're done fixing the bug, output the exact word SUBMIT on its own line

Example:
```bash
find . -name "*.py" | head -20
```"""

MAX_STEPS = 30
MAX_OUTPUT_CHARS = 8000
API_TIMEOUT = 60  # seconds per litellm call


def _extract_bash_blocks(text: str) -> list[str]:
    """Extract bash code blocks from model response."""
    pattern = r"```(?:bash|sh|shell)\n(.*?)```"
    return re.findall(pattern, text, re.DOTALL)


def _run_command(cmd: str, cwd: Path, timeout: int = 60) -> str:
    """Run a shell command and return output."""
    try:
        result = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            cwd=str(cwd),
            timeout=timeout,
        )
        output = result.stdout + result.stderr
    except subprocess.TimeoutExpired:
        output = "[Command timed out after 60s]"
    if len(output) > MAX_OUTPUT_CHARS:
        output = output[:MAX_OUTPUT_CHARS] + "\n[...truncated]"
    return output


class MiniSweAgent:
    """Minimal SWE-agent using litellm for any model."""

    name = "mini_swe"

    def __init__(self, model: str = "anthropic/claude-haiku-4-5-20251001"):
        self.model = model

    def run(
        self,
        problem_statement: str,
        repo_dir: Path,
        floop_context: str | None,
        timeout: int,
    ) -> RunResult:
        start = time.monotonic()
        total_input_tokens = 0
        total_output_tokens = 0
        total_cost = 0.0

        # Build initial messages
        user_content = (
            "A bug has been reported in this project:\n\n"
            f"---\n{problem_statement}\n---\n\n"
            "Fix this bug by editing the source code."
        )
        if floop_context:
            user_content = floop_context + "\n\n" + user_content

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ]

        status = "completed"
        error_message = None

        try:
            api_retries = 0
            max_api_retries = 3
            for step in range(MAX_STEPS):
                elapsed = time.monotonic() - start
                if elapsed >= timeout:
                    status = "timeout"
                    break

                try:
                    response = litellm.completion(
                        model=self.model,
                        messages=messages,
                        max_tokens=4096,
                        timeout=API_TIMEOUT,
                    )
                except Exception as api_exc:
                    api_retries += 1
                    logger.warning(
                        "API call failed (attempt %d/%d): %s",
                        api_retries, max_api_retries, api_exc,
                    )
                    if api_retries >= max_api_retries:
                        raise
                    continue

                api_retries = 0  # reset on success
                usage = response.usage
                if usage:
                    total_input_tokens += usage.prompt_tokens or 0
                    total_output_tokens += usage.completion_tokens or 0

                try:
                    cost = litellm.completion_cost(response)
                    total_cost += cost
                except Exception as exc:
                    logger.warning("Failed to compute cost for %s: %s", self.model, exc)

                assistant_text = response.choices[0].message.content or ""
                messages.append({"role": "assistant", "content": assistant_text})

                if "SUBMIT" in assistant_text:
                    break

                bash_blocks = _extract_bash_blocks(assistant_text)
                if not bash_blocks:
                    messages.append(
                        {
                            "role": "user",
                            "content": (
                                "Please provide a bash command to investigate or fix "
                                "the issue, or output SUBMIT if you're done."
                            ),
                        }
                    )
                    continue

                all_output = []
                for cmd in bash_blocks:
                    output = _run_command(cmd.strip(), repo_dir)
                    all_output.append(f"$ {cmd.strip()}\n{output}")

                combined = "\n\n".join(all_output)
                messages.append({"role": "user", "content": combined})

        except Exception as e:
            status = "error"
            error_message = str(e)

        duration = time.monotonic() - start

        return RunResult(
            instance_id="",  # filled by caller
            arm="",  # filled by caller
            model_patch="",  # filled by runner via git diff base_commit
            model=self.model,
            floop_enabled=floop_context is not None,
            status=status,
            duration_seconds=duration,
            input_tokens=total_input_tokens,
            output_tokens=total_output_tokens,
            cost_usd=total_cost,
            error_message=error_message,
            transcript=messages,
        )
