# Training Phase Protocol

The training phase uses 30 held-out tasks to build floop behaviors. These behaviors are then injected into floop-enabled arms during evaluation.

The key constraint is **no data leakage** — behaviors must encode general principles, not task-specific fixes.

## Steps

### 1. Run baseline on training tasks

```bash
uv run python -m harness.orchestrator --phase train
```

Produces transcripts in `results/transcripts/` and patches in `results/predictions/haiku_bare.jsonl`.

### 2. Evaluate patches

```bash
uv run python -m harness.swebench_eval --arm haiku_bare --split train
```

Runs SWE-bench Docker evaluation and marks each task as resolved or unresolved in the database.

### 3. Analyze failures and create behaviors

For each failed task:

1. Read the transcript — what did the agent try? Where did it go wrong?
2. Read the ground truth patch — what was the actual fix?
3. Identify whether a general principle would have helped.
4. If yes, create a behavior.

#### Good behaviors

Generalizable principles that apply across multiple tasks:

- "In pytest, fixture scope determines lifetime — function-scoped fixtures reset between tests, session-scoped persist"
- "Python string slicing: `s[a:b]` excludes index b. Off-by-one errors usually mean the end index needs +1"
- "When Django's `get()` raises MultipleObjectsReturned, the fix is usually in the queryset filter, not in exception handling"
- "Before editing a method, check if it's overridden in subclasses — the fix may need to go in the base class"

#### Bad behaviors (leakage)

These would contaminate the evaluation:

- "Change line 847 of query.py from `>=` to `>`" — task-specific
- "The fix for django-11099 is to add a null check in resolve()" — names the instance
- "In django/db/models/query.py, the _filter_or_exclude method is missing a clone() call" — maps to a specific patch

**Rule of thumb:** if it only helps with one task, it's leakage. If it helps with a class of tasks, it's a valid behavior.

#### Creating behaviors

```bash
floop learn \
  --description "Django ORM: when a QuerySet method chains, verify it returns a new QuerySet rather than mutating in place" \
  --tags "python,django,queryset,orm"
```

Not every failure needs a behavior. Focus on patterns that repeat across multiple failures.

### 4. Leakage audit

```bash
uv run python -m scripts.check_leakage
```

Scans every behavior for:

- Instance IDs from the eval split
- File paths or function names unique to eval tasks
- Literal code snippets matching eval ground truth patches

Do not proceed to the eval phase with any leakage warnings.

## Tips

- Start with failures where the agent got closest — a small nudge is most likely to help there.
- Group failures by repo and look for patterns. Multiple similar failures should become one behavior, not many.
- Read the ground truth diff, not the full file. Focus on what changed and why.
- Keep behaviors concise. One principle per behavior.
