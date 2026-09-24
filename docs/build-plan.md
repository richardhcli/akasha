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

**Purpose of this plan (M13–M21).** The MVP is code-complete and
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

**M18 (added 2026-09-23, user-directed — `docs/spec-questions.md` entry
M18-0)** is a second onboarding pass. M12 shipped the pieces (`akasha init`,
`akasha sync add`, the web-UI bootstrap link, the Windows installer); a live
run of the shipped daemon on a scratch vault the same day showed the
*sequence* is still the barrier: install → `init` → copy a secret → start the
daemon in a second terminal → `export` the secret and pass `--token` on every
verb → `sync add` → hand-add `tm: 1` front matter to every file. The file
watching itself was already event-driven and needed no work. The reference
point was `codegraph`, whose whole setup is one install line plus one
`init`, with no process for the user to babysit. Two defects found while
checking the ground truth are M18's first tasks: the built **wheel contains
zero `.sql` migrations** (so `uv tool install`/`pip install` yields a daemon
with no schema — only the PyInstaller path was ever fixed, T12.5), and the
CLI **never reads `AKASHA_TOKEN`** even though the quickstart tells users to
`export` it.

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
select it. Its scope is unchanged (same four docs, same DoD); its original
dependencies (T12.1–T12.5) are all `DONE`. **It is not, however, eligible
immediately**: it lives under `## M17 — ... (Depends on: M13, M14)`, and
that milestone gate — confirmed live against a real `fleet-orchestrator`
scan, 2026-08-05 — is load-bearing and blocks it until both M13 and M14
close, same as T17.2/T17.3. `docs/pre-mvp/**` is read-only reference and was
not edited to record this.

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

  M18 (onboarding):  T18.1 (wheel) ── free-standing, no dependency
                     T18.2 … T18.8 (CLI chain) ◄── T14.4   [share cli/main.py]
                     T18.12 (quickstart) ◄── T17.1 + T18.1–T18.8 [share quickstart.md]
                     T18.9 (token file) ◄── T18.5, T18.8 [cli/main.py]
                     T18.10a → T18.10b → T18.10c (.tmignore, track-by-default)
                       T18.10b/c share sync/reconcile.py with M19 → run after T19.4
  M19 (live transclusion): T19.1 (spec) → T19.2 → T19.3 → T19.4 → T19.5 / T19.6 → T19.7
                       T19.2–T19.4 share sync/reconcile.py → strictly sequential
                     T18.11 ── BLOCKED (real host)
  M20 (spans, marker-less files, no pause, join rule):
                     T20.1 (docs) → T20.2 (stage _cycle) → T20.3 (no tm: 1) → T20.4 (no pause)
                       → T20.5 (spans) → T20.6 (multi-line) → T20.7 (join rule) → T20.8 (e2e, docs)
                     all of T20.2–T20.7 edit sync/reconcile.py → strictly sequential
  M21 (refactor, behaviour-preserving): T21.1 … T21.7, after T20.7 where they share files
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
depends on M13 and M14 only. M18 has **no milestone gate** (its wheel fix
and env-var task are independent of everything above), but its CLI tasks
share `src/akasha/cli/main.py`, `tests/integration/test_cli_dry_run.py` and
`docs/user/cli.md` with M13/M14's, so they carry per-task dependencies on
T14.4 (the last M14 task to touch those files) and on each other, and run
sequentially; T18.1 is file-disjoint from all of them and may run in the
first parallel cohort with T18.2.

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

## M18 — Zero-flag onboarding: from install to a live, syncing vault in two commands (Depends on: nothing)

**Milestone DoD:** a user with only `uv`/`pipx` and a vault directory reaches a
live, syncing vault with **two commands and no second terminal** — (1) install,
(2) `akasha setup <vault>` — and every later verb works with `AKASHA_TOKEN`
in the environment and no per-call flags. Concretely: the installed wheel
carries its own migrations; the daemon is running detached; the vault is
registered and reconciled; the printed link opens the authenticated web UI;
a verb run while the daemon is down starts it (visibly, on the default
endpoint only); `akasha status` says in one screen why nothing is syncing;
`akasha render` shows an embed resolved to its source's current text; and the
Obsidian plugin can be installed into a vault with one command. Demonstrated
by an automated test that drives `setup` → edit → `set` → `render` against a
scratch `HOME`, never the real `tm-daemon` dir. `make check` + `make battery`
green. Per the 2026-09-23 rulings (`docs/spec-questions.md` M18-A, M18-B) the
DoD also includes: the human token is saved to a `0600` file the CLI reads
(T18.9), and every Markdown file under a sync root is tracked by default with
a `.tmignore` deny-list (T18.10a–c).

**What M18 does not do (guardrails).**
- It never mints, links, adopts or vets a node (design invariant 3). `setup`
  handles infrastructure only; a node still comes into being only when the
  human types `^tm-new` or calls a create verb.
- Per ruling M18-A the human token **is** saved (T18.9: `0600`, neutral path),
  so a local agent that shells out to `akasha` acts as the human — knowingly
  accepted. Nothing in M18 touches the API's agent-class-token → proposal
  rewrite (§4.11, T4.6), and no task lets an unauthenticated caller reach a
  `human only ∅` endpoint.
- It does not add text propagation between files: embeds stay `![[path#^tm-id]]`
  link-form on disk (`contract/render.py`), and `akasha render` (T18.7) is a
  read-only view. Mirroring text into other files would be a §4.7 grammar
  change adjacent to PRD F3 and is **not** proposed here; it needs its own
  ruling.
- No new endpoint, schema, ID format or grammar. New operational artifacts
  are limited to `tm-daemon.pid` (T18.3, neutral name, beside the existing
  lock file) and a plugin install directory inside a vault (T18.8).

