# Training Phase Protocol

The training phase uses 30 held-out tasks to build floop behaviors. The agent uses floop organically during execution — learning behaviors as it works and querying them at task start. These behaviors accumulate in a Docker volume and are frozen (read-only) for evaluation.

The key constraint is **no data leakage** — behaviors must encode general principles, not task-specific fixes. The agent is instructed to keep learnings generalizable via the floop CLI cadence prompt (see `floop_integration/inject.py`).

## Steps

### 1. Run floop arm on training tasks

```bash
make train
# or: make train ARM=gemini_flash_floop
```

The agent runs inside a sandboxed container with the `floop-train` Docker volume mounted read-write. As it works through tasks, it:
- Queries existing behaviors at task start (`floop active`)
- Learns new behaviors when it discovers insights (`floop learn`)
- Knowledge accumulates across all 30 tasks in the volume

Produces transcripts in `results/transcripts/` and patches in `results/predictions/`.

### 2. Evaluate patches

```bash
uv run python -m harness.swebench_eval --arm haiku_bare --split train
```

Runs SWE-bench Docker evaluation and marks each task as resolved or unresolved in the database.

### 3. Review agent-created behaviors

The agent creates behaviors organically during training. Review them for quality:

```bash
make shell
# inside container:
floop active --root /floop-store --json | jq .
```

#### Good behaviors (agent should produce these)

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

The floop cadence prompt instructs the agent to keep learnings generalizable, but review is still important.

### 4. Leakage audit

```bash
make leakage
# or: uv run python -m scripts.check_leakage --volume floop-train
```

Scans every behavior in the Docker volume for:

- Instance IDs from the eval split
- File paths or function names unique to eval tasks
- Literal code snippets matching eval ground truth patches

The leakage audit runs automatically before `make eval` — eval is blocked if leaks are found.

## Tips

- The agent learns organically during training — you don't need to manually create every behavior.
- Review agent-created behaviors after training. Prune any that are too task-specific.
- Group failures by repo and look for patterns. If the agent missed a pattern, you can manually add it via `make shell`.
- Keep behaviors concise. One principle per behavior.
- `make clean` resets all volumes for a fresh training run.
