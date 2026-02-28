"""Build floop context for prompt injection into agents."""

from __future__ import annotations

from pathlib import Path

from floop_integration.cli import get_active_behaviors


def build_floop_preamble(behaviors: list[dict]) -> str:
    """
    Convert active behaviors into a text preamble for agent prompt injection.

    Args:
        behaviors: List of behavior dicts from floop CLI

    Returns:
        Formatted preamble string to prepend to agent prompts
    """
    if not behaviors:
        return ""

    lines = [
        "## Learned Behaviors\n",
        "Apply these learned behaviors when relevant:\n",
    ]

    for b in behaviors:
        kind = b.get("kind", b.get("type", "behavior"))
        content = b.get("content", {})
        if isinstance(content, dict):
            text = content.get(
                "canonical", content.get("description", str(content))
            )
        else:
            text = str(content)

        tags = b.get("tags", [])
        tag_str = f" [{', '.join(tags)}]" if tags else ""
        lines.append(f"- [{kind}]{tag_str} {text}")

    return "\n".join(lines)


def get_floop_context(
    store_path: Path, context: str | None = None
) -> str | None:
    """
    Get floop context for prompt injection.

    Returns empty string if floop enabled but no active behaviors.
    Returns None only if called with no store (should not happen for floop arms).
    """
    behaviors = get_active_behaviors(store_path, context=context)
    if not behaviors:
        return ""
    return build_floop_preamble(behaviors)
