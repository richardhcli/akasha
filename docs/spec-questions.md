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


---

<!-- Entries below logged 2026-09-23 with docs/build-plan.md M18 (zero-flag
     onboarding). M18-0 records the authorization; M18-A and M18-B are the two
     open human rulings that gate BLOCKED rows T18.9 and T18.10; M18-C/D/E are
     pre-registered narrowest readings the M18 tasks must take (same role the
     T13.x/T14.x entries above play for M13/M14). -->

## M18-0 — Is a second onboarding pass in scope, given M12 already shipped `init`, `sync add`, the web-UI bootstrap link and the Windows installer (D6)?
- **Where:** `docs/build-plan.md` M18; live run of the shipped daemon on a scratch vault, 2026-09-23 (file watching itself was already event-driven and needed no change; the barrier was the *sequence*). Reference point: `codegraph`'s install + one `init`.
- **Narrowest reading taken:** D6 already established that first-run UX work is in scope (vision §7.9). M18 is limited to friction that was *observed or verified*, not speculated: (1) the built wheel contains zero `.sql` migrations (verified with `uv build --wheel` + `unzip -l`); (2) `AKASHA_TOKEN` is documented but never read; (3) the daemon needs a second terminal; (4) registration is a multi-step sequence; (5) no single command explains why nothing is syncing. It adds no endpoint, schema, ID format or grammar.
- **Resolution:** resolved 2026-09-23 — the user directed a codegraph-style onboarding milestone in this session (same authorization pattern as D4/D5/D6).

## M18-A — May any surface persist the human token so the CLI and Obsidian plugin authenticate without an exported variable? (gates T18.9)
- **Where:** `src/akasha/cli/main.py` (`--token` is the only credential path today, and after T18.2 `AKASHA_TOKEN`); `plugin-obsidian/src/settings.ts` (the plugin already stores a pasted token in its own `data.json`); §4.11 (agent-class tokens are proposal-rewritten; `human only ∅` endpoints); PRD design invariant 3.
- **Narrowest reading taken:** none of T18.1–T18.8 writes a human token to disk. The environment variable is the only credential source they add: it is set by the human, per shell. A token file at a well-known path is *ambient authority* — any local process that shells out to `akasha` (including an autonomous agent) would act as the human, bypassing the agent-token → proposal pathway (T4.6, expandability guardrail 1). A token written into `.obsidian/plugins/tm-hub/data.json` additionally lands inside a vault that may be cloud-synced or under git. `setup` therefore prints the token once (like `init`) and never stores it.
- **Options for the ruling:** (a) keep env-only — the reading in force; (b) an opt-in `--save-token`, neutral path under the `tm-daemon` config dir, mode `0600` (POSIX) / user-only ACL (Windows), never read when an agent token is in play; (c) per-vault plugin pre-fill behind the same opt-in and a cloud-path/`.git` warning.
- **Resolution:** resolved 2026-09-23 — the user ruled: "agents should be able to act like the human being." Narrowest reading taken: (1) the persisted-token option is **approved and on by default** — `init`/`setup` save the human token to a `0600` file at a neutral path under the config dir (`tm-token`), and the CLI (and, via an explicit `--with-token`, the plugin) use it when `--token`/`AKASHA_TOKEN` are absent, so an agent that shells out to `akasha` acts as the human; that consequence is knowingly accepted. (2) This rules on the CLI/plugin **credential channel** only. It does **not** repeal the API's agent-class-token → proposal rewrite (§4.11, T4.6), which no M18 task touches. If the ruling was meant to reach agent-class tokens themselves (letting them mutate truth directly), that is a separate and larger change to T4.6 / design invariant 3 and needs its own ruling — flagged here, not assumed. Tracked as T18.9.

