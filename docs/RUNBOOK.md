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

## Run 7: Eval — Curated behaviors A/B test (2026-03-01)

**Arms:** gemini_flash_bare vs gemini_flash_floop (21 behaviors: 9 core + 12 curated)
**Result:** 40 tasks (20 per arm). $2.68 total. SWE-bench verified evaluation.

**Setup:**
- Created `swe-bench-expert.fpack` with 12 curated debugging behaviors based on Run 6 transcript analysis
- Installed alongside floop-core pack (9 meta-behaviors) into floop-train volume
- Leakage audit: passed (0 eval-specific content in behaviors)
- SWE-bench evaluation: applied patches and ran test suites via `swebench.harness.run_evaluation`

**Results:**

| Metric | Bare | Floop | Delta |
|--------|------|-------|-------|
| Tasks | 20 | 20 | — |
| Completed (no timeout) | 19 | 18 | -1 |
| Patches generated | 4 (20%) | 6 (30%) | +50% relative |
| **Patches resolved** | **2 (10%)** | **0 (0%)** | **-100%** |
| Avg duration | 88s | 115s | +31% |
| Total cost | $1.20 | $1.48 | +23% |

**Resolved tasks (bare):**
- `django__django-16485` — bare resolved, floop submitted patch but FAILED tests
- `pylint-dev__pylint-6903` — bare resolved, floop submitted patch but FAILED tests

**Floop-only patches (all failed):**
- `astropy__astropy-14096` — patch failed tests
- `django__django-11999` — patch failed tests
- `django__django-13012` — patch failed tests
- `django__django-15037` — error: patch tried to delete nonexistent file

**Analysis:**

The curated behaviors **increased patch generation** (30% vs 20%) but **decreased patch quality** (0% vs 10% resolve rate). The behaviors encouraged the agent to try harder and not give up, which produced more patches — but the patches were wrong more often.

Two tasks that bare solved correctly (`django-16485`, `pylint-6903`), floop got wrong. This suggests the behavior context may have interfered with the model's natural problem-solving, steering it toward generic heuristics ("explore first", "verify APIs") instead of the specific reasoning needed.

**Possible explanations:**
1. **Context noise**: 21 behaviors (~3K tokens) added to every prompt may dilute the model's attention on the actual bug description
2. **Premature commitment**: behaviors like "set exploration budget, then commit" may cause the agent to commit to incorrect fixes faster
3. **Generic vs specific**: behaviors teach general strategies, but SWE-bench bugs require highly specific code reasoning
4. **Model quality ceiling**: Gemini 2.5 Flash may not be capable enough to benefit from behavioral guidance — a stronger model might leverage behaviors better

**Conclusion:** For this model/task combination, curated debugging behaviors via prompt injection **do not improve performance** and may actually hurt it. The behaviors successfully changed agent behavior (more patches, more exploration) but not in a way that improved correctness.

### What this means for floop

This doesn't invalidate floop as a concept. It shows that:
1. **Behavior injection works** — the agent clearly responded to the behaviors (different behavior observed)
2. **Behavior quality matters** — generic debugging heuristics may not be the right content
3. **Model capability is a confounder** — Gemini 2.5 Flash may be too weak to benefit; stronger models might leverage context better
4. **The benchmark is hard** — SWE-bench Verified has a ~30% solve rate even for top agents (Claude 3.5 Sonnet + SWE-agent). Gemini Flash is far below that baseline.

Potential next steps (not pursued in this experiment):
- Test with a stronger model (Gemini Pro, Claude Sonnet) that might leverage behaviors better
- Test more specific behaviors (e.g., "when debugging Django, check migrations first")
- Test fewer behaviors (reduce context noise) — try top-3 instead of 21
- Test on easier tasks where the model has a reasonable baseline solve rate

## Run 8: Isolating why floop hurt performance (2026-03-01)

**Goal:** Run 7 showed floop hurt performance (bare 10%, floop 0%). Three confounded hypotheses: (1) context noise — 21 behaviors diluted attention, (2) model too weak — Flash can't leverage guidance, (3) wrong content — generic heuristics don't help code reasoning. This run isolates each factor.

### Phase 1: Flash diagnostic (3 arms, ~$4)

