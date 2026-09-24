# Task status

Machine-checkable status for every task in `docs/build-plan.md` (M13–M19, 40
tasks total: 6 + 6 + 2 + 2 + 3 + 14 + 7). This file is the single source of truth for "what's done" — an
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
> evidence toward it, not a substitute. Neither blocks anything in M13–M18.
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
| T13.3 | Wire hub-side mutations to re-project their managed file | DONE | Run 20260806-031323-m13-m14-cohort2 (Path B, `fleet-worker`). The audit's flagship gap, now closed: `api/app.py`'s `create_app` constructs one `OriginTracker` on `app.state`, living for the app's whole lifetime; `daemon.py`'s `serve` wires the live `Watcher`'s `Reconciler` to that same shared instance (confirmed genuinely connected, not cosmetic — traced by the verifier: `getattr(app.state, "origin_tracker", None) or OriginTracker()` always picks up the real instance in production since it's truthy, the fallback only fires for test doubles). `routes/nodes.py` calls `project_node_change` (T13.2) after each mutating endpoint's store transaction commits — never inside it — for create/patch/delete/split/merge/vet; a projection failure is caught, logged, and swallowed (confirmed via a real monkeypatch test forcing a raise and asserting 2xx + persisted commit + logged failure). `routes/review.py` deliberately untouched (closed by T13.6). Verify: `tests/integration/test_projection_writeback.py tests/integration/test_api.py tests/battery/test_edit_battery.py` 109 passed — independent `fleet-verifier` re-ran it for real (exit 0) with extra scrutiny as the milestone's flagship task, independently re-ran the worker's own broader claims (full suite 678 passed, ruff clean, pyright 0 errors — exact match), and traced both highest-risk behaviors (tracker sharing, failure swallowing) through actual code rather than trusting the claim. No SPEC-QUESTIONs beyond the pre-registered T13.3 entry. |
| T13.4 | CLI `akasha set --task-state open\|done` | DONE | Run 20260806-031323-m13-m14-cohort2 (Path B, `fleet-worker`). Pure HTTP client of T13.1's field via `_mutate`; `task_state` only added to the request payload inside `if task_state is not None`, so omitting the flag sends today's exact pre-existing body — mirrors T13.1's server-side omitted-vs-null discipline client-side. Added `set_task_state` `DryRunCase` and its id to the meta-test's exclusion set next to `rm_with_redirect`. Verify: `tests/integration/test_cli.py tests/integration/test_cli_dry_run.py` 52 passed — independent `fleet-verifier` re-ran it for real (exit 0), confirmed the omission discipline via direct diff read, and confirmed a genuine live-daemon round trip (set → get confirms → reopen), not just a CLI-parsing test. No SPEC-QUESTIONs. |
| T13.5 | Web UI: node view shows and toggles task state + subtask structure | DONE | Run 20260806-cohort3 (Path B, `fleet-worker`). Task-type nodes (gated on `task_state !== null`) show `task_state`/maturity and navigable subtask/supertask structure via the existing `nodeLink` helper; a toggle PATCHes the real endpoint and re-renders strictly from the server response, never optimistically. A fully-closed supertask is "flagged for review" (backed by a real `subtasks_closed` review row, not a hardcoded string) and is never auto-closed — verified via a genuinely independent `httpx` GET after the UI toggle, not just DOM state. Verify (build-plan's literal line): `tests/integration/test_ui_task_view.py tests/integration/test_ui_node.py` 3 passed — independent `fleet-verifier` re-ran it for real with Chromium (exit 0), also re-ran the broader `test_ui_node_links.py test_ui_link_form.py` variant (9 passed), confirmed via `git diff -U0` that non-task node rendering is byte-identical to before, and confirmed design invariant 3 through the independent API assertion. `docs/spec-questions.md` T13.5 entry: maturity display is scoped to the task-gated section only, not all node types (narrowest reading of two build-plan sentences that only cohere that way). CONFIRMED_DONE on first verifier pass, no rework needed. |
| T13.6 | Project review-resolution commits back to the vault too | DONE | Run 20260806-cohort3 (Path B, `fleet-worker`). Added a T13.3-style `_reproject` helper to `routes/review.py`, called after `resolve_review`'s own transaction commits, reusing the exact same `reconcile.project_node_change` helper and shared `request.app.state.origin_tracker` — never a second mechanism. **Real finding along the way**: `POST /v1/review/{id}/resolve` never actually dispatches to `tms.review.approve_proposal`/`resolve_reassignment` — no HTTP route to either has ever existed in shipped code (pre-mvp T8.0's own scope was `resolve_review` only), despite this task's own build-plan prose implying otherwise. Narrowest reading taken: closed projection for the resolution surface that actually exists (`resolve_review`) rather than inventing new cause_kind-based HTTP dispatch that would let this endpoint mint nodes; the "approved create-proposal projects nothing" DoD item is verified by calling `tms.review.approve_proposal` directly (no HTTP path exists to test it through) — see `docs/spec-questions.md` T13.6 entry. Verify: `tests/integration/test_projection_writeback.py tests/integration/test_tms.py tests/integration/test_api.py` 99 passed — independent `fleet-verifier` re-ran it for real (exit 0), confirmed the "no HTTP route" claim by direct code trace (the single highest-risk claim), confirmed the 3 new tests assert real canonical/LF-only on-disk bytes (not just HTTP 200), and confirmed `_reproject` runs only after the resolver's transaction returns. First verifier pass was `CONTRADICTS_CLAIM`: the worker claimed it had appended the `docs/spec-questions.md` entry documenting the above finding but had not — only the in-code `# SPEC-QUESTION` comment existed. Fixed by writing the entry (content the verifier had already independently fact-checked as accurate before finding the gap), re-confirmed the verify command still passes 99/99. |

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
| T14.2 | CLI `akasha edge add` / `akasha edge rm` | DONE | Run 20260806-cohort3 (Path B, `fleet-worker`). Pure HTTP-client verbs over the already-shipped `POST /v1/edges`/`DELETE /v1/edges/{id}` — no client-side copy of the facet-binding rule anywhere (confirmed by direct code read); `--facet-span` mints a real facet on the target, asserted via a live `GET /v1/nodes/{dst}` (exact span text/count), not just CLI exit 0. Two `DryRunCase` rows added, confirmed not exempted from the AST meta-test. Two real findings logged rather than guessed past: (1) this task's own DoD text says the missing-binding 400 should map to exit 4, but the shipped, unmodified `_exit_code_for` maps `E_INVALID` to exit 1 (same as every other verb) — left the shared mapping untouched as a cross-cutting change out of this task's scope, needs a human ruling before T14.3/T14.4 hit the identical question; (2) `store.create_edge` does not validate that `src`/`dst` node ids exist (empirically probed — a typo'd id creates a silent dangling edge), a real integrity gap out of this task's Files list, flagged for a follow-up before T14.4's "zero dangling references" invariant. See `docs/spec-questions.md` T14.2 entries. Verify: `tests/integration/test_cli_edge.py tests/integration/test_cli_dry_run.py tests/integration/test_cli_graph.py` 42 passed — independent `fleet-verifier` re-ran it for real (exit 0), confirmed no Files-list violation (`kernel/store.py`/`routes/edges.py` genuinely untouched despite the create_edge finding), ruff/pyright clean. First verifier pass was `CONTRADICTS_CLAIM`: the worker claimed both spec-questions.md entries above were logged but neither existed on disk — same fabrication pattern as T13.6's first pass, caught independently in the same cohort. Fixed by writing both entries (content independently fact-checked accurate by the verifier before the gap was found), re-confirmed the verify command still passes 42/42. |
| T14.3 | CLI `akasha vet ID` (the S4 human act) | DONE | Run 20260805-m14-cli-vet (Path B, `fleet-worker`). Thin HTTP client over the already-shipped `POST /nodes/{id}/vet`; no client-side token-class check, so an agent token's real server 403 surfaces as a non-zero exit with no traceback and no proposal — the shared `_exit_code_for` mapping (flagged by T14.2's landing note as needing a human ruling before T14.4) confirmed byte-identical before/after this diff. Plain output says "vetted by you", never the literal word "true" (confirmed absent from both rendered output and `--help`'s docstring); `--json` still mirrors the real API response verbatim (which does contain a literal `true` boolean) — flagged via `docs/spec-questions.md` T14.3 entry since PRD R9's "including MCP responses" parenthetical is real counter-evidence the reading might need to extend further. Also fixed a pre-existing inaccuracy in `docs/user/cli.md`'s agent-token proposalization line (traced via `git log -p --follow` to commit `12d81d4`, predates this task; confirmed against §4.11's `human only ∅` endpoints). Verify: `tests/integration/test_cli_vet.py tests/integration/test_cli_dry_run.py` 35 passed — independent `fleet-verifier` re-ran it for real (exit 0), confirmed all 5 new test scenarios assert real post-mutation state (re-`GET` after both the human-vet and the `--dry-run` cases, not just exit codes), confirmed `docs/spec-questions.md` was correctly left untouched by the worker (this task's Files list excludes it — mirror of the T13.6/T14.2 fabrication pattern, this time correctly withheld rather than falsely claimed), and independently re-ran the full `tests/unit tests/property tests/integration` suite (702 passed). CONFIRMED_DONE on first pass, no rework needed. |
| T14.4 | CLI `akasha split` / `akasha merge` | DONE | Makes PRD §8 story 4 usable rather than property-tested only. Mirror `routes/nodes.py`'s existing `SplitBody`/`MergeBody` shapes exactly — invent no new part shape. Human-readable output must state how many reassignment review items opened and point at `akasha review list`; the queue is the point (zero dangling references is the invariant). Two `DryRunCase` rows. Depends on T14.3 (same file). Done 2026-09-23: `split`/`merge` verbs (plain output names successors, redirect, and the reassignment-review count via a read-only `GET /v1/review`; `--json` is the raw response); `tests/integration/test_cli_split_merge.py` (7 tests, live daemon) + two `DryRunCase` rows. |
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

---

## M18 — Zero-flag onboarding: from install to a live, syncing vault in two commands (Depends on: nothing)

Milestone DoD: a user with only `uv`/`pipx` and a vault reaches a live, syncing
vault with two commands and no second terminal (install, then
`akasha setup <vault>`); every later verb works with `AKASHA_TOKEN` in the
environment and no per-call flags; demonstrated by an automated test against a
scratch `HOME`. Per the 2026-09-23 rulings (`docs/spec-questions.md` M18-A,
M18-B) the DoD also includes the saved `0600` human-token file (T18.9) and
track-every-Markdown-file-by-default with a `.tmignore` deny-list
(T18.10a–c).

> **Eligibility note for the overnight/fleet scanner:** **T18.1 and T18.2 are
> file-disjoint and are the natural first parallel cohort** (T18.1:
> `pyproject.toml`, `kernel/store.py`, its own new test; T18.2: `cli/main.py`,
> `test_cli.py`, `cli.md`). **T18.2 → T18.3 → T18.4 → T18.5 → T18.6 → T18.7 →
> T18.8 is one strictly sequential chain** — every one touches
> `src/akasha/cli/main.py` and `docs/user/cli.md` — and T18.2 additionally
> waits on **T14.4** (the last M14 task to touch those files). **T18.12
> shares `docs/user/quickstart.md` with T17.1: T17.1 must be `DONE` first.**
> **T18.10a is file-disjoint from the whole CLI chain and may run in the first
> parallel cohort** with T18.1/T18.2. T18.9 extends the CLI chain (after T18.8).
> **T18.10b and T18.10c share `src/akasha/sync/reconcile.py` with M19
> (live transclusion) — run them after M19's reconcile tasks.** T18.10c edits
> `docs/mvp-spec.md` §4.7 and must leave every protected parser/linter/golden
> test **unmodified** (see the task). **T18.11 is `BLOCKED` (needs a real
> Linux/macOS host) and must never be flipped to `TODO` for an autonomous run**;
> `fleet-orchestrator` selects only literal `TODO`, so this keeps it out by
> construction. Every mutating/process verb needs its
> `--dry-run` behavior asserted (zero side effects) and must never touch the
> real `tm-daemon` config dir in tests.

| Task | Goal | Status | Notes |
|---|---|---|---|
| T18.1 | Make the wheel self-contained: ship the migrations | DONE | Verified 2026-09-23: `uv build --wheel` output has **0** `.sql` files; `_migrations_dir()`'s non-frozen branch resolves outside `site-packages` for an installed wheel. Blocks every `uv tool install`/`pipx`/`pip` route. Files list includes `kernel/store.py` per the ratified T12.5 precedent (spec-questions M18-E). No dependency; file-disjoint from T18.2. Done 2026-09-23: hatch force-include maps `migrations/` to `akasha/migrations`; `_migrations_dir()` prefers the packaged copy; `tests/integration/test_wheel_install.py` builds the wheel and migrates a fresh DB from the unzipped tree with no repo root. |
| T18.2 | Honor `AKASHA_TOKEN` and `AKASHA_BASE_URL` in the CLI | DONE | The quickstart already tells users to `export AKASHA_TOKEN`, but `--token` has no `envvar` — the export does nothing on its own. Environment is the only credential source this milestone adds (M18-A). Depends on T14.4 (shared `cli/main.py`). Done 2026-09-23: `envvar=` on both global options (flag > env > default); empty values normalized to unset; 5 tests in `test_cli.py`; documented in `cli.md`. |
| T18.3 | `akasha up` / `akasha down`: a detached daemon lifecycle | DONE | Adds `tm-daemon.pid` beside the existing lock (neutral name, M18-C). Idempotent both ways; tests spawn a real detached daemon against a `tmp_path` config. Depends on T18.2. Done 2026-09-23: `daemon.up`/`daemon.down` (+ `tm-daemon.pid`, written only after the lock is held); the lock — not the pid file — decides "running", so a stale pid is never signalled. 6 tests spawn a real detached daemon under a `tmp_path` HOME. |
| T18.4 | Start the daemon on demand for default-endpoint verbs | DONE | Default endpoint only — never for explicit `--base-url`/`AKASHA_BASE_URL`, `--dry-run`, or `AKASHA_NO_AUTOSTART`; always a visible stderr line. Safe because startup reconcile is idempotent (§4.8). Depends on T18.2, T18.3. Done 2026-09-23: `_request` retries once after `daemon.up` on a refused connection; `spawn_detached` is monkeypatched to blow up in the never-spawn tests. **Also pulled forward T18.9 step 4** (default endpoint = the default config's `bind:port`): on-demand start is only correct/testable with it. Typer vendors its own click, so the parameter source is compared by `.name`. |
| T18.5 | `akasha setup [VAULT]`: nothing to a live vault in one command | DONE | A **new** verb; `akasha init`'s exit-4 contract and `test_cli_init.py` stay untouched (shared mint helper only). Prints the token once + bootstrap link with a secrets warning; never writes the token to disk. Depends on T18.4. Done 2026-09-23: `setup` + shared `_open_migrated_db`/`_mint_human_token` (init's output/exit codes unchanged, its tests untouched). Handles `--dry-run` itself (plan only) so no `DryRunCase` row was needed — its HTTP calls live in a helper the meta-test's source scan doesn't reach, which is honest because dry-run has already exited. `test_cli_setup.py` proves a plain front-matter-less `.md` with `^tm-new` is minted through `setup`. |
| T18.6 | `akasha status`: one-screen diagnosis | DONE | Read-only (`_request` only). Whether `W_UNMANAGED_ANCHOR` reaches `/sync/status` is unverified — check, log a finding if not, do not widen scope. Depends on T18.5. Done 2026-09-23: `status` (GET-only, never autostarts; ASCII output; `--json`). Hints asserted against real state (a real root + real reconcile). Finding logged as spec-questions **M18-F**: `W_UNMANAGED_ANCHOR` no longer reaches `/sync/status` after T18.10b/c, so `status` says nothing about it. |
| T18.7 | `akasha render FILE`: see a transclusion resolved, headlessly | DONE | The only headless file-to-file view: embeds are link-form on disk and stay so; this prints them expanded, read-only, asserting the file's sha256 is unchanged. Also serves T15.1's embed leg. Depends on T18.6. Done 2026-09-23: `render FILE` (GET-only; adds `missing_ok` to `_request` so a 404 target is reported, not fatal; matches embeds with `grammar.EMBED_RE`, skips fences; file sha256 asserted unchanged; also serves T15.1's embed leg). |
| T18.8 | `akasha plugin install VAULT`: one-step Obsidian plugin install | DONE | `--from DIR` copies the *built* plugin (`main.js` is gitignored; bundling into the wheel is a recorded follow-up, not done). `data.json` gets `daemonUrl` only — never a token. Depends on T18.7. Done 2026-09-23: `plugin install` (own `plugin` sub-typer; `--from` defaults to a source checkout's `plugin-obsidian/`; `daemonUrl` set only when absent so a user's own value survives; the plugin's real token key is `apiToken`, not `token` — relevant to T18.9's `--with-token`). 10 tests, filesystem only. |
| T18.9 | Save the human token: a `0600` token file the CLI reads | DONE | Ruling M18-A (2026-09-23): agents may act as the human — a local agent that shells out to `akasha` uses the saved token, knowingly accepted. Narrow: rules on the CLI/plugin credential channel only; the API's agent-class-token → proposal rewrite (T4.6) is untouched. Neutral `tm-token` path, mode set at creation; plugin pre-fill only via explicit `--with-token` with a cloud/`.git` warning. Depends on T18.5, T18.8. Done 2026-09-23: `config.write_token/read_token/default_token_path` (0600 via `os.open` at creation, atomic replace, asserted by spying the open mode); `init`/`setup` save it beside the config (never overwrite; init's note goes to stderr so its stdout contract holds); resolution flag > env > file; **the file is used only for the default endpoint** (an explicit `--base-url` never receives it — a narrowing not in the plan, chosen so a local secret cannot be sent to a named host); revoked-token hint; `plugin install --with-token` writes the plugin's `apiToken` with the cloud/`.git` warning. |
| T18.10a | `.tmignore` matcher (pure, no I/O) | DONE | Ruling M18-B (2026-09-23): deny-list, not allow-list. Neutral name `.tmignore` per rule 6 (the user wrote `akashaignore` — one-line rename if they overrule rule 6). gitignore-style subset, stdlib only, built-in defaults (`.obsidian/`, `.git/`, `.trash/`, `node_modules/`, non-`.md`). File-disjoint — first-cohort eligible. Done 2026-09-23: `sync/ignore.py` (`parse_patterns`, `is_ignored`), 16 tests incl. a never-raises property. The non-`.md` default is case-insensitive (matches the watcher's filter). |
| T18.10b | Apply the deny-list in the watcher and discovery | DONE | Ignored paths never reach the debouncer or `discover_untracked_files`; editing `.tmignore` applies live. Shares `sync/reconcile.py` with M19 — run after it. Depends on T18.10a. Done 2026-09-23: `.tmignore` loaded per root in `sync/watcher.py` (`load_tmignore`, `iter_tracked_markdown`); filtered before the debouncer; a `.tmignore` event reloads and rescans; `discover_untracked_files` skips ignored paths; `Reconciler._cycle` is inert for an ignored path (the single choke point, covers startup/rescan/hub-side/mirror fan-out). Proven with a live watchdog watcher. |
| T18.10c | Track every non-ignored Markdown file by default | DONE | Reconcile-layer adoption shim (parse with virtual `tm: 1`); parser/linter/golden untouched and must pass **unmodified**; prose-only files are never written; real `tm: 1` added lazily on first projection. Amends §4.7's "never parsed" sentence. Depends on T18.10b. Done 2026-09-23: `reconcile.adopt_unmanaged` (in-memory `tm: 1`, injected into existing front matter, never a second block; prose-only files untouched and untracked). Parser/linter/render and the protected tests are unmodified (empty git diff). Battery case E25 added; §4.7 amended. |
| T18.11 | Login-time service install for Linux/macOS | BLOCKED: needs a real Linux/macOS host to attest (same class as T12.4/T12.5, `docs/acceptance.md` row 9) | Polish, not a prerequisite — T18.4 makes a stopped daemon safe. |
| T18.12 | Rewrite the quickstart around the two-command flow | TODO | Doc-only with objective checks (every command runs in order against a scratch `HOME`; every verb in `--help`). Depends on T17.1 (same file), T18.1–T18.9 and T18.10a–c. |

---

## M19 — Live transclusion: the same anchor in several files is one node, kept identical everywhere (Depends on: nothing)

Milestone DoD: with two files sharing a `^tm-id` line, an edit or checkbox toggle
in either rewrites the other within one sync cycle (no rescan, no restart); an
`akasha set`/UI edit rewrites both; deleting one mirror never deletes the node;
a same-line edit in both files loses nothing (conflict branch + one review, no
write ping-pong); a real watcher thread proves it end to end; the E05 case is
re-ruled explicitly and E04/E04b/single-file `E_DUP_ID` pass **unmodified**;
`make check` + `make battery` green. User ruling and its F3 consequences:
`docs/spec-questions.md` M19-0. Scope limit: **one-line blocks only** (M19-A).

> **Eligibility note for the overnight/fleet scanner:** **T19.1 is doc-only and
> first.** T19.2 → T19.3 → T19.4 is one strictly sequential chain (all three
> edit `src/akasha/sync/reconcile.py` and `tests/unit/sync/test_reconcile.py`).
> T19.5 and T19.6 are file-disjoint from each other and may run in parallel
> after T19.4. **T19.3 deliberately edits protected tests** (the E05 unit test,
> battery case and golden `expected_ops.json`) — its Files list and "Authorized
> changes" section name them exactly; nothing else in `tests/golden` or
> `tests/battery` may change in that task (verify with `git diff --stat`).
> **T18.10b/c (M18) share `reconcile.py` and run after T19.4.** T19.7 shares
> `docs/user/README.md` with T17.2/T17.3, so it waits for T17.3.

| Task | Goal | Status | Notes |
|---|---|---|---|
| T19.1 | Amend the spec and the PRD: same anchor across files is a mirror | DONE | Doc-only. Narrows §4.7 `E_DUP_ID` to "twice in one file", adds a Mirrors paragraph, and edits the **F3 row itself** in `vision.md` (§5 is normative) with a scoped exception (a mirror is one atom shown twice — identity, not substitution). No other F-row touched. **Landing evidence:** Run 2026-09-23 (interactive session, implemented inline — no separate `fleet-verifier`). §4.7 `E_DUP_ID` narrowed to "twice in one file"; Mirrors paragraph added; §4.8 propagation paragraph added; §6.2's E05 line re-worded; the **F3 row itself** in `vision.md` carries the scoped exception. Verify (objective greps): `twice in a sync root` absent from `mvp-spec.md`; `mirror` present in both docs; `git diff --stat` touched only `mvp-spec.md`/`vision.md`. **Correction (a late review caught the first draft of this note, which wrongly said no doc edit was needed):** `docs/user/dogfood-windows.md` (one manual-test row), `plugin-obsidian/TESTPLAN.md` (§4b + pass criteria) and `plugin-obsidian/src/clipboard.ts` (comments only; `tsc --noEmit` clean) DID state the old cross-file `E_DUP_ID` rule and were corrected — Files list completed under the ratified T8.0/T8.1 rule, logged in M19-0. `acceptance.md` needed none (its `E_DUP_ID` mention is the single-file certain-repair). Verified also that the plugin itself does **not** rewrite pasted anchors (`registerClipboard` is a no-op), so copy-paste mirrors work with the plugin enabled. |
| T19.2 | `ProjectionIndex`: a node may have several owning files | DONE | Additive `owners()`; `owner()` (last-writer) unchanged so the existing test passes. **No schema change** — membership is already derived from every file's base snapshot. Depends on T19.1. **Landing evidence:** Additive `ProjectionIndex.owners()` (frozenset); `owner()` unchanged in meaning, now falling back to a remaining holder instead of `None` when the last writer lets go of an id another file holds. No schema/table change — `build()` still derives from base snapshots (asserted by a two-snapshot test). `owner()` callers audited: `_compute_ops` (created/deleted branches, both moved to `owners()` in T19.3) and `project_node_change` (T19.4). Verify: `tests/unit/sync/test_reconcile.py` green; ruff clean; `pyright` 0 errors on the file. |
| T19.3 | Mirror-aware ops, and the explicit re-ruling of E05 | DONE | Pure logic. Join ⇒ existing adopt op flagged `mirror`, not `E_DUP_ID`; removing one mirror is silent; `E_DELETED_S1` filtered only when another owner exists. Authorized protected changes: `test_cross_file_dup_withholds_and_reviews`, the E05 battery case, `e05-cross-file-dup/expected_ops.json` — nothing else. Depends on T19.2. **Landing evidence:** Pure logic in `_compute_ops`/`diff_blocks`: join ⇒ adopt `Op(mirror=True)` (never `E_DUP_ID`); removal with another owner is silent; `E_DELETED_S1` filtered only when another owner exists (before `pause_and_diff` counts). **Protected-test changes were exactly the three authorized** and nothing else (`git diff --stat tests/golden tests/battery` confirms): the E05 unit test → `test_cross_file_dup_joins_as_mirror`, the E05 battery case, `e05-cross-file-dup/expected_ops.json` (`[]` → one adopt op). E04, E04b, `tests/golden/test_serialization.py` (single-file `E_DUP_ID`) and `tests/unit/contract/**` pass **unmodified**. Verify: `tests/unit/sync tests/unit/contract tests/golden tests/battery` 275 passed at landing. |
| T19.4 | Propagate a committed edit to every other mirror | DONE | The feature. `on_change` = `_cycle(path)` + `_cycle` on each other owner (three-way, never a blind write, non-recursive). Join with differing text: hub wins + conflict branch (M19-C). `project_node_change` reprojects all owners. Depends on T19.3. **Landing evidence:** The feature. `on_change` = `_cycle(path)` + a full three-way `_cycle` on every other owner of each committed id (non-recursive; a failing mirror is logged, never fails the source cycle). Join with differing text: hub wins + conflict branch + one review (M19-C). `project_node_change` reprojects all owners. **Mutation-checked**: disabling propagation fails 7 of the new tests; disabling join handling fails the join test; both restored byte-identical. 12 new unit tests + 2 API tests (`PATCH` body and `task_state` rewrite both mirror files in the same request, LF-only). Verify: `tests/unit/sync tests/integration/test_projection_writeback.py tests/battery` green. |
| T19.5 | Battery: mirror cases E21–E24 | DONE | New cases/fixtures only; existing E01–E20 untouched. E24 records the multi-parent `composes` choice (M19-B). Depends on T19.4. **Landing evidence:** New cases E21 (edit A→B, settles), E22 (concurrent same-line edit ⇒ one conflict, B's version kept, hub wins the file, settled), E23 (remove one mirror ⇒ node live, other file byte-identical, then last S0 mirror ⇒ deleted), E24 (re-indent in B ⇒ second `composes` parent, A untouched, M19-B); E21–E23 join the aggregate silent-guess counter (0). New fixture dirs only; the only deleted battery lines are the authorized E05 ones. `make battery`: 51 passed. |
| T19.6 | Live proof: a real watcher, file to file | DONE | Real `Watcher` thread modelled on the D10 wiring test; A→B within 3 s, checkbox, no echo loop (cycle count asserted), hub `PATCH` rewrites both. Depends on T19.4. **Landing evidence:** Real `watchdog` observer + persistent `OriginTracker`/`Reconciler`/`Watcher` + real `create_app`: A→B in **0.122–0.124 s** (debounce 0.1 s; expect ≈0.5 s at the production 500 ms), B→A, checkbox, non-`.md` ignored (D10), hub `PATCH` rewrites both files in-request, and **each mirror is written exactly once** (no ping-pong). **Real finding while stabilizing it:** an initial version asserted zero forwarded reconcile cycles for the daemon's own writes and failed ~1/40 — the origin tracker's echo record is single-use, so a second filesystem event for the same write reaches the reconciler as a *quiet* cycle (no write, no propagation). Harmless and pre-existing; the test now asserts on *writes*. Post-fix: 60/60 runs green (a 2.5% flake would still pass 60 runs ~22% of the time, so this is reassurance, not proof). Also verified by hand with a real `akasha daemon` process across three files (edit either side, checkbox, `akasha set`): all propagated, zero review items. |
| T19.7 | User guide: transclusion | TODO | New `docs/user/transclusion.md`; states the limits (one-line blocks, per-file indentation, conflict behavior). Every example executed against a scratch daemon. Depends on T19.6 and T17.3 (shared `docs/user/README.md`). |
