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
    ├── git clone --bare → git worktree add (isolated checkout)
    ├── floop_integration/ → build prompt preamble (if floop arm)
    ├── agents/ → run agent in worktree
    └── git diff → capture patch
    │
    ▼ save results
harness/db.py (SQLite, WAL mode)
results/predictions/*.jsonl
results/transcripts/
    │
    ▼ evaluate patches
harness/swebench_eval.py → SWE-bench Docker
    │
    ▼ import resolved/unresolved
harness/db.py
    │
    ▼ analyze
analysis/analyze.py → stats, CIs, McNemar's
analysis/charts.py → PNG/SVG
```

## Components

### harness/

- **config.py** — Loads `config/arms.toml` and `config/splits.json`. Maintains an agent registry mapping agent names to classes (lazy-loaded to avoid circular imports).
- **db.py** — SQLite with WAL mode for concurrent writes. Thread-local connections. Primary key is `(instance_id, arm)`. Uses `INSERT OR REPLACE` for upserts.
- **runner.py** — Repo checkout via bare clone + worktree (fast, COW on Linux). Creates the agent, runs it, captures `git diff` against the base commit. Cleans up worktrees after each run.
- **orchestrator.py** — Click CLI. Dispatches phases (smoke=2 tasks, train=30, eval=20x3 arms). Builds a shuffled queue, skips completed pairs, enforces budget. Prints live progress via Rich.
- **parallel.py** — ProcessPoolExecutor wrapper. Each worker runs tasks sequentially in its own worktrees. Cost guard checked before each task submission.
- **swebench_eval.py** — Invokes `python -m swebench.harness.run_evaluation` as a subprocess. Searches for the report JSON and imports resolved/unresolved status back into SQLite.

### agents/

- **base.py** — `RunResult` dataclass (all metrics for one run) and `Agent` protocol (just `name` + `run()`). Any class implementing this protocol can be used as an agent.
- **mini_swe.py** — Litellm-based agent loop. Sends the problem, extracts ```` ```bash ```` blocks from the response, executes them via subprocess, feeds output back. Stops on "SUBMIT" or step limit (30). Works with any litellm-supported model.
- **claude_code.py** — Wraps `claude -p` CLI. Uses `--allowedTools` to control floop access per arm. Parses JSON output for cost/token metrics.

### floop_integration/

Two paths depending on the agent:

- **For mini_swe:** `cli.py` calls `floop active --json` to get behaviors, `inject.py` formats them as a text preamble prepended to the prompt.
- **For claude_code:** Floop is accessed via MCP tools, controlled by `--allowedTools` on the CLI invocation. Bare arms exclude floop tool names.

### analysis/

- **analyze.py** — Resolve rates with bootstrap 95% CIs, gap closure metric, McNemar's test for paired binary outcomes, Cohen's h effect size.
- **charts.py** — Three chart types: grouped bar (resolve rates + CIs), cost-performance scatter, cost per resolved task. Outputs PNG and SVG.

## SQLite Schema

```sql
CREATE TABLE runs (
    instance_id TEXT NOT NULL,
    arm TEXT NOT NULL,
    model TEXT NOT NULL,
    floop_enabled BOOLEAN NOT NULL,
    model_patch TEXT,
    resolved BOOLEAN,           -- filled after SWE-bench evaluation
    status TEXT NOT NULL,        -- "completed" | "timeout" | "error"
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

WAL mode and `busy_timeout=5000` enable safe concurrent writes from parallel workers.

## Resume Logic

The orchestrator is safe to interrupt and re-run:

1. `load_completed()` reads all `(instance_id, arm)` pairs with status `completed` or `timeout`
2. Queue is rebuilt each time, skipping completed pairs
3. `INSERT OR REPLACE` in `save_run()` means re-running a failed task overwrites the previous result

## Repo Isolation

Each task needs the repo at a specific commit. The strategy:

1. **Bare clone** — one per repo, stored in `work/repos/`. Fetched once.
2. **Worktree** — one per task, created at the exact `base_commit`. Fast (no full copy). Fully isolated from other tasks.
3. **Cleanup** — worktree is removed after each run. `git worktree prune` cleans stale references.

This means parallel workers can run different tasks on the same repo without conflicts.

## Parallel Execution

```
Orchestrator
    │
    ├── Worker 1: task A (django worktree) → SQLite
    ├── Worker 2: task B (sympy worktree) → SQLite
    ├── Worker 3: task C (django worktree) → SQLite
    └── Worker 4: task D (flask worktree) → SQLite
```

- Workers are processes (ProcessPoolExecutor), not threads
- Each worker creates its own worktrees and DB connections
- SQLite WAL mode handles concurrent writes
- Cost guard is checked before submitting each new task