| Arm | Model | Context | Tests |
|-----|-------|---------|-------|
| `flash_bare` | gemini-2.5-flash | None | Replication — is 10% stable? |
| `flash_floop_3` | gemini-2.5-flash | 3 focused behaviors (no cadence) | Were 21 behaviors too many? |
| `flash_placebo` | gemini-2.5-flash | ~500 tok generic SE text | Does ANY extra text hurt? |

**The 3 behaviors** (chosen to address observed failure modes):
1. "Locate the exact function mentioned in the traceback before editing any code" (addresses wrong-function bug)
2. "Make the smallest possible change — never copy-paste code between functions" (addresses hallucination)
3. "After editing, verify your change by running: python -c 'import <module>'" (addresses no-verification)

**Decision gate:**
- bare ~10% and floop_3 >= bare → proceed to Phase 2
- bare drops to 0% → tasks too hard for Flash; skip to Pro only
- placebo also drops → problem is prompt length, not content

### Phase 2: Pro model (2 arms, ~$12)

| Arm | Model | Context | Tests |
|-----|-------|---------|-------|
| `gemini_pro_bare` | gemini-2.5-pro | None | Stronger baseline (expect 15-25%) |
| `pro_floop_3` | gemini-2.5-pro | 3 focused behaviors (no cadence) | Can a stronger model leverage behaviors? |

### Interpretation matrix

| Pattern | Meaning |
|---------|---------|
| bare=10%, placebo=10%, floop_3=15% | Focused behaviors help — floop works with fewer, better behaviors |
| bare=10%, placebo=5%, floop_3=5% | Any extra text hurts — need ultra-concise injection |
| bare=10%, placebo=10%, floop_3=5% | Behavior content is harmful — these behaviors steer wrong |
| pro_bare=25%, pro_floop_3=30%+ | Floop helps stronger models — positive result |
| pro_bare=25%, pro_floop_3=20% | Floop hurts even Pro — prompt-injected behaviors don't help for SWE-bench |

### Implementation notes

Override arms use `floop_context_override` instead of real floop volume. The harness computes context on the host and passes pre-built text to the container, bypassing `floop init`/`floop active`. This avoids volume setup complexity and ensures exact control over injected content.

### Results

#### Phase 1: Flash diagnostic

| Arm | Patches | Resolved | Rate | Completed | Timeouts | Cost |
|-----|---------|----------|------|-----------|----------|------|
| `gemini_flash_bare` (Run 7) | 4/20 | **2/20** | **10%** | 19 | 1 | $1.20 |
| `flash_floop_3` | 5/20 | **1/20** | **5%** | 19 | 1 | $1.46 |
| `flash_placebo` | 6/20 | **0/20** | **0%** | 19 | 1 | $1.41 |
| `gemini_flash_floop` (Run 7) | 6/20 | **0/20** | **0%** | 18 | 2 | $1.48 |

**Resolved tasks:**
- `gemini_flash_bare`: `django-16485`, `pylint-6903`
- `flash_floop_3`: `pylint-6903` only (the "locate exact function" behavior helped)
- `flash_placebo`: none
- `gemini_flash_floop` (Run 7): none

**Interpretation:** Clear dose-response — more prompt text = worse performance. Bare (0 extra chars) > floop_3 (511 chars) > placebo (2025 chars) = floop_21 (~3K chars). The 3 focused behaviors partially recovered `pylint-6903` that the 21-behavior version lost, but still lost `django-16485`. The problem is fundamentally **prompt length for Flash** — any extra context dilutes its limited attention.

#### Phase 2: Pro model

| Arm | Patches | Resolved | Rate | Completed | Timeouts | Cost |
|-----|---------|----------|------|-----------|----------|------|
| `gemini_pro_bare` | 0/20 | **0/20** | **0%** | 7 | 13 | $6.48 |
| `pro_floop_3` | 1/20 | **1/20** | **5%** | 8 | 12 | $6.27 |

**Resolved tasks:**
- `pro_floop_3`: `pylint-6903` (same task, behaviors helped Pro too)
- `gemini_pro_bare`: none

**Major confound: timeout.** Pro timed out on 13/20 (bare) and 12/20 (floop_3) tasks at 300s. Pro is much slower per API call than Flash (~2-4x thinking time), so most tasks never completed the agent loop. The 300s timeout that works for Flash is too tight for Pro.

**Despite the confound:** pro_floop_3 completed 1 more task (8 vs 7) and produced the only patch. The "locate the exact function" behavior consistently helps `pylint-6903` across both models — this is the one behavior with clear signal.

### Analysis

**What we learned:**

