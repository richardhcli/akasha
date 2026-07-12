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
full resolved history (M1: T1.3/T1.5/T1.6/T1.7; M3: T3.1/T3.2/T3.5/T3.6×2).

## T4.2 — audit_log INSERT: `store.py` (rule 0.4) vs. T4.2 Files list (`auth.py`/middleware)
- **Where:** `src/akasha/api/auth.py` (`record_mutation`, SPEC-QUESTION comment) → `src/akasha/kernel/store.py` (`append_audit`)
- **Narrowest reading taken:** The T4.2 Files list is "`src/akasha/api/auth.py` or middleware, `tests/unit/api/test_audit.py`" and does not list `kernel/store.py`, but non-negotiable rule 0.4 ("every mutation of persistent state goes through `kernel/store.py`; no other module writes SQLite directly") forbids INSERTing into `audit_log` from the API layer. Rule 0.4 is non-negotiable and controls, so the raw append-only INSERT is a minimal `store.append_audit` helper in `store.py`, and `auth.py` holds only the mutation-detection/recording policy (`record_mutation`, `MUTATING_METHODS`). This touches one file (`store.py`) beyond the Files list, but the alternative (direct INSERT in `auth.py`) would violate rule 0.4. Rule 0.8 says to log a SPEC-QUESTION when a task needs an unlisted file — hence this entry.
- **Resolution:** open (low-stakes: confirm the `store.py` touch is the intended reading of rule 0.4 for `audit_log`, or say audit is exempt from rule 0.4 and should INSERT directly from the API layer).

## T4.3 — `/health` path: literal `/health` vs. the "All under `/v1`" prefix rule
- **Where:** `src/akasha/api/app.py` (`create_app`, `@app.get("/health")`)
- **Narrowest reading taken:** §4.11's intro sentence says "All under `/v1`", but that same section's endpoint table writes the health cell as the literal `GET /health` (no `/v1`), and it is the one endpoint marked "no auth". Health/liveness probes are conventionally unversioned and unauthenticated, and the build-plan T4.3 wording also says "unauthenticated `/health`" (no prefix). Took the literal table path: `/health` is mounted at the root; the authenticated versioned routers (T4.4+) will mount under `/v1`.
- **Resolution:** open (low-stakes: confirm `/health` is intentionally exempt from the `/v1` prefix, or move it to `/v1/health`. Whichever is chosen becomes frozen once the T4.7 OpenAPI snapshot is taken, so worth confirming before T4.7).

## T4.4 — DB file path/name + single-shared-connection model (spec fixes neither)
- **Where:** `src/akasha/config.py` (`default_db_path`), `src/akasha/api/app.py` (`create_app`), `src/akasha/kernel/store.py` (`connect(check_same_thread=...)`)
- **Narrowest reading taken:** Spec §3 fixes the config dir (`tm-daemon/`) but never names the SQLite file. Chose `tm-daemon/store.db` — a neutral, product-name-free filename (rule 0.6). Connection model (user-approved default, AskUserQuestion 2026-07-12): ONE shared WAL connection on `app.state.conn`, opened with `check_same_thread=False` because Starlette runs sync routes in a threadpool; acceptable for a single-user localhost daemon issuing sequential requests (spec §3). All SQLite writes still route through `kernel/store.py` (rule 0.4).
- **Resolution:** open (low-stakes: confirm `store.db` filename and the single-shared-connection tradeoff, or specify a per-request connection / different filename before M5 sync builds on it).

## T4.4 — `POST /nodes/{id}/merge` request-body shape (spec lists only `merge_nodes(ids) -> redirect`)
- **Where:** `src/akasha/api/routes/nodes.py` (`MergeBody`, `merge_nodes` route)
- **Narrowest reading taken:** §4.11 shows `POST /nodes/{id}/merge` and §4.5 `merge_nodes(ids)` keeps `ids[0]` as survivor, but neither pins the HTTP body. Chose: path `{id}` is the survivor, body `{"ids": [other node ids]}`, and the route calls `store.merge_nodes([id, *ids])`. Mirrors `store.merge_nodes`'s first-id-survivor rule (already a resolved T1.6 narrowest reading) so the survivor is explicit in the URL.
- **Resolution:** open (low-stakes: confirm survivor = path id + body carries the others, or a different shape e.g. full `{"ids":[...]}` list in the body with survivor = ids[0]).

## T4.5 — `store.py` token/vault helpers despite not being in the Files list (rule 0.4 forces it)
- **Where:** `src/akasha/kernel/store.py` (`create_token`, `revoke_token`, `list_tokens`, `list_synced_vaults`)
- **Narrowest reading taken:** T4.5's Files list is `routes/{edges,search,tokens}.py, tests/integration/test_api.py` and omits `kernel/store.py`, but rule 0.4 forbids `routes/tokens.py` from writing the `tokens` table directly. Same precedent as the open T4.2 (`append_audit`) and T4.4 (`vet_node`) entries. Rule 0.4 controls; the additions are minimal (token create/revoke/list + one read-only vault-derivation helper). Raw secrets are never persisted or re-exposed (only `secret_hash` stored; bearer returned once at creation).
- **Resolution:** open (low-stakes: confirm the `store.py` touch is the intended reading, consistent with T4.2/T4.4 precedent).

