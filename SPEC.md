# floop-bench: Benchmark Harness Spec (v3 — Final)

## Mission

Measure whether floop closes the performance gap between a cheap model and an
expensive model on real software engineering tasks. Headline result:

**"Haiku + floop closes X% of the gap with Sonnet on SWE-bench at Y% of the cost."**

## Stack

| Component | Choice | Why |
|-----------|--------|-----|
| **Benchmark** | SWE-bench Verified (50 sampled tasks) | Pre-validated by humans, 50 sampled from 500 for budget |
| **Agent** | Claude Code CLI via `claude -p` | Native MCP support = native floop integration |
| **Auth** | Anthropic API key (pay-per-token) | Precise cost tracking, no TOS gray area, no rate limits |
| **Evaluation** | SWE-bench Docker harness | Gold-standard pass/fail, fully reproducible |
| **Small model** | Haiku 4.5 ($1/$5 per MTok) | The "cheap" model we're boosting with floop |
| **Large model** | Sonnet 4.5 ($3/$15 per MTok) | The "expensive" ceiling we're chasing |
| **Floop** | MCP tools via Claude Code | The intervention being tested |

## Budget: ~$50

| Phase | What | Runs | Est. Cost |
|-------|------|------|-----------|
| Build harness | Use Max subscription | 0 | $0 |
| Smoke test | API key, 2 tasks | ~4 | ~$2 |
| Training phase | Haiku bare on 30 train tasks | 30 | ~$6 |
| Eval: Haiku bare | 20 eval tasks | 20 | ~$4 |
| Eval: Haiku + floop | 20 eval tasks | 20 | ~$5 |
| Eval: Sonnet bare | 20 eval tasks | 20 | ~$20 |
| Debugging/retries buffer | | | ~$10 |
| **Total** | | **~114 runs** | **~$47** |

Cost assumptions: Haiku ~$0.20/task, Sonnet ~$1.00/task with prompt caching.
Real costs may be lower — SWE-rebench reports Opus at $0.72/task with caching.

### Cost Controls

```bash
# Reduce thinking tokens from default 32K to 8K (saves ~40% on output tokens)
export MAX_THINKING_TOKENS=8000

# Monitor spend
# After each run, Claude Code reports cost via --output-format json
# The harness tracks cumulative spend and can halt at a threshold
```

---

## Architecture

```
floop-bench/
├── pyproject.toml                # uv project, deps: click, rich, datasets, matplotlib, scipy
├── config/
│   ├── arms.toml                 # Model + floop config per arm
│   └── splits.json               # Train/eval task ID split (generated once, frozen)
├── harness/
│   ├── runner.py                 # Runs Claude Code on one task, captures git diff
│   ├── orchestrator.py           # Loops tasks × arms, resume support, cost tracking
│   ├── db.py                     # SQLite operations
│   └── swebench_eval.py          # Invokes SWE-bench Docker evaluation, imports results
├── behaviors/
│   ├── store/                    # Trained floop behavior store (the intervention)
│   └── README.md                 # Documents what's in the store and how it was built
├── results/
│   ├── results.db                # All run data
│   ├── predictions/              # JSONL files per arm (SWE-bench input format)
│   └── transcripts/              # Raw Claude Code output per run
├── analysis/
│   ├── analyze.py                # Pass rates, gap closure, McNemar's test, CIs
│   └── charts.py                 # Grouped bar chart, cost-performance scatter
├── scripts/
│   ├── generate_split.py         # One-time: split 50 Mini tasks into 30 train / 20 eval
│   ├── validate_harness.py       # Smoke test — the Ralph loop target
│   ├── check_leakage.py          # Audit behavior store for eval-task-specific content
│   └── estimate_cost.py          # Dry-run: estimate cost from prior run data
└── ralph.sh                      # Ralph loop runner
```

---

## Experimental Design

### Arms (3)

