# floop-bench

## Scientific Methodology — Non-negotiable

This is a benchmark project. Every claim must be backed by evidence. Every run must be documented honestly.

### Principles
1. **Name what you tested.** If floop-the-binary didn't run, don't call it a "floop arm." Call it what it is: prompt injection, hardcoded behaviors, manual heuristics — whatever is accurate.
2. **Separate the tool from the technique.** "3 hand-written sentences improved resolve rate" is a different finding than "floop improved resolve rate." Both are valuable. Don't conflate them.
3. **Report bad results.** A run where floop hurts performance is just as publishable as one where it helps. The goal is truth, not marketing.
4. **Document the full pipeline.** For each run: what binary ran, what version, where behaviors came from, how they were delivered to the agent, what the agent actually saw.
5. **Statistical honesty.** Report confidence intervals and p-values. Don't cherry-pick metrics. A +15pp result with p=0.45 is "not significant" — say so.
6. **Version everything.** floop version, model version, mini-SWE-agent version, config files — all go in the RUNBOOK.
7. **Reproducibility.** Another person should be able to re-run any experiment from the RUNBOOK + committed configs.

## Project Structure

- `config/` — YAML configs for each arm, `splits.json` for task splits
- `scripts/run_mswea.py` — CLI wrapper bridging mini-SWE-agent to floop-bench pipeline
- `harness/` — DB layer (`db.py`) and SWE-bench eval integration (`swebench_eval.py`)
- `analysis/analyze.py` — statistical analysis (bootstrap CIs, McNemar's test)
- `docs/RUNBOOK.md` — experiment log with per-run results and findings
- `results/` — predictions, trajectories, eval output (mostly gitignored)

## Key Technical Details

- mini-SWE-agent uses `swebench_xml.yaml` base config (XML action parsing, NOT tool calls)
- `-c` flag replaces config sections, doesn't merge — floop config must include full system_template
- `model_class: litellm` (not `openrouter`) for Gemini models
- Gemini TPM rate limit: 1M tokens/min input — use `--delay 60` between tasks
- SWE-bench eval per-instance `report.json` is more reliable than top-level summary
