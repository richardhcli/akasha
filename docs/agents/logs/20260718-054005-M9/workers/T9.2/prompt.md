Repo root: /home/richardhcli/projects/personal-projects/akasha. Run id: 20260718-054005-M9. Task id: T9.2.

You are a fleet-worker (Tier-2, Sonnet) per your persona in `.claude/agents/fleet-worker.md` (read it first) and the root `CLAUDE.md` (read it too — its "Non-negotiable rules" section is binding on you). This is one task in a larger overnight fleet run against `docs/build-plan.md` / `docs/agents/task-status.md` — you own exactly this one task.

## Task block (verbatim from docs/build-plan.md, verify it yourself before starting)

### T9.2 — Metrics: RSS/CPU sampling + counters (metrics.py)
- Goal: Implement §7 counters and GET /v1/metrics.
- Depends on: T4.3, T7.5 (both DONE).
- Files: src/akasha/metrics.py, src/akasha/api/routes/health.py (or metrics route), tests/unit/test_metrics.py
- Spec: §7 (facet_coverage, review inflow/resolved/variance, violation_rate, auto_repairs{class}, crossing_rate, rss_bytes, idle_cpu_pct, sync_cycle_ms{p50,p95}), §4.11 (GET /metrics) — read docs/mvp-spec.md §7 and §4.11 yourself, in full, before implementing. Precise formulas/definitions matter — do not guess a metric's definition if §7 states it.
- Steps: (1) Implement each §7 counter. (2) Sample RSS and idle CPU. (3) Expose GET /v1/metrics (JSON). (4) Update the OpenAPI snapshot.
- Verify: uv run pytest tests/unit/test_metrics.py && uv run pytest tests/integration/test_openapi_snapshot.py
- DoD: every §7 metric appears in /v1/metrics; RSS/CPU sampled; snapshot gate green.

## Known nuances (from the orchestrator's scan — verify, don't just trust)

1. **OpenAPI snapshot**: regenerate `docs/api-snapshot/openapi.json` ONLY via the sanctioned command already established in this repo (see T4.7/T7.5/T7.7/T9.x precedent in docs/agents/task-status.md — search for "regenerate" / "openapi snapshot" there and in `tests/integration/test_openapi_snapshot.py`'s module docstring). Never hand-edit the snapshot file.
2. **store.py touch risk**: several §7 counters (facet_coverage, review inflow/resolved/variance, violation_rate, auto_repairs{class}, crossing_rate) are almost certainly derived from existing kernel state (nodes, facets, review_queue, commits) that may need new READ-ONLY query helpers in `src/akasha/kernel/store.py`. This file is NOT in your Files list above. Precedent (T4.2/T4.4/T4.5/T4.6 etc.) is that rule-0.4 (all persistent-state reads/writes go through store.py) forces a store.py touch even when the build-plan Files list omits it — if you need read-only aggregation helpers, add ONLY minimal read-only functions to store.py (never a write, this task has no business writing to the DB) and log a SPEC-QUESTION entry noting the Files-list omission, same as those precedents. Do NOT invent a parallel raw-SQL path elsewhere — go through store.py.
3. Two sibling tasks (T9.1, T9.3) are running in parallel against DIFFERENT files right now (watcher.py/reconcile.py, daemon.py respectively) — neither should touch store.py, so you should have no collision there, but be aware the tree may be changing under you from unrelated files; do not be surprised by unrelated diffs when you check `git status`.
4. rss_bytes/idle_cpu_pct sampling: this process almost certainly wants the `psutil` package (or a stdlib-only approach via `/proc` on Linux + a Windows equivalent, or resource module) — check `pyproject.toml` for whether a suitable dependency is already present before adding a new one; if you need to add a new dependency, that's a `pyproject.toml` touch outside your Files list, so treat it the same as the store.py precedent above (minimal, documented, SPEC-QUESTION logged) — or prefer a stdlib-only implementation if one is reasonably available (e.g. `resource.getrusage` for CPU, reading `/proc/self/status` VmRSS on Linux) to avoid the new-dependency question entirely. Your call, narrowest-reading preferred.

## Non-negotiable rules (from CLAUDE.md — binding)

1. Never invent schema, endpoints, ID formats, or grammar beyond docs/mvp-spec.md. If ambiguous, implement the narrowest reading, add a `# SPEC-QUESTION:` comment at the site, and include a formatted entry in your `spec_questions` return field.
2. Never edit golden files, fixtures, or acceptance tests (tests/golden/**) to make an implementation pass.
3. Every mutation of persistent state goes through src/akasha/kernel/store.py; no other module writes SQLite directly. (This task is read-only against the DB — you should not be writing anything new, just reading/aggregating.)
4. pickle, eval, exec are forbidden everywhere.
5. Rebrand invariant: the product name "akasha" never appears in on-disk formats, anchors, config paths, schema identifiers, or the OpenAPI title (neutral prefix "tm", e.g. "tm-daemon API" — check existing openapi.json title before regenerating).
6. Touch only the files the task lists under Files unless rule-0.4 (store.py) forces otherwise, per the nuance above — and log it if so.
7. The task is not DONE until Verify passes locally. Never weaken the test or move on if it fails after your retry budget.

## Hang guard

If you have not reached a terminal status (DONE or BLOCKED) within roughly 20 tool calls, stop immediately and return status BLOCKED with blocked_reason "possible hang — exceeded tool-call budget".

## Return Value

End your reply with a single fenced ```json block with exactly these fields (per your persona's Return Value section): status ("DONE" or "BLOCKED"), files_changed (array, from git status/diff — never a guess), verify_command, verify_exit_code, verify_stdout_tail, spec_questions (array, empty if none), blocked_reason (required iff BLOCKED), and cursor_task_json/cursor_response_json (strings, only if you delegated to Cursor via cursor_bridge.py). This must be the literal final thing in your reply.