```toml
# config/arms.toml

[arms.sonnet_bare]
model = "claude-sonnet-4-5-20250929"
floop = false
description = "Sonnet without floop — performance ceiling"

[arms.haiku_bare]
model = "claude-haiku-4-5-20251001"
floop = false
description = "Haiku without floop — performance floor"

[arms.haiku_floop]
model = "claude-haiku-4-5-20251001"
floop = true
floop_store = "behaviors/store"
description = "Haiku with trained floop behaviors — the treatment"
```

We intentionally omit two arms from earlier iterations to stay within budget:
- ~~haiku_random (placebo)~~ — nice to have but not essential for the headline
- ~~sonnet_floop (bonus)~~ — interesting but doubles the Sonnet cost

These can be added in a follow-up if initial results are promising.

### Dataset: SWE-bench Verified Mini

- **50 tasks**, random subset of SWE-bench Verified
- Pre-validated by humans as solvable
- Available on HuggingFace: `princeton-nlp/SWE-bench_Verified`
- Each task: issue description, base commit, repo, ground truth patch, test suite in Docker

```python
from datasets import load_dataset
ds = load_dataset("princeton-nlp/SWE-bench_Verified", split="test")
print(f"{len(ds)} tasks loaded")
```

### Train/Eval Split

**30 train / 20 eval**, generated once and frozen.

```python
# scripts/generate_split.py
"""
Split the 50 Mini tasks into 30 train + 20 eval.

Rules:
- Stratify by repo so both splits contain tasks from the same repos where possible
- Fixed random seed (42), recorded in splits.json metadata
- NEVER regenerate after the training phase begins

Output: config/splits.json
{
  "seed": 42,
  "train": ["django__django-11099", ...],   // 30 task IDs
  "eval": ["sympy__sympy-20590", ...]       // 20 task IDs
}
"""
```

20 eval tasks is small. Be honest about this in the writeup — it's a pilot-scale study.
With 20 tasks, McNemar's test can detect ~25% absolute differences at p<0.05.

---

## Implementation Details

### 1. Claude Code Runner (`harness/runner.py`)

Core function: given a SWE-bench instance and an arm config, invoke Claude Code,
let it edit files, capture the resulting git diff.