1. **Prompt length matters for Flash.** Clear monotonic degradation: 0 chars (10%) > 511 chars (5%) > 2K chars (0%) > 3K chars (0%). Flash has limited attention capacity and any extra context competes with the bug description.

2. **Focused behaviors > many behaviors.** 3 behaviors (5%) beat 21 behaviors (0%) on Flash. The "locate the exact function" behavior specifically fixed the `pylint-6903` failure mode it was designed for — across both Flash and Pro.

3. **Pro needs more time.** 300s timeout is insufficient for Gemini 2.5 Pro's thinking-heavy agent loop. 65% timeout rate makes the Pro comparison unreliable. Would need 600-900s timeout for meaningful Pro data.

4. **One behavior has real signal.** `pylint-6903` was resolved by the focused behaviors on both Flash and Pro, but not by bare Pro or placebo. The "locate the exact function in the traceback" behavior is genuinely helpful for navigation-error bugs. But n=1 is not statistically significant.

**What this means for floop:**

The core finding is nuanced: behavioral guidance **can help** (pylint-6903 is proof), but the **injection cost** (extra tokens in context) can outweigh the benefit for weak models. Floop needs either:
- Ultra-concise behaviors (single sentences, not paragraphs)
- Smarter injection (only inject relevant behaviors per-task, not all)
- Stronger models that can absorb extra context without attention loss

## Run 8b: Pro re-run with 600s timeout (2026-03-01/02)

**Goal:** Run 8 Phase 2 was invalidated by 65% timeout rate at 300s. Doubled timeout to 600s and bumped `API_TIMEOUT` from 60→90s per litellm call to give Pro enough time to complete agent loops.

**Arms:** gemini_pro_bare, pro_floop_3 (same 3 focused behaviors as Run 8)
**Budget:** ~$26 (actual: $26.02)

### Results

| Arm | Patches | Resolved | Rate | Completed | Timeouts | Cost |
|-----|---------|----------|------|-----------|----------|------|
| `gemini_pro_bare` | 7/20 | **2/20** | **10%** | 7 | 13 | $12.93 |
| `pro_floop_3` | 4/20 | **1/20** | **5%** | 7 | 13 | $13.09 |

**Resolved tasks:**
- `gemini_pro_bare`: `django-11239`, `django-16082`
- `pro_floop_3`: `django-11999`

**Note:** pro_floop_3 hit Gemini daily rate limit after task 13 (429 `generate_requests_per_model_per_day`). The remaining 7 tasks were re-run after quota reset the following day.

### Timeout analysis

Despite doubling the timeout from 300s → 600s, the timeout rate stayed at **65%** for both arms. The tasks that complete do so well under 600s (34-473s), while the tasks that timeout consistently hit the ceiling. This is a bimodal distribution — Pro either solves it quickly or gets stuck in exploration loops, regardless of time budget.

| Metric | Run 8 (300s) | Run 8b (600s) |
|--------|-------------|---------------|
| Pro bare timeout rate | 65% (13/20) | 65% (13/20) |
| Pro floop timeout rate | 60% (12/20) | 65% (13/20) |
| Pro bare patches | 0 | 7 |
| Pro bare resolved | 0 | 2 |

The extra time helped Pro **produce patches** (0→7 for bare) but didn't reduce timeouts. The stuck tasks need a different approach (e.g., explicit "give up and submit what you have" instructions near timeout).

### Head-to-head comparison

| Instance | Bare status | Bare patch | Floop status | Floop patch |
|----------|-------------|------------|--------------|-------------|
| django-13012 | completed | patch | completed | no patch |
| django-17084 | completed | patch | **timeout** | no patch |
| scikit-learn-14710 | **timeout** | patch* | completed | patch |
| django-13809 | **timeout** | patch* | timeout | no patch |
| django-11239 | completed ✅ | patch | **timeout** | no patch |
| django-16082 | completed ✅ | patch | **timeout** | no patch |
| django-11999 | **timeout** | no patch | completed ✅ | patch |
| django-14792 | completed | no patch | completed | patch |
| django-11749 | completed | patch | completed | patch |

*patch produced before timeout

**Observation:** The arms resolved completely different tasks. No overlap — bare got `django-11239` + `django-16082`, floop got `django-11999`. This is noise, not signal. With n=20 and 65% timeouts, the effective sample is ~7 tasks per arm — far too small for meaningful comparison.

### Conclusions

