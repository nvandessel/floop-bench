# floop-bench

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)
[![CI](https://github.com/nvandessel/floop-bench/actions/workflows/ci.yml/badge.svg)](https://github.com/nvandessel/floop-bench/actions/workflows/ci.yml)

**Can AI agents learn from corrections and get better at coding tasks?**

floop-bench is an open benchmark for testing that question. It evaluates [floop](https://github.com/nvandessel/floop) — a tool that helps AI agents learn from human corrections — by running controlled A/B experiments on real software engineering tasks from [SWE-bench Verified](https://www.swebench.com/).

Every result is published here, whether it helps or hurts floop's case. The goal is truth, not marketing.

## What We've Found So Far

We've run 11 experiments across 4 months. Here are the key results:

| Run | What Was Tested | Bare | Floop | Delta | p-value | Significant? |
|-----|----------------|------|-------|-------|---------|-------------|
| 10 | 3 hand-written heuristics in system prompt | 4/20 (20%) | 7/20 (35%) | +15pp | 0.45 | No |
| 11a | `floop prompt` output from 3-behavior store | Pending | — | — | — | — |

**Total project spend:** ~$59

The strongest signal so far is a +15 percentage point improvement from three focused behavioral heuristics (Run 10). This is a medium effect size (Cohen's h = 0.34), but **not statistically significant** at n=20 tasks. We need larger sample sizes, multiple model families, and more runs to draw real conclusions.

For the full experiment log with methodology, versions, and analysis for every run, see [docs/RUNBOOK.md](docs/RUNBOOK.md).

## How We Test

Each experiment compares two arms on the same set of SWE-bench Verified tasks:

- **Bare arm**: A coding agent with no behavioral guidance
- **Floop arm**: The same agent with floop-generated behaviors injected into its system prompt

The agent runs inside a Docker sandbox, attempts to fix a real GitHub issue, and produces a git patch. [SWE-bench's Docker-based evaluator](https://github.com/princeton-nlp/SWE-bench) runs the repository's test suite against the patch to determine pass/fail.

Statistical analysis uses bootstrap confidence intervals and McNemar's test for paired comparisons. See [SPEC.md](SPEC.md) for the full experimental design.

### What counts as "floop"

We're careful to separate the tool from the technique:

- **Runs 7-10** tested hand-written heuristics injected as raw text — this tests the *technique* of behavioral prompting, not floop itself
- **Run 11+** tests `floop prompt` — the actual floop binary generating behavior text from a learned store

Both findings are valuable. We report exactly what was tested in each run.

## Where This Is Going

floop-bench is currently a manual evaluation harness. The roadmap:

| Level | Automated | Manual | Phase |
|-------|-----------|--------|-------|
| Manual | Nothing | Everything | **Now** (Runs 1-11) |
| Semi-auto | Post-consolidation tier 1 | Human reviews results | v0 |
| Auto + guardrails | Full loop overnight, proposes changes | Human approves | v1 |
| Full auto | Hypothesize, test, keep/discard | Weekly summary review | v2 |

When floop-bench gains the ability to autonomously hypothesize and test consolidation parameters, it becomes **floop-research**.

## Project Status

floop-bench is an active research project. The harness has been used for 11 experiment runs across multiple model configurations. The evaluation pipeline (task execution, SWE-bench evaluation, statistical analysis) is stable. Results update with each run.

## Getting Started

### Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/)
- Docker or Podman
- An API key for at least one model provider (see below)

### Setup

```bash
git clone https://github.com/nvandessel/floop-bench.git
cd floop-bench
uv sync

# Build the sandbox image (agents run inside this container)
make build
```

### API Keys

```bash
cp .env.example .env
# Edit .env with your key(s)
```

Set whichever keys you need for the models configured in `config/arms.toml`. Keys are forwarded into sandbox containers automatically.

Validate the environment:

```bash
uv run python -m scripts.validate_harness
```

### Running Experiments

```bash
# Smoke test (2 tasks, validates the pipeline)
make smoke

# Train phase (30 tasks, agent learns behaviors organically)
make train

# Eval phase (20 tasks, leakage audit runs first)
make eval

# Statistical analysis
uv run python -m analysis.analyze
uv run python -m analysis.charts
```

### Make Targets

| Target | Description |
|--------|-------------|
| `make build` | Build the sandbox container image |
| `make shell` | Interactive bash inside the sandbox |
| `make smoke` | Smoke test (2 tasks, sandboxed) |
| `make train` | Train phase (30 tasks, sandboxed) |
| `make eval` | Eval phase (20 tasks, leakage audit + sandboxed) |
| `make leakage` | Manual leakage audit against train volume |
| `make clean` | Remove volumes and sandbox image |

Override defaults: `make smoke ARM=gemini_flash_bare TIMEOUT=600 BUDGET=10`

### Configuration

Arms are defined in `config/arms.toml`. Each arm specifies a model, an agent backend, and whether floop is enabled. Model strings use [litellm format](https://docs.litellm.ai/docs/providers) — any litellm-supported model works.

Two agent backends are included:

- **`mini_swe`** — Lightweight agent loop using [litellm](https://github.com/BerriAI/litellm)
- **`claude_code`** — Wraps the [Claude Code](https://docs.anthropic.com/en/docs/claude-code) CLI

New agents can be added by implementing the protocol in `agents/base.py`.

### Dataset

50 tasks sampled from SWE-bench Verified (seed 42), stratified by repo into 30 train / 20 eval. The split is committed at `config/splits.json`.

### Cost Controls

The orchestrator tracks cumulative API spend and halts when `--budget` is exceeded. Interrupted runs resume automatically. Use `scripts.estimate_cost` for projections.

## Further Reading

- [docs/RUNBOOK.md](docs/RUNBOOK.md) — Full experiment log with per-run results
- [SPEC.md](SPEC.md) — Experimental design specification
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — Technical architecture and data flow
- [docs/TRAINING.md](docs/TRAINING.md) — Behavior creation protocol and leakage rules

## License

[Apache License 2.0](LICENSE)
