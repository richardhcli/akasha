# Agent Fleet Orchestrator — Architecture & Dispatch Protocol

**Status:** Development meta-tooling (not part of akasha product code).  
**Purpose:** Parallelize and optimize execution of akasha build-plan tasks across a 3-tier model hierarchy, strongly cutting token cost by matching task complexity to model cost.

---

## Tier System


| Tier       | Model                | Role                                                                                     | Invocation                      | Cost | Judgment?                                          |
| ---------- | -------------------- | ---------------------------------------------------------------------------------------- | ------------------------------- | ---- | -------------------------------------------------- |
| **Tier 1** | Opus 4.8             | Scanner only: eligible cohort from task-status + build-plan (no dispatch, no status I/O) | Claude Code Agent tool          | $$$  | Yes (decide what runs, parse ambiguity)            |
| **Tier 2** | Sonnet 5             | Task owner: decide Cursor vs direct, confirm Verify, own retries/BLOCKED                 | Claude Code Agent tool          | $$   | Medium (interpret spec, judge direct vs. delegate) |
| **Tier 3** | Cursor Grok 4.5 High | Edit executor **plus** local Verify loop; returns structured edit+verify evidence        | Subprocess (`cursor_bridge.py`) | $    | Low (follow JSON contract; may re-edit on Verify fail) |


**Rationale:** Frontier-model (Opus) time is expensive; spend it on judgment and cohort selection. Tier 2 (Sonnet) owns the task and escalation. Tier 3 (Cursor) is the cheapest place to run the edit→Verify→fix loop for mechanical/spec-following work — not merely a dumb patch applicator. Authoritative durable logging and `DONE` flips stay outside any LLM (caller + Workflow return values). Model selection is modular (env var + CLI flag) to support future swaps without code changes.

---



## Division of Responsibility (RACI)

Who does what today, and what is *authoritative* vs *advisory*:

| Concern                         | Cursor (T3)                         | Worker (T2)                                      | Independent verifier                | Caller (outer session)                          | Scanner (T1)        |
| ------------------------------- | ----------------------------------- | ------------------------------------------------ | ----------------------------------- | ----------------------------------------------- | ------------------- |
| Choose next cohort              | —                                   | —                                                | —                                   | invokes scanner                                 | **R** (structured)  |
| Spawn workers / verifiers       | —                                   | —                                                | —                                   | invokes Workflow (Path A, experimental) or `Agent` tool directly (Path B, default) | —                   |
| Edit files for a task           | **R** when delegated                | **R** when direct / on Cursor failure            | —                                   | —                                               | —                   |
| Run task `Verify` (first pass)  | **R** inside bridge session (target)| **A**: confirm / retry / BLOCKED                 | —                                   | —                                               | —                   |
| Independent re-Verify + git xchk| —                                   | —                                                | **R** / **A** for `CONFIRMED_*`     | —                                               | —                   |
| Durable run log under `logs/`   | contributes evidence fields only    | contributes structured result                    | contributes verdict                 | **R** / **A** (writes verbatim from Workflow)   | —                   |
| Flip `task-status.md` → `DONE`  | —                                   | —                                                | supplies verdict only               | **R** / **A** (only on `CONFIRMED_DONE`)        | —                   |
| `make check` / cohort settle    | —                                   | —                                                | —                                   | **R**                                           | —                   |


**R** = responsible (does the work). **A** = accountable (gate that must pass). Cells marked "contributes" are inputs to the accountable writer, never a substitute for it.

### Why this split (and what was suboptimal)

The *previous* Tier-3 contract treated Cursor as **edit-only**: the bridge explicitly did not run `Verify`, and the worker was told never to trust Cursor's self-report. That was correct about **trust** (never mark `DONE` from an LLM claim alone — see Verification Model / ORCHESTRATION-INCIDENT) but **suboptimal about cost and loop placement**:

1. **Verify belongs next to the editor.** Cursor already runs with `--force --trust` and can shell out. Forcing every Verify failure to bounce back to Sonnet for diagnosis doubles latency and spends $$ re-reading context Cursor already has open.
2. **Triple Verify without Cursor participation** (worker + independent verifier, with Cursor contributing nothing) wastes the cheapest tier on the loop that most often needs a second edit.
3. **Durable logging must not move to Cursor (or any worker LLM).** Workflow scripts have no filesystem access; the audit property is that prompts/results on disk are the *literal* harness-returned objects with no narration step. An agent "logging" by writing files reintroduces the fabrication surface the log was designed to close. Cursor should *emit* verify exit codes / usage / `files_changed` into the bridge JSON so the caller can persist them — it should not own `docs/agents/logs/`.

**Target contract (optimized):** Cursor = edit + local Verify loop + structured evidence. Worker = strategy, confirmation Verify, retries, BLOCKED. Independent verifier + caller = trust boundary and durable log. This keeps the incident-driven guarantees while putting mechanical re-edit cycles on `$` instead of `$$`.

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

The "orchestrator" role is now split across two components rather than one
prose-narrating agent — see "Verification model" below for why.

1. `fleet-orchestrator` **agent (scanner, Opus)** — reads task-status.md +
  build-plan.md, identifies all TODO tasks with satisfied dependencies,
   partitions by file disjointness into a cohort (sequential group collapsed
   to its first task, or a batch of fully parallel tasks), and returns that
   cohort as structured data. It does not spawn workers and does not write
   task-status.md.
2. Dispatch + verification, one `fleet-worker` agent per task (in parallel
   where file-disjoint) followed by a separate, independent verifier agent
   per task that re-runs Verify and cross-checks claimed files against
   `git status`. **Default mechanism (Path B): the caller dispatches this
   worker → verifier sequence directly via the `Agent`/`Task` tool** — see
   `docs/agents/runbook.md` "Path B: direct Task-tool dispatch" for the
   exact procedure and its stricter logging discipline (there is no
   deterministic script to enforce it here, so the caller carries that
   guarantee explicitly). **Experimental alternative (Path A):**
   `docs/agents/fleet-workflow.js`, a `Workflow` script that does the same
   dispatch inside deterministic code via `pipeline()`/`agent()` — stronger
   in principle, but confirmed unavailable from headless `claude -p`
   sessions (2026-07-18) and unverified even interactively; see
   `docs/agents/runbook.md` "Choosing a dispatch path" before reaching for
   it.
3. **Caller (the outer session)** — after the dispatch sequence above
   returns (Path B's direct calls, or Path A's `Workflow` if used), writes
   the durable log under `docs/agents/logs/<run_id>/` (see
   "Logging" below) and updates `docs/agents/task-status.md` /
   `docs/spec-questions.md`, flipping a task to `DONE` only when its
   verifier verdict is `CONFIRMED_DONE`. Runs `make check` (+ `make battery`
   for M5+ closures) before considering the cohort settled.



### Worker Responsibilities

