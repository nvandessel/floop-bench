# Adding New Agents

To add a new agent to floop-bench:

1. Create a new file in `agents/` (e.g., `agents/my_agent.py`)
2. Implement a class that conforms to the `Agent` protocol in `agents/base.py`:

```python
from pathlib import Path
from agents.base import Agent, RunResult

class MyAgent:
    name = "my_agent"

    def run(self, problem_statement: str, repo_dir: Path,
            floop_context: str | None, timeout: int) -> RunResult:
        # Your implementation here
        ...
```

3. Register it in `harness/config.py` by adding to `AGENT_REGISTRY`

## Available Agents

- **mini_swe** — Minimal SWE-agent using litellm (any model)
- **claude_code** — Claude Code CLI wrapper
