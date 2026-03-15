# Contributing to floop-bench

Thank you for your interest in contributing to floop-bench! This guide will help you get started.

## Prerequisites

- **Python 3.12+** ([install](https://www.python.org/downloads/))
- **[uv](https://docs.astral.sh/uv/)** (fast Python package manager)
- **Docker** or **Podman** (for sandboxed agent execution)
- **[floop CLI](https://github.com/nvandessel/floop)** (check `FLOOP_VERSION` in the Makefile for the tested version)

## Development Setup

```bash
# Clone the repository
git clone https://github.com/nvandessel/floop-bench.git
cd floop-bench

# Install dependencies
uv sync

# Build the sandbox container image
make build

# Validate the environment
uv run python -m scripts.validate_harness
```

## Workflow

1. **Find or create an issue** — Check existing issues or open a new one
2. **Fork and branch** — Create a feature branch from `main` (`feat/description` or `fix/description`)
3. **Make changes** — Follow existing code patterns
4. **Lint** — `ruff check .` and `ruff format --check .` must pass
5. **Submit a PR** — Reference the related issue

## Adding a New Experimental Arm

Arms are configured in three places:

1. **`config/arms.toml`** — Define the arm (model, agent, floop enabled/disabled)
2. **YAML config file** — Create `config/mswea_<name>.yaml` with model and agent settings. Note: `config/mswea_floop_*.yaml` files are gitignored (they're generated at runtime). Only the base configs are tracked.
3. **CLI** — Run with `uv run python -m scripts.run_mswea run --arm <name>`

See existing configs (`mswea_bare.yaml`, `mswea_floop.yaml`) as examples.

## Adding a New Agent Backend

1. Implement the protocol defined in `agents/base.py`
2. Register the agent in `harness/config.py`

## Scientific Rigor

This is a benchmark project. Contributions that add or modify experimental results must:

- **Document the run** in `docs/RUNBOOK.md` with full context (versions, config, methodology)
- **Report statistical measures** — p-values, confidence intervals, effect sizes
- **Report bad results honestly** — a run where floop hurts is just as valuable as one where it helps
- **Avoid leakage** — eval set tasks must never influence behavior creation (see `docs/TRAINING.md`)

## Commit Messages

Use [conventional commits](https://www.conventionalcommits.org/):

- `feat:` new features
- `fix:` bug fixes
- `docs:` documentation changes
- `test:` test additions or changes
- `chore:` maintenance

## Pull Request Expectations

- PRs should be focused — one logical change per PR
- Include a description of what changed and why
- Reference the related issue
- CI must pass

## Reporting Issues

- **Bugs**: Use the [bug report template](.github/ISSUE_TEMPLATE/bug_report.yml)
- **Features**: Use the [feature request template](.github/ISSUE_TEMPLATE/feature_request.yml)
- **Security**: See [SECURITY.md](SECURITY.md)

## License

By contributing, you agree that your contributions will be licensed under the [Apache License 2.0](LICENSE).
