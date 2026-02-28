# floop-bench

A benchmark harness for evaluating [floop](https://github.com/nvandessel/floop) on [SWE-bench](https://www.swebench.com/) tasks. Compares model performance with and without floop-injected behaviors across multiple experimental arms.

## Overview

floop-bench runs coding agents against real GitHub issues from the SWE-bench Verified dataset, then evaluates the generated patches using SWE-bench's Docker-based test harness. It's designed to be **agent-agnostic** — any agent that takes an issue and a repo and returns a diff can plug in.

The harness supports multiple experimental arms (model + agent + floop configuration), parallel execution, automatic resume, budget controls, and statistical analysis.

## Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/)
- Docker or Podman
- [floop](https://github.com/nvandessel/floop) CLI
- An API key for at least one model provider (see below)

Optional:
- [Claude Code](https://docs.anthropic.com/en/docs/claude-code) CLI (for the `claude_code` agent)

## Setup

```bash
git clone git@github.com:nvandessel/floop-bench.git
cd floop-bench
uv sync
```

### API Keys

The `mini_swe` agent uses [litellm](https://github.com/BerriAI/litellm), which supports most model providers. Set the API key for whichever provider your arms use:

```bash
# Anthropic (Claude Haiku, Sonnet, Opus)
export ANTHROPIC_API_KEY=sk-ant-...

# OpenAI (GPT-4o, GPT-4o-mini, o1, o3)
export OPENAI_API_KEY=sk-...

# Google (Gemini)
export GEMINI_API_KEY=...

# Groq (Llama, Mixtral — free tier available)
export GROQ_API_KEY=gsk_...

# Local models via Ollama (no key needed)
# Just run: ollama serve
```

Set whichever keys you need for the models configured in `config/arms.toml`. Multiple can be set at once if your arms span providers.

Validate the environment:

```bash
uv run python -m scripts.validate_harness
```

All 8 checks should pass before running experiments.

## Usage

### Smoke test

Run 2 tasks end-to-end to validate the pipeline:

```bash
uv run python -m harness.orchestrator --phase smoke
uv run python -m harness.swebench_eval --arm haiku_bare --split smoke
```

### Training phase

Run the baseline model on 30 training tasks, evaluate, then create floop behaviors from failure analysis:

```bash
uv run python -m harness.orchestrator --phase train
uv run python -m harness.swebench_eval --arm haiku_bare --split train
```

See [docs/TRAINING.md](docs/TRAINING.md) for the behavior creation protocol. Audit for data leakage before proceeding:

```bash
uv run python -m scripts.check_leakage
```

### Evaluation phase

Run all configured arms on 20 eval tasks:

```bash
uv run python -m harness.orchestrator --phase eval
uv run python -m harness.swebench_eval --arm sonnet_bare
uv run python -m harness.swebench_eval --arm haiku_bare
uv run python -m harness.swebench_eval --arm haiku_floop
```

### Analysis

```bash
uv run python -m analysis.analyze
uv run python -m analysis.charts
```

## CLI Reference

### Orchestrator

```
uv run python -m harness.orchestrator --phase {smoke,train,eval} [OPTIONS]
```

| Flag | Default | Description |
|------|---------|-------------|
| `--phase` | required | Experiment phase |
| `--budget` | 55.0 | Max total spend (USD) before halting |
| `--workers` | 1 | Number of parallel workers |
| `--timeout` | 300 | Per-task timeout (seconds) |

Re-running the same phase skips completed tasks automatically.

### SWE-bench Evaluation

```
uv run python -m harness.swebench_eval --arm ARM [--split SPLIT] [--max-workers N]
```

### Utility Scripts

| Script | Description |
|--------|-------------|
| `scripts.validate_harness` | Run 8 progressive environment checks |
| `scripts.generate_split` | Generate train/eval split (already committed) |
| `scripts.check_leakage` | Scan behavior store for eval data contamination |
| `scripts.estimate_cost` | Project remaining cost from historical run data |

## Configuration

### Arms

Arms are defined in `config/arms.toml`. Each arm specifies a model, an agent backend, and whether floop is enabled:

```toml
[arms.gpt4o_bare]
agent = "mini_swe"
model = "openai/gpt-4o"
floop = false

[arms.gpt4o_mini_floop]
agent = "mini_swe"
model = "openai/gpt-4o-mini"
floop = true
floop_store = "behaviors/store"
```

Model strings use [litellm format](https://docs.litellm.ai/docs/providers) (`provider/model-name`). Any litellm-supported model works — Anthropic, OpenAI, Google, Groq, Ollama, and others. See `config/arms.toml` for examples.

### Agents

Two agent backends are included:

- **`mini_swe`** — Lightweight agent loop using [litellm](https://github.com/BerriAI/litellm). Works with any litellm-supported model.
- **`claude_code`** — Wraps the [Claude Code](https://docs.anthropic.com/en/docs/claude-code) CLI.

New agents can be added by implementing the protocol in `agents/base.py` and registering in `harness/config.py`.

### Dataset

50 tasks sampled from [SWE-bench Verified](https://huggingface.co/datasets/princeton-nlp/SWE-bench_Verified) (seed 42), stratified by repo into 30 train / 20 eval. The split is committed at `config/splits.json`.

## Output

| Path | Contents |
|------|----------|
| `results/results.db` | SQLite database with all run data |
| `results/predictions/` | JSONL files per arm (SWE-bench format) |
| `results/transcripts/` | Raw agent output per run |
| `results/charts/` | Generated PNG/SVG charts |

## Cost Controls

The orchestrator tracks cumulative API spend and halts when `--budget` is exceeded. Interrupted runs resume automatically. Use `scripts.estimate_cost` for projections based on prior run data.

## Further Reading

- [docs/TRAINING.md](docs/TRAINING.md) — Behavior creation protocol and leakage rules
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — Technical architecture and data flow
- [SPEC.md](SPEC.md) — Full experimental design specification