```python
import subprocess, json, time, os, shutil
from pathlib import Path
from dataclasses import dataclass

@dataclass
class RunResult:
    instance_id: str
    arm: str
    model_patch: str        # git diff (the SWE-bench prediction)
    model: str
    floop_enabled: bool
    status: str             # "completed" | "timeout" | "error"
    duration_seconds: float
    input_tokens: int
    output_tokens: int
    cost_usd: float
    transcript_path: str | None = None
    error_message: str | None = None


def run_task(instance: dict, arm: dict, work_dir: Path,
             transcript_dir: Path, timeout: int = 300) -> RunResult:
    """
    Run Claude Code on a single SWE-bench task.

    1. Checkout repo at base_commit in an isolated directory
    2. Invoke `claude -p` with the issue as prompt
    3. Capture git diff as the patch
    4. Parse Claude Code JSON output for cost/token metrics
    """
    instance_id = instance["instance_id"]
    base_commit = instance["base_commit"]
    model = arm["model"]
    use_floop = arm.get("floop", False)

    # --- Repo checkout ---
    # Clone into work_dir if not exists, then create a worktree or temp copy
    # at the exact base_commit. Each run MUST be isolated.
    task_dir = setup_repo(instance, work_dir)

    # --- Build prompt ---
    prompt = build_prompt(instance["problem_statement"], use_floop)

    # --- Build command ---
    cmd = ["claude", "-p", prompt, "--output-format", "json", "--model", model]

    # Control max agent turns to cap cost
    cmd += ["--max-turns", "25"]

    # Tool access: same for all arms except floop tools
    base_tools = "Edit,Read,Write,Bash,Grep"
    if use_floop:
        floop_tools = ",mcp__floop__floop_active,mcp__floop__floop_learn,mcp__floop__floop_feedback"
        cmd += ["--allowedTools", base_tools + floop_tools]
    else:
        cmd += ["--allowedTools", base_tools]

    env = {**os.environ, "MAX_THINKING_TOKENS": "8000"}

    # --- Execute ---
    start = time.monotonic()
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True,
            cwd=str(task_dir), timeout=timeout, env=env
        )
        duration = time.monotonic() - start
    except subprocess.TimeoutExpired:
        return RunResult(
            instance_id=instance_id, arm=arm["name"],
            model_patch="", model=model, floop_enabled=use_floop,
            status="timeout", duration_seconds=timeout,
            input_tokens=0, output_tokens=0, cost_usd=0
        )

    # --- Capture patch ---
    diff = subprocess.run(
        ["git", "diff", base_commit],
        capture_output=True, text=True, cwd=str(task_dir)
    ).stdout

    # --- Parse metrics from Claude Code JSON ---
    metrics = parse_claude_json(result.stdout)

    # --- Save transcript ---
    transcript_path = transcript_dir / f"{instance_id}_{arm['name']}.json"
    transcript_path.write_text(result.stdout)

    # --- Cleanup worktree ---
    cleanup_repo(task_dir)

    return RunResult(
        instance_id=instance_id, arm=arm["name"],
        model_patch=diff, model=model, floop_enabled=use_floop,
        status="completed", duration_seconds=duration,
        input_tokens=metrics.get("input_tokens", 0),
        output_tokens=metrics.get("output_tokens", 0),
        cost_usd=metrics.get("cost", 0.0),
        transcript_path=str(transcript_path)
    )


def build_prompt(problem_statement: str, use_floop: bool) -> str:
    """
    Task prompt. IDENTICAL across arms except floop preamble.
    """
    preamble = ""
    if use_floop:
        preamble = (
            "Before starting, call the floop_active tool to check for "
            "learned behaviors relevant to this codebase or task type.\n\n"
        )
    return f"""{preamble}A bug has been reported in this project:

---
{problem_statement}
---

Fix this bug by editing the source code.

Rules:
- Do NOT modify or add test files
- Only edit existing source files
- Keep changes minimal
- Verify by running relevant tests if possible"""


def parse_claude_json(raw: str) -> dict:
    """
    IMPORTANT: The actual JSON structure from `claude -p --output-format json`
    must be verified empirically. Run this FIRST:

        claude -p "Say hello" --output-format json 2>/dev/null | python -m json.tool

    Then update this parser to match the real fields.
    """
    try:
        data = json.loads(raw)
        # These field paths are GUESSES — verify and fix
        return {
            "input_tokens": data.get("usage", {}).get("input_tokens", 0),
            "output_tokens": data.get("usage", {}).get("output_tokens", 0),
            "cost": data.get("usage", {}).get("cost", 0.0),
        }
    except (json.JSONDecodeError, KeyError, TypeError):
        return {}
```

### CRITICAL FIRST STEP: Verify Claude Code CLI

Before writing ANY harness code, run these commands and inspect the output:

```bash
# 1. What flags exist?
claude --help

# 2. What does JSON output look like?
claude -p "Say hello" --output-format json 2>/dev/null | python -m json.tool

# 3. Does --model work?
claude --model claude-haiku-4-5-20251001 -p "What model are you?" --output-format json

# 4. Does --allowedTools work? What's the syntax?
claude -p "List files in ." --allowedTools "Read,Bash" --output-format json

# 5. Does --max-turns exist?
claude -p "Say hello" --max-turns 1 --output-format json

# 6. Are floop MCP tools visible?
claude -p "List your available tools" --output-format json
```

**Adapt all flag names and JSON parsing to match real output.**
**Do NOT proceed until steps 1-6 produce expected results.**

### 2. Repo Checkout Strategy

Each task needs an isolated copy of the repo at the correct commit.

