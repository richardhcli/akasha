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
M6 (1 entry: T6.5, 2026-07-14), M8 (4 entries: T8.0/T8.1/T8.3/T8.5b,
2026-07-18 via fable rulings), and the **pre-dogfood triage** (11 entries,
2026-07-20/21 via a fable ruling: T7.1, T7.7, T7.3, T7.5×2, T7.6, T9.2×3,
T9.3, T10.2b — see that file's "Pre-dogfood spec-question triage" section
for the full ruling on each), and 2026-07-26 (2 entries: T9.6, T11.1's
sync-roots/watcher half — both closed by the same-day T9.6 live-watcher fix).

## D5 — Spec §4.13 names four views (+Dashboard, M10) but no fifth "settings"/auth affordance. Is adding a minimal shared token-entry UI in scope?
- **Where:** `src/akasha/ui/static/app.js` (`initAuthBar`), all six `src/akasha/ui/templates/*.html`.
- **Narrowest reading taken:** Same precedent T8.3's inline revise-textarea already set for "spec silent on a UI affordance": implement the smallest thing that closes a real, empirically-found gap (no in-page way to ever set the bearer token — see `docs/mvp-debug-plan.md` D5) rather than block on a spec amendment. One always-visible `#tm-auth-bar` bar per view, writing to the same `localStorage.tm_token` key every view already reads — no new persistence mechanism, no new endpoint, no schema change.
- **Resolution:** resolved 2026-07-31 — the user directed improving the UI's general UX as part of this session's dogfood pass; this is read as in-scope authorization for exactly this kind of minimal affordance. Implemented, tested (`tests/integration/test_ui_auth_bar.py` + shell-test updates), full gate green.

**Open questions: 1.** Every entry open as of M10's first code-complete
milestone (2026-07-19) has been triaged, resolved, and archived — see
`docs/archived-questions.md`. New ambiguities encountered during the
one-month dogfood gate or any future work should be logged here per the
entry format above.

