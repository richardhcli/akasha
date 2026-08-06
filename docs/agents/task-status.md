# Task status

Machine-checkable status for every task in `docs/build-plan.md` (M13–M17, 19
tasks total: 6 + 6 + 2 + 2 + 3). This file is the single source of truth for "what's done" — an
autonomous agent picking up work should read this file first, find the next
`TODO` task whose `Depends on` tasks are all `DONE` **and** whose milestone's
`Depends on:` milestones are all closed, and work it per the rules in
`docs/build-plan.md` §"How to use this plan" and root `CLAUDE.md`.

Status values: `TODO` → `IN PROGRESS` → `DONE`, or `BLOCKED: <reason>`.
A milestone is closed only when every task in it is `DONE` and the
milestone's own DoD (stated in `docs/build-plan.md`) passes.

When you finish a task: flip its status here in the same change that closes
the task, and note anything a future agent needs (e.g. a new
`docs/spec-questions.md` entry) in the Notes column.

> **Predecessor state — M0–M12 are DONE and archived, do not re-open them.**
> The plan that built the MVP lives at `docs/pre-mvp/build-plan.md` with its
> final per-task status at `docs/pre-mvp/task-status.md` (82 tasks). Every
> row there is `DONE` except pre-mvp **T11.2** (`BLOCKED: human-only`,
> permanently, by design — nothing in this file supersedes or unblocks it)
> and pre-mvp **T12.6**, which is carried forward into this plan as
> **T17.1** because a `TODO` row living only under `docs/pre-mvp/` is
> structurally undispatchable (`fleet-orchestrator` scans exactly
> `docs/build-plan.md` + this file). That predecessor state was confirmed by
> a full green run at the head of the 2026-08-05 planning session: **644
> tests passed** across `tests/unit tests/property tests/integration` plus
> `tests/battery`, `ruff` clean, `pyright src` 0 errors. All nine PRD §8
> acceptance stories are GREEN in `docs/acceptance.md`; hosted
> `windows-latest`/`ubuntu-latest` CI has been green since run
> `30183257449` (2026-07-26). Treat all of that as settled history: build
> forward, do not re-verify.
>
> **Two legs remain honestly pending from the predecessor plan** (tracked in
> `docs/acceptance.md` row 9, not re-registered as tasks here): the literal
> 24-hour `nightly-soak` run has still never completed on a real scheduled
> trigger (and per pre-mvp T9.8's finding, GitHub-hosted runners hard-cap at
> 6h, so its duration/chunking needs re-scoping before it can), and the
> real-deployment (non-ephemeral) autostart/kill-9 attestation has no CI
> equivalent by nature — the 2026-07-25 local-Windows demonstration is
> evidence toward it, not a substitute. Neither blocks anything in M13–M17.
>
> **What this plan is for.** A spec-vs-shipped-code audit on 2026-08-05 (the
> method `docs/agents/overnight-goals.md` §"When the list is empty"
> prescribes) found that the two capabilities the product is *for* are each
> reachable from only part of the system: todo sync's vault→hub half is
> real and battery-proven while its hub→vault half is unwired (no endpoint
> sets `task_state`; nothing re-projects a file after a hub-side commit),
> and the definition DAG's kernel is real and correct while no user surface
> can build or navigate it (no CLI edge/vet/split/merge/neighborhood/history
> verb; the Web UI's only write is a review resolution, so `POST /edges`'s
> `facet_span` has no UI at all). M13/M14 close those gaps, M15/M16 put both
> in front of a real user, M17 documents the result. Full reasoning and the
> four binding narrowest-readings: `docs/build-plan.md` header and
> `docs/spec-questions.md` entries T13.1, T13.3, T14.2, T14.6.

---

## M13 — Todo synchronization & transclusion: close the hub-side round-trip (Depends on: nothing)

Milestone DoD: a task node can be created, nested, completed and re-opened
from every surface (Obsidian checkbox, CLI, Web UI, HTTP API) with the change
landing in both hub and managed vault file within one sync cycle, with no
restart or manual rescan; PRD §8 story 8's loop exercised end-to-end through
real production paths; `make check` + `make battery` green.