```python
def setup_repo(instance: dict, base_dir: Path) -> Path:
    """
    Create an isolated checkout for one task run.

    Strategy: maintain one bare clone per repo, create worktrees per task.
    Worktrees are fast (COW on Linux) and fully isolated.
    """
    repo_slug = instance["repo"].replace("/", "__")  # e.g. "django__django"
    bare_path = base_dir / "repos" / repo_slug

    if not bare_path.exists():
        # First time seeing this repo — clone it
        subprocess.run([
            "git", "clone", "--bare",
            f"https://github.com/{instance['repo']}.git",
            str(bare_path)
        ], check=True)

    # Create worktree at the exact commit
    task_dir = base_dir / "worktrees" / instance["instance_id"]
    if task_dir.exists():
        shutil.rmtree(task_dir)

    subprocess.run([
        "git", "-C", str(bare_path), "worktree", "add",
        str(task_dir), instance["base_commit"]
    ], check=True)

    # Install dependencies if needed
    # SWE-bench instances have hints, but for Claude Code we let the agent
    # figure it out (it has Bash access). This matches real usage.

    return task_dir


def cleanup_repo(task_dir: Path):
    """Remove worktree after run."""
    # Get the bare repo path to properly remove the worktree reference
    shutil.rmtree(task_dir, ignore_errors=True)
    # Also: git worktree prune on the bare repo
```

### 3. Orchestrator (`harness/orchestrator.py`)

```python
"""
Main loop. Modes:
  --phase smoke     2 tasks, haiku_bare only (validate harness)
  --phase train     30 train tasks, haiku_bare only (generate training data)
  --phase eval      20 eval tasks × 3 arms (the actual experiment)

Features:
  - Resume: skips (instance_id, arm) pairs already in results.db
  - Shuffled queue: interleaves tasks and arms to avoid ordering bias
  - Live progress: prints running pass rate per arm
  - Cost guard: halts if cumulative spend exceeds --budget (default $55)
"""

import random, sys
from harness.runner import run_task, RunResult
from harness.db import save_run, load_completed, get_total_cost

def run_experiment(phase: str, budget: float = 55.0):
    dataset = load_mini_dataset()
    split = load_split("config/splits.json")
    arms = load_arms("config/arms.toml")

    # Select tasks and arms for this phase
    if phase == "smoke":
        task_ids = split["train"][:2]
        active_arms = [arms["haiku_bare"]]
    elif phase == "train":
        task_ids = split["train"]
        active_arms = [arms["haiku_bare"]]
    elif phase == "eval":
        task_ids = split["eval"]
        active_arms = [arms["sonnet_bare"], arms["haiku_bare"], arms["haiku_floop"]]
    else:
        sys.exit(f"Unknown phase: {phase}")

    # Build queue, skip completed
    completed = load_completed()
    queue = []
    for tid in task_ids:
        for arm in active_arms:
            if (tid, arm["name"]) not in completed:
                queue.append((tid, arm))

    random.seed(42)
    random.shuffle(queue)

    print(f"Phase: {phase} | {len(queue)} runs queued | Budget: ${budget}")

    for i, (tid, arm) in enumerate(queue):
        # Cost guard
        spent = get_total_cost()
        if spent >= budget:
            print(f"⚠️  Budget exhausted (${spent:.2f} >= ${budget}). Stopping.")
            break

        instance = dataset_lookup[tid]
        print(f"[{i+1}/{len(queue)}] {tid} / {arm['name']} (${spent:.2f} spent)")

        result = run_task(instance, arm, work_dir=..., transcript_dir=...)
        save_run(result)

        # Write SWE-bench prediction JSONL
        append_prediction(result, f"results/predictions/{arm['name']}.jsonl")

        print(f"  → {result.status} | {result.duration_seconds:.0f}s | ${result.cost_usd:.3f}")

        time.sleep(3)  # Brief courtesy delay

    print_summary()
```

### 4. SWE-bench Evaluation (`harness/swebench_eval.py`)

After all runs for an arm complete, evaluate patches:

```python
def evaluate_arm(arm_name: str):
    """
    Run SWE-bench's Docker evaluation on our predictions.

    Input: results/predictions/{arm_name}.jsonl
    Each line: {"instance_id": "...", "model_name_or_path": "...", "model_patch": "..."}

    SWE-bench applies the patch in a clean Docker container and runs the test suite.
    """
    predictions_path = f"results/predictions/{arm_name}.jsonl"

    subprocess.run([
        "python", "-m", "swebench.harness.run_evaluation",
        "--dataset_name", "princeton-nlp/SWE-bench_Verified",
        "--predictions_path", predictions_path,
        "--max_workers", "4",
        "--run_id", f"{arm_name}_eval",
    ], check=True)

    # Parse the SWE-bench output to get per-task resolved/unresolved
    # Import results back into our SQLite DB
    import_swebench_results(arm_name)
```