1. **Understand the task** — read its Goal/Depends/Files/Spec/Steps/Verify/DoD from build-plan.md.
2. **Decide edit strategy** — delegate to Cursor or edit directly (see decision tree in `fleet-worker.md`).
3. **If delegating:** build task JSON including `verify_cmd` (task_id, goal, files, constraints, verify_cmd) and pipe to `python scripts/fleet/cursor_bridge.py`. Cursor is expected to edit **and** run Verify locally (see bridge contract).
4. **Always confirm Verify** — re-run the task's `Verify` yourself after Cursor returns (or after a direct edit). Treat Cursor's reported exit code as **advisory evidence**, not authority. Never mark the worker result `DONE` solely on Cursor's narrative.
5. **Report back** — status (DONE / BLOCKED), Verify output, files touched, Cursor bridge evidence (if any), any spec questions.

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
  "constraints": "No pickle/eval/exec. Follow spec §7.1 exactly. Output must be valid JSON, schema: [{id, type, facets, ...}, ...]",
  "verify_cmd": "uv run pytest tests/unit/test_golden_fixtures.py -q"
}
```

`verify_cmd` is required for the optimized contract. The bridge prompt instructs Cursor to: (1) edit only listed files, (2) run `verify_cmd`, (3) on non-zero exit, diagnose and re-edit within a small budget (typically 1–2 fix passes), (4) stop and report rather than inventing passing tests.



### Output

Single JSON line to stdout:

**On completed edit + local Verify attempt:**

```json
{
  "status": "completed",
  "files_changed": ["tests/golden/fixtures_facet.json"],
  "diff_stat": "tests/golden/fixtures_facet.json | 120 +++++++++...",
  "cursor_result_text": "Generated 15 fixtures covering Entity, Definition, Claim, Relation...",
  "verify_command": "uv run pytest tests/unit/test_golden_fixtures.py -q",
  "verify_exit_code": 0,
  "verify_stdout_tail": "... 19 passed in 0.4s",
  "usage": {
    "inputTokens": 8500,
    "outputTokens": 2100,
    "cacheReadTokens": 0,
    "cacheWriteTokens": 0
  }
}
```

`status: "completed"` means the Cursor session finished and returned structured evidence — **not** that the task is fleet-`DONE`. If Cursor exhausted its local fix budget with Verify still failing, still return `completed` with `verify_exit_code != 0` so the worker can retry direct or BLOCKED; do not invent a green Verify.

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

**Important:** The bridge **does** ask Cursor to run `verify_cmd` and return exit code + output tail as structured fields. That is *local* verification — cheap fix loops on Tier 3. It does **not** replace the worker's confirmation Verify or the Workflow's independent verifier. The worker remains accountable for the worker-schema `verify_*` fields; the independent verifier remains the gate for `CONFIRMED_DONE`.

---



## Verification Model

Verification is intentionally layered. Cheaper tiers catch most failures;
only the outermost independent stage is allowed to authorize `DONE`.

```
Cursor local Verify  →  Worker confirmation Verify  →  Independent verifier
     (advisory)              (worker schema)              (CONFIRMED_*)
```

**Never trust a worker's (or Cursor's) self-report as the sole basis for
marking a task `DONE`.** The caller spawns a second, independent agent per
task — the verifier — after the worker returns, either inside
`docs/agents/fleet-workflow.js`'s `agent()` call (Path A) or via a direct
`Agent`/`Task`-tool call (Path B, the default — see
`docs/agents/runbook.md`). The verifier:

1. Re-runs the task's exact `Verify` command itself, via its own `Bash`
  call, and records the real exit code and output tail.
2. Checks every path the worker claimed in `files_changed` actually exists
  on disk and is non-empty.
3. Cross-checks `git status --porcelain` / `git diff --name-only` against
  the claim.
4. Returns a structured verdict: `CONFIRMED_DONE`, `CONTRADICTS_CLAIM`, or
  `CONFIRMED_BLOCKED`.

A task is flipped to `DONE` in `docs/agents/task-status.md` only on
`CONFIRMED_DONE`. `CONTRADICTS_CLAIM` is treated exactly like any other
Verify failure: `BLOCKED: verifier contradicted worker claim`, pipeline
stops for that task.

This exists because of a real incident, not a hypothetical: the
orchestrating turn once narrated a fabricated "worker complete" result
before the real background task had actually finished, and that
fabrication was indistinguishable from a genuine report by inspection
alone — only an independent, out-of-band filesystem check caught it. A
`Workflow` script (Path A) closes the *orchestrator*-side version of this
bug by construction (its `agent()` calls are real blocking awaits, not
narratable text); direct dispatch (Path B, the default) closes the same gap
by the caller's explicit discipline instead — never write down a result you
have not actually received (`docs/agents/runbook.md` Path B). The
independent verifier stage closes the *worker*-side version — including a
separately documented Claude Code failure mode where a background
subagent's tool call can be silently auto-denied and the subagent then
self-reports as if it succeeded. See the `ORCHESTRATION-INCIDENT` entry in
`docs/archived-questions.md` for the full writeup.

**What Cursor verification is for:** shrinking the Sonnet retry loop. When
Cursor returns `verify_exit_code: 0` and the worker's own re-run agrees,
the independent verifier usually confirms quickly. When Cursor returns
non-zero, the worker can skip a wasted "hope it passed" confirmation and
go straight to direct fix or BLOCKED. What Cursor verification is *not*
for: authorizing `task-status.md` updates or replacing the durable log.

## Logging

Every dispatch run — via the Workflow script or via direct `Task`-tool
dispatch (`docs/agents/runbook.md` Path B) — is persisted to
`docs/agents/logs/<run_id>/` (`run_id` format:
`<YYYYMMDD-HHMMSS>-<milestone-label>`):

```
docs/agents/logs/<run_id>/
  manifest.json              # {run_id, cohort: [task_ids], final_status: "IN_PROGRESS"|"COMPLETE"|"PARTIAL"|"ABORTED"}
  workers/<task_id>/
    prompt.md                 # exact prompt sent to the worker, verbatim
    result.json                # worker's schema-validated structured result, verbatim
  verify/<task_id>/
    prompt.md                  # exact prompt sent to the verifier, verbatim
    result.json                 # verifier's schema-validated verdict, verbatim
