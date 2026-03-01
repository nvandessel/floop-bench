# floop-bench

A benchmark harness for evaluating [floop](https://github.com/nvandessel/floop) on [SWE-bench](https://www.swebench.com/) tasks. Compares model performance with and without floop-injected behaviors across multiple experimental arms.

## Overview

floop-bench runs coding agents against real GitHub issues from the SWE-bench Verified dataset, then evaluates the generated patches using SWE-bench's Docker-based test harness. It's designed to be **agent-agnostic** — any agent that takes an issue and a repo and returns a diff can plug in.

The harness supports multiple experimental arms (model + agent + floop configuration), parallel execution, automatic resume, budget controls, and statistical analysis.

## Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/)
- Docker or Podman
- An API key for at least one model provider (see below)

All other dependencies (floop CLI, litellm, agent code) are packaged in the sandbox container image, built via `make build`.

## Setup

```bash
git clone git@github.com:nvandessel/floop-bench.git
cd floop-bench
uv sync

# Build the sandbox image (agents run inside this container)
make build
```

### API Keys

Copy `.env` and add your key(s). The Makefile loads this automatically:

```bash
cp .env.example .env
# Edit .env with your key(s)
```

`.env` format:

```
GEMINI_API_KEY=your-key-here
# ANTHROPIC_API_KEY=sk-ant-...
# OPENAI_API_KEY=sk-...
```

Set whichever keys you need for the models configured in `config/arms.toml`. Keys are forwarded into sandbox containers automatically.

Validate the environment:

```bash
uv run python -m scripts.validate_harness
```

All checks should pass before running experiments.

## Usage

### Smoke test

Run 2 tasks end-to-end to validate the pipeline:

```bash
make smoke
# or with a specific arm:
make smoke ARM=gemini_flash_bare
```

### Training phase

Run the baseline model on 30 training tasks. The agent uses floop organically during execution — learning behaviors as it works through tasks:

```bash
make train
```

Behaviors accumulate in a Docker volume (`floop-train`) across all 30 tasks. See [docs/TRAINING.md](docs/TRAINING.md) for details.

### Evaluation phase

Run all configured arms on 20 eval tasks. A leakage audit runs automatically before eval proceeds:

```bash
make eval
```

The train-phase floop volume is mounted read-only — the agent can query learned behaviors but cannot learn new ones.

### Manual leakage audit

```bash
make leakage
```

### Analysis

```bash
uv run python -m analysis.analyze
uv run python -m analysis.charts
```

## CLI Reference

### Make targets

| Target | Description |
|--------|-------------|
| `make build` | Build the sandbox container image |
| `make shell` | Interactive bash inside the sandbox |
| `make smoke` | Smoke test (2 tasks, sandboxed) |
| `make train` | Train phase (30 tasks, sandboxed) |
| `make eval` | Eval phase (20 tasks, leakage audit + sandboxed) |
| `make leakage` | Manual leakage audit against train volume |
| `make clean` | Remove volumes and sandbox image |

Override defaults with env-style args: `make smoke ARM=gemini_flash_bare TIMEOUT=600 BUDGET=10`

### Orchestrator (direct)

```
uv run python -m harness.orchestrator --phase {smoke,train,eval} [OPTIONS]
```

| Flag | Default | Description |
|------|---------|-------------|
| `--phase` | required | Experiment phase |
| `--budget` | 55.0 | Max total spend (USD) before halting |
| `--workers` | 1 | Number of parallel workers |
| `--timeout` | 300 | Per-task timeout (seconds) |
| `--no-sandbox` | off | Disable Docker sandbox (run agents directly on host) |

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
| `scripts.check_leakage` | Scan behavior store for eval data contamination (`--volume` for Docker volumes) |
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
