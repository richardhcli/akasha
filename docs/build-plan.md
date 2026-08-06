# akasha — Fine-Grained Agent Build Plan (post-MVP usability phase)

**Derived from:** MVP Implementation Specification v1.0 (`docs/mvp-spec.md` —
that document is authoritative; this one only sequences it into small,
verifiable steps) and PRD v1.6 (`docs/vision.md` — authoritative for *why*).

**Prerequisite — the whole of M0–M12 is DONE.** The original plan that built
the MVP (M0–M12, 82 tasks) is archived verbatim at
`docs/pre-mvp/build-plan.md`, with its final per-task status at
`docs/pre-mvp/task-status.md`. Every task there is `DONE` except T11.2
(`BLOCKED: human-only`, permanently, by design) and T12.6, which this plan
**carries forward as T17.1** (see "What happened to T12.6" below). That
state was confirmed by a full green test run at the head of this session:
**644 tests passed** across `tests/unit tests/property tests/integration`
plus `tests/battery`, with `ruff` clean and `pyright src` at 0 errors. All
nine PRD §8 acceptance stories are GREEN in `docs/acceptance.md`, and the
hosted `windows-latest`/`ubuntu-latest` CI runners have been genuinely green
since run `30183257449` (2026-07-26). **Do not re-open, re-litigate, or
re-verify any pre-mvp task**; build forward from that state.

**Purpose of this plan (M13–M17).** The MVP is code-complete and
acceptance-green, but a spec-vs-shipped-code audit performed 2026-08-05 (the
method `docs/agents/overnight-goals.md` §"When the list is empty" prescribes,
the same one that found T10.2c, T9.2c, T9.3b and T9.6) found that the two
capabilities the user named as the product's point are each reachable from
only part of the system:

1. **Todo synchronization / transclusion** (`docs/mvp-spec.md` §4.7's
   `task_line` grammar, `composes` edges from indentation, `^tm-new`
   minting, embeds/refs; §4.8's reconcile pipeline). The vault→hub half is
   real and battery-proven. The **hub→vault half is not wired**: no endpoint
   can change `task_state`, and nothing re-projects a managed file after a
   hub-side commit (`docs/spec-questions.md` T13.1/T13.3).
2. **The definition DAG** (§4.2 node/edge model, §4.6 maturity ladder, §4.9
   invalidation walk, PRD §7.1's ontology and composition edges). The kernel
   is real, correct and wired — `tms/invalidate.py` implements §4.9's
   pseudocode verbatim and fires from `store.commit_node`. But **no user
   surface can build or navigate the graph**: the CLI has no edge/vet/split/
   merge/neighborhood/history verb, and the Web UI's only write is a review
   resolution, so `POST /edges`'s `facet_span` (M7's facets-from-spans
   capture flow, PRD R8's designed fix for facet bootstrap) has no UI at all
   (`docs/spec-questions.md` T14.2/T14.6).

M13 and M14 close those gaps. M15 and M16 then put both objectives in front
of a real user with a real daemon, because automated tests have never been
the thing this project trusts on its own (every real Windows bug in its
history was found by running the product, not by a test). M17 rewrites the
user-facing docs around what M13/M14 land.

**Nothing in this plan invents schema, endpoints, ID formats, or grammar.**
Every task is either wiring an already-shipped code path to an already-
shipped call site, or exposing an already-shipped endpoint through a surface
that PRD §7.11's API-first-parity invariant says must have it. The four
ambiguities the audit hit are logged in `docs/spec-questions.md` (entries
T13.1, T13.3, T14.2, T14.6) with the narrowest reading each task must take.

**What happened to T12.6.** The pre-mvp plan's last open row (rewrite the
onboarding docs around the installer-first flow) is **carried forward here as
T17.1**, not left behind. Reason: `fleet-orchestrator` reads exactly
`docs/build-plan.md` and `docs/agents/task-status.md`; a `TODO` row living
only in `docs/pre-mvp/` is structurally undispatchable — no scan will ever
select it. Its scope is unchanged (same four docs, same DoD); its
dependencies (T12.1–T12.5) are all `DONE`, so it is eligible immediately.
`docs/pre-mvp/**` is read-only reference and was not edited to record this.

---

## How to use this plan (read before doing anything)

1. **Do tasks strictly in ID order** within a milestone, and never start a
   task whose `Depends on` tasks are not all `DONE`, or whose milestone's
   `Depends on:` milestones are not all closed. When in doubt, stop and ask;
   do not improvise ordering.
2. **One task = one focused change.** Touch only the files listed under
   `Files`. If you feel you must touch a file not listed, that is a signal
   the task is misunderstood — stop and add a `# SPEC-QUESTION:` note in
   `docs/spec-questions.md` instead of guessing. Rule 5 is the sole
   precedence exception: when a task necessarily persists state and its
   Files list accidentally omits `kernel/store.py`, add only the minimal
   store helper and record the omission; never write SQLite from a higher
   layer. **Files-list completion (ratified T8.0/T8.1):** a file may be
   added when it is *strictly entailed by the task's own Goal/DoD/Verify
   text* (e.g. the `tests/integration/test_cli_dry_run.py` table entry every
   new mutating CLI verb structurally requires — see T12.2's landing) — log
   the completion in `docs/spec-questions.md` and correct the Files line.
   Anything requiring judgment about *what* to build stays a stop-and-log.
3. **Never invent** schema, endpoints, ID formats, or grammar beyond
   `docs/mvp-spec.md` (spec rule 0.2). Implement the narrowest reading of
   any ambiguity; where this plan cites a `docs/spec-questions.md` entry,
   that entry's "Narrowest reading taken" is binding on the task.
4. **Never edit golden files, fixtures, or acceptance tests to make code
   pass** (spec rule 0.3). If a golden file looks wrong, that is a
   `# SPEC-QUESTION:`, not an edit.
5. **All persistent writes go through `kernel/store.py`** (spec rule 0.4);
   no other module writes SQLite.
6. **Pickle / eval / exec are forbidden** everywhere (spec rule 0.5).
7. The product name never appears in on-disk formats, anchors, config paths,
   or schema identifiers. The neutral on-disk prefix is `tm` (rule 0.6).
8. **A task is not DONE until its `Verify` command passes locally.** Run
   `make check` before closing any task, and `make battery` before closing
   any task in this plan (every milestone here is downstream of M5, so rule
   0.7's battery gate always applies). `make check` includes the
   `[chromium]` Playwright UI tests; `make check-fast` is the fallback
   **only** when a real headless browser genuinely isn't available, never a
   substitute when one is.
9. If a `Verify` command fails, the task stays `IN PROGRESS`. Do not mark it
   DONE, do not weaken the test, and do not move on.

### Per-task template

Every task below uses this shape:

- **Goal** — the single outcome.
- **Depends on** — task IDs that must be DONE first.
- **Files** — the only files you may create or edit.
- **Spec** — the authoritative section(s) to re-read before starting.
- **Steps** — the ordered actions.
- **Verify** — the exact command(s) to run.
- **DoD** — the machine-checkable pass condition.

### Status legend

Mark each task `TODO → IN PROGRESS → DONE` (or `BLOCKED: <reason>`) in
`docs/agents/task-status.md`. A milestone is closed only when every task in
it is `DONE` and the milestone's own DoD passes.

### Human-in-the-loop boundary (load-bearing, do not blur)

Deciding **which real personal-note spans become tracked claims, tasks,
definitions or entities is a human judgment call**, reserved for the human
throughout `docs/vision.md` (PRD §5 F-list, R9, and design invariant 3:
machine proposes, human is the only writer of truth). Tasks in this plan
that require that judgment are marked `BLOCKED: human-only` in
`docs/agents/task-status.md` and **must never be flipped to `TODO` for an
autonomous run** — `fleet-orchestrator` selects only literal `TODO` rows, so
this keeps them out of the overnight loop by construction rather than by
prompting discipline. This is the same boundary pre-mvp T11.2 sits behind
(still `BLOCKED: human-only`, unchanged and not superseded by anything
here).

The autonomous live-daemon tasks in M15/M16 are deliberately **content-
blind**, exactly as pre-mvp T11.4 was: they exercise mechanics against
fixtures the task itself generates from a fixed template, never against the
meaning of the user's real notes, and their reports must say so plainly so a
green result is never read as "the vault is now usable."

### Dependency map (critical path in bold)

```
  **M13 (todo sync)** ─┬─► **M15 (real-use: todo sync)**
                       │
                       └─┬─► M17 (docs)
                         │
  **M14 (definition DAG)** ─┴─► **M16 (real-use: definition DAG)**
```

M13 and M14 both depend on nothing (their real prerequisite, M0–M12, is
archived and closed — see the header). They are independent milestones and
may run concurrently, **but several of their tasks share files**
(`src/akasha/cli/main.py`, `src/akasha/ui/static/app.js`,
`tests/integration/test_cli_dry_run.py`); `fleet-orchestrator`'s
file-disjointness partition will place those in sequential groups
automatically. Within M13, T13.1 and T13.2 are file-disjoint and may run in
one parallel cohort. M15 and M16 are leaf milestones — nothing depends on
them, which is deliberate: each contains a `BLOCKED: human-only` task, so a
milestone gate pointing at them could never be satisfied. M17 therefore
depends on M13 and M14 only.

---

## M13 — Todo synchronization & transclusion: close the hub-side round-trip (Depends on: nothing)

**Milestone DoD:** a task node can be created, nested, completed and
re-opened from **every** surface — Obsidian checkbox, CLI, Web UI, HTTP API
— with the change landing in both the hub and the managed vault file within
one sync cycle, without a daemon restart or a manual rescan; PRD §8 story 8's
loop (`composes` from indentation, one state everywhere, supertask flagged
never auto-closed) is exercised end-to-end by an automated test that drives
the real production paths. `make check` and `make battery` green.

### T13.1 — Accept `task_state` on `PATCH /v1/nodes/{id}`
- **Goal** — Make a task's open/done state settable over HTTP, closing the gap `docs/acceptance.md` row 8 and pre-mvp T10.2c both disclosed ("`PATCH /nodes` cannot close a task today"): today the *only* production path that can change `task_state` is a vault checkbox toggle, so the CLI, Web UI, plugin and every agent are structurally unable to complete a task.
- **Depends on** — none (milestone gate only).
- **Files** — `src/akasha/api/routes/nodes.py`, `docs/api-snapshot/openapi.json`, `tests/integration/test_api.py`.
- **Spec** — §4.11 `PATCH /nodes/{id}` row; §4.2 `Node.task_state`; §4.5 `commit_node`; §4.10 `all_subtasks_closed`; `docs/spec-questions.md` **T13.1** (binding narrowest reading).
- **Steps** — (1) Add `task_state: str | None = None` to `PatchNodeBody`. (2) In `patch_node`, forward it to `store.commit_node` **only when the client actually supplied it** — `commit_node`'s `task_state` parameter is sentinel-guarded (`_UNSET_TASK_STATE`), and passing an explicit `None` means "clear it", which is *not* what an omitted field means. Use `payload.model_fields_set` (or an equivalent explicit presence check) to distinguish omitted from `null`; an omitted field must produce a call byte-identical to today's. (3) Reject a value outside `{"open","done"}` with the standard `400 E_INVALID` envelope — do not coerce, do not guess. (4) Change nothing about class defaulting: a checkbox toggle is ordinarily a `patch`-class commit, and `store.commit_node` already evaluates `all_subtasks_closed` on **every** commit (T10.2c), so the supertask trigger fires through this path for free — assert that rather than re-wiring it. (5) The agent-token proposal path (`mutation_gate`) is untouched: an agent PATCH still becomes a `cause_kind=proposal` review, never a mutation. (6) Regenerate the OpenAPI snapshot in the same change (§6.3 gate).
- **Verify** — `uv run pytest tests/integration/test_api.py tests/integration/test_openapi_snapshot.py`
- **DoD** — `PATCH /v1/nodes/{id}` with `{"task_state":"done","change_class":"patch","facets_touched":[]}` closes a task and returns the updated node; omitting the field leaves `task_state` untouched (asserted); an invalid value is a 400 with the standard envelope; closing the last open subtask through this endpoint enqueues exactly one `subtasks_closed` review on the parent and never auto-closes it (asserted end-to-end through the HTTP route, not by calling `triggers.evaluate`); snapshot gate green; `make check` + `make battery` green.

### T13.2 — `reconcile.project_node_change()`: resolve a node to its managed file and re-project it
- **Goal** — Add the reusable helper that answers "which managed file, if any, projects this node — and re-run §4.8's pipeline for exactly that file". Pure library-level change with no call site yet (T13.3 wires it), so the risky wiring lands separately from the logic.
- **Depends on** — none (milestone gate only).
- **Files** — `src/akasha/sync/reconcile.py`, `tests/unit/sync/test_reconcile.py`.
- **Spec** — §4.8 (the full `on_change` pipeline, in particular the `if V == B: write_if_diff(path, H)` hub-only branch), §1 ("the hub (SQLite) is the writer of record; each file-backed spoke is a projection under contract"); `docs/spec-questions.md` **T13.3** (binding narrowest reading).
- **Steps** — (1) Add `project_node_change(conn, node_ids, origin_tracker) -> list[str]` to `reconcile.py`: build the existing `ProjectionIndex` (`ProjectionIndex.build`), map each node id through its existing `owner()` lookup, de-duplicate the resulting paths, and for each path construct a `Reconciler(conn, origin_tracker)` and call its **existing** `on_change(path)` — reuse the pipeline verbatim, do not write a second projection path. Return the list of paths actually reconciled. (2) A node owned by no managed file yields no path and no work — unfiled nodes stay unfiled (they remain counted by `GET /sync/export`'s `unfiled_node_count`; this task must not invent a "file assignment" mechanism, which would be new spec). (3) A path that has vanished from disk between the index build and the call is not a crash — mirror `reconcile_all`'s existing `FileNotFoundError` handling exactly. (4) The helper takes the tracker as a parameter and never constructs its own — sharing the daemon's live `OriginTracker` is the entire point (echo suppression, D10's lesson). (5) Unit-test at the `tests/unit/sync/test_reconcile.py` level: a node projected into a managed file gets that file re-projected after a hub-side `commit_node` (vault text now shows the new body/checkbox); an unfiled node produces `[]` and touches no file; a second immediate call is a quiet no-op (`write_if_diff` returns False — the base snapshot was updated by the first).
- **Verify** — `uv run pytest tests/unit/sync/test_reconcile.py`
- **DoD** — the helper re-projects exactly the managed files that own the given nodes and nothing else; unfiled nodes are a no-op; repeat calls are quiet; no new endpoint, schema, or grammar; `make check` + `make battery` green.

### T13.3 — Wire hub-side mutations to re-project their managed file
- **Goal** — Close the audit's flagship gap: after a mutation through the API (CLI, Web UI, plugin, agent-approved proposal — every non-vault surface), the managed vault file that projects the affected node is refreshed within the same request, instead of staying stale until the next daemon restart, filesystem event, or manual `POST /sync/rescan`.
- **Depends on** — T13.1, T13.2.
- **Files** — `src/akasha/api/app.py`, `src/akasha/daemon.py`, `src/akasha/api/routes/nodes.py`, `tests/integration/test_projection_writeback.py` (new).
- **Spec** — §4.8, §1, §4.11 `/nodes*` mutating rows; `docs/spec-questions.md` **T13.3**.
- **Steps** — (1) In `api/app.py`'s `create_app`, construct one `OriginTracker` and hang it on `app.state` (single instance for the app's whole lifetime — never one per request, for the same reason `daemon.py` already documents for the watcher's tracker). (2) In `daemon.py`'s `serve`, make the live `Watcher`'s reconciler use **that** tracker instead of constructing its own, so a write made on the request path is recognized as an echo by the watcher and does not start a second cycle (debug-plan D10 is the precedent for what happens when these disagree). (3) In `routes/nodes.py`, after a successful `create_node`/`patch_node`/`delete_node`/`split`/`merge`/`vet` — i.e. after the store transaction has committed, never inside it — call `reconcile.project_node_change(conn, [affected ids], request.app.state.origin_tracker)`. Use a function-body deferred import if needed to avoid an import cycle, the same pattern `store.commit_node` already uses for `invalidate`. (4) A projection failure must never fail the API call: wrap the call so an exception is logged (structured JSON, per §3) and swallowed — the hub is the writer of record and its write already succeeded; a spoke projection is best-effort by doctrine (PRD §7.8). (5) Do **not** add a background thread, a polling loop, or a scheduler. (6) New integration test `tests/integration/test_projection_writeback.py` driving a **real** managed file through a **real** app: register a sync root, reconcile a file containing an anchored task line, then (a) `PATCH` its body over HTTP and assert the file on disk now shows the new body, (b) `PATCH task_state=done` and assert the file's checkbox is now `- [x]`, (c) assert the write is LF-only and canonical (§4.3 — the 2026-07-24 CRLF class of bug), (d) assert a node in no managed file causes no file writes anywhere under the root, (e) assert the review queue is unchanged by the projection itself (it enqueues nothing of its own); (f) **assert the swallow**: force `project_node_change` to raise (monkeypatch) and assert the mutation still returns its normal 2xx, the node is still committed, and the failure was logged — without this leg, the most likely silent regression in this task is untested.
- **Verify** — `uv run pytest tests/integration/test_projection_writeback.py tests/integration/test_api.py tests/battery/test_edit_battery.py`
- **DoD** — a hub-side edit or checkbox change through any `/v1/nodes*` mutating endpoint appears in the managed vault file without a restart or manual rescan, canonically and LF-only; unfiled nodes write nothing; a projection error never turns a successful mutation into an HTTP error (asserted, not just claimed); the E01–E20 battery is unregressed (0 silent guesses); `make check` + `make battery` green. *(The review-resolution mutation surface — `POST /v1/review/{id}/resolve`, which also commits through the store — is deliberately **out of this task's Files list** and is closed by T13.6; do not widen this task to reach it.)*

### T13.4 — CLI: `akasha set --task-state open|done`
- **Goal** — Give the terminal (and every script/agent driving it) the ability to complete or re-open a task, as a pure HTTP client of T13.1's field.
- **Depends on** — T13.1.
- **Files** — `src/akasha/cli/main.py`, `tests/integration/test_cli.py`, `tests/integration/test_cli_dry_run.py`, `docs/user/cli.md`.
- **Spec** — §4.12 `akasha set` row; §4.11 `PATCH /nodes/{id}`; `docs/spec-questions.md` **T14.2** (API-first parity reasoning); PRD §7.11.
- **Steps** — (1) Add `--task-state` (`open|done`, default `None`) to the existing `set_` command; include it in the payload **only when supplied**, so an omitted flag produces today's exact request body (T13.1's omitted-vs-null distinction must survive the CLI). (2) Everything else is free: `set` already routes through `_mutate`, so `--json`, `--dry-run`, `--token`, `--base-url` and the exit-code mapping need no new code. (3) `test_cli_dry_run.py`: `set` is already a registered mutating verb, so `_discovered_mutating_verbs()` is unchanged — but add a `set_task_state` `DryRunCase` variant covering the new flag and add its id to the meta-test's variant-exclusion set next to `rm_with_redirect` (the file's existing pattern for a flag variant of an existing verb). (4) Document the flag in `docs/user/cli.md` next to `set`.
- **Verify** — `uv run pytest tests/integration/test_cli.py tests/integration/test_cli_dry_run.py`
- **DoD** — `akasha set <id> --task-state done` closes a real task against a live test daemon and `akasha get <id>` reflects it; omitting the flag sends today's exact body; `--dry-run` issues zero HTTP mutations; the dry-run meta-test is green; `make check` + `make battery` green.

### T13.5 — Web UI: node view shows and toggles task state, and shows the subtask structure
- **Goal** — Make the node view usable for tasks: show whether a task is open or done, show its supertask and subtasks as navigable structure (not raw ids), and let the user toggle its checkbox — which, with T13.3, writes straight back to the Obsidian file.
- **Depends on** — T13.1, T13.3.
- **Files** — `src/akasha/ui/static/app.js`, `src/akasha/ui/templates/node.html`, `tests/integration/test_ui_task_view.py` (new).
- **Spec** — §4.13 Node view ("body, facets, 1-hop neighborhood, history, stale badge with cause"); §4.11 `PATCH /nodes/{id}`, `GET /nodes/{id}/neighborhood`; PRD §8 story 8; `docs/spec-questions.md` **T14.6** (the "spec silent on a UI affordance, build the smallest thing" precedent — D5/T8.3).
- **Steps** — (1) In `renderBody` (or a small sibling renderer), display `task_state` for task-type nodes — `Open` / `Done` — and display the node's `maturity` (already returned by `GET /nodes/{id}`, currently rendered nowhere). Non-task nodes must look exactly as they do today. (2) Add a **Tasks** section for task nodes: from the existing neighborhood payload, list `composes` children (subtasks) and `composes` parents (supertask), each as a `nodeLink` (the helper D8 already added — reuse it, do not write a second link builder), each showing its own state once fetched. Keep the fetch bounded (the 1-hop neighborhood only). (3) Add one toggle control that `PATCH`es `{task_state, change_class:"patch", facets_touched:[]}` through the existing `postJson`-style helper (extend it to allow `PATCH` rather than adding a second fetch wrapper), then re-renders the view from the server response — never optimistically from local state. (4) Copy discipline: PRD R9 — never the word "true"; a supertask whose subtasks are all closed is "flagged for review", never "complete". (5) Never auto-close a supertask from the UI (design invariant 3). (6) New Playwright test `tests/integration/test_ui_task_view.py` against a live daemon: seed a supertask + two subtasks with real `composes` edges; assert the node view shows state, maturity, and both subtasks as links; click the toggle on the last open subtask; assert the subtask reads Done, the supertask appears in `/review` flagged `subtasks_closed`, and the supertask's own state is still Open.
- **Verify** — `uv run pytest tests/integration/test_ui_task_view.py tests/integration/test_ui_node.py`
- **DoD** — a task's state, maturity, supertask and subtasks are visible and navigable in the Web UI; toggling completes the task through the real API; the supertask is flagged for review and never auto-closed; no new endpoint or schema; `make check` (with Chromium) + `make battery` green.

### T13.6 — Project review-resolution commits back to the vault too
- **Goal** — Close the second hub-side mutation surface, which `routes/nodes.py` does not cover: resolving a review with `revised` calls `store.commit_node` (§4.9: "the client submits a new commit; that commit is itself classified") and approving a proposal calls `store.create_node` — both through `POST /v1/review/{id}/resolve`, i.e. through the one write the Web UI already had before this plan. Without this, resolving a stale badge in the UI still leaves the Obsidian file showing the pre-revision text.
- **Depends on** — T13.3.
- **Files** — `src/akasha/api/routes/review.py`, `tests/integration/test_projection_writeback.py`.
- **Spec** — §4.9 (resolutions: `still_holds`/`revised`/`retracted`/`dismissed`; proposal approval records `still_holds`), §4.11 `POST /review/{id}/resolve`, §4.8; `docs/spec-questions.md` **T13.3**.
- **Steps** — (1) Read `tms/review.py`'s `resolve_review`/`approve_proposal`/`resolve_reassignment` first: they own their own transactions, so the projection call belongs in the **route**, after the resolver returns — never inside `tms/review.py`, and never inside a store transaction. (2) After a successful resolve, derive the affected node id(s) from the resolver's return value / the review row (`review_queue.node_id`; for an approved create-proposal, the newly minted id the approver returns) and call `reconcile.project_node_change(conn, ids, request.app.state.origin_tracker)` — the same helper and the same shared tracker T13.3 wired, not a second mechanism. (3) Resolutions that commit nothing (`still_holds`, `dismissed`) must still be safe to pass through the helper: a node whose projection is already current produces a quiet no-op cycle, which is the correct behavior — do not add a special case guessing which resolutions changed content. (4) A freshly-approved create-proposal mints a node that belongs to no managed file: it must project nothing (unfiled stays unfiled) — assert this rather than assuming it. (5) Same error discipline as T13.3: a projection failure is logged and swallowed, never converted into a failed resolution. (6) Extend `tests/integration/test_projection_writeback.py` (do not add a second file): resolve a real `facet_break` review with `revised` over HTTP against a real managed file and assert the file on disk now shows the revised body, LF-only and canonical; assert an approved create-proposal writes no file; assert a `still_holds` resolution leaves the file byte-identical.
- **Verify** — `uv run pytest tests/integration/test_projection_writeback.py tests/integration/test_tms.py`
- **DoD** — a `revised` resolution submitted through the API (and therefore through the Web UI's review view) lands in the managed vault file within the same request, canonically; `still_holds`/`dismissed` leave the file byte-identical; an approved create-proposal projects nothing; a projection failure never fails a resolution; `tests/integration/test_tms.py` unregressed; `make check` + `make battery` green.

---

## M14 — Definition DAG: make the graph creatable, navigable, and refactorable (Depends on: nothing)

**Milestone DoD:** a user can, without ever hand-writing an HTTP request,
create a definition with facets, link it to other nodes with facet-bound
justification edges (including facets born from a highlighted span), read a
node's 1-hop neighborhood and history, vet a node to S4, and split or merge a
definition with its inbound-edge reassignment queue — from the CLI and, for
the linking and navigation half, from the Web UI. `make check` and
`make battery` green.

### T14.1 — CLI: `akasha neighborhood ID` and `akasha history ID`
- **Goal** — Give the terminal read access to the graph. Both endpoints have shipped and been tested since M4; neither is reachable from any surface but raw HTTP, so the DAG is currently un-navigable outside the browser.
- **Depends on** — none (milestone gate only).
- **Files** — `src/akasha/cli/main.py`, `tests/integration/test_cli_graph.py` (new), `docs/user/cli.md`.
- **Spec** — §4.11 `GET /nodes/{id}/history · /neighborhood?hops=1`; §4.12 (verb list); PRD §7.11 (API-first parity: "the CLI tracks the API … nothing is ever UI-only"); PRD §7.5 (retrieval semantics); `docs/spec-questions.md` **T14.2** (binding narrowest reading — pure HTTP clients of shipped endpoints only).
- **Steps** — (1) Add `neighborhood(node_id, --hops INT = 1)` → `GET /v1/nodes/{id}/neighborhood?hops=`, and `history(node_id)` → `GET /v1/nodes/{id}/history`, both via the existing `_request` helper so `--json`, `--token`, `--base-url` and exit codes come free. (2) Both are **read-only** — no `_mutate`, therefore no `tests/integration/test_cli_dry_run.py` entry is required or permitted (its meta-test discovers mutating verbs only; adding a read verb there would break it). (3) Human-readable (non-`--json`) output stays deliberately plain: one line per edge (`src -edge_type-> dst`, plus facet binding when present) and one line per commit (hash, change class, message, ts) — no new formatting library, no ASCII-art graph. (4) Windows console safety: no non-ASCII glyphs in default output (pre-mvp T9.9 was a real `UnicodeEncodeError` crash from exactly this). (5) Document both verbs in `docs/user/cli.md`.
- **Verify** — `uv run pytest tests/integration/test_cli_graph.py`
- **DoD** — both verbs round-trip against a live test daemon (real edges/commits seeded, both plain and `--json` output asserted), exit 3 on an unknown id, emit ASCII-only default output; no server-side change; `make check` + `make battery` green.

### T14.2 — CLI: `akasha edge add` / `akasha edge rm`
- **Goal** — Let a user actually build the DAG: create facet-bound justification and composition edges, and retract them — the single biggest reason the graph is unbuildable outside the vault today.
- **Depends on** — T14.1 (same file: `cli/main.py`).
- **Files** — `src/akasha/cli/main.py`, `tests/integration/test_cli_edge.py` (new), `tests/integration/test_cli_dry_run.py`, `docs/user/cli.md`.
- **Spec** — §4.11 `POST /edges · DELETE /edges/{id}` (including the facet-binding validation rule and `facet_span`); §4.2 `Edge` (`facet_binding` REQUIRED for justification edge types, `None` allowed only for `composes`/`redirects_to`); §4.6; PRD §7.1; `docs/spec-questions.md` **T14.2**.
- **Steps** — (1) Add an `edge` sub-`typer.Typer()` app (same pattern as the existing `token_app`/`sync_app`) with `add SRC DST TYPE [--facet-binding ID|*] [--facet-span TEXT] [--mode track|pin] [--pinned-commit HASH]` and `rm EDGE_ID`. (2) `add` → `_mutate(state, "POST", "/v1/edges", payload)`; `rm` → `_mutate(state, "DELETE", f"/v1/edges/{id}", None)`. Pure client — **no client-side validation of the facet-binding rule**: the server already enforces it and its 400 must reach the user verbatim through the existing error envelope → exit-code mapping (inventing a second copy of the rule in the CLI is exactly the drift rule 0.2 exists to prevent). (3) `--facet-span` is passed straight through to the endpoint's existing `facet_span` field (T7.7), which creates the facet on the target — this is the terminal half of PRD R8's facets-from-spans flow. (4) Register the sub-app (`app.add_typer(edge_app, name="edge")`). (5) `tests/integration/test_cli_dry_run.py`: add `DryRunCase` rows for `edge add` and `edge rm` — the AST meta-test structurally requires one per new mutating verb (T12.2's landing note). (6) Document in `docs/user/cli.md`, including one worked example of a facet-bound `depends_on` edge.
- **Verify** — `uv run pytest tests/integration/test_cli_edge.py tests/integration/test_cli_dry_run.py`
- **DoD** — `akasha edge add` creates a real edge against a live test daemon (asserted via `GET /v1/nodes/{id}/neighborhood`), including a `--facet-span` case that creates a real facet on the target (asserted via `GET /v1/nodes/{dst}`); a justification edge with no binding fails with the server's own 400 and exit 4, not a client-side message; `akasha edge rm` retracts it (gone from the neighborhood, node still live); `--dry-run` issues zero mutations; dry-run meta-test green; `make check` + `make battery` green.

### T14.3 — CLI: `akasha vet ID` (the S4 human act)
- **Goal** — Make the top of the maturity ladder reachable. S4 is the one stage the spec says is a *user act* (§4.6, PRD §6), it gates what is exported as verified memory, and today nothing but a hand-written HTTP call can set it.
- **Depends on** — T14.2 (same file: `cli/main.py`).
- **Files** — `src/akasha/cli/main.py`, `tests/integration/test_cli_vet.py` (new), `tests/integration/test_cli_dry_run.py`, `docs/user/cli.md`.
- **Spec** — §4.11 `POST /nodes/{id}/vet` (human token only, ∅ — never proposalized); §4.6 (`S4 iff vetted flag set by human token`); PRD R9 (language: "vetted by you", never "true"); `docs/spec-questions.md` **T14.2**.
- **Steps** — (1) Add `vet(node_id)` → `_mutate(state, "POST", f"/v1/nodes/{id}/vet", None)`. (2) An agent-class token must receive the server's own 403 (the endpoint is `require_human`/∅ — it is *never* rewritten into a proposal); surface it through the existing envelope, adding no client-side token-class check. (3) Output copy says "vetted by you", never "true" (PRD R9) — and note in the help text that vetting is a claim about your own review, not about the world. (4) Add the `vet` `DryRunCase` row. (5) Document in `docs/user/cli.md`.
- **Verify** — `uv run pytest tests/integration/test_cli_vet.py tests/integration/test_cli_dry_run.py`
- **DoD** — `akasha vet <id>` with a human token sets `vetted` and the node's maturity reads `S4` on the next `akasha get`; an agent token gets the server's 403 mapped to a non-zero exit with no traceback; `--dry-run` mutates nothing; output contains no "true"-language; `make check` + `make battery` green.

### T14.4 — CLI: `akasha split` / `akasha merge`
- **Goal** — Make PRD §8 story 4's refactor operations usable rather than property-tested only: splitting or merging a definition, seeing the redirect, and seeing the per-inbound-edge reassignment queue it produces.
- **Depends on** — T14.3 (same file: `cli/main.py`).
- **Files** — `src/akasha/cli/main.py`, `tests/integration/test_cli_split_merge.py` (new), `tests/integration/test_cli_dry_run.py`, `docs/user/cli.md`.
- **Spec** — §4.11 `POST /nodes/{id}/split · /merge` ("returns redirect + reassignment queue"; merge: the path id survives, body `{"ids":[other_ids...]}`); §4.9 (`reassignment` items resolve via `still_holds`); §7.4/PRD §7.4; `docs/spec-questions.md` **T14.2**.
- **Steps** — (1) Add `split(node_id, --part 'TYPE=BODY' repeatable)` posting `{"parts":[...]}` in the exact shape the endpoint already accepts — read `routes/nodes.py`'s `SplitBody` and `store.split_node` first and mirror them; invent no new part shape. (2) Add `merge(node_id, other_ids...)` posting `{"ids":[...]}`, with the path id as the survivor per the spec's note. (3) Both through `_mutate`. (4) Human-readable output must state the resulting successor ids, the redirect, and **how many reassignment review items were opened**, then point at `akasha review list` — the queue is the whole point of the operation (zero dangling references is the invariant it protects). (5) Add both `DryRunCase` rows. (6) Document in `docs/user/cli.md`, including the "no refactor leaves a dangling id" guarantee and how to work the reassignment queue.
- **Verify** — `uv run pytest tests/integration/test_cli_split_merge.py tests/integration/test_cli_dry_run.py`
- **DoD** — against a live test daemon, `akasha split` on a node with inbound edges produces successors, a tombstone/redirect for the old id, and one reassignment review per inbound edge (count asserted against `GET /v1/review`); `akasha merge` produces the inverse with the path id surviving; both are visible via `akasha review list` and resolvable via `akasha review resolve <id> still_holds`; `--dry-run` mutates nothing; `make check` + `make battery` green.

### T14.5 — Web UI: make the 1-hop neighborhood navigable
- **Goal** — Turn the node view's neighborhood from a list of opaque id pairs (`abcd1234 -composes-> efgh5678`, plain text, no links) into the ranked 1-hop view PRD §7.5 describes: grouped by direction and edge type, showing each neighbor's body and node type, every neighbor a link.
- **Depends on** — none (milestone gate only; shares `app.js` with T13.5/T14.6 — the orchestrator will serialize).
- **Files** — `src/akasha/ui/static/app.js`, `tests/integration/test_ui_node_links.py`.
- **Spec** — §4.13 Node view ("1-hop neighborhood"); §4.11 `GET /nodes/{id}/neighborhood`, `GET /nodes/{id}`; PRD §7.5 (atom + immediate composition parents and justification neighbors, expandable hop-by-hop); debug-plan D8 (the id-as-plain-text class of defect this finishes closing — D8 fixed search/review/sync, never the neighborhood).
- **Steps** — (1) In `renderNeighborhood`, split the existing `edges` array into **outbound** (`edge.src === nodeId`) and **inbound** (`edge.dst === nodeId`) groups, and within each group sub-group by `edge_type`, `composes` first (composition ancestry) then justification types (§4.2's `JUSTIFICATION` set order). (2) Fetch each distinct neighbor id's node once (`GET /v1/nodes/{id}`, bounded by the 1-hop set) and render its `node_type` and a truncated body next to the link — reuse the existing `truncate` helper from the search view and the existing `nodeLink` helper (do **not** add a second link builder, and do **not** add or change any endpoint to carry bodies). (3) Show each edge's `facet_binding` when present (a `*` binding is displayed as such — it is what the facet-coverage metric counts against). (4) A neighbor fetch that fails must degrade to the plain id link, never blank the section. (5) Extend `tests/integration/test_ui_node_links.py` (do not add a second links test file — D8 owns this one) with a Playwright case: seed a node with one inbound `supports` edge and one outbound `composes` edge, assert both groups render with node type + body text, and assert clicking a neighbor navigates to `/node?id=<neighbor>`.
- **Verify** — `uv run pytest tests/integration/test_ui_node_links.py`
- **DoD** — the neighborhood section shows direction-grouped, type-labelled, facet-annotated neighbors with body previews and working links; a failed neighbor fetch degrades gracefully; no endpoint or schema change; `make check` (with Chromium) + `make battery` green.

### T14.6 — Web UI: facets-from-spans link form on the node view
- **Goal** — Build the never-built UI half of M7's DoD ("facets-from-spans capture flow in API/**UI**"): let the user link the node they are reading to another node by highlighting the span of the target that the link depends on, so facets accrete as a byproduct of linking. This is PRD R8's designed fix for facet bootstrap, and `facet_coverage` (§7) is a **gating** dogfood metric — "persistently low coverage means the TMS loop is inert."
- **Depends on** — T14.5.
- **Files** — `src/akasha/ui/static/app.js`, `src/akasha/ui/templates/node.html`, `tests/integration/test_ui_link_form.py` (new).
- **Spec** — §4.11 `POST /edges` + its `facet_span` behavior (T7.7: creates the facet on the target); §4.2 (`facet_binding` REQUIRED for justification edges); §4.13; PRD R8, PRD §7.1, §7 metrics (`facet_coverage`); `docs/spec-questions.md` **T14.6** (binding narrowest reading).
- **Steps** — (1) Add a small "Link this node" form to `node.html` (target node id, edge type from §4.2's closed `EdgeType` list, and a span field) plus the matching JS. (2) The span field is filled either by pasting or by a "use selection" button that copies the current text selection from the rendered target-body preview — keep it to standard `window.getSelection()`; no editor library, no new dependency. (3) Submit to the **existing** `POST /v1/edges` with `{src, dst, edge_type, facet_span, provenance:"human"}`; on success re-render the neighborhood section (T14.5) so the new edge is immediately visible. (4) Surface the server's 400 verbatim when a justification edge is submitted with neither a binding nor a span — the rule stays server-side only. (5) The form must never create a node; linking to an id that does not exist is the server's 404, shown as-is. (6) New Playwright test `tests/integration/test_ui_link_form.py`: seed two definitions, link them from the UI with a real span, assert the edge exists via the API, assert a **real facet** now exists on the target carrying that span, assert the new edge appears in the neighborhood without a page reload, and assert `GET /v1/metrics`'s `facet_coverage` is non-zero afterwards (the metric this flow exists to move).
- **Verify** — `uv run pytest tests/integration/test_ui_link_form.py tests/integration/test_ui_smoke.py`
- **DoD** — a user can create a facet-bound justification edge entirely from the Web UI, the highlighted span becomes a real facet on the target, the neighborhood updates in place, server-side validation errors are shown verbatim, and `facet_coverage` moves as a result; no new endpoint or schema; `make check` (with Chromium) + `make battery` green.

---

## M15 — Real-use validation: todo synchronization (Depends on: M13)

**Milestone DoD:** the todo-sync round trip has been exercised against a real
running daemon and real Obsidian-shaped files — not fixtures inside pytest —
at least once content-blind (T15.1) and once by the human on their own real
task lists (T15.2), with both outcomes written down honestly, including
anything that did not work.

**Note on milestone gating:** this milestone contains a `BLOCKED:
human-only` task, so it can never be "closed"; nothing in this plan depends
on it, deliberately (see the dependency map).

### T15.1 — Live end-to-end todo-sync exercise on a generated task vault (content-blind)
- **Goal** — Drive every leg of the todo-sync round trip through a real daemon against a real on-disk vault, and record what actually happened: `^tm-new` minting on task lines, indentation → `composes`, checkbox toggle in the vault → hub, hub-side completion (CLI/UI) → write-back to the vault (T13.3), last-subtask close → supertask review item, re-indent → reparent, and an embed of a task line in two other files showing one state. **Content-blind:** every file this task creates is generated from a fixed template the task itself writes — it never reads, copies, or interprets the user's real notes, and it makes **no** judgment about what deserves tracking (that is T15.2, human-only).
- **Depends on** — (milestone gate: M13 DONE).
- **Files** — `docs/dogfood/todo-sync-report.md` (new — counts, timings, observed behavior, and every failure; no personal note content, same leak discipline as `docs/dogfood/scaled-smoke-report.md`). Everything else this task touches (scratch vault, scratch `config.toml`, scratch DB) lives **outside the repo entirely**, under `$HOME/.local/share/akasha-dogfood/` or the Windows equivalent — never the default `tm-daemon` config dir, never inside the working tree.
- **Spec** — §4.7 (`task_line`, `new_line`, `embed`, `ref`, `indent`), §4.8 (reconcile pipeline), §4.10 (`all_subtasks_closed`), §4.11 (`/nodes`, `/review`, `/sync/*`), §4.12; PRD §8 story 8; `docs/dogfood/README.md` (the existing runbook — reuse its commands, do not re-derive them).
- **Steps** — (1) Follow `docs/dogfood/README.md` to stand up a scratch daemon with its own config/DB (use `akasha init` for the token — T12.1 exists now, do not use the old direct-store bootstrap). (2) Generate a small vault (~6 files) of realistic Obsidian shape from a fixed template: YAML front-matter, wikilinks, native non-`tm` `^block-id`s, prose paragraphs, and nested `- [ ]` task lists 3 levels deep — all fixed text, nothing derived from real notes. (3) Register it (`akasha sync add`), rescan, and walk the legs in order, recording the literal observed result of each: (a) add ` ^tm-new` to task lines → confirm real ids minted and lines rewritten with no echo loop; (b) confirm indentation produced real `composes` edges (`akasha neighborhood`, or the API if M14 has not landed); (c) toggle a checkbox in the file → confirm the hub's `task_state` follows; (d) complete a task through `akasha set --task-state done` → **confirm the vault file now shows `- [x]` without a restart or manual rescan** (T13.3's whole point); (e) close the last open subtask → confirm exactly one `subtasks_closed` review on the supertask and that the supertask was **not** auto-closed; (f) re-indent a subtask under a different parent → confirm the reparent retracted the old `composes` and created the new one; (g) embed the same task line into two other files (`![[file#^tm-<id>]]`) and confirm all three render one state after a hub-side toggle. (4) **Catalogue every linter/violation code that fired, and on how many files** — a bare "none" is only meaningful if the report shows it was actually counted (pre-mvp T11.4's discipline). (5) Record RSS before/after and the `sync_cycle_ms` p50/p95 from `GET /v1/metrics`. (6) Write the report, including a closing line stating plainly that this validates mechanics only and makes no claim about the user's real vault — that remains T15.2's human-only call.
- **Verify** — N/A as a single pytest command (live-daemon leg, same framing as pre-mvp T11.4 and T11.2). Two checks stand in: (a) `make check && make battery` must be green at the commit that lands the report (no code changes are expected from this task, so a red gate means something else broke); (b) an independent `fleet-verifier` re-queries the scratch DB directly (`sqlite3`: node/edge/`sync_files`/`review_queue` counts) and re-reads the scratch vault files on disk to confirm the report's claimed numbers and the `- [x]` write-back, rather than trusting the prose.
- **DoD** — `docs/dogfood/todo-sync-report.md` exists with **real, observed** (never projected) results for all seven legs (a)–(g), an actually-counted violation catalogue, real metric samples, an explicit list of anything that failed or surprised, and the content-blind disclaimer; no personal note content anywhere in the report; nothing written inside the repo except that file.

### T15.2 — MANUAL: run your own real todo lists through it for a week (human-only)
- **Goal** — The question no automated leg can answer: does the user actually want to keep their real tasks in this thing? A human puts their own real task lists under `^tm-` management in their real vault, works normally for a week, and records what the experience was — friction, violations against messy real content, whether the review queue stayed sane, whether they would keep doing it.
- **Depends on** — T15.1.
- **Files** — `docs/dogfood/todo-sync-human-log.md` (new — the human-authored observation record: counts, friction notes, verdict; never the vault content itself).
- **Spec** — §4.7, §4.8, §4.12; PRD §8 story 8, PRD §9 Phase-2 dogfood gate, PRD §11 (review inflow ≤ capacity; violation rate "low enough that the linter feels like a spellchecker, not a nag").
- **Steps (manual runbook — explicitly not automated, same DoD category as `plugin-obsidian/TESTPLAN.md` and pre-mvp T11.2)** — (1) Decide **as a human** which of your real tasks and lists you want tracked; add anchors accordingly. (2) Work normally for a week: complete tasks in Obsidian, complete some from the CLI or Web UI, nest and re-nest, embed a task somewhere else. (3) Each time something felt wrong — a violation you did not cause, a stale projection, a supertask flagged at the wrong moment, an edit you had to repeat — write it down at the time, not from memory. (4) Record: number of tracked tasks, violations by code, review items opened vs resolved, anything the linter flagged that a normal edit created, and a one-line verdict on whether you would keep using it. (5) Anything that looks like a defect becomes a new `docs/mvp-debug-plan.md` entry (that file's own D-series conventions), not a silent note here.
- **Verify** — N/A (manual, human-only leg — no autonomous worker may execute this task; see the human-in-the-loop boundary above). The DoD is the completed, dated log.
- **DoD** — a dated `docs/dogfood/todo-sync-human-log.md` written by the human, covering at least one real week, with real counts and an explicit keep/drop verdict; any defect found is filed as its own debug-plan entry.

---

## M16 — Real-use validation: the definition DAG (Depends on: M14)

**Milestone DoD:** the definition/claim/relation layer has been exercised
end-to-end against a real daemon — created, linked with facet-bound edges,
broken, adjudicated, split, navigated, vetted — once content-blind (T16.1)
and once by the human against their own real knowledge (T16.2), with both
outcomes written down honestly.

**Note on milestone gating:** as with M15, this milestone contains a
`BLOCKED: human-only` task and is deliberately a leaf.

### T16.1 — Live end-to-end definition-DAG exercise (content-blind)
- **Goal** — Prove the whole DAG loop works as one coherent feature against a real daemon, through the surfaces a user actually has (CLI + Web UI, never raw HTTP), and record what happened. **Content-blind:** every node body is a fixed generated string; nothing is derived from the user's real knowledge or notes.
- **Depends on** — (milestone gate: M14 DONE).
- **Files** — `docs/dogfood/definition-dag-report.md` (new — counts, timings, observations, failures; no personal content). Scratch daemon/DB outside the repo, as in T15.1.
- **Spec** — §4.2, §4.5, §4.6, §4.9, §4.11, §4.12, §4.13, §7 metrics; PRD §7.1 (node types, mandatory facet bindings), §7.3 (interface-break rule), §7.4 (refactor ops), §7.5 (retrieval), PRD §8 stories 3/4/5/6, PRD R8 (facet coverage as a gating metric).
- **Steps** — (1) Stand up a scratch daemon per `docs/dogfood/README.md` (`akasha init` for the token). (2) Build a small graph entirely through the CLI and Web UI: ~3 definitions with real facets, ~4 claims, ~2 reified relations, plus `composes`, `depends_on`, `supports` and `contradicts` edges — at least two of them created through the **Web UI's span form** (T14.6) so the facets-from-spans path is exercised for real, not just its test. (3) Read the graph back: `akasha neighborhood` at 1 and 2 hops, `akasha history`, and the node view's neighborhood — record whether it is genuinely navigable (could you find your way from a claim to its supporting evidence without knowing ids in advance?). (4) Break an interface: commit a `major` change removing/renaming a subscribed facet; record exactly which subscribers were flagged, whether any *shouldn't* have been (false-invalidation rate is a PRD §11 metric), whether the badge named cause and version, and that staleness did **not** recurse past an unreviewed node (§4.9's damper). (5) Adjudicate one item each way — `still_holds`, `revised`, `retracted` — through the UI, and record what each did. (6) Split one definition with real inbound edges; confirm a reassignment review per inbound edge and **zero dangling references** afterwards; resolve the queue. (7) Vet one node (`akasha vet`) and confirm it reads `S4`. (8) Record `GET /v1/metrics` before and after: `facet_coverage`, `review_inflow_7d`/`review_resolved_7d`, `crossing_rate`. (9) Read a node `--as-of` an earlier timestamp and confirm it renders the earlier belief state (story 5, from the CLI). (10) **Leave the evidence in place:** do not delete the scratch DB or the daemon's structured JSON log at the end of the run, and name both by absolute path in the report. Legs (4)–(5) are claims about a *sequence* (which subscribers were flagged, in what order, and what each adjudication did) and are **not** re-derivable from final DB state — the log is the only thing that can substantiate them, and the false-invalidation observation is the one PRD §11 metric this task exists to produce. (11) Write the report with a per-leg result table, every failure or surprise, and a closing line disclaiming any content-usability conclusion (that is T16.2).
- **Verify** — N/A as a single pytest command (live-daemon leg, same framing as T15.1/pre-mvp T11.4). Stand-ins: (a) `make check && make battery` green at the landing commit; (b) an independent `fleet-verifier` re-queries the scratch DB (`sqlite3`) for the node/edge/facet/review counts and the `vetted`/maturity values the report claims, re-runs one of the report's own CLI read commands against the scratch daemon, **and for legs (4)–(5) reads the preserved daemon JSON log** — those legs are sequence observations that final DB state cannot confirm, so a report claiming them without a log to back them is not verified.
- **DoD** — `docs/dogfood/definition-dag-report.md` exists with real observed results for legs (2)–(9), including the before/after `facet_coverage` numbers, an explicit statement of any false invalidation observed, a confirmed zero-dangling-reference check after the split, every failure recorded, the absolute paths of the preserved scratch DB and daemon log, and the content-blind disclaimer.

### T16.2 — MANUAL: put your own real definitions in it (human-only)
- **Goal** — The judgment call the system exists to make cheap but must never make: which of the user's own concepts, definitions and claims are worth tracking, how they decompose, and which facet of a definition a relation really depends on. A human does this for real, on their own domain, and records whether the resulting graph was worth having.
- **Depends on** — T16.1.
- **Files** — `docs/dogfood/definition-dag-human-log.md` (new — human-authored: counts, friction notes, verdict; never the content itself).
- **Spec** — PRD §5 F-list (F7 in particular), R9, R10 (the border toll), §6 (single-predicate rule, facets), §7.1, §11 (facet coverage; "first contradiction-with-provenance moment within week one"); `docs/mvp-spec.md` §4.2/§4.6/§4.9.
- **Steps (manual runbook — explicitly not automated)** — (1) Pick a domain you actually think in, and capture real definitions/claims from it — deciding, as a human, what is atomic enough to be a node and what is not. (2) When linking, actually use the span flow: highlight the part of the definition your relation depends on, and note whether that felt like ≤3 seconds of extra attention (PRD's hard budget) or like ontology work (the Cyc trap, F7). (3) Deliberately edit one definition in a way that breaks a facet other things depend on; record whether the right things were flagged and whether adjudicating them felt bounded. (4) Record: nodes created, facet coverage reached, review inflow vs what you actually resolved, crossing-rate friction (R10), whether any genuine "this contradicts what you believed, with source" moment occurred (PRD §11's conversion moment), and a one-line verdict. (5) Anything that looks like a defect becomes a `docs/mvp-debug-plan.md` entry, not a note here.
- **Verify** — N/A (manual, human-only leg — no autonomous worker may execute this task).
- **DoD** — a dated `docs/dogfood/definition-dag-human-log.md` written by the human with real counts, the facet-coverage number actually reached, an explicit verdict on whether the DAG was worth maintaining, and any defect filed separately.

---

## M17 — User-facing documentation for both objectives (Depends on: M13, M14)

**Milestone DoD:** a new user can install akasha, register a vault, run their
tasks through it, and build and navigate a definition DAG, using only
`docs/user/**` — with no step requiring them to read source code, and no
step describing a capability that does not exist.

### T17.1 — Rewrite onboarding docs around the installer-first flow (carried forward from pre-mvp T12.6)
- **Goal** — With T12.1–T12.5 all landed (`akasha init`, `akasha sync add`, the web-UI bootstrap link, `scripts/windows/setup.ps1`, and the compiled Inno Setup installer), make `docs/user/quickstart.md`, `web-ui.md`, `dogfood-windows.md` and `ops/autostart.md` describe the installer-first path as the default, with the from-source path demoted to a "developer setup" appendix (pointing at `docs/dev/setup.md`).
- **Depends on** — (milestone gate: M13, M14 DONE). *This is pre-mvp T12.6, renumbered and carried forward unchanged in scope — see the header's "What happened to T12.6". Its original dependencies T12.1–T12.5 are all DONE.*
- **Files** — `docs/user/quickstart.md`, `docs/user/web-ui.md`, `docs/user/dogfood-windows.md`, `docs/user/ops/autostart.md`.
- **Spec** — §4.12 (CLI verbs as they now actually exist), §4.13; `docs/pre-mvp/build-plan.md` T12.6 (original wording), `docs/dogfood/windows-service.md` (the supervisor-loop mechanism and its Task-Scheduler negative result), `docs/user/README.md` (index).
- **Steps** — (1) Quickstart leads with the installer, then `akasha init` → `akasha sync add` → the web UI; the from-source path moves to a clearly-labelled developer appendix. (2) Remove or correct every step that a landed task has obsoleted (e.g. any surviving `uv run python -c` bootstrap heredoc, any "no CLI verb to register a sync root" line, any "draft, uncompiled installer" language). (3) `ops/autostart.md` describes the shipped mechanism — Startup-folder shortcut + supervisor `.bat`, exit code 42 = user quit — and keeps the recorded negative result that Task Scheduler's native restart-on-failure does not work. (4) Do not describe anything that does not exist; if a step cannot be written without one, that is a `# SPEC-QUESTION:`, not prose.
- **Verify** — Doc-only. Objective checks, scoped to this task's own four files (`docs/user/README.md`'s stale project-maturity paragraph is **T17.2's** to fix — it is not in this Files list): `grep -n "uv run python -c" docs/user/quickstart.md docs/user/web-ui.md docs/user/dogfood-windows.md docs/user/ops/autostart.md` returns nothing; `grep -ni "no packaged installer\|uncompiled\|not yet packaged\|draft installer" docs/user/quickstart.md docs/user/web-ui.md docs/user/dogfood-windows.md docs/user/ops/autostart.md` returns nothing; every CLI verb named in these four files exists in `uv run akasha --help` (check each one). Plus a fresh-eyes read-through in which no step requires reading source code.
- **DoD** — both greps clean over the four files, every named verb real, and the four documents describe one coherent installer-first path end to end.

### T17.2 — Task/todo workflow guide
- **Goal** — One document that teaches the todo round trip as a user actually performs it: write tasks in Obsidian, anchor them, nest them, complete them from either side, watch the supertask get flagged, embed one task in several notes.
- **Depends on** — T13.5, T14.1.
- **Files** — `docs/user/obsidian.md`, `docs/user/README.md`.
- **Spec** — §4.7 (grammar — quote it exactly; this is a user-facing statement of the contract), §4.8, §4.10; PRD §8 story 8; `docs/dogfood/todo-sync-report.md` if T15.1 has landed (use its real observed behavior rather than describing intended behavior).
- **Steps** — (1) In `docs/user/obsidian.md`, add a task-workflow section: the exact `task_line` and `^tm-new` forms, what indentation does (`composes`), what a checkbox maps to, what happens when the last subtask closes (flagged for review, **never** auto-closed), and how to complete a task from the CLI/UI and see it land in the file. (2) State the in-contract obligation honestly: within contract it round-trips losslessly; out-of-contract edits are flagged, never guessed (PRD invariant 5) — and show what a violation actually looks like and how to repair it. (3) Cover embeds/refs: `![[note#^tm-<id>]]` shows one state everywhere; embeds are read-only projections of the hub's head. (4) Link the guide from `docs/user/README.md`. (5) In the same `README.md` edit, refresh its **stale project-maturity paragraph**, which still says "M0–M10 are done or code-complete… M11 (dogfood smoke test) is in progress… There is no packaged installer yet… (M12)" — all three claims are false as of this plan (M11's autonomous legs and all of M12 landed; the installer is compiled and live-verified; the only open pre-mvp row is the one carried forward here as T17.1). State the current position instead, and never overstate it: the two honestly-pending legs named at the top of `docs/agents/task-status.md` (the literal 24h soak, the real-deployment autostart attestation) stay disclosed. (6) No new capability may be described — only what is shipped.
- **Verify** — Doc-only. Objective checks: every anchor/task/embed form shown in the guide must be literally accepted by the shipped parser — verify by pasting each example into a scratch managed file under a live scratch daemon (or by checking each against `src/akasha/contract/grammar.py`'s regexes) and confirming it parses with zero violations; and `grep -ni "no packaged installer\|M11 (dogfood smoke test) is in progress\|not yet packaged" docs/user/README.md` returns nothing. Plus a fresh-eyes read-through.
- **DoD** — a user who has never seen the codebase can take a plain Obsidian task list to a fully managed, round-tripping one using only this guide; every example parses clean; `README.md`'s maturity paragraph is current and still discloses the two pending legs; no described capability is unimplemented.

### T17.3 — Definitions & the DAG guide
- **Goal** — One document that teaches the definition-DAG loop: create a definition with facets, link with a span, read the neighborhood, understand what makes something stale, adjudicate, split/merge, vet.
- **Depends on** — T14.4, T14.6.
- **Files** — `docs/user/definitions.md` (new), `docs/user/README.md`.
- **Spec** — §4.2, §4.6, §4.9, §4.11, §4.12, §4.13; PRD §6 (glossary: facet, change classes, maturity ladder, pin vs track), §7.1, §7.3, §7.4, R8, R9.
- **Steps** — (1) Explain, in user language, the pieces that make the loop work: node types, what a facet is and why edges bind to one, the maturity ladder S0→S4 and what each stage buys, and pin vs track. (2) Walk one worked example end to end using **only shipped surfaces**: `akasha new definition ... --facet`, link via the UI span form, `akasha neighborhood`, break a facet, see the badge, resolve it three ways, `akasha split`, work the reassignment queue, `akasha vet`. (3) State PRD R9's language rule and honor it throughout: "vetted by you", never "true"; the system guarantees your graph's internal consistency, not correspondence with reality. (4) Explain `facet_coverage` on the dashboard and why a low number means the loop is inert. (5) Link from `docs/user/README.md`.
- **Verify** — Doc-only. Objective check: every command shown runs successfully in order against a scratch daemon (run them; a command that errors is a doc bug), and `grep -rn "\btrue\b" docs/user/definitions.md` surfaces no claim-about-the-world usage (PRD R9). Plus a fresh-eyes read-through.
- **DoD** — every worked-example command executes as written against a fresh scratch daemon; the guide covers create → link-with-span → navigate → break → adjudicate → refactor → vet; R9 language respected throughout.

---

## Expandability guardrails (build-now-use-later — do NOT implement future phases)

These are constraints on the tasks above, not tasks themselves (spec §8):

- Keep the **agent-token → review-queue proposal pathway** (T4.6) intact;
  reserve `cause_kind=proposal` rendering. It is the Phase 3 decomposer's
  entry point. No task here may let an agent token mutate truth.
- Keep `api/schemas.py` **re-exportable** so a Phase 4 MCP facade can import
  only the HTTP API.
- The `tms/triggers.py` registry is the future host boundary — **do not add a
  script runner now**.
- All state stays **content-addressed with per-commit parents**. **Never
  introduce a global sequence counter** (multi-device/CRDT-friendliness).
- Treat the **golden corpus, OpenAPI snapshot, and no-pickle/canonical-bytes
  rules as sacred** (rule 0.3) — they are the Rust-migration enablers. Any
  task here that changes the served OpenAPI spec regenerates the snapshot in
  the same change (§6.3).
- **Never** let a machine decide what becomes tracked truth. Every task in
  this plan either wires an existing path or exposes an existing endpoint;
  none of them may add automatic node creation, automatic linking, or
  automatic vetting (design invariant 3).

**Explicit MVP non-goals — do not build even if easy:** LLM calls,
embeddings, MCP server, mobile, multi-user, task scheduling/recurrence,
prose management.
