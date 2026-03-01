"""Build floop context for prompt injection into agents."""

from __future__ import annotations

from pathlib import Path

from floop_integration.cli import get_active_behaviors

FLOOP_CLI_CADENCE = """\
## Floop — Learning & Recall System

You have access to `floop`, a tool that helps you learn from experience and recall relevant knowledge.

### At task start
Query for relevant behaviors before you begin work:
```bash
floop active --task bug-fix --json
```
Review the output and apply any relevant learned behaviors to your approach.

### When you learn something
When you discover an insight, a pattern, or correct a mistake, capture it:
```bash
floop learn --right "Always check for None before accessing .attribute in Django querysets" --wrong "Assumed queryset result was non-None without checking" --task bug-fix
```

### When to learn
- You found a non-obvious root cause
- You corrected an initial wrong approach
- You discovered a repo-specific pattern or convention
- You found a debugging technique that worked well

### Important
- Keep learned behaviors concise and generalizable (not instance-specific)
- Do NOT include specific file paths, variable names, or test cases from this task
- Focus on the transferable insight, not the specific fix"""


def build_floop_preamble(
    behaviors: list[dict], include_cadence: bool = True
) -> str:
    """
    Convert active behaviors into a text preamble for agent prompt injection.

    Args:
        behaviors: List of behavior dicts from floop CLI
        include_cadence: Whether to include floop CLI usage instructions

    Returns:
        Formatted preamble string to prepend to agent prompts
    """
    parts = []

    if include_cadence:
        parts.append(FLOOP_CLI_CADENCE)

    if behaviors:
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

        parts.append("\n".join(lines))

    return "\n\n".join(parts)


def get_floop_context(
    store_path: Path, task_type: str | None = None
) -> str | None:
    """
    Get floop context for prompt injection.

    Returns preamble with CLI cadence + any active behaviors.
    Returns None only if called with no store (should not happen for floop arms).
    """
    behaviors = get_active_behaviors(store_path, task_type=task_type)
    return build_floop_preamble(behaviors, include_cadence=True)