### 5. SQLite Schema (`harness/db.py`)

```sql
CREATE TABLE IF NOT EXISTS runs (
    instance_id TEXT NOT NULL,
    arm TEXT NOT NULL,
    model TEXT NOT NULL,
    floop_enabled BOOLEAN NOT NULL,
    model_patch TEXT,
    -- Filled after SWE-bench evaluation:
    resolved BOOLEAN,
    -- Metrics:
    status TEXT NOT NULL,
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

### 6. Analysis (`analysis/analyze.py`)

```python
"""
Statistical analysis of benchmark results.

Primary metric: Resolve rate (% of eval tasks where patch passes tests)
Primary comparison: haiku_floop vs haiku_bare
Headline number: Gap closure = (haiku_floop - haiku_bare) / (sonnet_bare - haiku_bare)

With 20 eval tasks:
- McNemar's test for paired binary outcomes
- Bootstrap 95% CIs (10,000 resamples) on rates and gap closure
- Cohen's h for effect size
- Be explicit about limited sample size in all reporting

Output:
- Console table with pass rates and CIs
- charts/ directory with PNG and SVG figures
"""

from scipy.stats import binom
import numpy as np

def compute_gap_closure(sonnet_rate, haiku_rate, floop_rate):
    """
    How much of the Sonnet-Haiku gap does floop close?
    Returns fraction in [0, 1] (or >1 if floop+haiku beats Sonnet).
    """
    gap = sonnet_rate - haiku_rate
    if gap <= 0:
        return None  # No gap to close (Haiku already matches Sonnet)
    return (floop_rate - haiku_rate) / gap

def bootstrap_ci(data, stat_fn, n_boot=10000, ci=0.95):
    """Bootstrap confidence interval for any statistic."""
    stats = []
    for _ in range(n_boot):
        sample = np.random.choice(data, size=len(data), replace=True)
        stats.append(stat_fn(sample))
    lower = np.percentile(stats, (1 - ci) / 2 * 100)
    upper = np.percentile(stats, (1 + ci) / 2 * 100)
    return lower, upper
```

### Charts to produce (`analysis/charts.py`):

1. **Grouped bar chart**: resolve rate per arm with 95% CI error bars
2. **Cost-performance scatter**: X = avg cost per task, Y = resolve rate, labeled points
3. **Cost per resolved task**: bar chart showing effective cost of each success

---

## Training Phase Protocol

This happens BEFORE evaluation, using ONLY the 30 training tasks.

### Step 1: Automated — Run Haiku on training tasks

```bash
uv run python -m harness.orchestrator --phase train
```

Produces 30 transcripts + 30 patches. Cost: ~$6.

### Step 2: Automated — Evaluate training patches

```bash
uv run python -m harness.swebench_eval --arm haiku_bare --split train
```

Tells you which tasks Haiku solved (expect ~10-20% = 3-6 tasks) and which it failed.

### Step 3: Manual — Analyze failures and create behaviors

This is the human-intensive part. For each failed task:

1. Read the transcript: what did Haiku try? Where did it go wrong?
2. Read the ground truth patch: what was the actual fix?
3. Ask: **is there a general principle that would have helped?**
4. If yes, create a behavior:

```bash
floop learn \
  --right "Django ORM: when a QuerySet method chains, verify it returns a new QuerySet rather than mutating in place" \
  --tags "python,django,queryset,orm"
