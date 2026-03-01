# Runbook — Experiment Log & Findings

## Run 1: Smoke test — Gemini 3.x preview (2026-02-28)

**Arms:** gemini_flash_bare (gemini-3-flash-preview)
**Result:** 1/2 completed, 1 timeout. 0 output tokens on timeout — Gemini preview model accepted requests but never responded.

**Findings:**
- Preview models are unreliable. Switched to stable `gemini-2.5-flash` / `gemini-2.5-pro`.
- `litellm.completion()` had no per-call timeout, so a hung API call burned the entire 300s task budget. Added 60s per-call timeout + 3 retries.

## Run 2: Smoke test — Haiku (2026-02-28)

**Arms:** haiku_bare (claude-haiku-4-5-20251001)
**Result:** 2/2 completed, $0.53 total.

**Findings:**
- $0.26/task average. Projected ~$39 for full experiment (50 tasks x 3 arms). Too expensive for the cheap arm.
- Django task: 193k input tokens — context grows with every step as conversation accumulates. MAX_STEPS=30 is too generous.

## Run 3: Smoke test — Gemini 2.5 Flash stable (2026-02-28)

**Arms:** gemini_flash_bare (gemini-2.5-flash)
**Result:** 2/2 completed, $0.08 total.

**Findings:**
- $0.04/task average. Projected ~$6 for full experiment. Well within budget.
- Stable models work reliably. No timeouts.

## Run 4: Train — Gemini 2.5 Flash + floop, attempt 1 (2026-02-28)

**Arms:** gemini_flash_floop (gemini-2.5-flash, floop=true)
**Result:** Mixed — some instant errors, some completed. $1.70 total for 30 tasks.

**Findings:**
- Instant errors caused by Dockerfile `WORKDIR /workspace`. The bind-mounted repo's `pyproject.toml` confused `uv run` into creating a fresh venv without litellm. Repos without a `pyproject.toml` worked fine (intermittent failures).
- Fix: `WORKDIR /app` so `uv run` stays in the pre-built venv.
- Floop volume was empty — `floop init` failed silently due to entrypoint bug (`floop` args passed to the agent CLI entrypoint instead of overriding it). Fix: `--entrypoint floop`.

## Run 5: Train — Gemini 2.5 Flash + floop, attempt 2 (2026-03-01)

**Arms:** gemini_flash_floop (gemini-2.5-flash, floop=true)
**Result:** 29/30 completed, 1 timeout. $1.70 total.

**Findings:**
- Floop store initialized correctly this time (`.floop` directory exists in volume).
- **0 behaviors learned.** The agent never called `floop learn` or `floop active`. The floop CLI cadence instructions are in the user message preamble, but Gemini 2.5 Flash ignores them entirely.
- Effectively a bare run — no floop data accumulated. The experiment requires the agent to actually use floop.

## Infrastructure bugs fixed along the way

| Bug | Symptom | Fix |
|-----|---------|-----|
| No per-call API timeout | Single hung call burns entire task budget | `timeout=60` on `litellm.completion()` + 3 retries |
| Hardcoded `docker` | "Docker not available" on Podman systems | `find_container_runtime()` prefers podman |
| Relative worktree paths | `git worktree add` failed on re-runs | `.resolve()` for absolute paths + prune before add |
| litellm stdout noise | Container output JSON parsing failed | `suppress_debug_info=True` + scan for last JSON line |
| `WORKDIR /workspace` | `uv run` created fresh venv in bind mount | `WORKDIR /app` |
| Missing `--entrypoint` | `floop init`/`floop active` hit agent CLI | `--entrypoint floop` for utility commands |
| SELinux denials | Container couldn't read bind-mounted files on Fedora | `:z` relabel flag on bind mount |
| No `.env` support | API keys lost between terminal sessions | Makefile `-include .env` + `.env.example` |

## Open problems

### Agent doesn't use floop (critical)

The floop CLI cadence instructions are in the user message preamble but Gemini 2.5 Flash ignores them. Options:

1. **System prompt** — move floop instructions there (more authoritative for most models)
2. **Force initial query** — run `floop active` before the agent loop and inject results into context
3. **Automatic floop learn** — inject learning calls after each step instead of relying on the model
4. **Structured tool use** — instead of bash-based floop CLI, expose floop as a proper tool/function call

The core question: should the agent use floop organically (prompt-based) or should the harness force it (programmatic)?
