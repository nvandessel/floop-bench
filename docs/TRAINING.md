# Training Phase Protocol

This is the most human-intensive part of the experiment. You'll analyze Haiku's failures on training tasks and distill them into general-purpose floop behaviors.

**Goal:** Create 30-80 behaviors that encode reusable debugging principles — not task-specific answers.

## Overview

1. Run Haiku on 30 training tasks (automated, ~$6)
2. Evaluate patches with SWE-bench Docker (automated)
3. Analyze failures and create behaviors (manual, 8-15 hours)
4. Audit for data leakage (automated)

## Step 1: Run Training Tasks

```bash
uv run python -m harness.orchestrator --phase train
```

This produces 30 transcripts in `results/transcripts/` and patches in `results/predictions/haiku_bare.jsonl`.

## Step 2: Evaluate Patches

```bash
uv run python -m harness.swebench_eval --arm haiku_bare --split train
```

This runs SWE-bench's Docker evaluation and marks each task as resolved or unresolved in the database. Expect Haiku to solve ~10-20% (3-6 tasks).

Check results:

```bash
uv run python -m analysis.analyze
```

## Step 3: Analyze Failures

For each failed task:

1. **Read the transcript** — what did Haiku try? Where did it go wrong?
2. **Read the ground truth patch** — what was the actual fix?
3. **Ask: is there a general principle that would have helped?**
4. If yes, create a behavior.

### Good Behaviors

Behaviors should encode **generalizable principles** that apply across multiple tasks:

- "In pytest, fixture scope determines lifetime — function-scoped fixtures reset between tests, session-scoped persist"
- "Python string slicing: `s[a:b]` excludes index b. Off-by-one errors usually mean the end index needs +1"
- "When Django's `get()` raises MultipleObjectsReturned, the fix is usually in the queryset filter, not in exception handling"
- "Before editing a method, check if it's overridden in subclasses — the fix may need to go in the base class"
- "When a test expects a specific exception message, grep for that message string to find the source"

### Bad Behaviors (Data Leakage)

These would inflate results and invalidate the experiment:

- "Change line 847 of query.py from `>=` to `>`" — task-specific code change
- "The fix for django-11099 is to add a null check in resolve()" — names the instance
- "In django/db/models/query.py, the _filter_or_exclude method is missing a clone() call" — too specific to one patch

**Rule of thumb:** if the behavior only helps with one specific task, it's leakage. If it helps with a class of tasks, it's a good behavior.

### Creating Behaviors

```bash
floop learn \
  --description "Django ORM: when a QuerySet method chains, verify it returns a new QuerySet rather than mutating in place" \
  --tags "python,django,queryset,orm"
```

Focus on patterns that repeat across multiple failures. Not every failure needs a behavior.

## Step 4: Leakage Audit

```bash
uv run python -m scripts.check_leakage
```

This scans every behavior for:

- Instance IDs from the eval split
- File paths or function names unique to eval tasks
- Literal code snippets matching eval ground truth patches

Fix any flagged behaviors before proceeding to the eval phase. **Never proceed to eval with leakage warnings.**

## Time Estimates

| Step | Time |
|------|------|
| Training runs + eval | ~2-3 hours (automated) |
| Human analysis and behavior writing | 8-15 hours |
| Leakage audit | ~1 hour |

## Tips

- Start with the failures where Haiku got closest — these are the most likely to benefit from a nudge in the right direction.
- Group failures by repo and look for patterns. If Haiku fails the same way on multiple Django tasks, that's one behavior, not three.
- Read the ground truth patch diff, not the full file. Focus on what changed and why.
- Keep behaviors concise. One principle per behavior. If you're writing a paragraph, split it up.
