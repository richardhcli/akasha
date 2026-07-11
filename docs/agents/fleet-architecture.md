# Agent Fleet Orchestrator — Architecture & Dispatch Protocol

**Status:** Development meta-tooling (not part of akasha product code).  
**Purpose:** Parallelize and optimize execution of akasha build-plan tasks across a 3-tier model hierarchy, cutting token cost by matching task complexity to model cost.

---

## Tier System

| Tier | Model | Role | Invocation | Cost | Judgment? |
|------|-------|------|-----------|------|-----------|
| **Tier 1** | Opus 4.8 | Orchestrator: dispatch, verify results, manage task-status | Claude Code Agent tool | $$$ | Yes (decide what runs, parse ambiguity) |
| **Tier 2** | Sonnet 5 | Task executor: owns one task, decides tier-3 delegation, verifies | Claude Code Agent tool | $$ | Medium (interpret spec, judge direct vs. delegate) |
| **Tier 3** | Cursor Grok 4.5 High | Code editor: edits files per task JSON spec | Subprocess (`cursor_bridge.py`) | $$ | No (mechanical, constrained by JSON contract) |

**Rationale:** Frontier-model (Opus) time is expensive; spend it on judgment and orchestration. Tier 2 (Sonnet) handles most task execution, with good spec-reading ability. Tier 3 (Cursor Grok 4.5 High) is strong at large-context code edits with spec-following — reserved for verbatim-from-spec work, golden corpus generation, and repetitive boilerplate. Model selection is modular (env var + CLI flag) to support future swaps without code changes.

---

## Dispatch Model

### File-Disjoint Parallelism

Per `docs/agents/runbook.md`, tasks whose `Files` lists don't overlap are safe to run in parallel (no write conflicts). Tasks touching the same file(s) must run sequentially.

Example:
```
T2.1 → Files: [src/akasha/ids.py]
T2.2 → Files: [src/akasha/canonical.py]
T2.3 → Files: [tests/unit/test_ids.py]
T2.4 → Files: [tests/unit/test_canonical.py]

All disjoint → dispatch all 4 in parallel, wait for all to finish.

T1.3 → Files: [src/akasha/kernel/store.py, tests/unit/kernel/test_store.py]
T1.4 → Files: [src/akasha/kernel/store.py, ...]
T1.5 → Files: [src/akasha/kernel/store.py, ...]

T1.3, T1.4, T1.5 all touch store.py → run sequentially, one at a time.
```

### Orchestrator Responsibilities

1. **Read task-status.md + build-plan.md** → identify all TODO tasks with satisfied dependencies.
2. **Partition by file disjointness** → group into cohorts (sequential, or a batch of parallels).
3. **Spawn fleet-worker agents** (Sonnet) — one per task, or all at once if parallel.
4. **Reconcile results** → update task-status.md (DONE / BLOCKED), collect spec questions, run `make check` + `make battery` to verify.
5. **Own writes** to `docs/agents/task-status.md` and `docs/spec-questions.md` — workers report; orchestrator writes.

### Worker Responsibilities

1. **Understand the task** — read its Goal/Depends/Files/Spec/Steps/Verify/DoD from build-plan.md.
2. **Decide edit strategy** — delegate to Cursor or edit directly (see decision tree in `fleet-worker.md`).
3. **If delegating:** build task JSON (task_id, goal, files, constraints) and pipe to `python scripts/fleet/cursor_bridge.py`.
4. **Always verify** — run the task's `Verify` command yourself; never trust Cursor's self-report.
5. **Report back** — status (DONE / BLOCKED), Verify output, files touched, any spec questions.

---

## Cursor Bridge Contract (`scripts/fleet/cursor_bridge.py`)

**Abstract interface:** the "edit executor" can be any script that implements this JSON protocol.

### Input

Read task JSON from stdin:
```json
{
  "task_id": "T2.4",
  "goal": "Generate 15 golden test fixtures covering all facet types",
  "files": ["tests/golden/fixtures_facet.json"],
  "constraints": "No pickle/eval/exec. Follow spec §7.1 exactly. Output must be valid JSON, schema: [{id, type, facets, ...}, ...]"
}
```

