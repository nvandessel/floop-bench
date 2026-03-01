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

## Run 6: Train — Hybrid floop integration (2026-03-01)

**Arms:** gemini_flash_floop (gemini-2.5-flash, floop=true, hybrid harness-forced)
**Result:** 29/30 completed, 1 timeout. $1.43 total. **1 behavior learned.**

**What changed from Run 5:**
- Harness now forces floop usage in three phases:
  1. **Pre-run:** harness calls `floop active`, injects behaviors into prompt
  2. **Agent run:** unchanged, prompt still encourages organic `floop learn`
  3. **Post-run fallback:** if agent didn't learn, extract insight from transcript via LLM call, then call `floop learn --scope local`
- Installed `floop-core` seedpack (9 meta-behaviors teaching agents how to use floop) into the volume at init
- Fixed cross-container persistence: floop stores derived behaviors in `~/.floop/` (global), not `--root` (local). In containers `~/.floop/` is ephemeral. Fixed with symlink `~/.floop → /floop-store/.floop` + `--scope local` on `floop learn`.
- Bumped floop to v0.11.1 (v0.10.0 pack install didn't persist behaviors)

**Findings:**
- **1 behavior out of 30 tasks.** The fallback `_extract_insight` prompt is too conservative — it asks for "generalizable, non-instance-specific" insights and gives the LLM an easy escape hatch (`NOTHING`). Flash takes that exit on 29/30 tasks.
- The one behavior learned was from `pydata/xarray-6938`: "ensure deep-copied mutable state for all child objects". Reasonable insight, but a single behavior provides zero statistical signal for eval.
- **Conclusion: auto-extraction from short transcripts doesn't work.** The transcripts are too compressed (4k chars), the bug fixes too instance-specific, and the extraction model too conservative.

### Transcript analysis — what the agent actually does wrong

Analyzed all 30 Run 6 transcripts to identify patterns:

**By the numbers:**
- 7/30 (23%) produced a model_patch
- 23/30 (77%) produced nothing
- Of the 7 patches, only 1-2 are likely correct (3-7% effective success rate)
- Most expensive failure: sphinx-doc__sphinx-10449 — 558K input tokens, $0.18, no output

**Top failure modes:**

| Mode | Count | Example | Description |
|------|-------|---------|-------------|
| Premature surrender | ~10 | django-14672 (8s, 900 tokens) | Agent reads problem, gives up without running any bash commands |
| Exploration thrashing | ~5 | sphinx-10449 (319s, 558K tokens) | Agent reads files endlessly, never attempts a fix |
| Hallucinated APIs | 2-3 | sphinx-8459, pylint-4551 | Agent imports modules/calls functions that don't exist, never verifies |
| Catastrophic over-editing | 1 | django-14631 (rewrote 280 lines) | Agent rewrites entire file instead of surgical fix |
| Shotgun patching | 1 | django-16116 | Agent duplicates same fix at 4 locations instead of finding the right one |
| No verification | all 7 | — | No agent ran tests or even `python -c "import ..."` after editing |

**The one success:** django-15103 — 4.6K tokens, 26s, one-line fix making `element_id` optional. The agent went directly to the right file and made the right change.

### Pivot: curated behaviors instead of auto-extraction

**Problem:** Auto-extraction produces ~1 behavior per 30 tasks. An eval with 1 behavior vs 0 behaviors is statistically meaningless.

**New approach:** Manually write 10-15 high-quality behaviors based on the transcript analysis. These encode the debugging strategies and anti-pattern avoidance that a human SWE-bench expert would teach a junior engineer:

1. "Always explore the codebase before giving up"
2. "Set an exploration budget, then commit to a fix"
3. "Verify your changes compile/import before submitting"
4. "Keep patches minimal — change the fewest lines possible"
5. "Verify APIs exist before using them"
6. etc.

This tests the **real floop value proposition**: does injecting known-good behaviors into an agent's context improve performance? If yes, floop works. If no, prompt-injected behaviors don't help (at least for this model/task combo).

The auto-extraction is a separate problem (how to generate good behaviors) that we can revisit after validating that good behaviors help at all.

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
| Floop global vs local store | Behaviors written to ephemeral `~/.floop/` in container | Symlink `~/.floop → volume` + `--scope local` |
| Floop pack install no-persist | Pack install reports success, behaviors lost on container exit | Symlink global→local so pack writes to volume |
| Floop v0.10.0 pack bug | `floop pack install` doesn't persist to SQLite | Upgraded to v0.11.1 |

## Cost ledger

| Run | Phase | Arm | Tasks | Cost | Notes |
|-----|-------|-----|-------|------|-------|
| 1 | smoke | gemini_flash_bare (3.x preview) | 2 | ~$0.00 | Model hung, 0 output tokens |
| 2 | smoke | haiku_bare | 2 | $0.53 | Too expensive for cheap arm |
| 3 | smoke | gemini_flash_bare | 2 | $0.08 | Baseline established |
| 4 | train | gemini_flash_floop | 30 | $1.70 | WORKDIR bug, floop init bug |
| 5 | train | gemini_flash_floop | 30 | $1.70 | 0 behaviors learned (prompt ignored) |
| 6 | train | gemini_flash_floop | 30 | $1.43 | 1 behavior learned (hybrid harness) |
| — | smoke (various) | mixed | ~10 | ~$0.80 | Debugging sessions |
| **Total** | | | | **~$6.24** | |
