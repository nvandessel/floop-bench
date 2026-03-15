"""
Claude Code CLI agent wrapper.

Runs `claude -p` with proper isolation and captures results.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

from agents.base import RunResult


class ClaudeCodeAgent:
    """Agent that wraps the Claude Code CLI."""

    name = "claude_code"

    def __init__(self, model: str = "claude-sonnet-4-5-20250929"):
        self.model = model

    def run(
        self,
        problem_statement: str,
        repo_dir: Path,
        floop_context: str | None,
        timeout: int,
    ) -> RunResult:
        # Build prompt
        prompt = self._build_prompt(problem_statement, floop_context)

        # Build command
        cmd = [
            "claude",
            "-p",
            prompt,
            "--output-format",
            "json",
            "--model",
            self.model,
            "--max-turns",
            "25",
        ]

        # Tool access — floop tools only for floop-enabled arms
        base_tools = "Edit,Read,Write,Bash,Grep"
        if floop_context is not None:
            floop_tools = (
                ",mcp__floop__floop_active"
                ",mcp__floop__floop_learn"
                ",mcp__floop__floop_feedback"
            )
            cmd += ["--allowedTools", base_tools + floop_tools]
        else:
            cmd += ["--allowedTools", base_tools]

        env = {**os.environ, "MAX_THINKING_TOKENS": "8000"}

        start = time.monotonic()
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                cwd=str(repo_dir),
                timeout=timeout,
                env=env,
            )
            duration = time.monotonic() - start
        except subprocess.TimeoutExpired:
            return RunResult(
                instance_id="",
                arm="",
                model_patch="",
                model=self.model,
                floop_enabled=floop_context is not None,
                status="timeout",
                duration_seconds=float(timeout),
                input_tokens=0,
                output_tokens=0,
                cost_usd=0,
            )

        # Parse metrics from Claude Code JSON output
        metrics = self._parse_output(result.stdout)

        status = "completed" if result.returncode == 0 else "error"
        error_msg = result.stderr[:500] if result.returncode != 0 else None

        return RunResult(
            instance_id="",
            arm="",
            model_patch="",  # filled by runner via git diff base_commit
            model=self.model,
            floop_enabled=floop_context is not None,
            status=status,
            duration_seconds=duration,
            input_tokens=metrics.get("input_tokens", 0),
            output_tokens=metrics.get("output_tokens", 0),
            cost_usd=metrics.get("cost", 0.0),
            error_message=error_msg,
        )

    def _build_prompt(self, problem_statement: str, floop_context: str | None) -> str:
        preamble = ""
        if floop_context:
            preamble = (
                "Before starting, call the floop_active tool to check for "
                "learned behaviors relevant to this codebase or task type.\n\n"
            )

        return (
            f"{preamble}A bug has been reported in this project:\n\n"
            f"---\n{problem_statement}\n---\n\n"
            "Fix this bug by editing the source code.\n\n"
            "Rules:\n"
            "- Do NOT modify or add test files\n"
            "- Only edit existing source files\n"
            "- Keep changes minimal\n"
            "- Verify by running relevant tests if possible"
        )

    def _parse_output(self, raw: str) -> dict:
        """Parse Claude Code JSON output for metrics."""
        try:
            data = json.loads(raw)
            # Claude Code JSON structure — verify empirically
            return {
                "input_tokens": data.get("usage", {}).get("input_tokens", 0),
                "output_tokens": data.get("usage", {}).get("output_tokens", 0),
                "cost": data.get("usage", {}).get("cost", 0.0),
            }
        except (json.JSONDecodeError, KeyError, TypeError):
            return {}