### Output

Single JSON line to stdout:

**On successful edit:**
```json
{
  "status": "completed",
  "files_changed": ["tests/golden/fixtures_facet.json"],
  "diff_stat": "tests/golden/fixtures_facet.json | 120 +++++++++...",
  "cursor_result_text": "Generated 15 fixtures covering Entity, Definition, Claim, Relation...",
  "usage": {
    "inputTokens": 8500,
    "outputTokens": 2100,
    "cacheReadTokens": 0,
    "cacheWriteTokens": 0
  }
}
```

**If Cursor unavailable (not on PATH or not logged in):**
```json
{
  "status": "unavailable",
  "reason": "cursor-agent not found on PATH"
}
```
Worker will fall back to direct edit; this is not an error.

**On timeout:**
```json
{
  "status": "timeout",
  "detail": "Cursor timed out after 600s"
}
```

**On error:**
```json
{
  "status": "error",
  "detail": "cursor-agent exited with code 1: ..."
}
```

### Model Selection (Modular)

**Current default:** `grok-4.5-high` (Cursor Grok 4.5 High — strong spec-following, large-context code work).

Override via:
- `--model <model_id>` flag (e.g., `--model composer-2.5`)
- `AKASHA_FLEET_CURSOR_MODEL` env var (e.g., `export AKASHA_FLEET_CURSOR_MODEL=gpt-5.3-codex-high`)

**Design principle:** Model selection is parameterized in two places for refactorability:
- `run_cursor()` function default (internal)
- CLI argparse default (external/API)

This allows swapping models without touching the orchestrator or worker agents.

### Invocation (from fleet-worker)

```bash
echo '{"task_id":"T2.4",...}' | python scripts/fleet/cursor_bridge.py
```

**Important:** The bridge does NOT run the task's `Verify` command. It only reports "edits happened"; the worker is responsible for verifying correctness.

---

## Retry & Escalation

### Worker Retry Logic

On `Verify` failure:
1. **Retry 1:** Diagnose. If delegated to Cursor, try direct edit; if direct, fix obvious bug.
2. **Retry 2:** Final attempt.
3. **BLOCKED:** After 2 retries, report `BLOCKED: <reason>` with full Verify output. **Never weaken the test.**

Per CLAUDE.md rule 9: if Verify doesn't pass, the task isn't done.

### Orchestrator Escalation

If a task goes `BLOCKED`:
1. Investigate whether it's a spec ambiguity (worker should have drafted a `SPEC-QUESTION:` comment).
2. If spec ambiguity: log it to `docs/spec-questions.md` and stop the pipeline.
3. If genuine obstacle (e.g., dep not installed): communicate to user.
4. Do NOT try to unblock automatically — stop and ask.

---

## Constraints & Guardrails

All workers must obey the **non-negotiable rules** from akasha's root `CLAUDE.md`:

- **Rule 0.1:** Work milestones in dependency order. Never start a task before its dependencies are DONE.
- **Rule 0.2:** Never invent schema/endpoints/grammar beyond the spec. On ambiguity, add `# SPEC-QUESTION:` and stop.
- **Rule 0.3:** Never edit golden files / fixtures to make tests pass. Golden files are acceptance criteria.
- **Rule 0.4:** All persistent writes go through `src/akasha/kernel/store.py`. No direct SQLite edits.
- **Rule 0.5:** No `pickle`, `eval`, `exec` anywhere (enforced by ruff + test).
- **Rule 0.6:** Product name never appears in on-disk formats. Use neutral `tm` prefix.
- **Rule 0.7:** Run `make check` before closing any task; `make battery` for M5+ tasks.
- **Rule 0.8:** One task = one focused change. Touch only listed files. If you need unlisted files, stop and log `SPEC-QUESTION:`.
- **Rule 0.9:** Task is DONE only when `Verify` passes. Never weaken tests or move on.

Orchestrator and workers will be briefed with this list verbatim.