> **Eligibility note for the overnight/fleet scanner:** all six tasks are
> mechanical and fully verifiable by `pytest`/`make check` — safe for
> autonomous dispatch. **T13.1 and T13.3 both touch
> `src/akasha/api/routes/nodes.py`; T13.3 and T13.6 both touch
> `tests/integration/test_projection_writeback.py` (T13.6's dependency
> already serializes them); T13.4 and (in M14) T14.1–T14.4 all touch
> `src/akasha/cli/main.py`; T13.5 and (in M14) T14.5/T14.6 all touch
> `src/akasha/ui/static/app.js` — dispatch each of those groups
> sequentially, never in parallel.** T13.1 and T13.2 are genuinely
> file-disjoint and are the natural first parallel cohort. T13.5 and the
> M14 UI tasks need a real headless Chromium (`uv run playwright install
> chromium`); `make check-fast` is a fallback only where a browser
> genuinely is not available, never a substitute (root `CLAUDE.md` rule 7).

| Task | Goal | Status | Notes |
|---|---|---|---|
| T13.1 | Accept `task_state` on `PATCH /v1/nodes/{id}` | DONE | Run 20260806-022441-m13-m14-kickoff (Path B, `fleet-worker`). `PatchNodeBody` gained `task_state: str \| None = None`; the route gates both the invalid-value 400/E_INVALID check and `commit_kwargs` construction behind `"task_state" in payload.model_fields_set`, so an omitted field never enters `commit_kwargs` and `store.commit_node`'s `_UNSET_TASK_STATE` sentinel default survives untouched — confirmed by an independent `fleet-verifier` reading the diff directly, not just trusting the worker's claim. OpenAPI snapshot regenerated to match. Verify: `tests/integration/test_api.py tests/integration/test_openapi_snapshot.py` 79 passed (worker's run and verifier's independent re-run both real, exit 0). Dedicated regression test `test_nodes_patch_omitting_task_state_leaves_it_untouched` exists (not just the happy path), plus invalid-value and `subtasks_closed`-side-effect tests. No SPEC-QUESTIONs beyond the pre-registered T13.1 entry. Note: the verifier's first pass returned `CONTRADICTS_CLAIM` solely because 4 parallel workers shared one working tree and `git status` showed the other 3 in-flight tasks' uncommitted files — not a defect in this task's own diff (confirmed no `task_state` references anywhere outside the 3 claimed files). Resolved by committing exactly T13.1's claimed files (commit `09a871a`) before flipping this row. |
| T13.2 | `reconcile.project_node_change()` helper | DONE | Run 20260806-022441-m13-m14-kickoff (Path B, `fleet-worker`). Reuses `ProjectionIndex.build`/`owner` and `Reconciler.on_change` verbatim — no second projection path; `origin_tracker` is a pure parameter, never constructed internally; unfiled nodes are a genuine no-op; a vanished path mirrors `reconcile_all`'s existing `FileNotFoundError` handling almost line-for-line. Worker self-caught and fixed two test-quality weaknesses (a tautological quiet-second-call assertion, an unfiled-node test that couldn't distinguish "unowned" from "nothing exists") via its own advisor consultation before finalizing. Verify: `tests/unit/sync/test_reconcile.py` 47 passed — independent `fleet-verifier` re-ran it for real (exit 0), confirmed both self-caught fixes are genuinely present in the final diff (control node that IS filed; first-call content assertions that already prove real work happened), and confirmed no second projection path via direct code review. Verifier flagged one minor non-blocking imprecision: the added commits-table-count assertion is structurally inert for the specific hub-only-branch scenario it exercises (that path never touches the commits table either way) — doesn't reintroduce the original tautology since the surrounding assertions genuinely discriminate, just doesn't add what its own comment claims. No SPEC-QUESTIONs beyond the pre-registered T13.3 entry. |
| T13.3 | Wire hub-side mutations to re-project their managed file | TODO | The audit's flagship gap: `on_change` has exactly three production entry points today (daemon startup, a filesystem event, `POST /sync/rescan`) and **no hub-side mutation triggers a projection refresh**, so an API/CLI/UI edit is invisible in Obsidian until a restart or manual rescan. Binding narrowest reading: `docs/spec-questions.md` **T13.3**. Share the daemon's single `OriginTracker` (debug-plan D10 is what happens when the request path and the watcher disagree about echoes); never fail an API call because a spoke projection failed (PRD §7.8 — projections are best-effort). Depends on T13.1, T13.2. |
| T13.4 | CLI `akasha set --task-state open\|done` | DONE | Run 20260806-031323-m13-m14-cohort2 (Path B, `fleet-worker`). Pure HTTP client of T13.1's field via `_mutate`; `task_state` only added to the request payload inside `if task_state is not None`, so omitting the flag sends today's exact pre-existing body — mirrors T13.1's server-side omitted-vs-null discipline client-side. Added `set_task_state` `DryRunCase` and its id to the meta-test's exclusion set next to `rm_with_redirect`. Verify: `tests/integration/test_cli.py tests/integration/test_cli_dry_run.py` 52 passed — independent `fleet-verifier` re-ran it for real (exit 0), confirmed the omission discipline via direct diff read, and confirmed a genuine live-daemon round trip (set → get confirms → reopen), not just a CLI-parsing test. No SPEC-QUESTIONs. |
| T13.5 | Web UI: node view shows and toggles task state + subtask structure | TODO | Also surfaces `maturity`, which `GET /nodes/{id}` has always returned and the UI has never rendered. Reuse the existing `nodeLink` (D8) and `truncate` helpers; PRD R9 copy discipline (never "true"; a fully-closed supertask is "flagged for review", never "complete"); never auto-close a supertask (design invariant 3). Needs Chromium. Shares `app.js` with T14.5/T14.6 — sequential. Depends on T13.1, T13.3. |
| T13.6 | Project review-resolution commits back to the vault too | TODO | The second hub-side mutation surface, which `routes/nodes.py` does not reach: `POST /v1/review/{id}/resolve` commits through `tms/review.py`'s `resolve_review` (`revised` → `store.commit_node`) and `approve_proposal` (→ `store.create_node`) — i.e. through the one write the Web UI already had before this plan, so without this a resolved stale badge still leaves the Obsidian file showing pre-revision text. The projection call belongs in the **route**, after the resolver returns (those resolvers own their own transactions) — same helper, same shared `OriginTracker` as T13.3, never a second mechanism. Deliberately split out of T13.3 so `routes/review.py` is an explicit Files-list entry rather than an implicit one. Depends on T13.3. |

