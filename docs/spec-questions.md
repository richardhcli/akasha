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
for the full ruling on each).

**Open questions: 3.** Every entry open as of M10's first code-complete
milestone (2026-07-19) has been triaged, resolved, and archived — see
`docs/archived-questions.md`. New ambiguities encountered during the
one-month dogfood gate or any future work should be logged here per the
entry format above.

## T11.1 — How does the very first human token get minted on a fresh DB, given `POST /v1/tokens` is `require_human`?
- **Where:** `src/akasha/api/routes/tokens.py` (`create_token`, `require_human`); `src/akasha/api/deps.py` (`require_human`); `docs/dogfood/README.md` step 6.
- **Narrowest reading taken:** Spec §4.11/§4.12 mark the whole `/tokens` row human-only, and there is no documented bootstrap endpoint or CLI flag for a brand-new DB with zero existing tokens. Treated this as a one-time pre-daemon operator/test-harness bootstrap step (an "embedded caller" per `store.connect`'s own docstring): mint one throwaway bootstrap token via a direct call into `kernel/store.py`'s `create_token` (never a second write path — still routed through `store.py` per rule 0.4, same pattern `tests/battery/soak.py:243` already uses), used solely to authorize the real `dogfood-smoke` token creation over genuine HTTP. This does not block T11.1 but is a real first-run UX gap for anyone standing up a fresh daemon without the test harness's direct-DB shortcut.
- **Resolution:** open.

## T11.1 — New sync roots are never scanned: `Watcher` has no production call site, and rescan/startup-reconcile only touch already-known files
- **Where:** `src/akasha/sync/watcher.py` (`Watcher` class); `src/akasha/daemon.py` (`serve()`); `src/akasha/api/app.py` (`create_app()`); `src/akasha/api/routes/sync.py` (`sync_rescan`); `src/akasha/sync/reconcile.py` (`reconcile_all`).
- **Narrowest reading taken:** `POST /v1/sync/roots` (`kernel/store.py::register_sync_root`) is a pure DB upsert with no filesystem walk. `Watcher` (debounce, cloud-path detection, `load_roots`) exists but is never instantiated in `daemon.py` or `api/app.py` (confirmed by grep — zero production call sites), despite `reconcile.py`'s own docstring claiming T5.6 wires `watcher = Watcher(conn, reconciler.on_change, ...)` in `daemon.serve`. Both startup `reconcile_all` and `POST /v1/sync/rescan` iterate only `store.list_sync_files` rows that already exist, never a fresh filesystem scan of a newly registered root. Empirically reproduced during T11.1: registering a root with 5 real `.md` files on disk, then calling `POST /v1/sync/rescan`, returned `{"files_reconciled": 0, "files_missing": 0, "reviews_open": 0}`, and `GET /v1/sync/status` still showed `"files": []` for that root. Does not block T11.1 (`violations: []` is still the literally-specified correct Verify result either way), but is a hard prerequisite gap for T11.2, whose step 2 relies on "the watcher" or `POST /v1/sync/rescan` detecting a hand-added `^tm-new` anchor — neither currently does anything for a root that has never been reconciled before. Left as visible prose in `docs/dogfood/README.md`'s "Known limitation" section, not buried in a comment.
- **Resolution:** Closed by T11.3 (run 20260725-064544-M11-discovery-wiring): added `reconcile.discover_untracked_files` (walks each registered `sync_roots.root_path` for `*.md` files with no `sync_files` row yet, spec §4.8's "run `on_change` for every managed file" read as not limited to already-tracked rows) and wired it into both `reconcile.reconcile_all` (startup/crash recovery) and `routes/sync.py::sync_rescan` (`POST /v1/sync/rescan`), so a freshly registered root's pre-existing files are discovered and reconciled on the very first call. `Watcher`'s absence from `daemon.serve()` (the live-event-listener half of this gap) is intentionally NOT addressed by T11.3 — out of scope for closing the startup/rescan discovery gap specifically; still open if a live (non-rescan-triggered) watch of a newly registered root is required. **See the dedicated T9.6 entry below** — this note's live-watch half is now a registered build-plan task rather than only prose here.

## T9.6 (registered 2026-07-26) — The live `Watcher` has zero production call sites; the daemon never detects a filesystem edit without a manual rescan
- **Where:** `src/akasha/sync/watcher.py` (`Watcher` class — complete, tested in isolation, T5.3); `src/akasha/daemon.py` (`serve()` — no `Watcher(...)` anywhere); `src/akasha/api/app.py` (same, confirmed by grep).
- **Narrowest reading taken:** N/A — this is not an implementation ambiguity resolved with a narrowest reading, it is a confirmed, verifiable gap between `mvp-spec.md`'s architecture diagram (§2: "Obsidian vault → watcher → sync/reconcile → kernel") and shipped code: `grep -n "Watcher(" src/akasha/daemon.py src/akasha/api/app.py` returns nothing. T5.4's own module docstring says "`.on_change(path)` matches `Watcher(on_cycle=...)` for T5.6", but T5.6 as actually built (`docs/agents/task-status.md`) only wired startup `reconcile_all`, never the live `Watcher` — no build-plan task ever claimed this wiring. Found via this project's established "audit spec vs shipped code, confirm a real production call site exists" procedure (the same method that found T10.2c, T9.2c, T9.3b, and T11.3's gap), prompted by the T11.1 entry above already flagging half of this same underlying fact. Today, a running daemon relies ENTIRELY on the one-time startup reconcile plus whatever manual `POST /v1/sync/rescan` calls a client makes — editing a vault file while the daemon is running produces no reaction at all until something explicitly triggers a rescan.
- **Registered as build-plan task T9.6** (`docs/build-plan.md`, M9) — see that task's Goal/Depends on/Files/Spec/Scope-narrowing/Steps/Verify/DoD for the full scoped fix, including the CRITICAL correctness pitfall (the watcher's `on_cycle` must be a single persistent `Reconciler`'s bound method, never a fresh one per event, or echo-suppression/cross-file-move-tracking silently breaks).
- **Resolution:** open — eligible for normal fleet-orchestrator dispatch once its `Depends on` are confirmed `DONE` (they all already are).