---

## Example Dispatch Flow

```
[Orchestrator starts]
→ Scan task-status.md: M0 is CLOSED, find next eligible milestone
→ M2 tasks (T2.1–T2.4) all TODO, all depend only on M0 (DONE)
→ Check file disjointness: all disjoint ✓
→ Flip all 4 to IN PROGRESS in task-status.md

[Spawn in parallel]
→ fleet-worker for T2.1 (IDs + checksums)
→ fleet-worker for T2.2 (Canonicalization)
→ fleet-worker for T2.3 (Canonicalization property tests)
→ fleet-worker for T2.4 (Golden corpus)

[T2.4 worker decides: "This is golden fixtures, verbatim from spec → delegate to Cursor"]
→ Build task JSON, pipe to cursor_bridge.py
→ Cursor returns status=completed, diff_stat shows fixtures.json +150 lines
→ Worker runs `make check` (task's Verify) → PASSED ✓
→ Report back to orchestrator: DONE, files={fixtures.json}, usage={...}

[All 4 workers finish]
→ Orchestrator flips all to DONE, runs `make check` to gate the milestone
→ `make check` passes ✓
→ Move to next phase: M1 + M3 are now eligible

[No spec questions, proceed to parallel cohorts...]
```

---

## Integration with Existing Runbook

The orchestrator's dispatch logic is a **pure automation of** the manual procedure in `docs/agents/runbook.md`. Both follow the same rules:
- File-disjoint parallelism (from runbook.md).
- Task structure and verification (from build-plan.md).
- Non-negotiable rules (from CLAUDE.md).

The orchestrator is an optional *acceleration*: spawn it via the Agent tool to run multiple tasks in parallel and batch-verify. The manual one-task-at-a-time procedure remains valid and is the fallback.

---

## Token Cost Estimation

Rough per-task costs (single pass, no retries):

| Task Type | Tier | Cost | Example |
|-----------|------|------|---------|
| Verbatim spec transcription (DDL, fixtures) | 3 (Cursor) | ~$0.01–0.05 | T1.1, T2.4 |
| Straightforward coding (parser, store logic) | 2 (Sonnet) | ~$0.10–0.30 | T1.3, T3.2 |
| Spec interpretation + judgment | 2 (Sonnet) | ~$0.30–0.50 | T5.4, T7.2 |
| Task orchestration (whole milestone) | 1 (Opus) | ~$2–5 | M2 cohort, all retries |

**Optimization win:** Without the fleet, every task (even T1.1 verbatim DDL) runs on Opus ($$$) or Sonnet ($$) at full cost. With the fleet, T1.1 runs on Cursor ($) and is verified by Sonnet, cutting 70–90% of token spend for mechanical tasks.

---

## Failure Modes & Fallbacks

| Failure | Orchestrator Behavior | Worker Behavior |
|---------|----------------------|-----------------|
| Cursor unavailable | (N/A — orchestrator doesn't call Cursor) | Fall back to direct edit; no cost |
| Cursor timeout | (N/A) | Report error to orchestrator; retry direct edit or BLOCKED |
| Worker fails Verify | Mark task BLOCKED, stop pipeline | (Rule 0.9: never weaken test) |
| Spec ambiguity found | Stop pipeline, ask user | Draft SPEC-QUESTION: comment + entry |
| make check fails | Stop pipeline, investigate | (Orchestrator re-runs make check after all workers) |

No failure path silently weakens guardrails or moves on. Failures surface, are logged, and block progress until resolved.

---

## Future Enhancements

1. **Alternative edit executors:** Replace `cursor_bridge.py` with a non-Cursor implementation (e.g., native Claude Code agent that shells out to `git apply` patches). Same JSON contract.
2. **Multi-pass refinement:** Orchestrator could retry a failed task at a higher tier (direct Opus edit) if worker+Cursor fails.
3. **Cost tracking:** Log token usage per task/tier/milestone for cost reporting.
4. **Distributed workers:** Run fleet-worker agents across multiple machines (not planned for MVP).
