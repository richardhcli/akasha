# Fleet Worker Task Assignment — T11.3

You are a `fleet-worker-claude` (pure-Claude executor — direct edits only,
never invoke `scripts/fleet/cursor_bridge.py` or any Cursor subprocess
under any circumstance, whatever your persona file otherwise says about
Cursor delegation).

## Task ID
T11.3 — Wire filesystem discovery for newly registered sync roots

## Goal
Close the gap T11.1 surfaced and logged in `docs/spec-questions.md`:
`POST /v1/sync/roots` is a pure DB upsert with no filesystem walk, and both
`reconcile.reconcile_all` (daemon startup) and `POST /v1/sync/rescan` only
iterate already-known `store.list_sync_files` rows — never a root's actual
directory. A brand-new root with real, pre-existing `.md` files on disk
therefore shows `files_reconciled: 0` / `"files": []` forever, because
nothing ever calls `Reconciler.on_change` on a path the store has never
seen before. Add discovery so both entry points also pick up files that
exist on disk but have no `sync_files` row yet.

## Depends on
T11.1 (DONE — run 20260725-030653-M11)

## Files (touch only these — do not add a third test file, do not touch anything else)
- `src/akasha/sync/reconcile.py`
- `src/akasha/api/routes/sync.py`
- `tests/integration/test_crash_recovery.py` (extend with the `reconcile_all` discovery test)
- `tests/integration/test_api.py` (extend with the `sync_rescan` discovery test)

**Exception, explicitly authorized (do not treat as scope creep or log a
SPEC-QUESTION for it):** this task's DoD (below) requires appending a
**Resolution:** line to the existing T11.1-era entry in
`docs/spec-questions.md` for this exact gap. That file is the repo-wide
ambiguity log every task is entitled to append to (CLAUDE.md rule 2) — edit
it to add the Resolution line only; do not delete or rewrite the existing
entry, and do not otherwise touch this file. Since the actual commit hash
doesn't exist yet at the time you write it, phrase the Resolution as
pointing at task **T11.3**, run **20260725-064544-M11-discovery-wiring**
(not a commit hash you cannot know).

## Spec
§4.8 "Startup: run `on_change` for every managed file (idempotent — this is
also crash recovery)" and §4.11 `POST /sync/rescan`; neither text limits
"every managed file" to rows already in `sync_files`, so walking each
registered root's directory for files `on_change` has never seen is the
narrowest reading that makes startup/rescan match what the spec prose
actually says, not a new endpoint or schema (rule 2).

## Steps
1. In `reconcile.py`, add a helper that, for each row from
   `store.list_sync_roots(conn)`, walks `Path(root["root_path"]).rglob("*.md")`
   — the same idiom `Reconciler` already uses internally (see its "other
   `*.md` file under the same sync root" conflict-candidate scan) — and
   yields any absolute path not already present in
   `{f["path"] for f in store.list_sync_files(conn)}`.
2. In `reconcile_all`, call this helper and run `reconciler.on_change(path)`
   on each newly discovered path exactly like the existing known-file loop
   (same try/except `FileNotFoundError` handling, same
   `files_reconciled`/`files_missing` counters — a file that vanishes
   between the walk and the read is not a crash).
3. Apply the same discovery step to `routes/sync.py`'s `sync_rescan`.
   **Default: duplicate the walk-and-append step there rather than
   refactoring `sync_rescan` to call `reconcile.reconcile_all` directly.**
   Only take the refactor path if you can first prove the endpoint's
   response schema (whatever it returns today, byte-for-byte) is
   unaffected — the OpenAPI snapshot is in the sacred/do-not-break list
   (CLAUDE.md rule 3 territory), and a refactor is much likelier to
   accidentally shift the response shape than a duplicated loop is. If in
   doubt, duplicate.
4. Do not touch `sync/watcher.py` — `Watcher` is a live-event listener, not
   a startup/rescan discovery mechanism, and wiring it into `daemon.serve()`
   is a separate concern this task does not need to touch to close the
   T11.1 gap.
5. Add a test reproducing T11.1's exact empirical repro: register a sync
   root pointing at a tmp dir containing pre-existing `.md` files never
   passed through `on_change`, call `reconcile_all` (or hit
   `POST /sync/rescan`), and assert `files_reconciled` now counts them and
   `store.list_sync_files` has rows for them — **plus** a second, separate
   test confirming a second call is idempotent (no duplicate `sync_files`
   rows, no duplicate node mints for unchanged content). Both tests are
   required; a single combined test is not sufficient for the DoD below.

## Verify
```
uv run pytest tests/integration/test_crash_recovery.py tests/integration/test_api.py
uv run ruff check src tests
uv run pyright src
uv run pytest tests/unit tests/property
uv run pytest tests/battery
```
(equivalently `make check && make battery` — both work, `make` is present
in this sandbox.)

## DoD
Registering a sync root against a directory with pre-existing `.md` files,
then calling `reconcile_all` or `POST /v1/sync/rescan`, discovers and
reconciles those files on the very first call (not just after a live
watcher event fires on each one individually); the T11.1-era
`docs/spec-questions.md` entry for this gap is updated with **Resolution:**
pointing at T11.3 / run 20260725-064544-M11-discovery-wiring (see Files
exception above).

## Non-negotiable rules (verbatim, CLAUDE.md)
1. Work in dependency order — already satisfied (T11.1 DONE).
2. Never invent schema, endpoints, ID formats, or grammar beyond
   `docs/mvp-spec.md`. Narrowest reading; `# SPEC-QUESTION:` + log entry on
   ambiguity.
3. Never edit golden files/fixtures/acceptance tests
   (`tests/golden/**`) to make an implementation pass.
4. Every mutation of persistent state goes through
   `src/akasha/kernel/store.py` — no other module writes SQLite directly.
5. All persisted bytes obey canonicalization (§4.3). `pickle`/`eval`/`exec`
   forbidden everywhere.
6. Product name never appears in on-disk formats/anchors/config
   paths/schema identifiers — neutral prefix `tm`.
7. Run `make check` (and `make battery`, this is M11) before considering
   done.
8. One task = one focused change — touch only the Files list above (plus
   the explicitly authorized `docs/spec-questions.md` exception).
9. Not `DONE` until Verify passes locally. If it fails, stays `IN
   PROGRESS` — do not weaken the test or move on.

## Hang guard
If you have not converged within roughly 30 tool calls, stop and return
`status: "BLOCKED"`, `blocked_reason: "possible hang — exceeded tool-call
budget"`.

## Return Value
End your reply with a fenced ```json block matching `WORKER_SCHEMA` per
`.claude/agents/fleet-worker.md` / `docs/agents/fleet-workflow.js`:
`status`, `files_changed`, `verify_command`, `verify_exit_code`,
`verify_stdout_tail`, `spec_questions` (empty array if none),
`blocked_reason` (only if BLOCKED).