```

Workflow scripts have no filesystem access, so this is written by the
caller (the outer Claude Code session) immediately after `await Workflow(...)` resolves, directly from the structured `results` array the
workflow returns — never from a re-narrated summary of it. This is what
makes the log trustworthy for manual review: every prompt on disk is the
literal string that was sent, and every result on disk is the literal
schema-validated object the harness returned, with no narration step in
between where a fabrication could be introduced.

**When there is no Workflow script** (Path B), there is also no filesystem
boundary and no automatic schema check standing between the caller and this
directory — so the caller writes these files via `scripts/fleet/log_run.py`
(which validates the same required fields `WORKER_SCHEMA`/`VERIFY_SCHEMA`
demand and refuses to write a malformed or duplicate entry) immediately
upon receiving each subagent's real result, before doing anything else with
it. See `docs/agents/runbook.md` "Path B" and "Logging" for the exact
procedure. This gap was real, not hypothetical: every task after `T4.1` in
the first logged run went through this exact caller-has-no-Workflow
situation and produced no log at all, because at the time neither Path B
nor `log_run.py` existed yet.

**Cursor's role in logging:** emit evidence into the bridge JSON
(`files_changed`, `verify_*`, `usage`) that the worker copies into its
structured result (`cursor_task_json` / `cursor_response_json`). The
caller then persists those fields inside `workers/<task_id>/result.json`.
Cursor must **not** write under `docs/agents/logs/` itself — agent-authored
log files would not be trustworthy for the same reason prose orchestration
is not.

## Hang Handling

Every worker and verifier prompt built by `fleet-workflow.js` includes a
soft guard: "if you haven't reached a terminal status within ~20 tool
calls, stop and report BLOCKED: possible hang." This is a prompt-level cap,
not a hard guarantee — there is no documented per-`agent()` wall-clock
timeout inside a Workflow script, and a script cannot reach in and kill a
single internal `agent()` call.

The real hard-kill lever is `TaskStop` on the entire `Workflow` invocation's
task ID, from the outer session, if a run shows no progress after a delay
sized to the cohort's expected runtime. On a force-stop: write
`manifest.json` with `final_status: "ABORTED"` and `hang_detected: true`,
and stop — do not retry automatically or burn further tokens. This is not
theoretical: a stalled planning subagent was force-stopped this exact way
while this system was being designed.

---



## Retry & Escalation



### Worker Retry Logic

On `Verify` failure (worker's confirmation, or Cursor returned non-zero):

1. **Retry 1:** Diagnose. If Cursor already reported a failing Verify with a
   useful stdout tail, use that diagnosis; prefer a focused direct edit over
   re-invoking Cursor blindly. If the first path was direct, fix the obvious bug.
2. **Retry 2:** Final attempt (direct edit preferred once Cursor has had its
   local fix budget).
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
→ Caller may mark cohort IN PROGRESS; invokes fleet-workflow.js

[Spawn in parallel via Workflow]
→ fleet-worker for T2.1 (IDs + checksums)
→ fleet-worker for T2.2 (Canonicalization)
→ fleet-worker for T2.3 (Canonicalization property tests)
→ fleet-worker for T2.4 (Golden corpus)

[T2.4 worker decides: "This is golden fixtures, verbatim from spec → delegate to Cursor"]
→ Build task JSON (incl. verify_cmd), pipe to cursor_bridge.py
→ Cursor edits fixtures, runs Verify locally, fix-loops if needed
→ Bridge returns status=completed, verify_exit_code=0, diff_stat, usage
→ Worker re-runs Verify (confirmation) → PASSED ✓
→ Independent verifier re-runs Verify + git cross-check → CONFIRMED_DONE
→ Caller writes docs/agents/logs/<run_id>/ from Workflow results; flips task-status

[All 4 workers finish + verifiers confirm]
→ Orchestrator flips all to Done, runs `make check` to gate the milestone
→ Continue (next phase OR stop)

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


| Task Type                                    | Tier       | Cost        | Example                |
| -------------------------------------------- | ---------- | ----------- | ---------------------- |
| Verbatim spec + local Verify loop            | 3 (Cursor) | ~$0.02–0.08 | T1.1, T2.4             |
| Straightforward coding (parser, store logic) | 2 (Sonnet) | ~$0.10–0.30 | T1.3, T3.2             |
| Spec interpretation + judgment               | 2 (Sonnet) | ~$0.30–0.50 | T5.4, T7.2             |
| Task orchestration (whole milestone)         | 1 (Opus)   | ~$2–5       | M2 cohort, all retries |


**Optimization win:** Without the fleet, every task runs on Opus ($$$) or Sonnet ($$). With the fleet, mechanical tasks run edit+Verify loops on Cursor ($), Sonnet only confirms / escalates, and Opus only scans cohorts — typically cutting 70–90% of token spend versus Opus-everywhere. Moving Verify *into* Cursor (vs edit-only Cursor) cuts a further Sonnet round-trip on the common "almost right, one fix" path.

---



## Failure Modes & Fallbacks


| Failure              | Orchestrator Behavior                    | Worker Behavior                                            |
| -------------------- | ---------------------------------------- | ---------------------------------------------------------- |
| Cursor unavailable   | (N/A — orchestrator doesn't call Cursor) | Fall back to direct edit; no cost                          |
| Cursor timeout       | (N/A)                                    | Report error to orchestrator; retry direct edit or BLOCKED |
| Cursor Verify ≠ 0    | (N/A)                                    | Use stdout tail; direct fix or BLOCKED (don't blind-retry Cursor) |
| Worker fails Verify  | Mark task BLOCKED, stop pipeline         | (Rule 0.9: never weaken test)                              |
| Spec ambiguity found | Stop pipeline, ask user                  | Draft SPEC-QUESTION: comment + entry                       |
| make check fails     | Stop pipeline, investigate               | (Caller re-runs make check after cohort)                   |


No failure path silently weakens guardrails or moves on. Failures surface, are logged, and block progress until resolved.

---



## Future Enhancements

1. **Bridge implementation of local Verify:** Update `scripts/fleet/cursor_bridge.py` + `compose_prompt` to pass `verify_cmd`, instruct the edit→Verify→fix loop, and parse/return `verify_exit_code` / `verify_stdout_tail` (target contract above; code may lag the doc until this lands).
2. **Alternative edit executors:** Replace `cursor_bridge.py` with a non-Cursor implementation (e.g., native Claude Code agent that shells out to `git apply` patches). Same JSON contract, including Verify fields. **Partially landed (2026-07-18):** `.cursor/agents/fleet-cursor-editor.md` is a native Cursor Task-tool subagent implementing the same abstract contract (task JSON in → `status`/`files_changed`/`verify_*` JSON out) without the subprocess hop — dispatch it directly with `model: "cursor-grok-4.5-high"` from a Cursor session instead of piping to `cursor_bridge.py`. Same trust rules apply: it is advisory evidence only, never the `DONE` authority.
3. **Multi-pass refinement:** Orchestrator could retry a failed task at a higher tier (direct Opus edit) if worker+Cursor fails.
4. **Cost tracking:** Aggregate `usage` from bridge JSON per task/tier/milestone into `manifest.json` for cost reporting (caller-side; still not agent-authored log prose) (determinsitic cost tracking per agent per task / tier / milestone)
5. **Distributed workers:** Run fleet-worker agents across multiple machines (not planned for MVP).