## T4.6 — `store.py` `review_queue`/proposal helpers despite not being in the Files list (rule 0.4 forces it)
- **Where:** `src/akasha/kernel/store.py` (`enqueue_review`, `_mint_unique_review_id`, `mint_unassigned_node_id`, `get_edge_dst`)
- **Narrowest reading taken:** T4.6's Files list is `routes/*` (shared dependency), `tests/integration/test_api.py` and omits `kernel/store.py`, but rule 0.4 forbids `api/deps.py::mutation_gate` from INSERTing into `review_queue` directly. Same precedent as the open T4.2/T4.4/T4.5 entries above. Rule 0.4 controls; the additions are minimal (one INSERT helper + two small read-only lookups + one id-mint reuse, zero new schema).
- **Resolution:** open (low-stakes: confirm the `store.py` touch is the intended reading, consistent with T4.2/T4.4/T4.5 precedent).

## T4.6 — agent `POST /nodes` create-proposal: what goes in `review_queue.node_id` (schema has no payload column)
- **Where:** `src/akasha/kernel/store.py` (`mint_unassigned_node_id`), `src/akasha/api/routes/nodes.py` (`create_node`)
- **Narrowest reading taken:** `review_queue.node_id` is `NOT NULL` (frozen §4.4 DDL) and the table has no payload/content column, but an agent's proposed `POST /nodes` has no existing node to reference — the create is exactly what's pending human approval. Considered and rejected: (a) a sentinel string like `"__unassigned__"` — invents an ad hoc value outside the id8 format the rest of the schema/UI would expect to render/link; (b) actually creating the node and rolling it back — no transactional visibility across the request/response boundary that would make this safe and legible. Chose instead: mint a fresh id8 via the *existing* node-id scheme (`ids.mint()` + collision-check against `nodes.id`, i.e. reuse of `_mint_unique_id`'s exact logic, now exposed as `mint_unassigned_node_id`) so the id is guaranteed not to collide with any real node, record it as `review_queue.node_id`, but do **not** insert a `nodes` row for it. The proposed content itself (node_type/body/facets/etc.) is recorded separately in `cause_ref` as canonical JSON (`{"method", "path", "body"}` — see the next entry). Zero new id format, zero new schema (rule 2).
- **Resolution:** open (needs a human decision: is a "would-be id, not a real node" an acceptable `review_queue.node_id` semantics, or should the resolution flow instead mint+create the node eagerly at review-approval time with a *different* correlation mechanism? This affects how the future `/review/{id}/resolve` endpoint, M7's territory, must interpret a `cause_kind='proposal'` row's `node_id`).

## T4.6 — proposal payload storage: `cause_ref` = canonical JSON of `{method, path, body}` (schema has no dedicated payload column)
- **Where:** `src/akasha/api/deps.py` (`mutation_gate`)
- **Narrowest reading taken:** `review_queue` has a single nullable `cause_ref TEXT` column and no structured payload column. To make an agent's proposed mutation fully recoverable for a human reviewer (and for the future `/review/{id}/resolve` to actually apply it), stashed the serialized would-be request — `{"method": <HTTP verb>, "path": <request path>, "body": <parsed JSON body>}` — as canonical JSON (`kernel/canonical.canonical_json`; never pickle/eval/exec, rule 0.5) in `cause_ref`. This reuses the one column the frozen schema offers rather than adding a new one.
- **Resolution:** open (low-stakes: confirm this `{method, path, body}` shape is the intended "would-be request" encoding for a human/future reviewer to consume, or if a different shape — e.g. omitting `method`/`path` since the route is implied by which proposal-creating endpoint queued it — is preferred).

## T4.6 — `POST /edges` / `DELETE /edges/{id}` proposal target: `node_id` = `dst`
- **Where:** `src/akasha/api/routes/edges.py` (`create_edge`, `delete_edge`)
- **Narrowest reading taken:** An edge proposal has two candidate node ids (`src`/`dst`) and `review_queue.node_id` only holds one. Chose `dst`: spec §4.6's maturity model already treats a node's *inbound* live edges as the maturity-relevant signal (`store.create_edge`/`retract_edge` both recompute `dst`'s maturity, never `src`'s), so `dst` is the node whose review-relevant state the edge proposal would actually change if approved — the "narrowest defensible target" the task instructions asked for. For `DELETE /edges/{id}` the target edge's `dst` is looked up read-only via the new `store.get_edge_dst` before the gate runs.
- **Resolution:** open (low-stakes: confirm `dst` over `src`, or whether both ids should somehow be preserved — e.g. inside `cause_ref` alongside the request body, which they already are since the full edge payload is recorded there regardless of which one becomes `node_id`).

## T4.5 — `/vaults` has no dedicated table in the frozen §4.4 DDL (durability gap)
- **Where:** `src/akasha/api/routes/vaults.py` (module docstring has full reasoning), `src/akasha/kernel/store.py` (`list_synced_vaults`)
- **Narrowest reading taken:** §4.11 requires `GET/POST /vaults` (human-only ∅) but §4.4's DDL has no `vaults` table — only per-file `sync_files` (with a `vault` column). Adding `migrations/002_vaults.sql` was considered and REJECTED: it violates rule 2 (schema-freeze) and would fail the existing acceptance test `tests/unit/kernel/test_schema.py::test_no_unlisted_tables_beyond_spec_and_bookkeeping` (an unlisted file, outside T4.5's scope to alter). Implemented instead: `GET /vaults` = union of `sync_files.vault`-derived names (spec-sanctioned, durable) + a process-local in-memory registry populated by `POST /vaults` (mirrors `api/auth.py`'s rate-limiter precedent for acceptable non-persisted operational state). **Risk: a vault registered via `POST /vaults` does NOT survive a daemon restart** until a `sync_files` row exists for it (M5).
- **Resolution:** open — needs a human / M5-owner decision on whether a real `vaults` table belongs in T5.1 (base store) schema work, and whether `test_no_unlisted_tables_beyond_spec_and_bookkeeping` is then updated in that same scoped task. This is the one **not-low-stakes** M4 SPEC-QUESTION: it affects the durability contract of `/vaults` and should be resolved before the Obsidian plugin (M6) relies on vault registration surviving restarts.

## T4.8 — `akasha review` verbs call `/v1/review*`, which doesn't exist until T7.5
- **Where:** `src/akasha/cli/main.py` (module docstring, `review_list`/`review_resolve`)
- **Narrowest reading taken:** Spec §4.12 lists `akasha review [list|resolve ID RESOLUTION]` mapping to `GET /v1/review?status=open` / `POST /v1/review/{id}/resolve` (§4.11), but those routes are M7/T7.5 work and are not registered on the T4.4-era `create_app()` yet — calling them today gets FastAPI's own unregistered-route 404 (not the spec §4.11 `{"error":{...}}` envelope). Implemented the verb exactly against the documented endpoint shape (no invented alternative path/body) and made the CLI's generic error handler tolerate a non-envelope body: any HTTP 404 (envelope or not) still maps to exit code 3 via `_parse_error_body`'s fallback. `tests/integration/test_cli.py::test_review_list_against_unimplemented_endpoint_fails_gracefully` asserts only "does not crash with an unhandled traceback and exits non-zero" — it does NOT assert a successful round-trip, since the server side doesn't exist yet.
- **Resolution:** open (low-stakes: no code change expected once T7.5 lands `/v1/review*` for real — confirm the CLI's exit-code mapping, in particular whether `resolve`'s human-only-∅ 403 should surface differently from a generic error, once the real route exists).

## T4.8 — `set` verb's `--class` has no spec-given default
- **Where:** `src/akasha/cli/main.py` (`set_`, `ChangeClass` default)
- **Narrowest reading taken:** Spec §4.12 shows `akasha set ID [--body ...] [--class patch|minor|major] [--touch FACET]` as fully optional, but the API's `PATCH /nodes/{id}` body requires `change_class` (no server-side default, §4.11/`PatchNodeBody`). Defaulted `--class` to `patch` — the smallest/least-invalidating change class — when the flag is omitted, since that is the narrowest (least surprising, least TMS-invalidation-triggering) reading of "optional."
- **Resolution:** open (low-stakes: confirm `patch` is the intended default for an omitted `--class`, or that the CLI should instead require `--class` explicitly, i.e. treat its absence as a usage error).

## T4.8 — `--base-url` flag is CLI-only wiring, not one of spec §4.12's named flags
- **Where:** `src/akasha/cli/main.py` (`main` callback, `DEFAULT_BASE_URL`)
- **Narrowest reading taken:** Spec §4.12 names exactly three global flags (`--json`, `--dry-run`, `--token`) and never says how the CLI locates the daemon; §3 fixes the daemon's own default bind (`127.0.0.1:7433`). Added a fourth, spec-silent `--base-url` option (defaulting to that same `127.0.0.1:7433`) purely so the CLI can be pointed at a non-default/test daemon (needed for `tests/integration/test_cli.py`'s live-daemon fixture, which binds an ephemeral port). This mirrors the T4.3/T4.4 precedent of adding spec-silent plumbing (`/health` path, db path) rather than inventing schema/endpoint/grammar.
- **Resolution:** open (low-stakes: confirm `--base-url` as an acceptable CLI-only addition, or that base-URL discovery should instead come from `config.toml` only with no CLI override).