1. **600s didn't help timeouts.** Pro's timeout rate is structural (agent loops get stuck), not a time budget issue. Going from 300s→600s produced more patches but the same percentage of timeouts.

2. **Pro matches Flash on resolve rate.** Both Pro bare and Flash bare resolve 10% (2/20). Pro costs 10x more ($12.93 vs $1.20) for the same performance, suggesting Gemini 2.5 Pro doesn't bring meaningful capability gains for this agent harness + SWE-bench combo.

3. **Floop result is inconclusive.** Pro floop resolved 1/20 (5%) vs bare's 2/20 (10%), but on completely different tasks. With 65% of tasks timing out, the comparison lacks statistical power. We cannot determine whether behaviors help or hurt Pro.

4. **Experiment is budget-constrained.** At $26/run for Pro (40 tasks), we cannot afford the ~5 runs needed to reduce noise. Further Pro experiments are not cost-effective.

### What this means for the benchmark

The harness + mini_swe agent + SWE-bench Verified combination has fundamental limitations:
- **Flash** is cheap ($1.20/arm) but too weak to benefit from behavioral guidance (context noise dominates)
- **Pro** might benefit but is too slow (65% timeouts) and too expensive ($13/arm) to test with statistical power
- **The agent loop** (bash-only, no file editing tools, no test running) caps performance regardless of model or behaviors

To make further progress, we'd need either:
- A better agent (SWE-agent-style with proper tools) that raises the baseline above 10%
- A cheaper strong model where we can afford enough runs for statistical power
- An easier benchmark where current agent + model combos have a 30%+ baseline

## Cost ledger

| Run | Phase | Arm | Tasks | Cost | Notes |
|-----|-------|-----|-------|------|-------|
| 1 | smoke | gemini_flash_bare (3.x preview) | 2 | ~$0.00 | Model hung, 0 output tokens |
| 2 | smoke | haiku_bare | 2 | $0.53 | Too expensive for cheap arm |
| 3 | smoke | gemini_flash_bare | 2 | $0.08 | Baseline established |
| 4 | train | gemini_flash_floop | 30 | $1.70 | WORKDIR bug, floop init bug |
| 5 | train | gemini_flash_floop | 30 | $1.70 | 0 behaviors learned (prompt ignored) |
| 6 | train | gemini_flash_floop | 30 | $1.43 | 1 behavior learned (hybrid harness) |
| 7 | eval | bare + floop | 40 | $2.68 | 10% bare vs 0% floop resolved |
| 8a | eval | flash_floop_3 | 20 | $1.46 | 5% — 3 behaviors partial recovery |
| 8b | eval | flash_placebo | 20 | $1.41 | 0% — placebo text hurts too |
| 8c | eval | gemini_pro_bare | 20 | $6.48 | 0% — 65% timeout rate at 300s |
| 8d | eval | pro_floop_3 | 20 | $6.27 | 5% — behaviors help Pro on pylint-6903 |
| 8b-bare | eval | gemini_pro_bare (600s) | 20 | $12.93 | 10% — same timeout rate, more patches |
| 8b-floop | eval | pro_floop_3 (600s) | 20 | $13.09 | 5% — hit daily rate limit, re-ran next day |
| 9-bare | eval | mswea_bare | 20 | $2.27 | mini-SWE-agent, 20% resolve |
| 9-floop | eval | mswea_floop | 20 | $2.68 | mini-SWE-agent, 15% resolve (rate-limited) |
| — | smoke (various) | mixed | ~10 | ~$1.00 | Debugging sessions |
| **Total** | | | | **~$55.75** | |

## Run 9: mini-SWE-agent A/B test (2026-03-03)

