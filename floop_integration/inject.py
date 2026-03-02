"""Build floop context for prompt injection into agents."""

from __future__ import annotations

from pathlib import Path

from floop_integration.cli import get_active_behaviors


# ~500 tokens of plausible-but-useless software engineering text.
# Used as a control to test whether ANY extra prompt text hurts performance,
# independent of content quality.
PLACEBO_TEXT = """\
## Software Engineering Principles

Good software engineering practice involves several foundational principles that \
guide effective development. The DRY principle (Don't Repeat Yourself) suggests \
that every piece of knowledge should have a single, unambiguous representation \
within a system. This reduces the risk of inconsistencies and makes maintenance \
easier over time.

The SOLID principles provide a framework for object-oriented design. The Single \
Responsibility Principle states that a class should have only one reason to change. \
The Open/Closed Principle suggests that software entities should be open for \
extension but closed for modification. The Liskov Substitution Principle requires \
that objects of a superclass should be replaceable with objects of a subclass \
without affecting program correctness. The Interface Segregation Principle states \
that no client should be forced to depend on methods it does not use. The \
Dependency Inversion Principle suggests that high-level modules should not depend \
on low-level modules; both should depend on abstractions.

Code readability is often more important than cleverness. Clear variable names, \
consistent formatting, and well-structured functions make code easier to understand \
and maintain. Comments should explain why something is done, not what is done — \
the code itself should be clear enough to show the what.

Testing is an essential part of software development. Unit tests verify individual \
components in isolation, integration tests verify that components work together, \
and end-to-end tests verify the complete system. A good test suite gives developers \
confidence to refactor and extend code without fear of breaking existing functionality.

Version control systems like Git enable collaborative development by tracking \
changes over time. Meaningful commit messages, feature branches, and code reviews \
help maintain code quality and share knowledge across teams. Regular integration \
of changes reduces the risk of merge conflicts and integration problems."""


# Three focused behaviors addressing the specific failure modes observed in Run 7.
# No cadence instructions — just the behaviors themselves.
TOP3_BEHAVIORS = [
    {
        "kind": "behavior",
        "content": {
            "canonical": (
                "Locate the exact function mentioned in the traceback before "
                "editing any code — read the error to identify the right file "
                "and function."
            ),
        },
        "tags": ["bug-fix", "navigation"],
    },
    {
        "kind": "behavior",
        "content": {
            "canonical": (
                "Make the smallest possible change — a one-line fix is better "
                "than rewriting a block. Never copy-paste code between functions."
            ),
        },
        "tags": ["bug-fix", "minimal-diff"],
    },
    {
        "kind": "behavior",
        "content": {
            "canonical": (
                "After editing, verify your change by running: "
                "python -c 'import <module>'"
            ),
        },
        "tags": ["bug-fix", "verification"],
    },
]


def get_override_context(override: str) -> str | None:
    """Build context for a floop_context_override arm.

    Args:
        override: "placebo" for generic text, "top3" for focused behaviors

    Returns:
        Pre-built context string, or None if override is unrecognized
    """
    if override == "placebo":
        return PLACEBO_TEXT
    if override == "top3":
        return build_floop_preamble(TOP3_BEHAVIORS, include_cadence=False)
    return None


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