## M18-B — May the daemon adopt files that lack `tm: 1` front matter? (gates T18.10)
- **Where:** `docs/mvp-spec.md` §4.7 ("files without it are never parsed for management, but `^tm-` anchors found in unmanaged files raise advisory lint `W_UNMANAGED_ANCHOR`"); `contract/linter.py`; `docs/spec-questions.md` §4.4 frozen schema (no room for a per-root flag without a spec change).
- **Narrowest reading taken:** no adoption. `setup` and `status` only *tell* the user that files become managed once their front matter contains `tm: 1`. Any adoption changes a sentence of §4.7 and writes to a file the user has not opted in.
- **Options for the ruling:** (a) hints only — in force; (b) adopt a file only when it contains an end-of-line `^tm-new` outside a fenced block (the user's own explicit mint request is the opt-in; logged, undoable, mints nothing itself); (c) a per-root opt-in flag (requires a schema column — a spec change).
- **Resolution:** resolved 2026-09-23 — the user ruled: auto-adoption is **deny-listed, not allow-listed**: every Markdown file under a sync root is tracked by default, and an ignore file opts paths out. Narrowest reading taken: (1) **file name** — the user wrote `akashaignore`; root `CLAUDE.md` rule 6 (rebrand invariant: the product name never appears in on-disk formats or config paths) makes the neutral **`.tmignore`** the default reading (a one-line rename if the user overrules rule 6 here). (2) **format** — gitignore-style patterns, one `.tmignore` at each sync root's top; built-in defaults deny `.obsidian/`, `.git/`, `.trash/`, `node_modules/` and every non-`.md` file; `.gitignore` is not consulted. (3) **"managed by default" means parsed, not rewritten** — the parser, linter and golden tests are untouched (adoption is a reconcile-layer shim that treats a non-ignored file lacking front matter as `tm: 1` in memory); a file with no contract constructs is never written, and the real `tm: 1` is added lazily on first projection (§4.7's own words: "added by the daemon on first projection"), never at adoption. (4) an ignored file's `^tm-` anchors keep raising the existing advisory `W_UNMANAGED_ANCHOR`. This amends §4.7's sentence "files without it are never parsed for management"; T18.10c owns that spec edit. Tracked as T18.10a–c.

## M18-C — `tm-daemon.pid`, detached spawn, and on-demand start: how far may the CLI go in starting a process the user did not explicitly start?
- **Where:** `src/akasha/daemon.py` (`serve`, single-instance lock `tm-daemon.lock`), `src/akasha/cli/main.py`.
- **Narrowest reading taken:** a pid file is an *operational* artifact (like the lock file), not persisted state — neutral name `tm-daemon.pid` beside the lock (rule 0.6), written only after the lock is held, removed in the same `finally`; it is not part of any §4.3 canonical format. On-demand start (T18.4) is limited to the **default local endpoint** (never an explicit `--base-url`/`AKASHA_BASE_URL`, never `--dry-run`), can be disabled with `AKASHA_NO_AUTOSTART`, and always prints a visible notice — the dogfood-plan §B rule that the daemon must not do things the user cannot see. It is *correct*, not merely convenient, because startup reconcile is idempotent (§4.8): edits made while the daemon was down are reconciled when it starts.
- **Resolution:** open.

## M18-D — §4.12's verb list names none of `up`, `down`, `setup`, `status`, `render`, `plugin install`; may they be added?
- **Where:** `docs/mvp-spec.md` §4.12; precedent `docs/spec-questions.md` T14.2 (verbs added for API-first parity).
- **Narrowest reading taken:** `up`/`down`/`setup`/`plugin install` are process/local-file verbs of the same class as `daemon`/`init`/`tray` (already not pure HTTP clients); `status`/`render` are read-only HTTP clients over existing endpoints (`/health`, `/sync/status`, `/review`, `/nodes/{id}`), exactly as `neighborhood`/`history` are. No endpoint is added. `render` never writes a file — embeds stay `![[path#^tm-id]]` link-form on disk (`contract/render.py`), so it is a *view*, not text propagation; mirroring an embed's text into other files would be a §4.7 grammar change adjacent to PRD F3 and is deliberately not proposed. Recorded follow-up (not built): bundling the built plugin `main.js` into the wheel needs a CI build-ordering decision.
- **Resolution:** open.