**Goal:** Runs 7-8b capped at ~10% resolve rate with our homebrew `mini_swe` agent, too low for statistical power. mini-SWE-agent (SWE-agent's official lightweight successor, v2.2.6) scores ~60% on published benchmarks with Gemini 2.5 Flash. Switching to it should raise the baseline enough to detect floop's effect. Reuses the same 3 focused behaviors from Run 8 (shortest effective injection).

**Arms:** mswea_bare vs mswea_floop (3 behaviors, ~100 extra tokens)
**Agent:** mini-SWE-agent v2.2.6 with `swebench_xml.yaml` base config (XML action parsing, bash-only)
**Model:** Gemini 2.5 Flash (temperature=0, drop_params=true)
**Eval tasks:** 20 from `config/splits.json` eval split (SWE-bench Verified)

### Setup

- Installed mini-SWE-agent via `uv pip install mini-swe-agent`
- Created `config/mswea_bare.yaml` (model config only) and `config/mswea_floop.yaml` (model + 3 behaviors in `agent.system_template`)
- `system_template` fully replaces the base config's template (not merged), so floop config includes the full XML format instructions from `swebench_xml.yaml`
- Created `scripts/run_mswea.py` wrapper: `run`, `import-results`, `evaluate` subcommands bridging mini-SWE-agent output to floop-bench's DB/JSONL/eval pipeline
- Updated `analysis/analyze.py` to auto-detect any `*_bare` / `*_floop` arm pairs for paired comparisons (was previously hardcoded for haiku arms)

### Results

| Arm | Patches | Resolved | Rate | Submitted | RateLimitError | IndexError | LimitsExceeded | Cost |
|-----|---------|----------|------|-----------|----------------|------------|----------------|------|
| `mswea_bare` | 14/20 | **4/20** | **20%** | 14 | 2 | 1 | 3 | $2.27 |
| `mswea_floop` | 5/20 | **3/20** | **15%** | 5 | 12 | 2 | 1 | $2.68 |

**Resolved tasks:**
- `mswea_bare`: `django-13809`, `django-15037`, `django-15930`, `pylint-6903`
- `mswea_floop`: `astropy-14096`, `django-11551`, `django-13012`

### Major confound: Gemini TPM rate limits

**The floop arm results are invalid for A/B comparison.** The bare arm ran first with fresh Gemini quota and 14/20 tasks produced patches. The floop arm ran afterward and hit the 1M tokens-per-minute (TPM) input rate limit — 12/20 tasks exited with `RateLimitError` before producing any output.

| Exit status | Bare | Floop |
|-------------|------|-------|
| Submitted (produced patch) | 14 | 5 |
| RateLimitError (no output) | 2 | 12 |
| IndexError (empty Gemini response) | 1 | 2 |
| LimitsExceeded (cost limit) | 3 | 1 |

**All 4 bare-resolved instances were rate-limited in the floop arm** — floop never got to attempt them. The "Δ rate: -5.0%" in the analysis output is meaningless because the arms attempted different subsets of tasks.

On instances that both arms actually completed (produced patches), floop was **3/5 = 60%** vs bare's **4/14 = 29%**. But these are different tasks, so this comparison is also unreliable.

### Statistical analysis (for the record, not meaningful)

```
McNemar's test (floop vs bare, n=20):
  chi2 = 0.000, p = 1.0000
  Cohen's h = -0.132

Concordance table (n=20):
  Both solved:  0
  Only bare:    4
  Only floop:   3
  Neither:      13
```

Zero overlap in resolved tasks. p=1.0 — no detectable difference, but this is because the arms effectively ran on different task subsets.

### What went right

1. **mini-SWE-agent works.** The integration pipeline (`run_mswea.py`) successfully bridges mini-SWE-agent's output format to floop-bench's eval/analysis pipeline.
2. **Bare arm baseline: 20% (4/20).** This is 2x our homebrew agent's 10% and closer to published results. With a functioning baseline, floop has headroom to show improvement.
3. **Per-task cost: $0.11-0.13.** Very affordable — a clean 20-task arm costs ~$2.50.
4. **Analysis pipeline generalizes.** Auto-detected `mswea_bare`/`mswea_floop` pair without code changes.

### What went wrong

1. **Gemini TPM rate limit (1M input tokens/min)** destroyed the floop arm. Running 20 tasks sequentially with 1 worker still exceeded the per-minute budget as tasks ran faster than the minute cooldown.
2. **Sequential arm execution** meant arms faced different rate limit conditions. This is the fundamental flaw.
3. **IndexError (empty Gemini choices[])** — a known Gemini issue with the XML action format. Affects both arms (~5-10% of tasks).

### Lessons for Run 10

To get a valid A/B comparison:
1. **Interleave arms** — run tasks in shuffled order (bare-A, floop-A, bare-B, floop-B, ...) so both arms face identical rate limit conditions
2. **Add retry with backoff** — if a task exits with RateLimitError, wait 60s and retry (up to 3 attempts)
3. **Spread over time** — run with longer delays between tasks to stay under the 1M TPM/min ceiling
4. **Or use a different provider** — Anthropic Claude or OpenAI models have higher rate limits on paid tier