```

### Behavior quality rules

**Good** (generalizable principles):
- "In pytest, fixture scope determines lifetime — function-scoped fixtures reset between tests, session-scoped persist"
- "Python string slicing: `s[a:b]` excludes index b. Off-by-one errors usually mean the end index needs +1"
- "When Django's `get()` raises MultipleObjectsReturned, the fix is usually in the queryset filter, not in exception handling"

**Bad** (task-specific answers = data leakage):
- "Change line 847 of query.py from `>=` to `>`"
- "The fix for django-11099 is to add a null check in resolve()"

### Step 4: Audit for leakage

```bash
uv run python -m scripts.check_leakage
```

Scans every behavior for:
- Instance IDs from the eval split
- File paths or function names unique to eval tasks
- Literal code snippets that match eval ground truth patches

### Time estimate

- Automated runs + eval: ~2-3 hours
- Human analysis and behavior writing: **8-15 hours** (the real work)
  - ~24 failed tasks to analyze
  - Not every failure needs a behavior — focus on patterns that repeat
  - Aim for 30-80 quality behaviors
- Leakage audit: ~1 hour

---

## Floop MCP Configuration

floop must be configured as an MCP server in Claude Code's settings for the
`haiku_floop` arm to access it.

Typical location: `~/.claude/settings.json` or project `.claude/settings.json`:

```json
{
  "mcpServers": {
    "floop": {
      "command": "floop",
      "args": ["mcp-serve", "--store", "/absolute/path/to/floop-bench/behaviors/store"]
    }
  }
}
```

**IMPORTANT**: Check floop's actual MCP server command:
```bash
floop --help
floop mcp --help        # or however the subcommand works
```

The `--allowedTools` flag on Claude Code controls whether the agent can call floop
tools. For `haiku_bare` and `sonnet_bare` arms, floop tools are excluded from the
allowed list, so even though the MCP server is running, the agent can't access it.

---

## Validation Script (Ralph Loop Target)

```python
# scripts/validate_harness.py
"""
Progressive smoke test. Exits 0 when the harness is ready.
Each check builds on the previous — fix them in order.
"""
import subprocess, sys, json, sqlite3
from pathlib import Path

def check(name, fn):
    try:
        ok, detail = fn()
        status = "✅" if ok else "❌"
        if not ok:
            print(f"  {status}  {name}: {detail}")
        else:
            print(f"  {status}  {name}")
        return ok
    except Exception as e:
        print(f"  ❌  {name}: {e}")
        return False

def c_deps():
    import click, rich, datasets, matplotlib, scipy
    return True, ""

def c_swebench():
    import swebench
    return True, ""

def c_docker():
    r = subprocess.run(["docker", "info"], capture_output=True)
    return r.returncode == 0, "Docker not running"

def c_dataset():
    from datasets import load_dataset
    ds = load_dataset("princeton-nlp/SWE-bench_Verified", split="test")
    return len(ds) > 0, f"Got {len(ds)} tasks"

def c_split():
    p = Path("config/splits.json")
    if not p.exists():
        return False, "Run: uv run python -m scripts.generate_split"
    data = json.loads(p.read_text())
    return len(data.get("train", [])) == 30 and len(data.get("eval", [])) == 20, \
           f"Expected 30/20, got {len(data.get('train',[]))}/{len(data.get('eval',[]))}"

def c_claude_cli():
    r = subprocess.run(["claude", "--version"], capture_output=True)
    return r.returncode == 0, "claude CLI not found"