## M18-E — T18.1 must touch `kernel/store.py` (outside a packaging task's natural Files list)
- **Where:** `src/akasha/kernel/store.py` (`_migrations_dir`); `pyproject.toml` (`[tool.hatch.build.targets.wheel]`).
- **Narrowest reading taken:** same mechanical Files-list completion as T12.5. Migrations ship inside the package as `akasha/migrations` via a hatch `force-include` (the repo-root directory is **not** moved — golden fixtures, `build-exe.ps1`'s `--add-data` and every test resolve it), and `_migrations_dir()` gains one branch between the unchanged frozen branch and the unchanged repo-root fallback, so no existing caller's behavior changes. Verified 2026-09-23: the unmodified wheel has 0 `.sql` members.
- **Resolution:** resolved 2026-09-23 — landed in T18.1 as a mechanical completion (T12.5 precedent); the gate is green and `tests/integration/test_wheel_install.py` proves the built wheel migrates a fresh DB.

## M18-F — `W_UNMANAGED_ANCHOR` no longer reaches `GET /sync/status` (finding from T18.6)
- **Where:** `src/akasha/contract/linter.py` (`W_UNMANAGED_ANCHOR`), `src/akasha/sync/reconcile.py` (`_cycle`, `adopt_unmanaged`), `src/akasha/api/routes/sync.py` (`/sync/status`), `akasha status`.
- **Finding (checked empirically, 2026-09-23):** the advisory lint fires only when the pipeline parses an *unmanaged* file. Since T18.10c a non-ignored file with any `^tm-` anchor is adopted (parsed as managed), and an ignored file is not reconciled at all (T18.10b), so no path enqueues it any more — it cannot appear in `/sync/status`. The parser/linter still implement it and their unit tests still pin it (unmodified).
- **Narrowest reading taken:** `akasha status` says nothing about it, per T18.6 step 3; `sync/` untouched.
- **Resolution:** open — if a "you have anchors in an ignored file" notice is wanted, it is a new (small) task.

## M18-G — Two deviations from the T18.4/T18.9 wording taken while implementing them
- **Where:** `src/akasha/cli/main.py` (`main` callback, `_request`), `src/akasha/config.py`.
- **(1) T18.9 step 4 pulled into T18.4.** The default endpoint is the address in the *default config* (`http://bind:port`, equal to `DEFAULT_BASE_URL` when there is none), not the literal 7433. On-demand start (T18.4) is only correct — and only testable without colliding with a real daemon on 7433 — with it; it changes nothing for a user with no config.
- **(2) The saved token is used only for the default endpoint (narrowing of T18.9 step 3).** An explicit `--base-url`/`AKASHA_BASE_URL` never receives it: ruling M18-A approved the file as the credential for *the local daemon*, not a secret to be sent to whatever host is named on a command line. Such calls pass `--token`/`AKASHA_TOKEN`.
- **Resolution:** resolved 2026-09-23 — both are the narrowest readings that satisfy the tasks' own DoDs; revisit (2) only if the user wants the file honoured for named hosts too.

---

<!-- Entries below logged 2026-09-23 with docs/build-plan.md M19 (live
     transclusion). M19-0 is the user's ruling; M19-A/B/C pre-register the
     narrowest readings the M19 tasks must take. -->

## M19-0 — May text propagate between files that share an anchor (F3 exception)?
- **Where:** `docs/vision.md` §5 F3 ("Silent global propagation of edits"), F2; `docs/mvp-spec.md` §4.7 (`E_DUP_ID`: "same anchor twice in a sync root (copy without cut)"), §4.8; `src/akasha/sync/reconcile.py` (`ProjectionIndex`, `_compute_ops`).
- **Narrowest reading taken:** the user ruled (2026-09-23) that editing a transcluded block in one Markdown file must change the same text in the other file(s) as soon as possible, and (asked and answered) that **the same `^tm-id` anchor in several files is the declaration** — no new syntax. F3's stated reason is that a *parent* asserting something about a child cannot have its truth re-derived by a substitution engine. A mirror is not substitution into a different atom: it is **one atom shown more than once** (identity), its propagation is the patch-class rendering §6 already allows for within-facet edits (`SYNC_CHANGE_CLASS`), and interface breaks (facet breaks, retraction) still flag dependents unchanged. F3 is therefore *narrowed by a scoped exception*, not repealed — T19.1 edits the F3 row itself because §5 is normative. Consequence knowingly accepted: an accidental copy-paste of an anchored line silently becomes a mirror (detach with `^tm-new`); cross-file `E_DUP_ID` stops being a violation (single-file `E_DUP_ID` and its certain-repair are unchanged). The protected E05 fixtures/tests that pinned the old behavior are re-ruled by T19.3 only, by name. **Files-list completion (ratified T8.0/T8.1 rule), found by a late review at T19.1's landing:** `plugin-obsidian/TESTPLAN.md` §4b/pass criteria and `plugin-obsidian/src/clipboard.ts` comments (comment-only) told testers to expect an `E_DUP_ID` review on a cross-file copy, and `docs/user/dogfood-windows.md` carried the same manual-test row; all three were corrected as strictly entailed by T19.1's Goal. The plugin itself does not rewrite pasted anchors (`registerClipboard` is a documented no-op), so mirrors work with it enabled.
- **Resolution:** resolved 2026-09-23 — user ruling (syntax choice: "Same anchor = mirror").

## M19-A — The grammar is one line per block; what about multi-line "sections"?
- **Where:** `docs/mvp-spec.md` §4.7 (every block is exactly one line); `src/akasha/sync/reconcile.py` (`_body_line`, `E_UNPROJECTABLE_BODY`).
- **Narrowest reading taken:** mirrors work on **one-line blocks** (a task line or a one-line paragraph) — exactly the units the contract already round-trips. A multi-line section (several paragraphs, a heading with its body) cannot be a mirror: a hub body containing a newline is already unprojectable and is left as-is with one review item. Mirroring ranges would need a block-range grammar extension (start/end anchors) — a §4.7 change not proposed here.
- **Resolution:** resolved 2026-09-24 -- superseded by **M20-A/M20-B** (spans; multi-line spans are in scope).

## M19-B — Indentation and `composes` edges when a task is mirrored into a file with different nesting
- **Where:** `src/akasha/sync/reconcile.py` (`_compute_ops` `reparented`, `kernel_apply` `reparented`); §4.7 (indent ⇒ `composes`).
- **Narrowest reading taken:** `hub_state_for` substitutes only text and `task_state`, so each file keeps its own indentation and no existing code changes. A `reparented` op in *any* mirror retracts that file's old parent edge and creates its new one; the `composes` relation is a DAG (many parents allowed), so a task nested under P in A and under Q in B has both edges. Known limitation, logged not fixed: edges carry no per-file provenance, so if two files place the same node under the **same** parent, un-nesting it in one file retracts the shared edge for both. Tested by E24.
- **Resolution:** open.

## M19-C — When a file joins a mirror with text that differs from the hub, which side wins?
- **Where:** `src/akasha/sync/reconcile.py` (`kernel_apply` adopt path today commits the vault's text when it differs — correct for a *move*, unsafe for a *join*).
- **Narrowest reading taken:** on a **join** (another file already owns the anchor) **the hub wins**: the mirror is rewritten to the hub head and the file's differing version is preserved as a conflict branch with exactly one review item, reusing the existing `conflict_handler` — nothing is lost and nothing is silently guessed. Reason: the common divergence is a stale paste (the source was edited between copy and paste), and letting it overwrite the hub would regress the source file with no signal. Cost: a user who pastes and immediately edits the pasted line in the same save sees it rewritten plus a review item (this includes a cut-and-edit in one save when the destination reconciles before the source); their next ordinary edit propagates normally. A **move** (no other owner) keeps today's adopt behavior unchanged. This is the one behavior most likely to be reversed by the user; flipping it is a one-branch change in T19.4.
- **Resolution:** resolved 2026-09-24 -- revised by **M20-D** (stale paste: hub wins silently; new text with a newer file time: the new change wins).

## M19-D — A propagated cycle commits the other file's own edit: is it relayed onward?
- **Where:** `docs/mvp-spec.md` §4.8 ("the propagated cycle does not itself propagate further"); `src/akasha/sync/reconcile.py` (`Reconciler.on_change`); debug-plan D12.
- **Narrowest reading taken:** the sentence exists to stop ping-pong (A→B→A…). It was written for a propagated cycle that only writes the hub's text into the mirror. A propagated cycle can also **commit** something: if that file held its own unsaved-to-hub edit to a *different* mirrored line (two files edited within one debounce window), the three-way cycle commits it, and under the literal sentence it was then never relayed -- the two files stayed different forever, silently (found by an end-to-end run after a one-command `akasha setup`; two files edited 5 ms apart). Now `on_change` relays each node a propagated cycle **commits** to that node's other owners, and only those: a hub-to-file write-back commits nothing, so it is never relayed and the ping-pong the sentence guards against still cannot occur (`test_mirror_propagation_does_not_recurse_or_ping_pong` unchanged and green). A per-`on_change` cycle cap (`MAX_PROPAGATION_CYCLES`) is a runaway guard. Same-line concurrent edits are unchanged (M19-C/E22: one commit, one conflict, nothing lost). §4.8's sentence is amended to match.
- **Resolution:** resolved 2026-09-24 -- user ruling ("accept"): relay what a propagated cycle commits; §4.8 is amended accordingly.

## M20-F — Is an anchor glued to the text (no space before `^tm-id`) a lost anchor?
- **Where:** `docs/mvp-spec.md` §4.7 (`managed_par := text SP anchor EOL`); `src/akasha/contract/grammar.py` (`ANCHOR_EOL_RE`, `MANAGED_PAR_RE`, `TASK_LINE_RE`, `NEW_LINE_RE`, `NEW_MARKER_EOL_RE`); the `sandbox/init` run of 2026-09-24.
- **Narrowest reading taken:** the EBNF's mandatory `SP` was read literally, so typing at the end of a line (cursor before the anchor eats its space: `…text^tm-id`) made the block lose its anchor -- `E_LOST_ANCHOR`, then a pause of the whole file when that is more than 25 % of its blocks (four pauses in the sandbox). The space is now optional on read; `render` always emits the canonical single space, so the line is re-spaced by the next write-back: a silent repair with no violation, pause or review item. Applies equally to `^tm-new`.
- **Resolution:** resolved 2026-09-24 -- user ruling ("make the glued anchor a silent repair generally"). Tests: `tests/unit/contract/test_parser.py`, `tests/unit/sync/test_reconcile.py`. M20-A…E (spans, marker removal, join rule) are logged by task T20.1 of `docs/proposals/2026-09-24-refactor-spans-marker.md`.

<!-- M20 (2026-09-24): user rulings on transclusion spans, marker-less files, the join rule and
     pause removal. Plan: docs/proposals/2026-09-24-refactor-spans-marker.md; tasks: build-plan M20. -->

## M20-A — How is only part of a line, or several lines, transcluded? What is the id syntax?
- **Where:** `docs/mvp-spec.md` §4.7; `src/akasha/contract/grammar.py`.
- **Narrowest reading taken:** with no tokens the entire single line is shared (`text ^tm-id`, unchanged). Otherwise a **span**: `{TEXT}{tm-<id8>}`. The start/end tokens are named constants in `grammar.py` (`SPAN_OPEN`, `SPAN_CLOSE`, id wrapper), currently `{` and `}`; the id inside the braces is byte-identical to the `^` form (`tm-` + id8, checksummed), so `kernel.ids` is unchanged. Scan rule: balanced braces, closing at the first depth-0 `}` immediately followed by `{tm-<valid id8>}`; otherwise the `{` is literal. Spans do not nest. Obsidian shows the braces and id in reading view (an optional plugin decoration can dim them).
- **Resolution:** resolved 2026-09-24 -- user ruling ("braces are acceptable; id should be `{tm-...}`, the same format as `^tm-...`").

## M20-B — Are multi-line spans in scope for the first spans milestone?
- **Where:** `docs/mvp-spec.md` §4.7 (was: every block is one line); supersedes M19-A.
- **Narrowest reading taken:** yes. The open token may be on an earlier line than `}{tm-…}`; the hub body then contains newlines, which `E_UNPROJECTABLE_BODY` no longer rejects for spans. Scan is capped (200 lines / 64 KiB) so a stray `{` cannot make parsing quadratic. A damaged terminator makes the text prose again (ordinary delete rules).
- **Resolution:** resolved 2026-09-24 -- user ruling ("multi-line spans are in scope for the first spans milestone").

## M20-C — Is the `tm: 1` front-matter marker kept?
- **Where:** `docs/mvp-spec.md` §4.7 file-level rule; `contract/{parser,render,linter}.py`; `sync/reconcile.py::adopt_unmanaged`; `sync_files.contract_version`.
- **Narrowest reading taken:** removed entirely, no backward compatibility. The daemon never reads, writes or interprets front matter (an initial `---…---` block is skipped as raw lines). It served two purposes: a consent gate (redundant since M18-B: every non-ignored Markdown file is tracked and `.tmignore` opts out) and a grammar version stamp (now hub-owned). Protected tests and goldens that pin the marker are changed by T20.3 **by name** (see build-plan T20.3); nothing else. Risks accepted: a note that documents akasha and ends a line with ` ^tm-xxxxxxxx` is adopted (already true under M18-B; mitigated by fences and `.tmignore`); no per-file version means a future token change needs a hub-driven rewrite.
- **Resolution:** resolved 2026-09-24 -- user ruling ("remove `tm:1` and frontmatter edits entirely; push back only if critical" -- no critical objection found).

## M20-D — Which side wins when a file joins a mirror with differing text?
- **Where:** `docs/mvp-spec.md` §4.7 Mirrors rule (4); `sync/reconcile.py` (`_cycle`, `op.mirror`); revises M19-C.
- **Narrowest reading taken:** decided in this order: equal to the hub head ⇒ quiet; equal to an **earlier version in the node's history** ⇒ stale paste, hub wins, file rewritten, no review (timestamps cannot detect this: yesterday's text saved today has a fresh mtime); **new** text and the file was changed after the hub head's commit `ts` ⇒ the new change wins and propagates; new text but the hub head is newer, or the file time is unreliable (future mtime, conservative/cloud root) ⇒ the old M19-C behaviour (hub wins + conflict branch + one review).
- **Resolution:** resolved 2026-09-24 -- user ruling ("if a new paste has new changes, based on timestamps in the database, the new change should win") with the accepted history guard.

## M20-E — Is the whitespace inside a span's braces part of the shared text?
- **Where:** `docs/mvp-spec.md` §4.7 Spans; `contract/parser.py`, `contract/render.py`.
- **Narrowest reading taken:** no. The node body is the span text **trimmed** (`canonicalize_text` strips trailing whitespace per line, so storing padding would make render never equal the file). The whitespace just inside the braces is **per file**, like indentation: never added, removed or normalized by the daemon; a mirror keeps its own; a new `{text}{tm-new}` span gets none. Padding is typed by the user.
- **Resolution:** resolved 2026-09-24 -- user ruling ("no padding needed; padding must be manually added by the user"), with the per-file storage reading.

## M20-G — What replaces "pause & diff"? What does "resolve, else change the ID" mean per violation?
- **Where:** `docs/mvp-spec.md` §4.7 Violations, §4.8 pseudocode; PRD R11 (which adopted pause & diff) and F13 (no heuristic re-anchoring); `contract/linter.py` (`pause_and_diff`), `sync/reconcile.py`, battery E13.
- **Narrowest reading taken:** a file is never paused. F13 forbids re-attaching a damaged line to its old node by similarity, so "resolve" means the certain repairs; wherever a line's identity is ambiguous, "change the ID" = the line gets a **new node**:

  | Path | Handling |
  |---|---|
  | pause (> 25 % of blocks) | removed; per-block handling always applies |
  | `E_ID_CHECKSUM` | line byte-identical to a base block ⇒ that block's id is restored (certain, like an exact lost anchor; found by replaying the sandbox: a corrupted id on an unchanged mirror line must not detach it); otherwise a new node for the line, silently |
  | `E_UNKNOWN_ANCHOR` (well-formed, hub has never seen it) | **adopted under its own id** -- the assistant's reading of "resolve, else change the ID": re-minting would give each file's copy a different id after a hub reset or on a second machine, silently splitting every mirror and making two hubs re-id each other's files forever |
  | `E_DUP_ID`, no identical copy | copy identical to base keeps the id; the others get new nodes |
  | `E_LOST_ANCHOR`, exact | re-insert the anchor (certain repair, unchanged) |
  | `E_LOST_ANCHOR`, fuzzy | the line becomes a new node; the old node follows the ordinary delete rules (S0 deleted, S1+ reviewed) |
  | `E_DELETED_S1` | unchanged: a review, nothing deleted |

  Existing pause reviews are dismissed on the file's next cycle; `/sync/status` keeps `pauses` as an always-empty list. Conservative (cloud) roots keep routing repairs to review (unchanged; not covered by the ruling). **Design invariant 3 (machine never creates tracked truth):** the new node's text is the human's own line, already tracked under the old id; the daemon only assigns it an identity, exactly as it does for a human-typed `^tm-new`. **Consequence to be aware of:** a formatter storm that strips anchors *and* rewrites text hard-deletes that file's unmirrored S0 nodes (text survives as prose and in the new nodes; hub history for those nodes goes). Protected changes, by name, in T20.4: battery E13 (redefined, kept inside the silent-guess aggregate), golden `e13-pause-storm`, `tests/unit/contract/test_pause_and_diff.py`, the pause cases in `tests/golden/test_serialization.py`.
- **Resolution:** resolved 2026-09-24 -- user ruling ("do not pause a file: always seek to resolve as fast as possible, else change the ID"); the per-violation table is the assistant's reading of it, shown to the user.
