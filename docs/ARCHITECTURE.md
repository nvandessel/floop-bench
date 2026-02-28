# Architecture

## Data Flow

```
SWE-bench Verified (500 tasks)
    │
    ▼ sample 50, split 30/20
config/splits.json
    │
    ▼ orchestrator builds queue
harness/orchestrator.py
    │
    ▼ for each (task, arm):
harness/runner.py
    ├── git clone --bare → git worktree add
    ├── floop_integration/ → build prompt preamble (floop arms only)
    ├── agents/ → run agent in worktree
    └── git diff → capture patch
    │
    ▼ save
harness/db.py → SQLite (WAL mode)
results/predictions/*.jsonl
results/transcripts/
    │
    ▼ evaluate
harness/swebench_eval.py → SWE-bench Docker
    │
    ▼ import results
harness/db.py → resolved / unresolved
    │
    ▼ analyze
analysis/analyze.py → stats
analysis/charts.py → PNG/SVG
```

## Components

### harness/

| File | Role |
|------|------|
| `config.py` | Loads `arms.toml` and `splits.json`. Agent registry (lazy-loaded). |
| `db.py` | SQLite with WAL mode. Context-managed connections. PK is `(instance_id, arm)`. |
| `runner.py` | Repo checkout (bare clone + worktree), agent dispatch, diff capture, cleanup. |
| `orchestrator.py` | Click CLI. Phase dispatch, queue building, resume, budget guard. |
| `parallel.py` | ProcessPoolExecutor wrapper. Cost guard per task submission. |
| `swebench_eval.py` | Subprocess call to `swebench.harness.run_evaluation`. Imports results. |

### agents/

| File | Role |
|------|------|
| `base.py` | `RunResult` dataclass and `Agent` protocol (`name` + `run()`). |
| `mini_swe.py` | Litellm agent loop. Extracts bash blocks, executes, feeds output back. Stops on SUBMIT or step limit. |
| `claude_code.py` | Claude Code CLI wrapper (`claude -p`). Uses `--allowedTools` to control floop access. |

### floop_integration/

Two integration paths depending on agent:

- **mini_swe**: `cli.py` calls `floop active --json`, `inject.py` formats behaviors as a text preamble prepended to the prompt.
- **claude_code**: Floop is accessed via MCP tools. `--allowedTools` includes/excludes floop tool names per arm.

### analysis/

| File | Role |
|------|------|
| `analyze.py` | Resolve rates, bootstrap 95% CIs, McNemar's test, Cohen's h, gap closure. |
| `charts.py` | Grouped bar (resolve rates + CIs), cost scatter, cost per resolved. PNG + SVG. |

## Database Schema

```sql
CREATE TABLE runs (
    instance_id TEXT NOT NULL,
    arm TEXT NOT NULL,
    model TEXT NOT NULL,
    floop_enabled BOOLEAN NOT NULL,
    model_patch TEXT,
    resolved BOOLEAN,
    status TEXT NOT NULL,       -- "completed" | "timeout" | "error"
    duration_seconds REAL,
    input_tokens INTEGER,
    output_tokens INTEGER,
    cost_usd REAL,
    transcript_path TEXT,
    error_message TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (instance_id, arm)
);
```

## Resume

The orchestrator is safe to interrupt and re-run. `load_completed()` returns all `(instance_id, arm)` pairs with status `completed`, `timeout`, or `error`, and the queue skips them. `save_run()` uses `INSERT ... ON CONFLICT ... DO UPDATE` with `COALESCE` to preserve existing `resolved` values.

## Repo Isolation

Each task needs the repo at a specific commit:

1. **Bare clone** — one per repo in `work/repos/`, fetched once.
2. **Worktree** — one per task at the exact `base_commit`. Fast, fully isolated.
3. **Cleanup** — worktree removed after each run, stale refs pruned.

Parallel workers can run different tasks on the same repo without conflicts.

## Parallel Execution

```
Orchestrator
    ├── Worker 1 → own worktrees → SQLite
    ├── Worker 2 → own worktrees → SQLite
    ├── Worker 3 → own worktrees → SQLite
    └── Worker 4 → own worktrees → SQLite
```

Workers are processes (ProcessPoolExecutor). Each creates its own worktrees and DB connections. SQLite WAL mode handles concurrent writes. Cost guard is checked before submitting each task.
