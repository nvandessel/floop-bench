"""Agent protocol for floop-bench. Any agent that conforms to this protocol can be used."""

from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Protocol, runtime_checkable


@dataclass
class RunResult:
    """Result of running an agent on a single SWE-bench task."""

    instance_id: str
    arm: str
    model_patch: str  # git diff
    model: str
    floop_enabled: bool
    status: str  # "completed" | "timeout" | "error"
    duration_seconds: float
    input_tokens: int
    output_tokens: int
    cost_usd: float
    transcript_path: str | None = None
    error_message: str | None = None
    transcript: list[dict] | None = None  # agent conversation for fallback learning

    def to_prediction(self) -> dict:
        """Format as SWE-bench prediction JSONL entry."""
        return {
            "instance_id": self.instance_id,
            "model_name_or_path": self.model,
            "model_patch": self.model_patch,
        }

    def to_dict(self) -> dict:
        d = asdict(self)
        d.pop("transcript", None)  # never serialize transcript
        return d


@runtime_checkable
class Agent(Protocol):
    """Protocol that all agents must conform to."""

    name: str

    def run(
        self,
        problem_statement: str,
        repo_dir: Path,
        floop_context: str | None,
        timeout: int,
    ) -> RunResult: ...
