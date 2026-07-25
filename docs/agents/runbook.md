# Overnight autonomous-agent runbook

This describes how to actually kick off an unattended run through
`docs/build-plan.md`, once you're ready to. Nothing here runs automatically —
this is the procedure a human (or a session acting on the human's explicit
request) follows to start one.

## Choosing a dispatch path

There are two dispatch mechanisms, but they are **not** currently equally
available — pick based on the confirmed status below, not on preference:

- **Path B — direct `Agent`/`Task`-tool dispatch. Use this by default,
  everywhere, including the `claude -p` overnight loop in
  `scripts/fleet/overnight_runner.sh`.** Confirmed working: Cursor's `Task`
  tool (2026-07-17) and Claude Code's headless `claude -p` via the `Agent`
  tool (2026-07-18 — see the Path A caveat below for why this, not
  `Workflow`, is what the overnight loop actually uses). You are the caller
  *and* the dispatcher, so the discipline below has to be carried by you
  explicitly instead of by a deterministic script.
- **Path A — `Workflow` tool. Experimental; interactive Claude Code only;
  do not use for headless/`claude -p` dispatch.** Confirmed **not**
  available from headless `claude -p` (2026-07-18, both empirically — a
  live `claude -p` session given an explicit `Workflow({scriptPath, args})`
  instruction searched via `ToolSearch` and correctly reported the tool
  does not exist in its toolset, rather than fabricating a result — and per
  Anthropic's own docs: dynamic workflows are gated behind a `/config`
  opt-in some plans default to off, saved workflow scripts live under
  `.claude/workflows/` and are invoked by name/slash-command, and the
  natural-language `ultracode` trigger that would otherwise turn a prompt
  into a workflow run is explicitly excluded for "a prompt passed with
  `-p`" — see code.claude.com/docs/en/workflows). Whether it works from an
  *interactive* Claude Code session is plausible per those same docs but
  has not been tested here. Do not point `overnight_runner.sh` at it. See
  the `WORKFLOW-TOOL-HEADLESS-GAP` entry in `docs/archived-questions.md`
  for the full writeup, including what a future interactive-only attempt
  would require (the `/config` toggle, relocating
  `docs/agents/fleet-workflow.js` under `.claude/workflows/`, and invoking
  it by slash-command rather than by asking a headless session to call a
  tool literally named `Workflow`).

**Both paths converge on the same contract where both apply:** the same
eligibility scan, the same worker/verifier schemas, the same durable log
layout under `docs/agents/logs/<run_id>/`, and the same rule that a task
becomes `DONE` in `docs/agents/task-status.md` only on a `CONFIRMED_DONE`
verdict. If Path A is ever confirmed usable from an interactive session, it
remains an *accelerant* over Path B (a deterministic script closes the
ORCHESTRATION-INCIDENT failure mode by construction instead of by
discipline) — not a different contract.

## Path A: fleet-dispatch Workflow

> **Status (2026-07-18): experimental, interactive-only, unverified. Do not
> use for `overnight_runner.sh` or any headless `claude -p` invocation** —
> see "Choosing a dispatch path" above. This section is kept because the
> design (a deterministic script closing the ORCHESTRATION-INCIDENT failure
> mode by construction) is still the better one *if* a `Workflow` tool is
> genuinely reachable, and because it documents what a future interactive
> session would need to do to actually try it.

`docs/agents/fleet-architecture.md` defines a 3-tier agent hierarchy (Opus
scanner → Sonnet worker → Cursor editor). Dispatch and verification are
implemented as a `Workflow` script, `docs/agents/fleet-workflow.js`, not as
a prose-narrating orchestrator agent — see "Why this is a Workflow, not an
orchestrator agent" below for why that distinction matters.

**How to use:**
1. Spawn a `fleet-orchestrator` agent via the Agent tool to scan
   `docs/agents/task-status.md` + `docs/build-plan.md` and return the next
   eligible, file-disjoint cohort (see `.claude/agents/fleet-orchestrator.md`
   — it is scanner-only now, it does not dispatch or narrate results). If
   you want this run's workers to be pure-Claude (no Cursor delegation),
   say so explicitly in the orchestrator's spawn prompt (e.g. "use
   pure-Claude workers, no Cursor") — this is the primary signal it checks;
   `AKASHA_FLEET_WORKER_MODE=claude-only` is a best-effort fallback if unset
   (see `docs/agents/fleet-architecture.md` §"Worker Mode Selection"). The
   orchestrator stamps its resolved choice as `worker_agent_type` on every
   task in the cohort it returns.
2. Generate a `run_id` (`<YYYYMMDD-HHMMSS>-<milestone-label>`) and invoke:
   ```
   Workflow({
     scriptPath: "docs/agents/fleet-workflow.js",
     args: { run_id, cohort: <cohort from step 1> },
   })
   ```
3. When the Workflow returns, its `results` array contains, per task, the
   worker's structured result and an independent verifier's structured
   verdict (`CONFIRMED_DONE` / `CONTRADICTS_CLAIM` / `CONFIRMED_BLOCKED`).
   Only flip a task to `DONE` in `docs/agents/task-status.md` when its
   verdict is `CONFIRMED_DONE` — a `CONTRADICTS_CLAIM` verdict means the
   worker claimed success but independent re-verification found otherwise;
   treat it as `BLOCKED: verifier contradicted worker claim` and stop the
   pipeline for that task, same as any other Verify failure.
4. Persist a durable log for the run under `docs/agents/logs/<run_id>/`
   (see "Logging" below) using the real structured data the Workflow
   returned — never a re-narrated summary of it.
5. Repeat from step 1 for the next eligible cohort.

**If a `Workflow` tool isn't available in your session, this path cannot be
followed literally (there is no script to await) — use Path B instead.**
As of 2026-07-18 that includes every headless `claude -p` session tested
(see the status note above) — Path B is not just the fallback for "no
`Workflow` tool", it is currently the *only* confirmed-working path there.

### Why this is a Workflow, not an orchestrator agent

An earlier version of this system had an `fleet-orchestrator` agent spawn
`fleet-worker` agents via the `Agent` tool directly and report on their
results in prose. A live incident showed this is unsafe: the orchestrating
turn narrated a fake "worker complete" result — before the real background
task's actual completion had arrived — and that fabrication was
indistinguishable from a genuine report until an independent filesystem
check caught the contradiction. A `Workflow` script's `agent()` calls are
real synchronous awaits in deterministic code: the script cannot proceed
until the harness resolves the actual subagent result, so this failure mode
is closed by construction, not by prompting discipline. See the
`ORCHESTRATION-INCIDENT` entry in `docs/archived-questions.md` for the full
incident writeup.

## Path B: direct Task-tool dispatch

**This is the default path as of 2026-07-18** — use it whenever your
session has a `Task`/`Agent`-style subagent tool, which in practice means
always: confirmed on Cursor (2026-07-17, `Task` tool) and confirmed under
Claude Code's headless `claude -p` (2026-07-18) as far as: `subagent_type:
"fleet-worker"` resolved correctly via Claude Code's native
`.claude/agents/*.md` loading, ran, and produced the exact on-disk artifact
asked for (verified directly from the outer session, not from the worker's
self-report). The run then hit the account's real usage-window limit before
it could spawn the independent verifier or reach the logging step — so the
verifier `subagent_type` resolution and the full
worker→verifier→`log_run.py` chain are reasoned-correct but still pending a
live re-run once the window resets. Because there is no deterministic
script standing between you and each subagent's result, **you** are the
boundary that the ORCHESTRATION-INCIDENT closed for Path A by construction
— the same guarantee has to come from discipline here instead. That
discipline is one rule, applied consistently:

> **Never write down — in a log file, in `task-status.md`, or in your own
> reply — a result you have not actually received.** If you dispatched a
> subagent in the background, wait for its real completion notification
> before saying anything about its outcome. Do not estimate, anticipate, or
> narrate ahead of the actual return value, no matter how confident you are
> about what it will say. This is the literal ORCHESTRATION-INCIDENT failure
> mode, ported to whatever tool you're using instead of `Workflow`.

**How to use:**
1. Spawn a `fleet-orchestrator` agent via `Task`
   (`subagent_type: "fleet-orchestrator"`, foreground) to scan
   `docs/agents/task-status.md` + `docs/build-plan.md` and return the next
   eligible, file-disjoint cohort. Identical scanner, identical output
   contract as Path A — this step does not change based on dispatch
   mechanism. If you want pure-Claude workers for this run, state that
   explicitly in the orchestrator's spawn prompt (fallback:
   `AKASHA_FLEET_WORKER_MODE=claude-only`) — see
   `docs/agents/fleet-architecture.md` §"Worker Mode Selection". The
   returned cohort carries `worker_agent_type` per task.
2. Generate a `run_id` (`<YYYYMMDD-HHMMSS>-<milestone-label>`) the same way.
3. For each task in the cohort:
   a. Build a task-specific prompt with the same content
      `fleet-workflow.js`'s `buildWorkerPrompt()` would produce (Goal /
      Depends on / Files / Spec / Steps / Verify command / DoD, the
      non-negotiable rules, the hang guard, and an explicit instruction to
      end the reply with a fenced ```json block matching `WORKER_SCHEMA`
      in `docs/agents/fleet-workflow.js`). Dispatch it via `Task` with
      `subagent_type: task.worker_agent_type` (i.e. `"fleet-worker"` or
      `"fleet-worker-claude"`, per the orchestrator's stamped value —
      default to `"fleet-worker"` only if the cohort task object is
      missing the field entirely). If your `Task` tool rejects
      `"fleet-worker-claude"` as an invalid enum value — plausible for the
      same reason `fleet-verifier` was rejected on 2026-07-17 in step (c)
      below: a newly-added persona file can postdate whatever fixed the
      enum in your session — fall back to `subagent_type: "fleet-worker"`
      (confirmed-resolving) and add an explicit line to the worker prompt:
      "Do not invoke `scripts/fleet/cursor_bridge.py` or any Cursor
      subprocess under any circumstance — edit directly only." This
      preserves the pure-Claude guarantee even when the harness doesn't yet
      recognize the dedicated agent type; unlike the verifier's
      `generalPurpose` fallback in (c), don't fall back to
      `generalPurpose` here — `fleet-worker`'s own prompt already carries
      the retry/BLOCKED/return-schema discipline this task needs, the inlined
      line just strips its Cursor option. Respect file-disjoint parallelism
      exactly as `docs/agents/fleet-architecture.md` §"File-Disjoint
      Parallelism" describes: file-disjoint tasks may be dispatched as
      multiple backgrounded `Task` calls together; tasks sharing a file
      must run sequentially, one at a time.
   b. Wait for the worker's real result per the rule above. For a
      backgrounded call, that means waiting for the actual completion
      notification — not proceeding on a guess in the meantime.
   c. Once received, build a verifier prompt with the same content
      `buildVerifierPrompt()` would produce (or, equivalently, the **Steps**
      / **Return Value** sections of `.claude/agents/fleet-verifier.md`,
      demanding a fenced ```json block matching `VERIFY_SCHEMA`). Try
      `subagent_type: "fleet-verifier"` first — if your `Task` tool rejects
      it as an invalid enum value (as Cursor's did on 2026-07-17, since that
      persona file postdates whatever fixed the enum), fall back to
      `subagent_type: "generalPurpose"` with the same prompt content
      inlined; this is exactly what `fleet-workflow.js` already does for
      its own verifier call (it sets no `agentType` there either).
   d. Wait for the verifier's real result per the rule above.
4. Persist every prompt and result **immediately on receipt, before doing
   anything else with it** — see "Logging" below. Do this before touching
   `task-status.md`.
5. Update `docs/agents/task-status.md` using only `CONFIRMED_DONE` verdicts,
   exactly like Path A step 3 — `CONTRADICTS_CLAIM` is
   `BLOCKED: verifier contradicted worker claim`, pipeline stops for that
   task.
6. Repeat from step 1 for the next eligible cohort.

**Fallback within this path:** the manual one-task-at-a-time procedure in
"Starting a run" below remains valid if you prefer single-task control over
dispatching a whole cohort at once.

## Logging

Every dispatch run — Path A or Path B — should be persisted to
`docs/agents/logs/<run_id>/` for a durable, human-reviewable audit trail:

```
docs/agents/logs/<run_id>/
  manifest.json              # {run_id, cohort: [task_ids], final_status, notes}
  workers/<task_id>/
    prompt.md                 # the exact prompt sent to the worker (verbatim)
    result.json                # the worker's schema-validated structured result
  verify/<task_id>/
    prompt.md                  # the exact prompt sent to the verifier (verbatim)
    result.json                 # the verifier's schema-validated verdict
```

`final_status` is one of `IN_PROGRESS` (cohort dispatched, not yet fully
resolved — legitimate, not just a terminal-state enum: this is the real
state a manifest sits in between tasks landing), `COMPLETE`, `PARTIAL`, or
`ABORTED`.

**Path A:** write these files from the Workflow's returned `results` array
directly after `await Workflow(...)` resolves — the workflow script itself
has no filesystem access, so this is the caller's responsibility, and it
must come from the structured return value, not a freeform description of
it. You may write the files directly or pipe each result through
`scripts/fleet/log_run.py` (below) for the same schema-validation Path B
gets — either is acceptable here because the Workflow's return value is
already fully-trusted structured data.

**Path B:** you have no automatic schema validation and no filesystem-access
boundary protecting you from accidentally narrating instead of transcribing
— so routing every result through the mechanical writer is not optional
here, it's the only thing standing in for what a `Workflow` script would
have enforced by construction. Extract the fenced ```json block from the
subagent's real, received final message verbatim (copy it — do not retype
or "clean up" any field), save the exact prompt you sent to a temp file,
and run:

```bash
python scripts/fleet/log_run.py task \
  --run-id <run_id> --task-id <task_id> --kind worker \
  --prompt /tmp/<task_id>-worker-prompt.md --result -   # paste/pipe the JSON block
```

(and the equivalent `--kind verify` call for the verifier's result). Update
the manifest as tasks land:

```bash
python scripts/fleet/log_run.py manifest \
  --run-id <run_id> --cohort <task_id> [<task_id> ...] --status IN_PROGRESS
```

`log_run.py` validates required fields for the given `--kind` (mirroring
`WORKER_SCHEMA`/`VERIFY_SCHEMA` in `fleet-workflow.js`) and **refuses to
write anything** — no partial files — if the JSON is malformed or missing a
required field, or if a `(run_id, task_id, kind)` entry already exists
(pass `--force` for a genuine re-verification). It fails loudly instead of
silently; a validation error means you go find out why, not paper over it.
It never talks to a model and never invents a field — it is exactly as
trustworthy as the JSON you give it, which is why the "copy the fenced
block verbatim" step above is the load-bearing part, not the script.

## Hang handling

A worker or verifier agent that stalls should self-report `BLOCKED:
possible hang — exceeded tool-call budget` (Path A: this guard is baked
into every prompt `fleet-workflow.js` builds; Path B: include the same
instruction in your own prompt, or rely on `.claude/agents/fleet-worker.md`'s
/ `fleet-verifier.md`'s built-in hang-guard section) — a soft, prompt-level
cap, not a guarantee.

**Path A:** there is no documented hard per-`agent()` timeout inside a
Workflow script. If a `Workflow` run itself appears wedged (no progress
after a delay sized to the cohort's expected runtime), force-stop it with
`TaskStop` on the Workflow's task ID, write a `manifest.json` with
`final_status: "ABORTED"` and `hang_detected: true`, and stop — do not
retry automatically. This has been exercised in practice (a stalled
planning subagent was force-stopped this way during this system's design).

**Path B:** if a backgrounded `Task` call appears wedged (no completion
notification after a delay sized to the task's expected runtime), apply the
same rule as everywhere else in this path — do not fabricate or guess its
outcome, and do not let the silence stop you from logging what you do know.
Record `docs/agents/logs/<run_id>/manifest.json` via `log_run.py ...
--status ABORTED --notes "<task_id> never returned a completion
notification as of <time>"`, and stop dispatching further tasks in that
cohort until a human looks at it.

## Preconditions

- `docs/agents/task-status.md` is up to date — the next `TODO` task's
  dependencies are all `DONE`.
- `make check` is green on the current tree (`uv run ruff check src tests &&
  uv run pyright src && uv run pytest tests/unit tests/property`).
- No open `BLOCKED:` entries in `docs/agents/task-status.md` on the critical
  path (`M0 → M1/M2 → M3 → M4 → M5 → M7 → M10`, per the dependency map in
  `docs/build-plan.md`).

## Starting a run

Use Path B (direct `Task`/`Agent`-tool dispatch) by default, or Path A (the
`Workflow` tool) only from an interactive session where you've confirmed it
actually works — see "Choosing a dispatch path" above — following a
procedure that:

1. Reads `docs/agents/task-status.md` to find the next `TODO` task per
   milestone in dependency order (`M0 → {M1, M2} → M3 → M4 → {M5, M7} → {M6,
   M8} → M9 → M10`; `M6` and `M8` are parallelizable once their deps close,
   per `docs/build-plan.md`'s dependency map).
2. Spawns one agent per task, each briefed with: the task's full entry from
   `docs/build-plan.md` (Goal/Depends on/Files/Spec/Steps/Verify/DoD), the
   root `CLAUDE.md` rules, and an instruction to run the task's `Verify`
   command before reporting done.
3. On success, flips that task's row in `docs/agents/task-status.md` to
   `DONE` and moves to the next eligible task. On failure, sets
   `BLOCKED: <reason>` and stops that branch rather than guessing past it.
4. Never starts a task whose dependencies aren't `DONE` — `pipeline()` (Path
   A) or a background-and-await group (Path B) is safe within a milestone's
   independent tasks; tasks with `Depends on` pointing at same-milestone
   siblings need a barrier or sequential stage.

Because tasks within a milestone often share files (e.g. `T1.3`–`T1.7` all
touch `src/akasha/kernel/store.py`), run same-file tasks **sequentially**,
not in parallel — parallel agents editing the same file will conflict.
`docs/build-plan.md`'s per-task `Files` list tells you which tasks are
file-disjoint and safe to fan out.

## Guardrails carried over from `docs/build-plan.md` and root `CLAUDE.md`

- Never invent schema, endpoints, ID formats, or grammar beyond
  `docs/mvp-spec.md`. Ambiguity → narrowest reading + `# SPEC-QUESTION:` +
  an entry in `docs/spec-questions.md`.
- Never edit golden files, fixtures, or acceptance tests to make something
  pass.
- All persistent writes go through `src/akasha/kernel/store.py`.
- `pickle`/`eval`/`exec` are banned everywhere (enforced by
  `tests/unit/test_no_pickle_ban.py` and the ruff config in
  `pyproject.toml`).
- A task is not `DONE` until its `Verify` command passes locally.

## Morning review

Check `docs/agents/task-status.md` for `BLOCKED:` rows and
`docs/spec-questions.md` for anything logged overnight — both need a human
decision before the next run continues past them.

For the practical "how do I start/watch/stop the tmux overnight loop and
what's it working on tonight" walkthrough, see
`docs/agents/overnight-guide.md` (procedure) and
`docs/agents/overnight-goals.md` (current goal set).