## D4 — What origin(s) should the daemon's CORS policy allow for browser-embedded clients (the Obsidian plugin, `app://obsidian.md`)?
- **Where:** `src/akasha/api/app.py` (`create_app`, `_CORS_ALLOWED_ORIGINS`); see `docs/mvp-debug-plan.md`'s D4 entry for the full empirical finding (first live Obsidian-vault dogfood run: every plugin→daemon fetch failed CORS preflight, status bar stuck on `TM: offline`) and fix writeup.
- **Narrowest reading taken:** Spec §4.11/§3 document the API surface and the `127.0.0.1`-only bind but say nothing about CORS/allowed origins, so there is no documented default to fall back to. Allow exactly `app://obsidian.md` (the plugin's fixed Electron origin), not a wildcard `*` — this daemon carries bearer tokens, and wildcard-plus-credentials would be a real weakening of the localhost-only security posture spec §3 establishes, unspec'd by anything in `mvp-spec.md`. `allow_credentials=False` since auth is a bearer token header, never a cookie.
- **Resolution:** resolved 2026-07-31 — the user, acting as the human this entry asked to adjudicate it, explicitly directed implementing this exact narrowest reading. `CORSMiddleware` registered with `allow_origins=["app://obsidian.md"]` only; guarded by a test (`tests/integration/test_cors.py::test_no_wildcard_origin_is_ever_configured`) that fails if this is ever loosened to `*`. Full gate green (see D4 in `docs/mvp-debug-plan.md`).

## D6 — Is a first-run/onboarding UX overhaul (bootstrap token, sync CLI verb, web-UI login link, Windows packaging/tray) in scope now, ahead of `docs/user/quickstart.md`'s "no packaged installer yet (Phase 4+)" framing?
- **Where:** `docs/onboarding-ux-report.md` (new, this entry's full writeup); no `src/` files touched by this entry.
- **Narrowest reading taken:** `docs/vision.md` §7.9 already names a packaged single executable (PyInstaller/Nuitka), tray presence, and Task-Scheduler/NSSM autostart as the intended Windows-first distribution ("a later polish step," not a Phase-5 deferred item) — this is confirmation, not invention, that the class of work is in scope. The open T11.1 entry below independently already flags the bootstrap-token gap as "a real first-run UX gap." Rather than edit `src/` directly, produced a full audit + a proposed M12 task breakdown (`docs/onboarding-ux-report.md`), because this session runs without a Windows host or an established `make check`/`make battery`/Playwright gate (rule 0.9) to close any implementation task against — the project's own history shows several real bugs (CRLF write-back, RSS-sampler ctypes truncation, `winerror` handling) were only catchable on a real Windows run.
- **Resolution:** resolved 2026-08-02 — the user directed this UX audit directly in this session (same authorization pattern as D4/D5). `docs/build-plan.md` M12 (T12.1–T12.6) now carries the proposed tasks; Tier 0 (T12.1–T12.3) is implementable and verifiable in a Linux sandbox via `make check` and was dispatched the same session. Tier 1/2 (T12.4/T12.5, Windows packaging) are folded in as `TODO` gated on a real Windows host per rule 0.9 — not started here. T12.1's own sub-decision (bootstrap-token transport) is resolved in the entry immediately above: CLI verb, not endpoint.

## T12.5 — `MIGRATIONS_DIR`'s repo-root-relative resolution breaks inside a PyInstaller-frozen build; is touching `kernel/store.py` (outside T12.5's original Files list) authorized to fix it?
- **Where:** `src/akasha/kernel/store.py` (`_migrations_dir`/`MIGRATIONS_DIR`).
- **Narrowest reading taken:** T12.5 (packaged Windows executable, `docs/onboarding-ux-report.md`) surfaced this live while building the exe: `MIGRATIONS_DIR = Path(__file__).resolve().parents[3] / "migrations"` walks up to the repo root, which does not exist inside a PyInstaller bundle (`sys._MEIPASS` is the only real filesystem root there), so a frozen `akasha.exe`'s very first `run_migrations` call would silently find zero `.sql` files. Same recurring precedent as T9.2/T9.3b/T10.2/T10.2b's minor Files-list completions: fixed with a `getattr(sys, "frozen", False)` branch that resolves from `sys._MEIPASS` instead, guarded so the non-frozen path (every existing test, the CLI, `uv run akasha daemon`) is byte-for-byte unchanged — confirmed by re-running the full gate after the change (see task-status.md M12/T12.5 for the pass counts). No schema/endpoint/grammar change; `app.py`'s `_UI_DIR` needed no equivalent fix since it was already package-relative, not repo-root-relative.
- **Resolution:** resolved 2026-08-02, same session — self-resolved (mechanical Files-list completion, zero behavior change for any non-frozen caller, full gate re-verified green on the real Windows host before and after).

## T11.1 — How does the very first human token get minted on a fresh DB, given `POST /v1/tokens` is `require_human`?
- **Where:** `src/akasha/api/routes/tokens.py` (`create_token`, `require_human`); `src/akasha/api/deps.py` (`require_human`); `docs/dogfood/README.md` step 6.
- **Narrowest reading taken:** Spec §4.11/§4.12 mark the whole `/tokens` row human-only, and there is no documented bootstrap endpoint or CLI flag for a brand-new DB with zero existing tokens. Treated this as a one-time pre-daemon operator/test-harness bootstrap step (an "embedded caller" per `store.connect`'s own docstring): mint one throwaway bootstrap token via a direct call into `kernel/store.py`'s `create_token` (never a second write path — still routed through `store.py` per rule 0.4, same pattern `tests/battery/soak.py:243` already uses), used solely to authorize the real `dogfood-smoke` token creation over genuine HTTP. This does not block T11.1 but is a real first-run UX gap for anyone standing up a fresh daemon without the test harness's direct-DB shortcut.
- **Resolution:** resolved 2026-08-02 — the user, asked directly (`AskUserQuestion`, transport choice: CLI verb vs. `POST /v1/bootstrap` endpoint), ruled **(a)**: a new `akasha init` CLI verb that talks to `kernel/store.py` directly, same "embedded caller" precedent this entry already used, rather than a new authless HTTP endpoint. Tracked as `docs/build-plan.md` T12.1 (M12).

---

<!-- Entries below logged 2026-08-05 by the post-MVP usability audit that
     produced the new docs/build-plan.md (M13–M17). Each was found by the
     spec-vs-shipped-code method docs/agents/overnight-goals.md §"When the
     list is empty" prescribes (grep the implementing function, confirm a
     real production call site), the same method that found T10.2c, T9.2c,
     T9.3b and T9.6. None is a bug report — each is a genuine ambiguity
     about how far a shipped surface may be extended without inventing
     spec (rule 0.2). -->

## T13.1 — `task_state` is settable by no HTTP endpoint at all; may `PATCH /nodes/{id}` accept it?
- **Where:** `src/akasha/api/routes/nodes.py` (`PatchNodeBody`); `src/akasha/kernel/store.py` (`commit_node`'s existing sentinel-guarded `task_state` keyword, added by T5.4 for the sync checkbox path).
- **Narrowest reading taken:** §4.11's `PATCH /nodes/{id}` row reads "commit edit (body/facets, change_class, facets_touched, message)" and names no `task_state`, while §4.2's `Node` model carries `task_state` and §4.5's `commit_node` accepts it. Consequence in shipped code (grep-verified 2026-08-05, and already disclosed in `docs/acceptance.md` row 8 / task-status T10.2c as "a pre-existing, separate HTTP-surface gap"): the **only** way to close a task in a running daemon is toggling a checkbox in a managed vault file — the CLI, the Web UI, the Obsidian plugin and any agent are all structurally unable to complete a task, so PRD §8 story 8's loop is reachable from exactly one surface. Narrowest reading adopted for T13.1: add `task_state` as an **optional** field on the existing `PatchNodeBody`, forwarded verbatim to `store.commit_node`'s existing keyword — no new endpoint, no new column, no schema change, omission behaving byte-identically to today.
- **Resolution:** open.

## T13.3 — Nothing re-projects a managed file after a hub-side commit; what triggers §4.8's "hub-only change" branch in production?
- **Where:** `src/akasha/sync/reconcile.py` (`Reconciler.on_change`'s `if V == B: write_if_diff(path, H)` branch, and `ProjectionIndex.owner`); `src/akasha/daemon.py` (`serve`, the only startup/watcher wiring); `src/akasha/api/routes/sync.py` (`sync_rescan`).
- **Narrowest reading taken:** §4.8 defines the pipeline as `on_change(path)`, and §1 states "the hub (SQLite) is the writer of record; each file-backed spoke is a projection under contract" — but grep-verified 2026-08-05, `on_change` has exactly three production entry points: daemon startup (`reconcile.reconcile_all`), a filesystem event (T9.6's live `Watcher`), and `POST /v1/sync/rescan`. **No hub-side mutation triggers a projection refresh.** So an edit made through `PATCH /nodes`, the CLI, or the Web UI is invisible in Obsidian until the user restarts the daemon, hits rescan, or happens to touch the file — and the spec's own hub-only branch (`V == B and H != B`) is exercised in tests only by an explicit `reconciler.on_change(...)` call (see `tests/battery/test_edit_battery.py`'s E16). Narrowest reading adopted for T13.2/T13.3: after an API mutation commits, run the **existing** `on_change` for only the managed file(s) that already project the affected node (resolved via the existing `ProjectionIndex`), sharing the daemon's `OriginTracker` so the write is echo-suppressed exactly as a watcher-driven write is. No new endpoint, no new schema, no new grammar, no polling loop, and no projection of unfiled nodes (a node in no managed file stays unfiled and keeps being counted by `GET /sync/export`'s `unfiled_node_count`).
- **Resolution:** open.

## T13.5 — Does the maturity display apply to all node types or only task-gated ones?
- **Where:** `src/akasha/ui/static/app.js`, inline `// SPEC-QUESTION (T13.5):` comment directly above `renderTaskComposesList`/`renderTaskSection` (Task section block).
- **Narrowest reading taken:** `docs/build-plan.md`'s T13.5 step 1 reads "display `task_state` for task-type nodes ... and display the node's maturity (already returned by `GET /nodes/{id}`, currently rendered nowhere)" immediately followed by "Non-task nodes must look exactly as they do today." Read together literally, a maturity display applied to every node type would itself be a visual change for non-task nodes, contradicting the second sentence — the two clauses only cohere if maturity is scoped to the same task-gated section as `task_state`. Narrowest reading adopted: maturity is surfaced only inside the new task-gated `#node-task` section (present only when `node.task_state !== null`), not as an always-on addition to `renderBody` for every node type. Non-task nodes (definitions, claims, evidence) still never show a maturity value in the UI after this task — flagged in case a later task (e.g. T17.x documentation, or a future dashboard/detail-view task) wants maturity surfaced generically instead.
- **Resolution:** open.

## T13.6 — `POST /v1/review/{id}/resolve` never dispatches to `approve_proposal`/`resolve_reassignment`
- **Where:** `src/akasha/api/routes/review.py` (module-level docstring, top of file).
- **Narrowest reading taken:** T13.6's build-plan prose describes proposal approval as happening "through `POST /v1/review/{id}/resolve`", but tracing the code shows pre-mvp T8.0 wired this route to `tms.review.resolve_review` only, for the four standard resolutions (`still_holds|revised|retracted|dismissed`); `tms.review.approve_proposal` and `tms.review.resolve_reassignment` have never had an HTTP route at all (no task's Files list has ever included that wiring, and T13.6's own Files list is this module plus a test file, not a new dispatch mechanism). Adding cause_kind-based routing to reach those two functions would let this endpoint mint nodes — a capability change §4.11 does not describe. Narrowest reading adopted: T13.6 closes projection for the resolution surface that actually exists in production (`resolve_review`) and does not invent new routing behavior. The "approved create-proposal projects nothing" DoD item is verified at the layer where proposal approval actually lives (calling `tms.review.approve_proposal` directly, then `reconcile.project_node_change` on the minted id) — see `tests/integration/test_projection_writeback.py`. Whether proposal approval and reassignment resolution should ever get an HTTP route is a separate, un-scoped question for a future task.
- **Resolution:** open.

## T14.2 — §4.12's verb list has no edge/vet/split/merge/neighborhood/history verbs, yet §7.11 requires API-first parity ("nothing is ever UI-only")
- **Where:** `src/akasha/cli/main.py` (registered verbs: `daemon`, `tray`, `init`, `new`, `get`, `set`, `rm`, `search`, `review list|resolve`, `token create|revoke|list`, `sync add`, `export`); §4.11's endpoint table (`POST /edges`, `DELETE /edges/{id}`, `POST /nodes/{id}/vet`, `/split`, `/merge`, `GET /nodes/{id}/neighborhood`, `/history` — all shipped and tested).
- **Narrowest reading taken:** §4.12's literal verb list is narrower than §4.11's endpoint table, so today a user cannot create a justification edge, vet a node to S4, split/merge a definition, or read a neighborhood/history from any surface except raw HTTP — the whole definition-DAG layer is unreachable from both the CLI and the Web UI. PRD §7.11 states the opposite intent ("any capability of any UI must exist as an API endpoint first; the CLI tracks the API — generated from the daemon's OpenAPI spec — so new endpoints become verbs at near-zero cost and nothing is ever UI-only"). Narrowest reading adopted for M14: add **pure HTTP-client verbs over already-shipped endpoints only** — zero server-side change, zero new endpoint, identical `--json`/`--dry-run`/exit-code plumbing every existing verb already gets. Established precedent: T12.1 (`init`) and T12.2 (`sync add`) both added verbs absent from §4.12's list on exactly this reasoning. Any new mutating verb must also gain a `DryRunCase` row in `tests/integration/test_cli_dry_run.py`, whose AST meta-test structurally requires it (T12.2's landing note).
- **Resolution:** open.

## T14.2 — the task's DoD asserts exit 4 for `POST /edges`' facet-binding 400; the shipped mapping produces exit 1
- **Where:** `src/akasha/cli/main.py` (`_exit_code_for`, see inline `# SPEC-QUESTION (T14.2)` comment); `src/akasha/api/routes/edges.py:98`; `docs/mvp-spec.md` §4.12 exit-code table.
- **Narrowest reading taken:** T14.2's DoD text says a justification edge submitted with no `facet_binding` must fail with "CLI exit code 4". The server returns `400 E_INVALID` (`routes/edges.py:98`), which the existing, unmodified `_exit_code_for` maps to exit 1: `E_INVALID` is not 404/409, is not `E_NEEDS_REDIRECT`, and contains neither "CONFLICT" nor "VIOLATION". §4.12's exit-code table reads "4 conflict/violation/needs-redirect"; every other use of the word "violation" in this codebase (`cause_kind='violation'` review rows, `sync/reconcile.py`) names the contract-violation concept, not generic request/model validation. `E_INVALID` is used identically (400 → exit 1) by `new`, `set`, `sync add`, and `token create`'s own client-side/server-side validation, and no existing test anywhere pins a different exit code for `E_INVALID`. Narrowest reading adopted: leave the shared `_exit_code_for` mapping untouched (widening it would be a cross-cutting behavior change to every verb's error handling, not scoped to `edge add`) — `tests/integration/test_cli_edge.py::test_edge_add_justification_without_binding_surfaces_server_400` asserts the real, observed exit code 1 plus the server's verbatim `facet_binding` message reaching the caller (never a client-side duplicate of the rule). Needs a human ruling before T14.3/T14.4 land, since `vet`'s 403 `E_HUMAN_ONLY` and split/merge's own 400s will hit the identical table-reading question.
- **Resolution:** open.

## T14.2 (finding, not this task's scope) — `store.create_edge` does not validate that `src`/`dst` node ids exist
- **Where:** `src/akasha/kernel/store.py` (`create_edge` / `_recompute_maturity`); `src/akasha/api/routes/edges.py` (`create_edge`); `src/akasha/api/deps.py` (`mutation_gate`).
- **Narrowest reading taken:** Empirically confirmed (not inferred) via direct `store.create_edge` probes: creating an edge with a nonexistent `src` succeeds silently, and creating an edge with a nonexistent `dst` also succeeds silently. `store.create_edge` only validates the `facet_binding` rule via the `Edge` pydantic model; it never existence-checks `src`/`dst`. `_recompute_maturity(conn, edge.dst)` is a no-op when the node no longer exists, so it does not backstop this. `mutation_gate` returns `None` immediately for human tokens with no existence check. None of `tests/integration/test_api.py`'s nine edge tests exercises a missing src/dst. This means `akasha edge add` (T14.2's new CLI surface, now the primary way a human builds the DAG) will silently create a dangling edge from a typo'd node id with no error at all — directly relevant to the "zero dangling references" invariant the build-plan cites for split/merge (T14.4). Not fixed here: `kernel/store.py`/`api/routes/edges.py` are not in T14.2's `Files` list (rule 8).
- **Resolution:** open — needs a dedicated task/fix, likely alongside or before T14.4 (split/merge's reassignment-queue invariant depends on edges always pointing at real nodes).

## T14.3 — Does PRD R9 ("never say 'true' ... including MCP responses") also constrain `akasha vet --json`'s machine-readable payload, or only human-facing copy?
- **Where:** `src/akasha/cli/main.py`, `vet()`'s `if state.json_mode:` branch (marked with an inline `# SPEC-QUESTION (T14.3)` comment at the site).
- **Narrowest reading taken:** `POST /nodes/{id}/vet`'s real API response includes a literal JSON boolean `"vetted": true` (`kernel/model.py`'s `Node.vetted: bool` field is unchanged by this task, correctly — reshaping it would invent a divergent response shape, rule 0.2). `akasha vet`'s plain (non-`--json`) output was rewritten to never render the literal word "true", satisfying PRD R9's literal text ("system language says 'vetted by you,' never 'true'") for the human-facing copy this task asked for. `--json` mode, however, passes the real API response through verbatim — the same contract every other verb's `--json` mode already gives scripted callers — which means a freshly-vetted node's `--json` output does contain the literal boolean token `true`. PRD R9's own parenthetical, "including MCP responses," is real evidence the no-"true" rule may have been intended to reach at least one other machine-facing surface, which weakens (without settling) the narrowest reading taken here (`--json` is a documented, versioned wire contract, not "system language"/copy in R9's sense — contrasted with the MCP surface's generated prose). Left `--json` output as an unmodified, faithful mirror of the real API response pending a human ruling.
- **Resolution:** open.

## T14.6 — M7's DoD requires the facets-from-spans capture flow "in API/UI"; only the API half exists
- **Where:** `src/akasha/api/routes/edges.py` (`facet_span`, T7.7 — shipped); `src/akasha/ui/static/app.js` (only `POST` in the entire UI is `/v1/review/{id}/resolve`).
- **Narrowest reading taken:** §5's M7 milestone text says "facets-from-spans capture flow in API/UI (`POST /edges` accepts `facet_span` and creates the facet on the target)" and PRD R8 makes this the designed mitigation for facet bootstrap — with `facet_coverage` (§7) as a **gating** dogfood metric, since "persistently low coverage means the TMS loop is inert and the facet-from-span capture flow (R8) gets redesigned before anything else ships". §4.13's four-view list does not itself name a link/relate affordance, and grep-verified 2026-08-05 no UI code ever POSTs `/v1/edges`, so the only way a facet is ever born from a span today is a hand-written HTTP call. Narrowest reading adopted for T14.6: the smallest possible affordance on the **existing** node view — select/paste a span of the target's body, choose an edge type, submit to the **existing** `POST /v1/edges` with `facet_span` — no new endpoint, no new view, no schema change. Same "spec silent on a UI affordance, implement the smallest thing that closes an empirically-found gap" precedent as T8.3's inline revise-textarea and D5's auth bar.
- **Resolution:** open.