def c_claude_api():
    """Verify API key auth works. Costs a fraction of a cent."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return False, "ANTHROPIC_API_KEY not set"
    r = subprocess.run(
        ["claude", "-p", "Say OK", "--output-format", "json",
         "--model", "claude-haiku-4-5-20251001", "--max-turns", "1"],
        capture_output=True, text=True, timeout=30
    )
    return r.returncode == 0 and len(r.stdout) > 0, "API call failed"

def c_db():
    db = sqlite3.connect("results/results.db")
    db.execute("SELECT count(*) FROM runs")
    return True, ""

def c_single_task():
    """Run one training task end-to-end. The real test."""
    # This check runs ONE actual task through the full pipeline.
    # It's slow (~2 min) and costs ~$0.20 but validates everything.
    r = subprocess.run(
        ["uv", "run", "python", "-m", "harness.orchestrator",
         "--phase", "smoke"],
        capture_output=True, text=True, timeout=600
    )
    if r.returncode != 0:
        return False, r.stderr[-500:] if r.stderr else "Unknown error"
    # Verify result is in DB
    db = sqlite3.connect("results/results.db")
    count = db.execute("SELECT count(*) FROM runs").fetchone()[0]
    return count > 0, f"{count} run(s) in DB"

def c_swebench_eval():
    """Verify SWE-bench can evaluate a prediction."""
    # Only run this if we have at least one prediction
    pred_files = list(Path("results/predictions").glob("*.jsonl"))
    if not pred_files:
        return False, "No predictions yet — run smoke phase first"
    # Try evaluating one instance from predictions
    r = subprocess.run([
        "python", "-m", "swebench.harness.run_evaluation",
        "--predictions_path", str(pred_files[0]),
        "--max_workers", "1",
        "--run_id", "validate_check",
    ], capture_output=True, text=True, timeout=600)
    return r.returncode == 0, r.stderr[-500:] if r.stderr else ""

def c_floop():
    """Check floop MCP is configured and responds."""
    r = subprocess.run(
        ["claude", "-p", "Call floop_active to check for behaviors",
         "--output-format", "json", "--max-turns", "1",
         "--model", "claude-haiku-4-5-20251001"],
        capture_output=True, text=True, timeout=30
    )
    # Check if the output mentions floop or behaviors
    return "floop" in r.stdout.lower() or "behavior" in r.stdout.lower(), \
           "floop_active tool not accessible"

def main():
    checks = [
        ("Python dependencies", c_deps),
        ("SWE-bench installed", c_swebench),
        ("Docker running", c_docker),
        ("Dataset loads", c_dataset),
        ("Train/eval split exists", c_split),
        ("Claude Code CLI available", c_claude_cli),
        ("Claude Code API auth works", c_claude_api),
        ("SQLite DB works", c_db),
        ("Single task pipeline", c_single_task),
        ("SWE-bench evaluation", c_swebench_eval),
        ("Floop MCP integration", c_floop),
    ]

    passed = sum(check(name, fn) for name, fn in checks)
    total = len(checks)

    if passed == total:
        print(f"\n🎉 All {total} checks passed! Harness is ready.")
        sys.exit(0)
    else:
        print(f"\n⚠️  {passed}/{total} passed. Fix the first failing check and re-run.")
        sys.exit(1)

if __name__ == "__main__":
    main()
```

### Ralph Loop (`ralph.sh`)

```bash
#!/bin/bash
# Iterate until validate_harness.py passes all checks.
# Run from the floop-bench project root.

set -euo pipefail
MAX_ITERS=25
ITER=0

while [ $ITER -lt $MAX_ITERS ]; do
    ITER=$((ITER + 1))
    echo ""
    echo "══════════════════════════════════════"
    echo "  Ralph iteration $ITER / $MAX_ITERS"
    echo "══════════════════════════════════════"

    uv run python -m scripts.validate_harness 2>&1 | tee /tmp/floop_validation.log

    if [ $? -eq 0 ]; then
        echo ""
        echo "✅ Harness ready after $ITER iterations!"
        exit 0
    fi

    echo ""
    echo "Feeding errors to Claude Code for fixing..."
    claude -p "You are building the floop-bench benchmark harness.

The validation script failed. Here is the output:

---
$(cat /tmp/floop_validation.log)
---

Context: Read SPEC.md in this project root for the full specification.

Instructions:
1. Focus on the FIRST failing check only
2. Read the relevant source files to understand the current state
3. Fix the issue
4. Do NOT re-run the validation — I will do that on the next iteration

Be precise. Make the minimal change needed to fix the failing check."

    sleep 3
done

echo "❌ Did not converge after $MAX_ITERS iterations."
exit 1
```

---

## Implementation Order

Build sequentially. Each step has a clear "done" signal.

### Step 1: Project skeleton
- `uv init`, add dependencies
- Create directory structure
- Copy this spec into the project root as `SPEC.md`
- **Done when**: `uv run python -c "import click, rich, datasets, matplotlib, scipy"` works

### Step 2: SWE-bench setup
- `pip install swebench` (or add to deps)
- Verify Docker works
- Load SWE-bench Verified Mini dataset
- Implement `generate_split.py`, run it once, commit `splits.json`
- **Done when**: `c_dataset` and `c_split` checks pass

### Step 3: Claude Code wrapper
- Run `claude --help`, document actual flag names
- Test `-p`, `--output-format json`, `--model`, `--allowedTools`, `--max-turns`
- Implement `parse_claude_json` based on REAL output structure
- **Done when**: `c_claude_cli` and `c_claude_api` checks pass

### Step 4: Database
- Implement `db.py` with schema creation and CRUD
- **Done when**: `c_db` check passes

### Step 5: Single task pipeline
- Implement `runner.py` (repo checkout, Claude Code invocation, diff capture)
- Implement minimal `orchestrator.py` with `--phase smoke`
- **Done when**: `c_single_task` check passes (one task runs end-to-end)

### Step 6: SWE-bench evaluation integration
- Implement `swebench_eval.py`
- Write prediction JSONL from run results
- Invoke SWE-bench Docker evaluation
- Import resolved/unresolved back into SQLite
- **Done when**: `c_swebench_eval` check passes

### Step 7: Floop integration
- Configure floop MCP in Claude Code settings
- Test `floop_active` tool access
- Verify `--allowedTools` correctly enables/disables floop per arm
- **Done when**: `c_floop` check passes

### Step 8: Full orchestrator
- Add `--phase train` and `--phase eval` modes
- Resume support, cost tracking, budget guard
- Progress logging
- **Done when**: all validation checks pass, Ralph loop exits with success

### Step 9: Analysis
- Implement `analyze.py` and `charts.py`
- Can run on partial data from the smoke test
- **Done when**: charts generate from real run data

---

## After Harness is Ready: Execution Sequence

```bash
# 1. Training phase (~$6, ~2-3 hours compute)
uv run python -m harness.orchestrator --phase train
uv run python -m harness.swebench_eval --arm haiku_bare --split train

# 2. Human work: analyze failures, create behaviors (~8-15 hours)
#    (Read transcripts, write floop behaviors, audit for leakage)
uv run python -m scripts.check_leakage

# 3. Evaluation phase (~$30, ~4-6 hours compute)
uv run python -m harness.orchestrator --phase eval

# 4. SWE-bench evaluation of all arms (~1-2 hours, Docker builds)
uv run python -m harness.swebench_eval --arm sonnet_bare
uv run python -m harness.swebench_eval --arm haiku_bare
uv run python -m harness.swebench_eval --arm haiku_floop

# 5. Analysis
uv run python -m analysis.analyze
uv run python -m analysis.charts
```

---

## What NOT to Do

- **Do NOT write a custom agent.** Claude Code IS the agent.
- **Do NOT use subscription auth for the study.** API key = precise costs + no TOS risk.
- **Do NOT skip SWE-bench Docker evaluation.** It's the standard. Roll your own = not credible.
- **Do NOT modify test files.** SWE-bench runs the ORIGINAL tests.
- **Do NOT put task-specific info in behaviors.** General principles only.
- **Do NOT regenerate the split after training begins.**
- **Do NOT assume Claude Code flag names.** Verify with `--help` first.
- **Do NOT parallelize** until sequential works perfectly.

## Success Criteria

**Harness ready** (Ralph loop target):
All 11 validation checks pass. One task runs end-to-end through Claude Code →
git diff → SWE-bench Docker evaluation → result in SQLite.

**Study complete**:
20 eval tasks × 3 arms resolved/unresolved. Statistical analysis with CIs.
Publication-ready charts. Behavior store documented and versioned.

**Publishable regardless of outcome**:
- If floop helps: "Haiku + floop closes X% of the Sonnet gap at Y% of the cost"
- If floop doesn't help: "We tested behavior injection on SWE-bench Mini and found
  it helps with X-type tasks but not Y-type. Here's what we learned about why."
- Either way: open-source the harness, data, and analysis for reproducibility.