## M14 — Definition DAG: make the graph creatable, navigable, and refactorable (Depends on: nothing)

Milestone DoD: a user can create a definition with facets, link it with
facet-bound justification edges (including facets born from a highlighted
span), read a node's 1-hop neighborhood and history, vet a node to S4, and
split/merge a definition with its reassignment queue — from the CLI, and for
the linking/navigation half from the Web UI — without hand-writing HTTP.
`make check` + `make battery` green.

> **Eligibility note for the overnight/fleet scanner:** all six tasks are
> mechanical and autonomously dispatchable. **T14.1 → T14.2 → T14.3 → T14.4
> form one strict sequential chain (all touch `src/akasha/cli/main.py`, and
> all but T14.1 touch `tests/integration/test_cli_dry_run.py`); T14.5 →
> T14.6 form a second chain (both touch `src/akasha/ui/static/app.js`).**
> The two chains are file-disjoint from each other and may run
> concurrently — but both also collide with M13's T13.4 (CLI) and T13.5
> (UI), so the orchestrator's disjointness partition must be trusted rather
> than assumed. Every new **mutating** CLI verb structurally requires a
> `DryRunCase` row (T12.2's landing note) — T14.1's two verbs are read-only
> and must **not** get one, or the meta-test breaks.

| Task | Goal | Status | Notes |
|---|---|---|---|
| T14.1 | CLI `akasha neighborhood ID` / `akasha history ID` | DONE | Run 20260806-022441-m13-m14-kickoff (Path B, `fleet-worker`). Pure read-only HTTP-client verbs via the existing `_request` helper (never `_mutate`) — correctly excluded from `tests/integration/test_cli_dry_run.py`'s AST meta-test (confirmed still 21/21 green). ASCII-only default output (pre-mvp T9.9 precedent), documented in `docs/user/cli.md`. Verify: `tests/integration/test_cli_graph.py` (new file) 8 passed — independent `fleet-verifier` re-ran it for real (exit 0), confirmed the diff scope against `git diff`, confirmed the test drives a real live daemon with real seeded edges/commits rather than a source-string check, and confirmed ASCII-only output via the test's own `result.output.isascii()` assertions. No SPEC-QUESTIONs beyond the pre-registered T14.2 entry. |
| T14.2 | CLI `akasha edge add` / `akasha edge rm` | TODO | The single biggest reason the DAG is unbuildable outside a vault today. Pure client — **no client-side copy of the facet-binding rule**; the server's 400 must reach the user verbatim. `--facet-span` passes through to the shipped T7.7 behavior (creates the facet on the target). Two `DryRunCase` rows required. Depends on T14.1 (same file). |
| T14.3 | CLI `akasha vet ID` (the S4 human act) | TODO | S4 is the one maturity stage the spec calls a *user act* and it gates what is exported as verified memory; today only a hand-written HTTP call can set it. Endpoint is `require_human`/∅ — an agent token gets a real 403 and is **never** proposalized; do not add a client-side token-class check. PRD R9 copy: "vetted by you", never "true". One `DryRunCase` row. Depends on T14.2 (same file). |
| T14.4 | CLI `akasha split` / `akasha merge` | TODO | Makes PRD §8 story 4 usable rather than property-tested only. Mirror `routes/nodes.py`'s existing `SplitBody`/`MergeBody` shapes exactly — invent no new part shape. Human-readable output must state how many reassignment review items opened and point at `akasha review list`; the queue is the point (zero dangling references is the invariant). Two `DryRunCase` rows. Depends on T14.3 (same file). |
| T14.5 | Web UI: make the 1-hop neighborhood navigable | DONE | Run 20260806-022441-m13-m14-kickoff (Path B, `fleet-worker`). `renderNeighborhood` now groups by direction (outbound/inbound) then edge type (`composes` first, then justification types via `EDGE_TYPE_ORDER`), fetches each distinct neighbor once via the pre-existing `GET /v1/nodes/{id}` (confirmed no endpoint/schema change), reuses the existing `nodeLink`/`truncate` helpers, shows `facet_binding` (including literal `*`), and degrades a failed neighbor fetch to a plain link (`.catch` → `[id, null]`, `nodeLink` still called unconditionally) instead of blanking the section. Extended (not duplicated) `tests/integration/test_ui_node_links.py`. Verify: 4 passed — independent `fleet-verifier` re-ran it twice for real (exit 0 both times), read the actual diff to confirm helper reuse and the real degrade-on-failure code path (not just test-mock theater — the new test intercepts the real endpoint with a forced 500 via `page.route(...).fulfill`), and confirmed via diff content that `app.js`'s own change never touches `task_state` or any endpoint beyond the pre-existing one, despite the shared working tree showing other tasks' concurrent uncommitted work at verification time. No SPEC-QUESTIONs. |
| T14.6 | Web UI: facets-from-spans link form on the node view | DONE | Run 20260806-031323-m13-m14-cohort2 (Path B, `fleet-worker`). "Link this node" form (target id, closed EdgeType list, span via paste or `window.getSelection()`) posts to the pre-existing `POST /v1/edges` — no new endpoint/schema; on success re-renders T14.5's neighborhood in place; server's facet-binding-rule 400 surfaced verbatim, never duplicated client-side. Verify: `tests/integration/test_ui_link_form.py tests/integration/test_ui_smoke.py` 4 passed — independent `fleet-verifier` re-ran it for real against real Chromium (exit 0), confirmed via direct API calls that a real facet now exists on the target with the exact submitted span (not just that the edge was created) and that `facet_coverage` moves from 0.0 to nonzero in the fixture — the flagged highest-risk corner was not cut. Two non-blocking gaps noted: the test asserts "nonzero after" rather than a literal before/after value comparison, and doesn't directly assert absence of a page reload (confirmed correct by code review instead — `preventDefault()` + in-place refresh). No SPEC-QUESTIONs. |

## M15 — Real-use validation: todo synchronization (Depends on: M13)

Milestone DoD: the todo-sync round trip exercised against a real running
daemon and real Obsidian-shaped files — once content-blind (T15.1) and once
by the human on their own real task lists (T15.2) — with both outcomes
written down honestly, including whatever did not work.

> **Eligibility note for the overnight/fleet scanner:** T15.1 is
> content-blind (every file it creates comes from a fixed template it writes
> itself; it never reads or interprets real notes) and is therefore safe for
> autonomous dispatch — the same test pre-mvp T11.4 passed. It does need a
> **real environment**: a live daemon, a writable scratch tree outside the
> repo, and enough of a session to run a real reconcile loop. That is an
> environment precondition, not a `BLOCKED`. **T15.2 is `BLOCKED:
> human-only` and must never be flipped to `TODO` by any agent, refresh of
> `docs/agents/overnight-goals.md`, or milestone-closing pressure** —
> deciding which of the user's real tasks become tracked nodes is reserved
> for the human (`docs/vision.md` PRD §5 F-list, R9, design invariant 3).
> This milestone therefore can never be "closed", which is why nothing in
> `docs/build-plan.md` depends on it.

| Task | Goal | Status | Notes |
|---|---|---|---|
| T15.1 | Live end-to-end todo-sync exercise on a generated task vault (content-blind) | TODO | Seven legs (mint → composes-from-indent → vault checkbox → hub-side completion writing back to the file → supertask flag → reparent → embed shows one state) driven against a real daemon, plus an **actually-counted** violation catalogue (a bare "none" is meaningless unless the report shows it was counted — pre-mvp T11.4's discipline) and real metric samples. Scratch vault/config/DB live outside the repo; the only in-repo file is `docs/dogfood/todo-sync-report.md`. Verify is a live leg, not a pytest command: `make check`+`make battery` green at the landing commit, plus an independent `fleet-verifier` re-querying the scratch DB and re-reading the vault files on disk. Reuse `docs/dogfood/README.md`'s commands and `akasha init` (T12.1) for the token — not the old direct-store bootstrap. |
| T15.2 | MANUAL: run your own real todo lists through it for a week | BLOCKED: human-only — deciding which real personal tasks/lists become tracked nodes is an explicit human judgment call (`docs/vision.md` human-in-the-loop invariant, PRD §5 F-list, R9, design invariant 3), never delegated to an autonomous worker. Depends on T15.1. Same standing boundary as pre-mvp T11.2, which remains `BLOCKED: human-only` in `docs/pre-mvp/task-status.md` and is not superseded by this row. | |

## M16 — Real-use validation: the definition DAG (Depends on: M14)

Milestone DoD: the definition/claim/relation layer exercised end-to-end
against a real daemon — created, linked with facet-bound edges, broken,
adjudicated, split, navigated, vetted — once content-blind (T16.1) and once
by the human against their own real knowledge (T16.2), both written down
honestly.

> **Eligibility note for the overnight/fleet scanner:** identical shape to
> M15. T16.1 is content-blind (every node body is a fixed generated string)
> and autonomously dispatchable given a real live-daemon environment;
> **T16.2 is `BLOCKED: human-only` permanently** — deciding which of the
> user's own concepts and claims are worth tracking, how they decompose, and
> which facet a relation truly depends on is precisely the judgment PRD §5's
> F7 and R9 reserve for the human. This milestone is likewise a deliberate
> leaf that nothing depends on.

| Task | Goal | Status | Notes |
|---|---|---|---|
| T16.1 | Live end-to-end definition-DAG exercise (content-blind) | TODO | Build a small graph through the CLI **and the Web UI's span form** (so PRD R8's facets-from-spans path runs for real, not just its test), then read it back, break a facet, adjudicate three ways, split with a real reassignment queue, vet to S4, and read a node `--as-of`. Record `facet_coverage` before/after, any **false** invalidation observed (a PRD §11 metric), and confirm staleness did not recurse past an unreviewed node (§4.9's damper). Verify is a live leg: `make check`+`make battery` green plus an independent `fleet-verifier` re-querying the scratch DB and re-running one of the report's own CLI read commands. Only in-repo file: `docs/dogfood/definition-dag-report.md`. |
| T16.2 | MANUAL: put your own real definitions in it | BLOCKED: human-only — deciding which of the user's own concepts/definitions/claims become tracked nodes, how they decompose under the single-predicate rule, and which facet a relation depends on is exactly the human judgment `docs/vision.md` reserves (PRD §5 F7, R9, design invariant 3). Depends on T16.1. Never dispatch to an autonomous worker. | |

## M17 — User-facing documentation for both objectives (Depends on: M13, M14)

Milestone DoD: a new user can install akasha, register a vault, run their
tasks through it, and build and navigate a definition DAG using only
`docs/user/**` — no step requiring source-code reading, no step describing a
capability that does not exist.

> **Eligibility note for the overnight/fleet scanner:** all three tasks are
> doc-only and autonomously dispatchable, but each has an **objective**
> verification step that must actually be run (greps that must come back
> empty; every documented CLI verb present in `uv run akasha --help`; every
> grammar example parsing clean; every worked-example command executing
> against a scratch daemon). "Doc-only" is not "verification-optional" —
> rule 0.9 still applies. T17.1 and T17.2/T17.3 are file-disjoint
> (`quickstart/web-ui/dogfood-windows/ops-autostart` vs `obsidian.md` /
> `definitions.md`), except that **T17.2 and T17.3 both touch
> `docs/user/README.md`** — sequential.

| Task | Goal | Status | Notes |
|---|---|---|---|
| T17.1 | Rewrite onboarding docs around the installer-first flow | TODO | **This is pre-mvp T12.6, carried forward and renumbered** — its original scope, files and DoD are unchanged, and its original dependencies T12.1–T12.5 are all `DONE` in `docs/pre-mvp/task-status.md`. Carried forward rather than left behind because a `TODO` row under `docs/pre-mvp/` can never be selected by `fleet-orchestrator`, which scans only `docs/build-plan.md` + this file. `docs/pre-mvp/**` is read-only and was not edited to record this. |
| T17.2 | Task/todo workflow guide | TODO | Teach the round trip as a user performs it, quoting §4.7's grammar exactly and stating the in-contract obligation honestly (lossless within contract; violations flagged, never guessed — PRD invariant 5). Prefer `docs/dogfood/todo-sync-report.md`'s **observed** behavior over intended behavior if T15.1 has landed. Every example must parse clean against the shipped parser — verify, do not assume. Also owns the fix for `docs/user/README.md`'s **stale project-maturity paragraph** ("M0–M10 … M11 in progress … no packaged installer yet … M12 planned" — all three now false), while keeping the two honestly-pending legs at the top of this file disclosed. Shares `docs/user/README.md` with T17.3. Depends on T13.5, T14.1. |
| T17.3 | Definitions & the DAG guide | TODO | New `docs/user/definitions.md`: node types, facets and why edges bind to one, the S0–S4 ladder, pin vs track, then one worked example end to end using only shipped surfaces. PRD R9 language rule ("vetted by you", never "true") is both content and a verification check. Every command shown must actually execute in order against a fresh scratch daemon. Shares `docs/user/README.md` with T17.2. Depends on T14.4, T14.6. |
