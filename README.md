# floop-bench

Benchmark harness measuring whether [floop](https://github.com/nvandessel/floop) closes the performance gap between a cheap model and an expensive model on real software engineering tasks.

**Headline result format:** "Haiku + floop closes X% of the gap with Sonnet on SWE-bench at Y% of the cost."

## What This Measures

Three experimental arms run the same SWE-bench tasks:

| Arm | Model | Floop | Role |
|-----|-------|-------|------|
| `sonnet_bare` | Sonnet 4.5 | No | Performance ceiling |
| `haiku_bare` | Haiku 4.5 | No | Performance floor |
| `haiku_floop` | Haiku 4.5 | Yes | The treatment |

The key metric is **gap closure**: how much of the Sonnet-Haiku performance gap does floop close?

Dataset: 50 tasks sampled from [SWE-bench Verified](https://huggingface.co/datasets/princeton-nlp/SWE-bench_Verified), split into 30 train / 20 eval.

## Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) package manager
- Docker or Podman (for SWE-bench evaluation)
- [floop](https://github.com/nvandessel/floop) CLI
- An Anthropic API key

Optional (for the Claude Code agent):
- [Claude Code](https://docs.anthropic.com/en/docs/claude-code) CLI

## Setup

```bash
git clone git@github.com:nvandessel/floop-bench.git
cd floop-bench
uv sync
```

### API Key

Set your Anthropic API key:

```bash
export ANTHROPIC_API_KEY=sk-ant-...
```

Add it to `~/.zshrc` or `~/.bashrc` to persist across sessions.

### Validate

```bash
uv run python -m scripts.validate_harness
```

All 8 checks should pass before running any experiments.

## Running the Experiment

### Phase 1: Smoke Test (~$0.40)

Validates the full pipeline with 2 tasks:

```bash
uv run python -m harness.orchestrator --phase smoke
```

Then evaluate the patches:

```bash
uv run python -m harness.swebench_eval --arm haiku_bare --split smoke
```

### Phase 2: Training (~$6)

Run Haiku bare on 30 training tasks to generate failure data:

```bash
uv run python -m harness.orchestrator --phase train
uv run python -m harness.swebench_eval --arm haiku_bare --split train
```

Then analyze failures and create floop behaviors. See [docs/TRAINING.md](docs/TRAINING.md) for the full protocol.

Audit for data leakage before proceeding:

```bash
uv run python -m scripts.check_leakage
```

### Phase 3: Evaluation (~$30)

Run all 3 arms on 20 eval tasks:

```bash
uv run python -m harness.orchestrator --phase eval
```

Evaluate each arm's patches:

```bash
uv run python -m harness.swebench_eval --arm sonnet_bare
uv run python -m harness.swebench_eval --arm haiku_bare
uv run python -m harness.swebench_eval --arm haiku_floop
```

### Phase 4: Analysis (free)

```bash
uv run python -m analysis.analyze
uv run python -m analysis.charts
```

Charts are saved to `results/charts/`.

## CLI Reference

### Orchestrator

```bash
uv run python -m harness.orchestrator --phase {smoke,train,eval} [OPTIONS]
```

| Flag | Default | Description |
|------|---------|-------------|
| `--phase` | required | Experiment phase |
| `--budget` | 55.0 | Max total spend in USD — halts all workers when exceeded |
| `--workers` | 1 | Parallel workers (each gets isolated repo worktrees) |
| `--timeout` | 300 | Per-task timeout in seconds |

The orchestrator automatically **resumes** — re-running the same phase skips already-completed tasks.

### SWE-bench Evaluation

```bash
uv run python -m harness.swebench_eval --arm ARM [--split SPLIT] [--max-workers N]
```

### Utility Scripts

| Script | What it does |
|--------|-------------|
| `scripts.validate_harness` | Progressive checks — 8 must pass |
| `scripts.generate_split` | Generate train/eval split (run once, already committed) |
| `scripts.check_leakage` | Scan behavior store for eval data contamination |
| `scripts.estimate_cost` | Project remaining cost from historical run data |

## Cost Estimates

| Phase | Est. Cost |
|-------|-----------|
| Smoke (2 tasks, Haiku) | ~$0.40 |
| Train (30 tasks, Haiku) | ~$6 |
| Eval: haiku_bare (20 tasks) | ~$4 |
| Eval: haiku_floop (20 tasks) | ~$5 |
| Eval: sonnet_bare (20 tasks) | ~$20 |
| Buffer | ~$10 |
| **Total** | **~$45** |

Control costs with `--budget`, resume support (re-run to skip completed tasks), and `MAX_THINKING_TOKENS=8000` (set automatically for the Claude Code agent).

Check live projections:

```bash
uv run python -m scripts.estimate_cost
```

## Results

| Path | Contents |
|------|----------|
| `results/results.db` | SQLite database with all run data |
| `results/predictions/` | JSONL files per arm (SWE-bench input format) |
| `results/transcripts/` | Raw agent output per run |
| `results/charts/` | Generated PNG/SVG visualizations |

### Key Metrics

- **Resolve rate** — % of eval tasks where the patch passes tests
- **Gap closure** — `(haiku_floop - haiku_bare) / (sonnet - haiku_bare)`
- **McNemar's test** — paired comparison of floop vs bare outcomes
- **Bootstrap 95% CIs** — on all rates and gap closure
- **Cost per resolved task** — effective cost of each successful fix

## Configuration

Arms are defined in `config/arms.toml`. To change models, agents, or add arms:

```toml
[arms.my_custom_arm]
agent = "mini_swe"          # or "claude_code"
model = "anthropic/claude-haiku-4-5-20251001"
floop = true
floop_store = "behaviors/store"
description = "My custom arm"
```

The `agent` field selects which agent backend to use. See `agents/base.py` for the protocol any agent must implement.

## Further Reading

- [SPEC.md](SPEC.md) — Full experimental design specification
- [docs/TRAINING.md](docs/TRAINING.md) — Training phase protocol
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — Technical architecture reference