### T18.1 — Make the wheel self-contained: ship the migrations
- **Goal** — Fix the defect the 2026-09-23 audit verified: `uv build --wheel` on this repo yields a wheel with **zero** `.sql` files (`unzip -l … | grep -c '\.sql'` → `0`), because `pyproject.toml`'s `packages = ["src/akasha"]` cannot see the repo-root `migrations/` and `kernel/store.py::_migrations_dir()`'s non-frozen branch resolves `parents[3]/"migrations"`, which for an installed wheel is a path outside `site-packages`. Only the PyInstaller path was ever fixed (T12.5). Until this lands, no `uv tool install`/`pipx install`/`pip install` route can work.
- **Depends on** — none.
- **Files** — `pyproject.toml`, `src/akasha/kernel/store.py`, `tests/integration/test_wheel_install.py` (new). (`kernel/store.py` is outside a packaging task's natural Files list; its inclusion follows the ratified T12.5 precedent — `docs/spec-questions.md` T12.5 and M18-E.)
- **Spec** — §3 (toolchain), §4.4 (migrations are forward-only numbered `.sql`); `docs/spec-questions.md` T12.5, **M18-E**.
- **Steps** — (1) Add a hatch `force-include` mapping the repo-root `migrations/` into the wheel as package data at `akasha/migrations`; **do not move the directory** (golden fixtures, `scripts/windows/build-exe.ps1`'s `--add-data`, and every existing test resolve the repo-root path). (2) In `_migrations_dir()`, keep the frozen branch first and unchanged; add a second branch — if `Path(__file__).resolve().parents[1] / "migrations"` exists (installed wheel), use it; otherwise fall through to the existing repo-root path, so every source-checkout caller is byte-for-byte unchanged. (3) New `tests/integration/test_wheel_install.py`: build the wheel into `tmp_path` (`uv build --wheel -o`), assert its `.sql` member set equals `migrations/*.sql` byte-for-byte, then **without network** unzip it to a temp dir and, in a subprocess with `PYTHONPATH` pointing at the unzipped tree, cwd elsewhere, and the repo root **not** importable, call `store.run_migrations` on a fresh temp DB and assert the schema exists (e.g. `nodes`, `sync_roots`, `tokens` tables). Skip cleanly (with a stated reason) only if `uv` is not on `PATH`.
- **Verify** — `uv run pytest tests/integration/test_wheel_install.py tests/unit -k "migrat or wheel"`
- **DoD** — the built wheel contains every migration, identical to the repo's; an unzipped-wheel subprocess with no repo root on the path migrates a fresh DB successfully; the frozen and source-checkout resolution paths are unchanged (existing migration tests green); `make check` + `make battery` green.

### T18.2 — Honor `AKASHA_TOKEN` and `AKASHA_BASE_URL` in the CLI
- **Goal** — Make the environment variable the quickstart already tells users to `export` actually work, so a user sets the secret once per shell instead of passing `--token "$AKASHA_TOKEN"` on every call. This is the cheapest real onboarding win: today (`cli/main.py`'s callback) `--token` has no `envvar`, so `export AKASHA_TOKEN=…` does nothing on its own.
- **Depends on** — T14.4 (shares `src/akasha/cli/main.py` and `docs/user/cli.md`; run after it, never in parallel).
- **Files** — `src/akasha/cli/main.py`, `tests/integration/test_cli.py`, `docs/user/cli.md`.
- **Spec** — §4.12 (global flags `--token`, `--base-url`); §4.11 preamble (token classes); `docs/spec-questions.md` **M18-A** (why the environment is the *only* credential source this milestone adds).
- **Steps** — (1) In the `main` callback, give `--token` `envvar="AKASHA_TOKEN"` and `--base-url` `envvar="AKASHA_BASE_URL"`. Precedence is typer's: explicit flag > environment > default. (2) An empty-string `AKASHA_TOKEN` must behave as unset (assert it — an exported-but-empty variable must not become an empty bearer). (3) `--help` may name the variables but must never print a token value (assert against `show_default`/`show_envvar` leaking a set value). (4) No other behavior change: `--json`, `--dry-run`, exit-code mapping untouched; `daemon`/`init`/`tray` still ignore both. (5) Document both variables in `docs/user/cli.md` next to the global flags.
- **Verify** — `uv run pytest tests/integration/test_cli.py tests/integration/test_cli_dry_run.py`
- **DoD** — with `AKASHA_TOKEN` set and no `--token`, a verb authenticates against a live test daemon; an explicit `--token` overrides the variable; an empty variable is treated as unset; `--help` output contains no secret; the dry-run meta-test is unchanged and green; `make check` + `make battery` green.

### T18.3 — `akasha up` / `akasha down`: a detached daemon lifecycle
- **Goal** — Remove "open a second terminal and leave it running": one verb starts the daemon detached and waits until it is healthy; one stops it. Idempotent in both directions.
- **Depends on** — T18.2 (shares `cli/main.py`, `docs/user/cli.md`).
- **Files** — `src/akasha/daemon.py`, `src/akasha/cli/main.py`, `tests/integration/test_cli_up.py` (new), `docs/user/cli.md`.
- **Spec** — §4.12 (`daemon` is the only verb that is not an HTTP client — `up`/`down` are process verbs of the same class, like `init`/`tray`); the existing single-instance lock (`daemon.py`, T4.9); `docs/spec-questions.md` **M18-C**, **M18-D**.
- **Steps** — (1) `daemon.serve()` writes its pid to `tm-daemon.pid` beside `tm-daemon.lock` **after** the lock is acquired and removes it in the same `finally` that logs shutdown; a second instance must never touch the first's pid file. (2) `up [--config PATH]`: if `GET /health` already answers at the config's `bind:port`, print the URL and exit 0 (no second spawn). Otherwise spawn `[sys.executable, "-m", "akasha.cli.main", "daemon", "--config", PATH]` — or `[sys.executable, "daemon", …]` when `sys.frozen` — detached (`start_new_session=True` on POSIX; `DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP` on Windows) with stdio to the null device (the daemon already writes its own rotating log in the config dir; print that path), then poll `/health` with a bounded timeout. Timeout → exit 1 naming the log path. Lock held but `/health` silent → exit 4 (spec §4.12 conflict class, same mapping `daemon` uses). (3) `down [--config PATH]`: read `tm-daemon.pid`; a missing file or a pid that is no longer a live process is a clean "not running" exit 0 and removes a stale file; otherwise terminate (POSIX `SIGTERM`; Windows terminate) and wait for the lock to release. State plainly in the docstring that an abrupt stop is safe by design — startup reconcile is idempotent (§4.8) and the project already survives `kill -9`. (4) Tests spawn a **real** detached daemon against a `tmp_path` config on a free port (never the default `tm-daemon` dir — assert `HOME`/`APPDATA` redirection): `up` → `/health` answers; second `up` → same pid, exit 0; `down` → process gone, lock released, pid file removed; stale pid file handled; a second `daemon` after `up` still exits 4.
- **Verify** — `uv run pytest tests/integration/test_cli_up.py tests/integration/test_daemon_lock.py tests/integration/test_daemon_lock_multiprocess.py`
- **DoD** — `up` leaves a healthy detached daemon and returns; repeat `up` is a no-op; `down` stops it and leaves no stale pid or held lock; the existing lock tests are unregressed; nothing touches the real config dir; `make check` + `make battery` green.

### T18.4 — Start the daemon on demand for default-endpoint verbs
- **Goal** — Give the user codegraph's "nothing to babysit" property: if an HTTP-client verb finds the daemon down, it starts it (once, visibly) and retries, so the daemon never has to be a thing the user remembers.
- **Depends on** — T18.2, T18.3 (shares `cli/main.py`).
- **Files** — `src/akasha/cli/main.py`, `tests/integration/test_cli_autostart.py` (new), `docs/user/cli.md`.
- **Spec** — §4.8 (startup reconcile is idempotent, so edits made while the daemon was down are reconciled on start — this is what makes lazy start *correct*, not just convenient); `docs/dogfood-plan.md` §B (the daemon must never do something the user cannot see); `docs/spec-questions.md` **M18-C**.
- **Steps** — (1) In the HTTP request helper, on a connection-refused error, and **only when neither `--base-url` nor `AKASHA_BASE_URL` was supplied** (the default local endpoint) and `AKASHA_NO_AUTOSTART` is unset, run T18.3's `up` logic once and retry the request once. Never for an explicit base URL (test daemons, remote), never for `--dry-run` (it issues no HTTP). (2) Always print one line to stderr — `started daemon (log: <path>)` — never silent. (3) When a verb fails 401 and **no token was supplied by any route** (no flag, no env), append a one-line hint pointing at `akasha setup` and `export AKASHA_TOKEN=…`; when a token *was* supplied, print nothing extra. (4) Tests redirect `HOME`/`APPDATA` to `tmp_path` (assert the real `tm-daemon` dir is untouched): a verb against a down default endpoint starts a daemon, prints the notice, and succeeds; an explicit `--base-url` to a dead port fails without spawning anything (assert no process was started); `AKASHA_NO_AUTOSTART=1` disables it; `--dry-run` never spawns.
- **Verify** — `uv run pytest tests/integration/test_cli_autostart.py tests/integration/test_cli_up.py tests/integration/test_cli.py`
- **DoD** — a default-endpoint verb against a stopped daemon starts it exactly once with a visible notice and completes; every explicit-endpoint and `--dry-run` path spawns nothing (asserted, not assumed); the hint appears only when no credential was supplied; `make check` + `make battery` green.

### T18.5 — `akasha setup [VAULT]`: nothing to a live vault in one command
- **Goal** — Collapse the first-run sequence (init → start daemon → register vault → rescan → open UI) into one verb, **without touching `akasha init`'s documented contract** (exit 4 when a token exists; covered by `tests/integration/test_cli_init.py`).
- **Depends on** — T18.4 (shares `cli/main.py`).
- **Files** — `src/akasha/cli/main.py`, `tests/integration/test_cli_setup.py` (new), `tests/integration/test_cli_dry_run.py` (only if `_discovered_mutating_verbs()` picks the verb up — see Steps), `docs/user/cli.md`.
- **Spec** — §4.11 (`POST /sync/roots`, `POST /sync/rescan`, both human-only), §4.12; T11.1/T12.1 (`init`'s bootstrap transport ruling — `setup` reuses the same `auth`/`store` primitives, no second mint path); T12.2 (`sync add`); T12.3 (web-UI `?token=` bootstrap link); `docs/spec-questions.md` **M18-A**, **M18-D**.
- **Steps** — (1) Extract `init`'s mint sequence into one private helper that both `init` and `setup` call; `init`'s output, exit codes and tests stay byte-identical. (2) `setup [VAULT] [--config PATH] [--name NAME]`: fresh DB → run migrations and mint one human token (as `init`); a token already exists → skip minting; if no credential is then available via `--token`/`AKASHA_TOKEN`, exit 4 with the exact instruction to supply it (a token is unrecoverable, so a re-run cannot fetch it). (3) `up` (T18.3). (4) When `VAULT` is given: `POST /v1/sync/roots` (name defaults to the basename, as `sync add`) then `POST /v1/sync/rescan`; re-running with the same vault is a no-op (the registration is an upsert). Print `sync.watcher.detect_cloud_path`'s OneDrive/Dropbox warning if it applies (the daemon already logs it; surface it here too). (5) Print, once: the web-UI link with the bootstrap token (`http://HOST:PORT/?token=…`), the `export AKASHA_TOKEN=…` line, and a plain warning that the link and token are secrets (browser history, shared terminals). (6) After registering, tell the user that every Markdown file under the vault is tracked by default and that a `.tmignore` file at the vault root opts paths out (T18.10a–c; until those land the older §4.7 rule still applies and files need `tm: 1` front matter) — a hint, never an auto-edit. (7) `--dry-run` must print the plan and mint nothing, spawn nothing and register nothing (test all three). If the meta-test's AST discovery flags the verb because it calls `_mutate`, add its `DryRunCase` row rather than exempting it.
- **Verify** — `uv run pytest tests/integration/test_cli_setup.py tests/integration/test_cli_init.py tests/integration/test_cli_sync_add.py tests/integration/test_cli_dry_run.py`
- **DoD** — against a scratch `HOME`, one `setup <vault>` yields a healthy detached daemon, exactly one human token, a registered and reconciled root, and the printed link authenticates the web UI; a second run is idempotent; `init`'s tests are unchanged and green; `--dry-run` has zero side effects (asserted); `make check` + `make battery` green.

### T18.6 — `akasha status`: one-screen diagnosis of "why isn't it syncing"
- **Goal** — Replace guesswork with one read-only command that reports the state a first-time user needs and names the three classic first-run failures.
- **Depends on** — T18.5 (shares `cli/main.py`).
- **Files** — `src/akasha/cli/main.py`, `tests/integration/test_cli_status.py` (new), `docs/user/cli.md`.
- **Spec** — §4.11 (`GET /health`, `GET /sync/status`, `GET /review`); precedent T14.1 (read-only verbs use `_request`, never `_mutate`, and are correctly outside the dry-run meta-test).
- **Steps** — (1) Report: daemon reachable (version, contract version); whether the token was accepted; each sync root (path, files tracked); open violations and pauses grouped by code; open review count. (2) Emit a hint line for each of: no credential supplied; no sync roots registered; a root registered but zero files tracked (→ check the path and any `.tmignore`; before T18.10c lands, files need `tm: 1` front matter). (3) **Do not change `sync/`**: whether `W_UNMANAGED_ANCHOR` (the advisory lint for a `^tm-` anchor in an unmanaged file, `contract/linter.py`) actually reaches `GET /sync/status` is unverified — check it empirically against a scratch daemon; if it does not, record that in `docs/spec-questions.md` as a finding and have `status` say nothing about it, rather than widening scope. (4) Exit 0 when healthy; the shared mapping for unreachable/unauthorized. `--json` emits the versioned `cli/v1` envelope. ASCII-only default output (T9.9/T14.1 precedent).
- **Verify** — `uv run pytest tests/integration/test_cli_status.py tests/integration/test_cli_graph.py`
- **DoD** — each of the three first-run failures produces its named hint against a real scratch daemon (asserted by real state, not string-only); a healthy vault produces none; the verb issues only GETs; `make check` + `make battery` green.

### T18.7 — `akasha render FILE`: see a transclusion resolved, headlessly
- **Goal** — Give the user (and T15.1's embed leg) a way to *see* an embed resolve without Obsidian: print a managed file with each `![[path#^tm-id]]` replaced by the target node's **current** body. This is the only headless file-to-file view the system can offer, because on disk an embed is a link and stays one (`contract/render.py`: "managed-file bytes stay the wiki-link form").
- **Depends on** — T18.6 (shares `cli/main.py`).
- **Files** — `src/akasha/cli/main.py`, `tests/integration/test_cli_render.py` (new), `docs/user/cli.md`.
- **Spec** — §4.7 (`embed`: "render the target's current body (read-only …)"); §4.11 `GET /nodes/{id}`; `docs/spec-questions.md` **M18-D**.
- **Steps** — (1) Read `FILE` locally, parse it with the shipped `contract` parser, and for each embed line `GET /v1/nodes/{embed.id}`. (2) Print the file with each embed line replaced by the target's body, quoted and labelled with its id (`> … (^tm-<id>, from <path>)`); a tombstoned or missing target prints a visible `[unresolved: ^tm-<id> (<status>)]` marker rather than disappearing. (3) **Never write any file** — read-only by construction; assert `sha256` of `FILE` and of the source file are unchanged across every call. (4) `--json` lists each embed with its resolved body/state. (5) The command is a viewer, not propagation: it must say so in `--help` ("does not modify files").
- **Verify** — `uv run pytest tests/integration/test_cli_render.py`
- **DoD** — against a live scratch daemon with `A.md` (anchored) and `B.md` (embeds it): `render B.md` shows A's text; after `akasha set <id> --body …` it shows the new text; `B.md`'s bytes are identical throughout (asserted); a deleted target prints the unresolved marker; `make check` + `make battery` green.

### T18.8 — `akasha plugin install VAULT`: one-step Obsidian plugin install
- **Goal** — Remove the manual npm-build-and-copy-three-files step from onboarding, without pretending Obsidian's own consent step can be automated.
- **Depends on** — T18.7 (shares `cli/main.py`).
- **Files** — `src/akasha/cli/main.py`, `tests/integration/test_cli_plugin_install.py` (new), `docs/user/cli.md`.
- **Spec** — `docs/user/obsidian.md` (manual install and the Restricted-mode requirement), `plugin-obsidian/manifest.json`; D4/D7 (CORS and `.obsidian/` watcher handling, already fixed); `docs/spec-questions.md` **M18-A**, **M18-D**.
- **Steps** — (1) `plugin install VAULT --from DIR` copies `manifest.json` and the **built** `main.js` from `DIR` (a built `plugin-obsidian/`; `main.js` is gitignored and produced by `npm run build`, so this task does not bundle it — bundling built assets into the wheel is recorded as a follow-up in M18-D, not done here) into `VAULT/.obsidian/plugins/tm-hub/`, creating directories as needed. A missing `main.js` is a clear one-line "run `npm ci && npm run build` in plugin-obsidian/ first" error, exit 3. (2) Write `data.json` with **`daemonUrl` only** — the token is never written by this task (M18-A); an existing `data.json` is merged, never clobbered, and any existing `token` field is preserved untouched. (3) Add `tm-hub` to `VAULT/.obsidian/community-plugins.json` (create if absent; preserve existing entries and order; no duplicates). (4) Print exactly what remains for the human: turn off Restricted mode once in Obsidian, enable `tm-hub`, paste the token in the plugin's settings. (5) Honor the global `--dry-run`: print the file operations, write nothing (assert). (6) Idempotent: a second run changes nothing.
- **Verify** — `uv run pytest tests/integration/test_cli_plugin_install.py`
- **DoD** — against a `tmp_path` vault, the three files land where Obsidian expects them; `data.json` never contains a token this task did not find already; re-running is a byte-identical no-op; `--dry-run` writes nothing (asserted); an unbuilt plugin dir fails clearly; `make check` + `make battery` green.

### T18.9 — Save the human token: a `0600` token file the CLI reads (ruling M18-A)
- **Goal** — Authenticate once. `init`/`setup` save the human token they mint; every verb then finds it without `--token`/`AKASHA_TOKEN`; `plugin install --with-token` can pre-fill the plugin. Per ruling M18-A an agent that shells out to `akasha` acts as the human — accepted, not a bug.
- **Depends on** — T18.5, T18.8 (shares `cli/main.py`).
- **Files** — `src/akasha/config.py`, `src/akasha/cli/main.py`, `tests/integration/test_cli_token_file.py` (new), `tests/integration/test_cli_init.py`, `docs/user/cli.md`.
- **Spec** — §4.11 (token classes; `human only ∅`), §4.12; T11.1/T12.1 (`init`'s bootstrap ruling); rule 6 (neutral names); `docs/spec-questions.md` **M18-A** (binding: what is and is not approved).
- **Steps** — (1) `config.py`: `default_token_path(config_dir)` → `<config dir>/tm-token` (neutral name, rule 6) plus read/write helpers. Write with the mode set **at creation** (`os.open(..., 0o600)`, then atomic replace) so the secret is never briefly world-readable; on Windows rely on the per-user `%APPDATA%` ACL and say so in the docstring (no ACL code). (2) `init` and `setup` save the token through the shared mint helper; `init`'s stdout, exit codes and the once-only printing are unchanged (extend `test_cli_init.py`, do not weaken it). An existing token file is never overwritten by `setup`. (3) Credential resolution order: `--token` > `AKASHA_TOKEN` > token file, read from the default per-OS config dir. (4) Default base URL is derived from the config's `bind:port` when neither `--base-url` nor `AKASHA_BASE_URL` was given, so a token file for a non-default port is actually used; absent a config it equals today's `DEFAULT_BASE_URL`. (5) The file only ever holds the human token `init`/`setup` minted — agent-class tokens are never written there and `token create`'s output is unchanged. (6) `plugin install --with-token` writes the token into the plugin's `data.json`; opt-in only, and warn on stderr when `sync.watcher.detect_cloud_path(vault)` matches or `VAULT/.git` exists, because the token would then sit in a synced or committed location. (7) Update T18.4's 401 hint: when the credential came from the file, say it was rejected and may have been revoked. (8) Tests use a `tmp_path` `HOME`/`APPDATA` (assert the real config dir is untouched): file created with mode `0600` (POSIX), correct precedence, revoked-token hint, `--dry-run` writes nothing.
- **Verify** — `uv run pytest tests/integration/test_cli_token_file.py tests/integration/test_cli_init.py tests/integration/test_cli_setup.py tests/integration/test_cli_dry_run.py`
- **DoD** — after `setup`, a verb with no flag and no env var authenticates from the saved file; precedence is flag > env > file; the file is `0600` at creation and holds only the human token; `init`'s tests unchanged and green; the plugin token is written only with `--with-token`, with the cloud/git warning; `make check` + `make battery` green.

### T18.10a — `.tmignore` matcher (pure, no I/O) (ruling M18-B)
- **Goal** — The deny-list logic, isolated and testable before anything depends on it.
- **Depends on** — none (file-disjoint from everything else in M18).
- **Files** — `src/akasha/sync/ignore.py` (new), `tests/unit/sync/test_ignore.py` (new).
- **Spec** — `docs/spec-questions.md` **M18-B** (binding: name `.tmignore`, gitignore-style, built-in defaults, `.gitignore` not consulted); rule 6.
- **Steps** — (1) `is_ignored(rel_path, patterns) -> bool` plus `parse_patterns(text) -> list[str]`, stdlib only (no new dependency). Supported subset, stated in the docstring: blank lines and `#` comments; `*`, `?`, `**`; a trailing `/` = directory-only; a leading `/` = anchored to the root; `!` negation; last matching pattern wins. An unsupported construct is skipped with a returned warning, never guessed at. (2) Built-in default denies (applied before user patterns, overridable by `!`): `.obsidian/`, `.git/`, `.trash/`, `node_modules/`, and every non-`.md` file. (3) Paths are POSIX root-relative; the function must give the same answer for a Windows-separator input. (4) Tests cover each supported construct, precedence/negation, the defaults, and a hypothesis property that `is_ignored` never raises on arbitrary pattern text.
- **Verify** — `uv run pytest tests/unit/sync/test_ignore.py`
- **DoD** — the supported subset behaves as documented; defaults deny exactly the listed paths; arbitrary pattern text never raises; ruff and pyright strict clean; `make check` green.

### T18.10b — Apply the deny-list in the watcher and discovery (ruling M18-B)
- **Goal** — Ignored paths never enter the debouncer or `discover_untracked_files`, and editing `.tmignore` takes effect without a restart.
- **Depends on** — T18.10a, and T19.4 (the last M19 task to touch `sync/reconcile.py`; run after M19).
- **Files** — `src/akasha/sync/watcher.py`, `src/akasha/sync/reconcile.py`, `tests/unit/sync/test_watcher.py`, `tests/integration/test_watcher_wiring.py`.
- **Spec** — §4.8 (startup discovery), the existing `_is_managed_candidate` `.md` filter and debug-plan D7/D10 (the watcher's prior ignore fixes); `docs/spec-questions.md` **M18-B**.
- **Steps** — (1) Load each root's `.tmignore` (absent = defaults only) and filter raw events through `is_ignored` **before** they reach the debouncer, alongside the existing `.md`/temp-file/non-content-event filters. (2) `discover_untracked_files` skips ignored paths. (3) An event for the root's own `.tmignore` reloads the patterns and triggers one rescan of that root. (4) A file that becomes ignored after it was tracked stops being reconciled; its `sync_files` row and base snapshot are left in place (narrowest reading — no deletion of history), and `akasha status` may show it. (5) Tests: a real watcher thread with a `tmp_path` root — an ignored file's edit produces no cycle; a normal file's still does within the debounce window; editing `.tmignore` un-ignores and picks up the file.
- **Verify** — `uv run pytest tests/unit/sync/test_watcher.py tests/integration/test_watcher_wiring.py tests/unit/sync/test_reconcile.py`
- **DoD** — ignored paths are inert (asserted with a live watcher, not just the pure function); `.tmignore` edits apply live; existing watcher tests unregressed; `make check` + `make battery` green.

### T18.10c — Track every non-ignored Markdown file by default (ruling M18-B)
- **Goal** — Remove the "hand-add `tm: 1` to every file" step. A non-ignored `.md` file under a sync root is parsed as managed even without front matter; nothing is written to a file until it actually has something to project.
- **Depends on** — T18.10b (and therefore T19.4).
- **Files** — `docs/mvp-spec.md` (amend §4.7's sentence only), `src/akasha/sync/reconcile.py`, `tests/unit/sync/test_reconcile.py`, `tests/battery/test_edit_battery.py` (**only** to add a case; existing cases are not edited).
- **Spec** — §4.7 (file-level rule; lossless container), §4.8, spec rule 0.3; `docs/spec-questions.md` **M18-B** (binding). **Protected tests that must stay green and unmodified** — they pin the *parser/linter* rule this task deliberately leaves alone: `tests/unit/contract/test_parser.py` (`test_unmanaged_file_*`), `tests/unit/contract/test_linter.py` and `tests/golden/test_serialization.py` (`W_UNMANAGED_ANCHOR`), `tests/unit/contract/test_render.py` (`test_front_matter_absent_when_unmanaged`).
- **Steps** — (1) Do **not** change `contract/parser.py`, `linter.py` or `render.py`. In `Reconciler.on_change`, when `parse(V).managed` is false and the path is not ignored (T18.10a/b), parse an **in-memory adopted copy** of `V` instead: if `V` already opens with a YAML front-matter block that lacks `tm:`, **inject** `tm: 1` as a key inside it; only when `V` has no front matter at all, prepend a `tm: 1` front-matter block. Never emit a second front-matter block — Obsidian keeps properties such as `title:`/`tags:` in that YAML and a duplicate would corrupt them. (2) **Write suppression:** if the adopted file has no contract constructs at all (no anchored block, `^tm-new`, embed or ref), do nothing — no write, no base-snapshot rewrite, byte-identical on disk. (3) The first cycle that has something to project writes the real front matter (§4.7: added on first projection), then behaves exactly as a managed file. (4) An ignored file keeps today's behavior, including advisory `W_UNMANAGED_ANCHOR`. (5) Amend §4.7's "files without it are never parsed for management" to state the new rule and cite M18-B. (6) Tests: an all-prose file with foreign `^abc123` block ids is byte-identical after reconcile; a file with `- [ ] x ^tm-new` and no front matter is minted and gains `tm: 1`; a file whose existing front matter has `tags:`/`title:` keeps them byte-for-byte and gains only a `tm: 1` key (exactly one front-matter block, asserted); an ignored file with the same line mints nothing; the whole existing battery and the protected tests pass unchanged; add one battery case for adoption, never edit an existing one.
- **Verify** — `uv run pytest tests/unit/sync/test_reconcile.py tests/unit/contract tests/golden tests/battery`
- **DoD** — a plain prose file is never modified; a file with a minting request is adopted and gains `tm: 1` on first projection; ignored files are unchanged; every protected test passes **unmodified** (state the git diff of those files is empty); the battery reports 0 silent guesses; `make check` + `make battery` green.

### T18.11 — Login-time service install for Linux/macOS (**BLOCKED: needs a real host**)
- **Goal** — Extend what T12.5 did for Windows (Startup shortcut + supervisor) to a `systemd --user` unit and a `launchd` agent, so the daemon is up before the first verb.
- **Why blocked** — same reasoning as T12.4/T12.5: the unit-file/plist generation is unit-testable, but "it actually survives a reboot and a `kill -9`" has no honest CI equivalent (`docs/acceptance.md` row 9). T18.4's on-demand start already makes a stopped daemon safe, so this is polish, not a prerequisite. Becomes `TODO` only when a real Linux/macOS host is available to attest it.
- **Depends on** — T18.3.
- **Files / Verify / DoD** — to be defined at that time.

### T18.12 — Rewrite the quickstart around the two-command flow
- **Goal** — Make `docs/user/quickstart.md` lead with what M18 built, and demote the manual sequence.
- **Depends on** — T17.1 (**same file**: it rewrites the quickstart installer-first and must land first so this task edits its result, not a soon-to-be-stale version), T18.1–T18.9, T18.10a–c.
- **Files** — `docs/user/quickstart.md`.
- **Spec** — the landed behavior of T18.1–T18.8 only; `docs/user/cli.md`.
- **Steps** — (1) Lead with install + `akasha setup <vault>`; then `akasha status`, `akasha render`, `akasha plugin install`. (2) State honestly what is still manual: turning off Obsidian's Restricted mode and enabling the plugin (and pasting the token unless `--with-token` was used). State the two behaviors the rulings created: the human token is saved to a `0600` file so anything that runs `akasha` as you acts as you (M18-A), and every Markdown file under the vault is tracked unless `.tmignore` excludes it (M18-B). (3) Keep the from-source sequence as a developer appendix pointing at `docs/dev/setup.md`. (4) Describe nothing that does not exist; a missing capability is a `# SPEC-QUESTION`, not prose.
- **Verify** — Doc-only, with objective checks: every command shown runs successfully in order against a scratch `HOME` (a command that errors is a doc bug); every verb named appears in `uv run akasha --help`; `grep -n "uv run python -c" docs/user/quickstart.md` returns nothing.
- **DoD** — a new user can go from nothing to a syncing vault using only the quickstart; every command executes as written; the still-manual steps are stated, not hidden.

---

## M19 — Live transclusion: the same anchor in several files is one node, kept identical everywhere (Depends on: nothing)

**Origin.** User ruling, 2026-09-23 (`docs/spec-questions.md` M19-0): editing a transcluded block in one Markdown file must change the same text in the other file(s) as soon as possible. Syntax ruling (asked and answered the same day): **no new syntax — the same `^tm-id` anchor appearing in more than one file *is* the declaration** (a *mirror*). To make an independent copy instead, replace the pasted anchor with `^tm-new`.

**Why this is smaller than it sounds.** `hub_state_for` already projects every file from the hub's *current* node, so two files sharing an anchor already render from one source. Three things stop mirrors from working today, all in `src/akasha/sync/reconcile.py`: (1) `ProjectionIndex` allows **one** owning file per node ("last writer wins"); (2) `_compute_ops` flags a second file's copy of an anchor as cross-file `E_DUP_ID` and never applies it, and deleting one copy can hard-delete a node another file still shows; (3) nothing re-projects the *other* files after a file-side commit — `on_change(A)` writes only A. **No schema or new table is needed**: `ProjectionIndex.build` already derives membership from every file's base snapshot, so a node in two files is already recorded durably; only the in-memory index changes from "one owner" to "a set of owners".

**Milestone DoD:** with `A.md` and `B.md` both containing `^tm-<id>` lines: an edit or checkbox toggle in **either** file rewrites the other within one sync cycle (debounce + cycle, no rescan, no restart); an `akasha set`/UI edit rewrites **both**; deleting one mirror never deletes the node; deleting the last follows the existing delete rules; an edit made in both files at once loses nothing (conflict branch + one review, no write ping-pong); a real watcher thread proves it end to end; the E05 case is re-ruled explicitly and E04/E04b/single-file `E_DUP_ID` pass unmodified; the battery reports 0 silent guesses; `make check` + `make battery` green.

**Scope limits (stated, not hidden).**
- **One-line blocks only.** The contract grammar is line-oriented — a paragraph or task is exactly one line, and a hub body containing a newline is unprojectable (`E_UNPROJECTABLE_BODY`). A multi-line "section" cannot be mirrored; that would be a grammar extension (block ranges) and is not proposed here (M19-A).
- Indentation is per file; a mirror keeps its own nesting. A `reparented` op in any file may add a further `composes` parent (M19-B).
- Any local editor that has the target file open and dirty can race the daemon's rewrite; Obsidian reloads a clean file that changed on disk. This is the pre-existing write-back behavior, not new to M19.

### T19.1 — Amend the spec and the PRD: same anchor across files is a mirror (doc-only)
- **Goal** — Make the normative documents say what the user ruled, before any code changes, so no later task is implementing against a contradicted spec. F3 (PRD §5) is normative: "reintroducing any item requires overturning its stated reason", so its row is edited here, not just logged.
- **Depends on** — none.
- **Files** — `docs/mvp-spec.md`, `docs/vision.md`; `docs/user/dogfood-windows.md` and `docs/acceptance.md` **only if** they state that a cross-file duplicate anchor is a violation. **Files list completed at landing** (ratified T8.0/T8.1 rule; logged under `docs/spec-questions.md` M19-0): `plugin-obsidian/TESTPLAN.md` (§4b and the pass criteria told a tester to expect an `E_DUP_ID` review on a cross-file copy) and `plugin-obsidian/src/clipboard.ts` (**comments only**), strictly entailed by this task's Goal of leaving no document contradicting the ruling.
- **Spec** — §4.7, §4.8; PRD §5 F2/F3, §6 ("edits within a facet propagate automatically as rendering"); `docs/spec-questions.md` **M19-0**, **M19-A**, **M19-B**, **M19-C** (binding wording).
- **Steps** — (1) §4.7: narrow `E_DUP_ID` from "same anchor twice in a sync root (copy without cut)" to "twice **in one file**", keeping its certain-repair unchanged. Add a *Mirrors* paragraph: an anchor live in two or more files is one node projected into each; indentation is per file; edits commit at `SYNC_CHANGE_CLASS`; blocks are one line; joining with text that differs from the hub keeps the hub's text and preserves the vault's version as a conflict branch plus one review (M19-C); removing one mirror never deletes the node; to detach, replace the anchor with `^tm-new`. (2) §4.8: state that ownership is a *set* and that after a file's cycle commits, every other file holding an affected anchor is reconciled through the same three-way pipeline (never a blind write). (3) `vision.md` F3 row: append a scoped exception — a block the human co-anchors in several files is **one atom shown more than once** (identity, not substitution); its propagation is patch-class rendering as §6 already allows; interface breaks (facet breaks, retraction) still flag dependents exactly as before; nothing here lets text propagate into a *different* atom. (4) Do not weaken any other F-row.
- **Verify** — Doc-only, objective: `grep -n "twice in a sync root" docs/mvp-spec.md` returns nothing; `grep -n -i "mirror" docs/mvp-spec.md docs/vision.md` shows the §4.7/§4.8 paragraphs and the F3 exception; `git diff --stat` touches only the listed files.
- **DoD** — the spec, the PRD and the four spec-questions entries agree; F3's exception is scoped as above and no other §5 row changed.

### T19.2 — `ProjectionIndex`: a node may have several owning files
- **Goal** — Represent "this node lives in these files" without changing any existing caller's behavior.
- **Depends on** — T19.1.
- **Files** — `src/akasha/sync/reconcile.py`, `tests/unit/sync/test_reconcile.py` (**add** tests; the existing `ProjectionIndex` test around `index.owner("x1") == "a.md"` stays unchanged and must pass).
- **Spec** — §4.8; T13.2's `project_node_change` docstring; `docs/spec-questions.md` **M19-0**.
- **Steps** — (1) Keep `_owner` (last-writer) so `owner()` is byte-for-byte unchanged. Add `_owners: dict[str, set[str]]`, maintained by `update()` (a path that no longer contains an id is removed from that id's set; an empty set is deleted) and read by new `owners(node_id) -> frozenset[str]`. (2) `build()` is unchanged in *source* (base snapshots) — assert in a test that building from two base snapshots containing the same id yields both owners; **no migration, no new table**. (3) Audit and list every `owner()` caller in the task's landing note: `_compute_ops` (created and deleted branches) and `project_node_change`; `store.py`'s comment mention is prose only.
- **Verify** — `uv run pytest tests/unit/sync/test_reconcile.py`
- **DoD** — `owners()` correct across updates/removals/rebuilds; `owner()` unchanged; ruff and pyright strict clean; `make check` green.

### T19.3 — Mirror-aware ops, and the explicit re-ruling of E05
- **Goal** — A second file's copy of an anchor becomes a mirror instead of a violation, and removing one mirror stops threatening the node. Pure logic (zero I/O) — propagation is T19.4.
- **Depends on** — T19.2.
- **Files** — `src/akasha/sync/reconcile.py`, `tests/unit/sync/test_reconcile.py`, `tests/battery/test_edit_battery.py` (**the E05 case and its comments only**), `tests/golden/reconcile/e05-cross-file-dup/expected_ops.json`.
- **Spec** — §4.7 (as amended by T19.1), §4.8; spec rule 0.3; `docs/spec-questions.md` **M19-0**, **M19-C**.
- **Authorized changes to protected tests (spec rule 0.3, by the user's ruling of 2026-09-23 — this task is the explicit authorization)** — (a) `tests/unit/sync/test_reconcile.py::test_cross_file_dup_withholds_and_reviews` is replaced by `test_cross_file_dup_joins_as_mirror` (one `created` adopt op for the id, no `E_DUP_ID`, no extra review item); (b) the E05 battery case (`_case_e05`, `test_e05_cross_file_dup_is_review_only_no_silent_apply` and their comments) now expects **no** `E_DUP_ID`, exactly one adopt op, node count still 1 and node history unchanged; (c) `tests/golden/reconcile/e05-cross-file-dup/expected_ops.json` changes from `[]` to `[{"kind": "created", "node_id": "3iwckm6b"}]`. **Everything else must pass unmodified**, in particular E04/E04b, `tests/golden/test_serialization.py` (the single-file `E_DUP_ID` copy-paste repair), and `tests/unit/contract/**`.
- **Steps** — (1) `_compute_ops` created branch: when `projection.owners(id) - {current_path}` is non-empty, emit the existing adopt `Op(kind="created", node_id=...)` with a new optional `mirror: bool = False` field set `True` — never `E_DUP_ID`. A one-owner-or-none id behaves exactly as today. (2) Deleted branch: if any other owner exists, skip silently (this file's copy is being removed; the node lives on); only removal from the last file reaches the existing hard-delete / `E_DELETED_S1` handling, and the `anchor_elsewhere` scan is kept as is. (3) `diff_blocks`: drop `E_DELETED_S1` review items for ids that still have another owner, **before** `pause_and_diff` counts violations. (4) Same anchor twice in **one** file stays `E_DUP_ID` (linter, untouched). (5) Unit tests: join emits one adopt op; removal with another owner is silent; removal of the last S0 owner still emits `deleted`; removal of the last S1+ owner is still withheld with `E_DELETED_S1`; the `E_DELETED_S1` filter applies only when another owner exists.
- **Verify** — `uv run pytest tests/unit/sync/test_reconcile.py tests/unit/contract tests/golden tests/battery`
- **DoD** — the listed protected changes and no others (`git diff --stat` on `tests/golden` and `tests/battery` shows exactly the files named above); E04, E04b and every other battery case pass unmodified; single-file `E_DUP_ID` unchanged; `make check` + `make battery` green.

### T19.4 — Propagate a committed edit to every other mirror
- **Goal** — The feature: after a file's cycle commits a node's text or checkbox, every *other* file holding that anchor is brought up to date immediately, through the same three-way pipeline.
- **Depends on** — T19.3.
- **Files** — `src/akasha/sync/reconcile.py`, `tests/unit/sync/test_reconcile.py`, `tests/integration/test_projection_writeback.py`.
- **Spec** — §4.8 (as amended); T13.2/T13.3 (`project_node_change`, echo suppression via the shared `OriginTracker`); `docs/spec-questions.md` **M19-B**, **M19-C**.
- **Steps** — (1) Split `Reconciler.on_change` into a private `_cycle(path) -> set[str]` (the existing body, returning the node ids whose text/state this cycle **committed** — `modified`, `checkbox_toggled`, and an adopt that committed; not conflicted ops) and a public `on_change` that runs `_cycle(path)` and then, for each returned id and each `p` in `projection.owners(id) - {path}` (each `p` once), runs `_cycle(p)`. A propagated `_cycle` **never propagates further** (no recursion, no ping-pong); a `FileNotFoundError` for one mirror is skipped, never aborting the others. Never write the hub render straight into a mirror — running the full three-way cycle is what preserves that file's other, unsaved-then-saved edits. (2) Mirror join (`op.mirror`): if the vault's text/state differs from the hub head, **the hub wins** — do not commit the vault text; hand the op to `conflict_handler` so the vault version is preserved as a conflict branch with one review (M19-C); identical text is a quiet no-op. (3) Concurrent edits to the same line in A and B: the first cycle commits, the second sees `hub_changed_since` and takes the existing conflict path — assert exactly one conflict review, both versions retrievable, and a bounded number of file writes. (4) `project_node_change`: resolve **all** owners via `owners()` (keep its signature and its once-per-path dedup), so an API/CLI/UI hub-side edit rewrites every mirror. (5) Every write goes through `write_if_diff`, so each is recorded in the origin tracker and echo-suppressed. (6) Tests: A edit → B rewritten within the same `on_change` call; checkbox toggle propagates; A's edit while B has an unrelated edit on another line → B keeps it and gains the mirror text; concurrent same-line edit → one conflict, no ping-pong; hub `PATCH` rewrites both files; removing one mirror leaves the node live and the other file byte-identical; join with differing text → hub wins + conflict review.
- **Verify** — `uv run pytest tests/unit/sync/test_reconcile.py tests/integration/test_projection_writeback.py tests/battery/test_edit_battery.py`
- **DoD** — every leg in Step 6 asserted against real files and a real store; no recursion (asserted by counting cycles); a projection failure in one mirror never fails the source file's cycle; battery unregressed; `make check` + `make battery` green.

### T19.5 — Battery: mirror cases
- **Goal** — Put the new behavior under the same "0 silent guesses" discipline as E01–E20.
- **Depends on** — T19.4.
- **Files** — `tests/battery/test_edit_battery.py` (**append** cases only), new golden fixture directories `tests/golden/reconcile/e21-mirror-edit/`, `e22-mirror-concurrent/`, `e23-mirror-remove-one/`, `e24-mirror-reparent/`.
- **Spec** — `docs/mvp-spec.md` §6.2 (edit battery), the existing E-case helpers (`_conn`, `_seed_hub_from_json`, `_register_root`); rule 0.3 (these are **new** fixtures; no existing fixture is edited).
- **Steps** — E21 edit in A reaches B; E22 concurrent same-line edit → one conflict review, both versions kept; E23 remove one mirror → node live and the other file unchanged, then remove the last (S0) → deleted; E24 reparent in B adds a second `composes` parent and leaves A's edge intact (M19-B). Each returns `silently_mutated` like its neighbours and is added to the battery's aggregate silent-guess count.
- **Verify** — `uv run pytest tests/battery`
- **DoD** — E21–E24 green; existing E01–E20 untouched and green; aggregate silent-guess count 0; `make battery` green.

### T19.6 — Live proof: a real watcher, file to file
- **Goal** — Prove the user-visible claim with the real production path, not just `on_change` called directly (debug-plan D10 is the precedent for what direct-call tests miss).
- **Depends on** — T19.4.
- **Files** — `tests/integration/test_mirror_live.py` (new).
- **Spec** — `tests/integration/test_watcher_wiring.py::test_live_edit_is_reconciled_with_no_manual_rescan` (the pattern to model); §4.8; PRD §8 story 8.
- **Steps** — (1) Real `Watcher` thread + shared `OriginTracker` + `Reconciler` on a `tmp_path` root with `A.md` and `B.md` sharing one anchor. (2) Write a new body into `A.md` on disk → assert `B.md`'s bytes change within 3 s with no rescan call, and record the measured latency. (3) Toggle the checkbox in `B.md` → `A.md` follows. (4) Count cycles: the daemon's own write to the mirror must **not** trigger another reconcile (echo-suppressed), and after settling, neither file changes again. (5) A hub-side `PATCH /v1/nodes/{id}` through the real app rewrites both files. (6) `.md` only and non-content events still ignored (no regression of D10).
- **Verify** — `uv run pytest tests/integration/test_mirror_live.py tests/integration/test_watcher_wiring.py`
- **DoD** — A→B, B→A, checkbox and hub-side legs pass against real files under a real observer thread; no echo loop (asserted); measured latency recorded in the test's docstring; `make check` + `make battery` green.

### T19.7 — User guide: transclusion
- **Goal** — Teach the feature as a user performs it, including its limits.
- **Depends on** — T19.6, T17.3 (both edit `docs/user/README.md`; T17.2/T17.3 own it first).
- **Files** — `docs/user/transclusion.md` (new), `docs/user/README.md`.
- **Spec** — §4.7 (as amended), `docs/spec-questions.md` M19-A/B/C.
- **Steps** — (1) How to create a mirror (copy the anchored line, `^tm-id` included, into another file), how to edit either, how to detach (`^tm-new`). (2) State the limits plainly: one-line blocks only; per-file indentation; conflict behavior (hub wins, your version is kept and reviewed); a dirty editor buffer can race the rewrite. (3) Show what a hub-side `akasha set` does to both files. (4) Link from `docs/user/README.md`.
- **Verify** — Doc-only, objective: every command and file shown is run in order against a scratch daemon and behaves as written; every anchor form shown parses with zero violations against the shipped parser.
- **DoD** — a user can create, edit and detach a mirror using only this guide; every example executes as written; the limits are stated.

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

---

## M20 — Spans, marker-less files, no pause, and the revised join rule (Depends on: M19)

**Origin.** User rulings of 2026-09-24 on the `sandbox/init` run, logged as `docs/spec-questions.md` **M20-A … M20-G** (M20-F, the glued anchor, and debug-plan D11–D14 are already done). Design and evidence: `docs/proposals/2026-09-24-refactor-spans-marker.md`.

Milestone DoD: a note is transcluded either as a whole line (`text ^tm-id`) or as a span (`{text}{tm-id}`, possibly multi-line) and an edit in any copy reaches every copy within one sync cycle; **no note ever gains front matter** and the daemon never reads or writes front matter; **no file is ever paused** (each violation is repaired or the line gets a new node; only a deleted S1+ node is reviewed); a join with a stale paste is silent and a join with a newer change wins; a real-CLI end-to-end test proves all of it after one `akasha setup`; `make check` + `make battery` green.

> **Eligibility note for the overnight/fleet scanner:** T20.1 is doc-only and first. **T20.2–T20.7 all edit `src/akasha/sync/reconcile.py` and are one strictly sequential chain.** T20.3 and T20.4 **deliberately edit protected tests** — each names them under "Authorized changes"; nothing else in `tests/golden` or `tests/battery` may change, and T20.2 must pass **every** existing test unmodified (if one needs a change, the refactor is wrong).

### T20.1 — Rulings, spec, PRD and plan (doc-only)
- **Goal** — Land every M20 ruling in the normative documents before any code changes, so the code tasks implement a settled spec.
- **Depends on** — nothing.
- **Files** — `docs/mvp-spec.md` (§4.7, §4.8, §6.2 E13, §4.11 row), `docs/vision.md` (R11 superseded note), `docs/spec-questions.md` (M19-A/C/D resolved; M20-A…G), `docs/build-plan.md`, `docs/agents/task-status.md`, `docs/proposals/2026-09-24-refactor-spans-marker.md`.
- **Verify** — `grep -c "M20-" docs/spec-questions.md` ≥ 7; `grep -n "pause_and_diff" docs/mvp-spec.md` empty; `git diff --stat` touches only the files above.
- **DoD** — the spec states: no file marker, spans, balanced-brace scan, per-file padding, the violation-resolution table, the join order, and the relay (M19-D).

### T20.2 — Stage the reconcile cycle (pure refactor)
- **Goal** — Split `Reconciler._cycle` (283 lines, 41 branches) into named stages so T20.3–T20.7 each touch one stage.
- **Depends on** — T20.1.
- **Files** — `src/akasha/sync/reconcile.py` only.
- **Steps** — stages `read → (adopt) → parse+lint → repair → diff → apply → write-back → snapshot`, each a private method returning an outcome (`Quiet | HubOnly | Applied`) so early exits are returns, not nesting; keep the metrics `try/finally` in one wrapper and the ignore/`refresh` guards at the top.
- **Verify** — `uv run pytest tests/unit tests/property tests/integration tests/battery tests/golden` with **zero test modifications** (`git diff --stat tests` empty).
- **DoD** — no behaviour change; `_cycle` under ~60 lines; `make check` + `make battery` green.

### T20.3 — Remove the `tm: 1` marker and every front-matter edit
- **Goal** — The daemon never reads, writes or interprets front matter (M20-C).
- **Depends on** — T20.2.
- **Files** — `src/akasha/contract/{parser,render,linter,grammar}.py`, `src/akasha/sync/reconcile.py` (delete `adopt_unmanaged`), `src/akasha/kernel/store.py`, `migrations/003_drop_sync_files_contract_version.sql`, `src/akasha/api/routes/sync.py`, `docs/user/*` wording, the tests below.
- **Steps** — (1) `BlockSet` loses `managed`, `contract_version`, `front_matter`; `parse` skips an initial `---…---` block as raw lines and never looks for `tm:`. (2) `render` emits raw lines plus blocks, no header. (3) Delete `adopt_unmanaged`; a file with no construct is untouched and untracked as before. (4) Remove `W_UNMANAGED_ANCHOR` and the `not managed` early return. (5) Drop `sync_files.contract_version` (migration 003, store, route). (6) Real-CLI test: a note gains **no** front matter through `setup`, a mint, an edit and a mirror.
- **Authorized changes to protected tests (the only ones) — as landed:** **no golden fixture was edited**: a `tm: 1` line inside a fixture is now an ordinary YAML key that passes through verbatim, so every existing golden round-trips unchanged. Changed: golden case `contract_w_unmanaged_anchor` (directory deleted) and its entries in `tests/golden/test_serialization.py`; the `.managed` assertion there; the unit tests of retired behaviour (`W_UNMANAGED_ANCHOR` in `test_linter.py`, the `adopt_unmanaged` tests in `test_reconcile.py`, the "unmanaged" tests in `test_parser.py`, the header tests in `test_render.py`, `FRONT_MATTER_TM_RE` in `test_grammar.py`); tests that constructed `BlockSet(managed=…, contract_version=…, front_matter=…)` or the `sync_files.contract_version` column (`test_pause_and_diff.py`, `test_schema.py`, `test_gc.py`, `test_api.py`, the property generators); battery E25 (redefined: no header ever, front matter kept byte for byte) and `test_cli_setup.py` (no header after setup).
- **Verify** — `make check`, `make battery`; `grep -rn "tm: " src` finds no writer.
- **DoD** — no code path writes `tm:`; a tracked file's bytes outside its constructs never change.

### T20.4 — Never pause a file: repair, else give the line a new id (M20-G)
- **Goal** — Remove pause & diff; every violation is resolved per the M20-G table.
- **Depends on** — T20.3.
- **Files** — `src/akasha/contract/linter.py`, `src/akasha/sync/reconcile.py`, `src/akasha/api/routes/sync.py` (`pauses` always `[]`), `src/akasha/cli/main.py` (status), `src/akasha/kernel/store.py` (dismiss helper), tests below.
- **Steps** — delete `pause_threshold`/`pause_and_diff` and the `_cycle` branch; implement the table (checksum / unknown anchor / no-identical duplicate / fuzzy lost anchor ⇒ the line gets a new node via the existing `^tm-new` mint path, the old node untouched except by the ordinary delete rules); dismiss a path's open pause reviews on its next cycle; conservative roots unchanged.
- **Authorized changes to protected tests (by name) — as landed:** battery **E13** (redefined as a real formatter storm: an exact lost anchor, a reworded line, a bad checksum, a duplicate and a deleted S1 node in one file; asserts nothing is lost, the S1 node stays and is the only review, no pause, and the file keeps syncing) and **E15** (a malformed checksum gets a new node; nothing else changes), both kept in the silent-guess aggregate; golden fixture `e13-pause-storm` replaced by the additive `e13-formatter-storm`; `tests/golden/test_serialization.py` (checksum and pause cases; the `contract_pause_and_diff` fixture is kept and now asserts a > 25 % storm is resolved with repairs only); `tests/unit/contract/test_pause_and_diff.py` deleted; the review-path tests in `test_linter.py` (checksum, ambiguous duplicate, fuzzy lost anchor); `test_reconcile.py` (`test_e_id_checksum_*`, `test_unknown_anchor_*`, `test_pause_makes_zero_writes`); `test_cli_status.py::test_violations_are_grouped_by_code`. **Not** in the list because unaffected: E04/E05/E07 and every mirror case.
- **Design decision recorded (M20-G):** an unknown, checksum-valid anchor is **adopted under its own id** (`store.create_node(node_id=…)`), not re-minted — re-minting would split every mirror after a hub reset and make two hubs re-id each other's files forever.
- **Verify** — `make check`, `make battery`; a real-watcher test of a formatter storm: the file keeps syncing and no text is lost.
- **DoD** — no code path can pause a file; existing pause reviews are dismissed.

### T20.5 — Span grammar (single line)
- **Goal** — `{text}{tm-id}` shares only the text between the braces (M20-A, M20-E).
- **Depends on** — T20.4.
- **Files** — `src/akasha/contract/{grammar,parser,render}.py`, `src/akasha/sync/reconcile.py`, tests (new goldens are additive).
- **Steps** — constants; balanced-brace scan (cap 200 lines / 64 KiB); `Block(kind="span")` with `prefix`, `suffix`, per-file `lead`/`trail` padding; `hub_state_for` substitutes trimmed text into the span keeping each file's padding; `{text}{tm-new}` minting; a damaged terminator ⇒ prose.
- **Verify** — property test `parse(render(G)) == G` extended with spans; battery E26–E28.
- **DoD** — whole-line behaviour byte-identical; padding never touched by the daemon.

### T20.6 — Multi-line spans
- **Goal** — a span may cross lines (M20-B).
- **Depends on** — T20.5.
- **Files** — as T20.5 plus `Block.end_line_no`.
- **Steps** — parser state machine within the cap; `raw_lines` excludes the range; render emits the range as one unit; diff/base comparison on the joined text; the newline guard applies to whole-line blocks only.
- **Verify** — battery E29–E31 (edit either copy; blank lines inside; inner braces); property test.
- **DoD** — a multi-paragraph section edited in any file changes in every file.

### T20.7 — Join rule (M20-D)
- **Goal** — a stale paste is silent; a newer change wins.
- **Depends on** — T20.6.
- **Files** — `src/akasha/sync/reconcile.py`, `src/akasha/kernel/store.py` (`node_versions` read helper), tests.
- **Steps** — replace the `op.mirror` branch by `classify_join`: equal ⇒ quiet; equals an earlier version ⇒ hub wins, no review; new text and file mtime after the hub head `ts` ⇒ commit as a sync edit and propagate; else (older, future mtime, conservative root) ⇒ today's conflict path.
- **Authorized changes to protected tests (by name) — as landed:** `tests/unit/sync/test_reconcile.py::test_mirror_join_with_differing_text_hub_wins_and_is_reviewed` (replaced by five cases with explicit file times; the old test passed only because filesystem mtimes lag the hub's microsecond clock by a few ms) and the demo self-test's differing-copy step (now: an old version pasted back is silent; new wording pasted later wins); E05/E21–E24 unchanged.
- **Verify** — unit tests per case; real-watcher test "paste then edit within one window".
- **DoD** — the sandbox case ("modify the new version, will the old change?") passes.

### T20.8 — End to end, docs, plugin
- **Goal** — prove the milestone through the real CLI and document it.
- **Depends on** — T20.7.
- **Files** — `tests/integration/test_setup_then_transclusion.py` (extend), `docs/user/{quickstart,cli}.md`, optional `plugin-obsidian` decoration.
- **Verify** — the extended test plus the sandbox replay; `make check` + `make battery`.
- **DoD** — after one `akasha setup`, whole-line and span (single and multi-line) transclusion, glued typing and formatter damage all converge with no front matter, no pause, no review item.

---

## M21 — Behaviour-preserving refactor (Depends on: T20.7 for tasks sharing `reconcile.py`)

Measured hotspots: `store.py` 2 597 lines, `reconcile.py` 1 781, `cli/main.py` 1 618; `commit_node` 203 lines, `setup` 125, `plugin_install` 116; 17 function-local `akasha` imports. Every task is a pure move/extract: **zero test modifications**, `make check` + `make battery` green, goldens untouched.

| Task | Change | Depends on |
|---|---|---|
| T21.1 | Extract `MirrorPropagator` (owner lookup, work list, cap, failure isolation) from `Reconciler.on_change` | T20.7 |
| T21.2 | Split `_compute_ops` by concern; `kernel_apply` if-chain → op-kind dispatch table | T20.7 |
| T21.3 | Watcher: extract `RootRegistry` (roots, `.tmignore` patterns, lock, `root_of`) taking rows, never a connection | — |
| T21.4 | One shared `rescan` for the route, `reconcile_all` and startup (removes the throwaway `Reconciler`) | T21.1 |
| T21.5 | `cli/main.py` → package (`app`, `client`, `output.emit` replacing 16 `if state.json_mode:` forks, `verbs/*`, `onboarding`) | — |
| T21.6 | `kernel/store.py` → `kernel/store/` package behind the same façade (rule 4 unchanged); split `commit_node` | — |
| T21.7 | Break the import cycles behind the 17 local imports (injected hooks) | T21.6 |
| T21.8 | **Efficiency** (measured, no behaviour change): skip the wasted projection and re-parse in an edit cycle, cache id checksums, project files with one light read, prefilter prose lines in the parser, lazy heavy CLI imports | — |
| T21.9 | **Verbosity**: condense internal docstrings/comments that narrate task history (AST-verified: the code, docstrings aside, must be identical); dedupe repeated review-queue and repair boilerplate | — |
