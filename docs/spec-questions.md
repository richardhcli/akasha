# Spec questions

Log of ambiguities hit while implementing `docs/build-plan.md`. Per build-plan
rule 0.2 / rule 2: never invent schema, endpoints, ID formats, or grammar
beyond `docs/mvp-spec.md`. When something is ambiguous, implement the
narrowest reading, add a `# SPEC-QUESTION:` comment at the site, and log an
entry here so a human can resolve it.

Entry format:

```
## <task ID> — <one-line question>
- **Where:** <file:line>
- **Narrowest reading taken:** <what was implemented in the meantime>
- **Resolution:** <filled in once a human answers; leave "open" until then>
```

This file holds **open** questions only. Resolved entries are moved to
`docs/archived-questions.md` in a batch when the milestone that raised them
closes (context-size optimization — an agent scanning for outstanding
ambiguities shouldn't have to read past closed ones). See that file for the
full resolved history: M1 (T1.3/T1.5/T1.6/T1.7), M3 (T3.1/T3.2/T3.5/T3.6×2),
M4 (13 entries, 2026-07-12), M5 (10 entries: T5.1/T5.5/T5.8-*, 2026-07-13),
and M6 (1 entry: T6.5, 2026-07-14).

**Open questions: 10** (M7's 6, logged 2026-07-14, still open; +4 M8 entries
T8.0/T8.1/T8.3/T8.5b, logged 2026-07-17. T7.2 delete_node gap RESOLVED
2026-07-15 via follow-up T7.2b. All 10 need a product/spec decision before
archiving.).

## T8.5b — spec §3's "single shared connection" daemon model is unsafe under concurrency (amend §3)
- **Where:** `docs/mvp-spec.md` §3 ("The daemon uses one shared connection (`check_same_thread=False`, because synchronous FastAPI routes run in a thread pool)... This is the MVP concurrency model"); implemented in `src/akasha/api/deps.py` (`get_conn`) + `src/akasha/api/app.py`.
- **What was falsified:** §3 asserts one shared `sqlite3.Connection` is the concurrency model, justified by `check_same_thread=False`. But that flag only disables the *same-thread assertion*; it does NOT make concurrent multi-thread use of one connection safe. The Web UI's node view (T8.2) issues **4 concurrent `fetch()`es** (`Promise.all`), which FastAPI runs on separate threadpool threads sharing the one connection. Reproduced directly (240 concurrent requests): ~10% failed with `sqlite3.InterfaceError` (500), a *valid token rejected* (401, corrupted `tokens` read in `auth.authenticate`), and an *existing node missing* (404, corrupted read in `store.get_node`) — i.e. the writer-of-record returns **wrong reads** under concurrency, a data-integrity defect. This is the first client to ever issue concurrent requests, so it was latent until M8.
- **Narrowest reading / fix taken (T8.5b, user-directed "ensure concurrency is possible"):** `get_conn` now opens a **fresh WAL connection per request** (concurrent readers + one writer; `store.connect` gained `PRAGMA busy_timeout=5000`) and closes it at request end. `app.state.conn` is kept only for the pre-serving startup reconcile (`daemon.py`); test/embedded callers that inject a connection (`create_app(conn=...)`) still get the shared connection via the `db_path is None` branch (they drive the app sequentially, so it's safe). After the fix: 240/240 concurrent requests → 200. Guarded by `tests/integration/test_concurrency.py` (fast) + `tests/integration/test_ui_smoke.py` (end-to-end browser). The serializing-lock alternative was rejected per the user's directive (it would serialize the UI's parallel fetches).
- **Resolution:** open — **amend spec §3** to specify per-request WAL connections (not one shared connection) as the daemon concurrency model, ratifying the fix.

## T8.3 — "revised" resolution cannot be truly one-click
- **Where:** `src/akasha/ui/static/app.js` (`renderReviewItem`, Review view, inline `# SPEC-QUESTION`-style comment).
- **Narrowest reading taken:** §4.13 asks for "one-click resolutions," but the `revised` resolution (spec §4.9) requires a new node body plus `change_class`/`facets_touched` that the server cannot infer — it cannot be literally one-click. Implemented `still_holds`/`retracted` as true one-click buttons, `dismissed` as one-click but only rendered when `cause_kind === "violation"` (mirrors the server's violations-only 409 rule so the UI never offers a guaranteed-fail action), and `revised` as a minimal inline `<textarea>` + "submit revised" button that POSTs `{resolution:"revised", new_body:<textarea>, change_class:"minor", facets_touched:[]}` — the smallest affordance short of a full editor. Cap-10 banner is a display-only signal over the uncapped `GET /v1/review` (renders when open count ≥ 10).
- **Resolution:** open — confirm the minimal inline-body affordance is the intended `revised` UX, or specify a richer edit flow (e.g. route to the node edit view).

## T8.0 — `/v1/review` HTTP endpoints were spec-defined but never in any task's Files list
- **Where:** `docs/build-plan.md` (no task created `src/akasha/api/routes/review.py`); spec §4.11 line ~315 defines `GET /review?status=open` + `POST /review/{id}/resolve`; `src/akasha/cli/main.py` docstring expected T7.5 to "land them"; but T7.5's `Files` list was `tms/review.py` + `test_tms.py` only.
- **Narrowest reading taken:** the endpoints are spec-mandated, so building them is implementation, not invention (rule 0.2 satisfied). Inserted a new focused prerequisite task **T8.0** (Files: `routes/review.py`, `app.py`, regenerated OpenAPI snapshot, `test_api.py`) rather than reopening T7.5 (which delivered exactly its Files list). Endpoint contract decisions: `GET /review?status=open` returns the **uncapped** open set via `store.find_open_reviews` (the §4.9 daily-cap-10 is a T8.3 *display/banner* concern, NOT an endpoint limit — a capped endpoint could not answer "does node X have an open facet_break," which the T8.2 badge needs and which a 1-review seed fixture would mask); added an optional `node` filter param for the badge's per-node query; `POST /review/{id}/resolve` is human-only (∅, `require_human`) and covers the four standard resolutions (`still_holds|revised|retracted|dismissed`) via `tms/review.py` `resolve_review` (proposal-approval / split-reassignment flows are out of T8.0 scope).
- **Resolution:** open — a spec editor should add `T8.0`'s route to the build-plan §4.11 task coverage (it was an M4→T7.5 sequencing gap) and confirm the uncapped-endpoint + `node`-filter contract.

## T8.1 — build-plan Files list omits the vendored htmx asset and the integration test
- **Where:** `docs/build-plan.md` T8.1 `Files:` (lists only `base.html`, `static/app.js`, `api/app.py`); actual deliverable also added `src/akasha/ui/static/htmx.min.js` and `tests/integration/test_ui_shell.py`.
- **Narrowest reading taken:** both additions are mechanically required by the task's own Goal ("Wire htmx"; DoD "static files served directly") and Verify ("assert in a lightweight integration test"), not scope creep — a vendored `htmx.min.js` IS "the static file to copy," and the Verify literally demands a test. Added both, touched no other unlisted file, `pyproject.toml` untouched (no jinja2). Same class as prior incomplete-Files-list cases; a spec editor should add these two paths to T8.1's Files line (and note the M8 rendering architecture: static shells + client-side vanilla-JS render, no jinja2 — recorded in `docs/agents/task-status.md` M8 header).
- **Resolution:** open.

## T7.1 — `composes_touched_facet` predicate is undefined in §4.9
- **Where:** `src/akasha/tms/invalidate.py` (`_composes_touched_facet`, inline `# SPEC-QUESTION:`).
- **Narrowest reading taken:** §4.9's pseudocode calls `composes_touched_facet(edge, touched)` but never defines it. The predicate's first two clauses (`facet_binding in touched` / `facet_binding == '*'`) already cover every `composes` edge with a specific or wildcard binding, so this third clause can only add coverage for a `composes` edge with `facet_binding IS NULL` (a whole-node composition, no facet binding). Implemented as: such a whole-node `composes` edge is touched by ANY non-empty `touched` set — any interface break on the target is relevant to a plain "this node composes that node" subscription, regardless of which facet broke. Covered by a unit test (fires on non-empty touched, silent on empty).
- **Resolution:** open.

## T7.7 — `Facet.name` for facets-from-spans-minted facets
- **Where:** `src/akasha/kernel/store.py` (`mint_facet_from_span`, inline `# SPEC-QUESTION:`).
- **Narrowest reading taken:** §4.2's `Facet.name` is a "short label, unique per node," but the facets-from-spans capture flow (§T7.7) supplies only `facet_span` (the highlighted text) — no name. Implemented `name = facet_id` (the id8 is unique by construction; the span text is neither guaranteed unique nor a short label). The mint commit uses `change_class="minor"` (a brand-new v1 facet is neither removed/renamed nor version-bumped, so §4.9's heuristic gives non-major — NOT a spec question, just noted).
- **Resolution:** open.

## T7.2 -- S1+ node retraction via `delete_node` never wires into `invalidate`
- **Where:** `src/akasha/kernel/store.py` (`delete_node` -- NOT edited by T7.2, which only wired `commit_node`).
- **Narrowest reading taken:** spec 4.9 says "node retraction is always major touching all facets." T7.1's `invalidate` already flags every bound subscriber when handed the full facet-id set (unit-tested), and T7.2 wired that trigger into `commit_node` only (per T7.2's Files list + the orchestrator's sanctioned-edit scope). But a real S1+ tombstone via `delete_node` does not go through `commit_node`, so an actual node-retraction API/CLI call -- as opposed to a synthetic "major commit touching all facets" -- would not currently fire `invalidate`. Left as-is (out of T7.2's scope); the synthetic path is covered and tested.
- **Resolution:** **RESOLVED 2026-07-15 (fixed now — product decision: fix, not defer).** Follow-up task T7.2b wired the S1+ tombstone branch of `store.delete_node` through `invalidate`: it captures the node's full facet-id set before tombstoning, and (after the tombstone UPDATE, BEFORE `_reassign_inbound_edges` so subscriber edges still have `dst == node_id`) calls `invalidate(conn, node_id, head_hash, touched=all facet_ids)` inside the existing transaction (uses `enqueue_review_within_transaction`, deferred import to dodge the circular import — same pattern as `commit_node`). S0 hard-delete branch untouched. `tests/integration/test_tms.py::test_s1_node_retraction_flags_dependents` proves both the pure-tombstone path (facet-bound AND `'*'`-bound dependents flagged) and the `redirect_to` path (dependent flagged, proving invalidate runs before edge reassignment). Full gate green (integration 109). Since `DELETE /nodes/{id}` calls `store.delete_node`, the API path is covered too.

## T7.3 -- where does `recheck_after`'s per-node ISO-date/period schedule persist?
- **Where:** `src/akasha/tms/triggers.py` (module docstring + `run_daily_tick`, inline `# SPEC-QUESTION:`).
- **Narrowest reading taken:** spec 4.10 gives `recheck_after` "params: an ISO date, period" but 4.4's DDL has no column/table for a per-node recheck schedule, and T7.3's Files list is `tms/triggers.py` only (no migration allowed). Adopted: the date/period ride transiently in a caller-supplied `TriggerContext` per `evaluate`/`run_daily_tick` call rather than inventing new persisted state; `run_daily_tick(conn, contexts)` is a thin iterate-and-evaluate wrapper. Sourcing which nodes are due and their schedules (where `recheck_date`/`period` live between ticks) is left to whichever later task wires this into the daemon's daily-tick driver.
- **Resolution:** open -- resolve when the daily-tick daemon driver lands (needs a decision on persisting recheck schedules, possibly a migration).

## T7.5 -- daily-cap ordering references a "user flag" with no backing DDL column
- **Where:** `src/akasha/tms/review.py` (`active_queue`, inline `# SPEC-QUESTION:`).
- **Narrowest reading taken:** spec 4.9's daily cap orders by "(staleness age, inbound-edge count, user flag)" but `review_queue`'s DDL (4.4) has no user-flag/priority column. Implemented the sort key as `(created_at ASC, inbound_edge_count DESC)` with the user-flag tiebreaker treated as absent/constant (nothing to read). The cap is READ-SIDE only (`enqueue_review` stays unbounded — a write-side cap would silently drop TMS signals, violating zero-silent-guesses).
- **Resolution:** open -- a future task adding a user-flag/priority affordance (UI flag on a review) would need a migration + a third sort key.

## T7.5 -- resolution enum has no member for create-node-proposal approval
- **Where:** `src/akasha/tms/review.py` (`approve_proposal` / `_PROPOSAL_APPROVAL_RESOLUTION`, inline `# SPEC-QUESTION:`).
- **Narrowest reading taken:** the `resolution` enum is `still_holds|revised|retracted|dismissed` (4.4) — none semantically means "a create-node proposal was approved and its node minted." `approve_proposal` records `still_holds` ("accepted as proposed, no revision") when it mints + resolves. This is a genuine semantic gap (not merely a missing column); a dedicated `approved` value would need a migration + enum change.
- **Resolution:** open -- worth a human decision on whether proposal approval deserves a distinct resolution value.

## T7.6 -- split reassignment queue has no `cause_kind` enum member
- **Where:** `src/akasha/kernel/store.py` (`split_node`, per-inbound-edge `enqueue_review_within_transaction` call, inline `# SPEC-QUESTION:`); consumed by `src/akasha/tms/review.py` `resolve_reassignment`.
- **Narrowest reading taken:** `review_queue.cause_kind` is a closed enum (4.4): `facet_break|subtasks_closed|evidence_retracted|recheck|conflict|violation|proposal` -- none means "an inbound edge needs human reassignment after a split." Reusing any existing member is UNSAFE (each is load-bearing elsewhere: `recheck` gates triggers idempotence, `violation` makes an item dismissible, `proposal` routes to the mint path -- all in modules T7.6 must not touch), so overloading one would suppress a real review or mis-route resolution. Chose a new, clearly-flagged value `"reassignment"` (`enqueue_review` does no runtime enum validation, and there is no DB CHECK constraint -- verified -- so it is mechanically safe) pending a spec amendment to add it to the closed enum. `resolve_reassignment` records the outcome as `"still_holds"` (the resolution enum likewise has no "reassigned" member -- mirrors the `approve_proposal` precedent, cf. the other T7.5 entry).
- **Resolution:** open -- needs a spec amendment to add `reassignment` to the `cause_kind` enum (and possibly a `reassigned` resolution value). This is the same class of gap as the T7.5 proposal-approval-resolution question.
