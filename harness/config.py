"""Configuration loading for floop-bench arms and splits."""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib


@dataclass
class ArmConfig:
    """Configuration for one experimental arm."""

    name: str
    agent: str
    model: str
    floop: bool
    floop_store: str | None = None
    description: str = ""


def load_arms(config_path: Path | str = "config/arms.toml") -> dict[str, ArmConfig]:
    """Load arm configurations from TOML file."""
    path = Path(config_path)
    with open(path, "rb") as f:
        data = tomllib.load(f)

    config_dir = path.parent
    arms = {}
    for name, cfg in data.get("arms", {}).items():
        floop_store = cfg.get("floop_store")
        if floop_store:
            store_path = Path(floop_store)
            if not store_path.is_absolute():
                floop_store = str((config_dir / store_path).resolve())
        arms[name] = ArmConfig(
            name=name,
            agent=cfg["agent"],
            model=cfg["model"],
            floop=cfg.get("floop", False),
            floop_store=floop_store,
            description=cfg.get("description", ""),
        )
    return arms


def load_split(split_path: Path | str = "config/splits.json") -> dict:
    """Load train/eval split."""
    path = Path(split_path)
    with open(path) as f:
        return json.load(f)


AGENT_REGISTRY: dict[str, type] = {}


def register_agent(name: str, cls: type) -> None:
    """Register an agent class by name."""
    AGENT_REGISTRY[name] = cls


def get_agent_class(name: str) -> type:
    """Get agent class by name. Lazily imports to avoid circular deps."""
    if not AGENT_REGISTRY:
        from agents.mini_swe import MiniSweAgent

        register_agent("mini_swe", MiniSweAgent)
        try:
            from agents.claude_code import ClaudeCodeAgent

            register_agent("claude_code", ClaudeCodeAgent)
        except ImportError:
            pass

    if name not in AGENT_REGISTRY:
        raise ValueError(
            f"Unknown agent: {name}. Available: {list(AGENT_REGISTRY.keys())}"
        )
    return AGENT_REGISTRY[name]


def create_agent(arm: ArmConfig):
    """Create an agent instance from arm config."""
    cls = get_agent_class(arm.agent)
    return cls(model=arm.model)
