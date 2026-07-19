You are a fleet-worker executing build-plan task **T10.2b — Contradiction surfacing at capture (story 2, non-LLM)**. This task was registered TODAY (2026-07-19) by a fable-model spec ruling after T10.3's acceptance audit found that PRD §8 story 2 was never built. **Read these three authoritative sources in full before touching anything** — they are more current and detailed than anything you might recall:
- `docs/build-plan.md`, the `### T10.2b — Contradiction surfacing at capture` entry (Goal/Depends/Files/Spec/Steps/Verify/DoD).
- `docs/mvp-spec.md` §4.11 — the `POST /nodes` row AND the **"Contradiction surfacing at capture"** definition paragraph directly under the endpoint table. That paragraph is the exact response contract; implement precisely it, nothing beyond it.
- `docs/spec-questions.md`, the two `## T10.3` entries (story-2 gap + citation drift) for the narrowest-reading rationale.

Nothing is implemented yet (confirmed via git status — start from scratch). Do NOT re-litigate the design; it is settled by the ruling.

## Settled design (do not re-open)
On the **human-token `POST /v1/nodes` 201 path only**, after the node is created, compute an additive response field `contradiction_candidates` (a list; `[]` unless the created node's `type == "claim"`). Candidates come from a **non-LLM FTS5 heuristic reusing the EXISTING `nodes_fts` index** — no new index, no new table, no embeddings, no model call (PRD §5 F-list: the truth path stays machine-free). The agent-token path stays a 202 proposal (T4.6) and never carries candidates.

## Files (touch ONLY these — one focused change, rule 0.8)
1. `src/akasha/kernel/store.py` — a new **read-only** helper (e.g. `find_contradiction_candidates(conn, node_id, body, *, limit=5)`), rule-0.4 completion per the T9.2/T10.2 precedent (T10.2 added `list_live_node_ids` the same way). No write verbs, no transaction, no new table/index.
2. `src/akasha/api/routes/nodes.py` — populate the field on the human 201 path in `create_node` (the agent branch returns `_proposal_response` unchanged).
3. `docs/api-snapshot/openapi.json` — regenerate in the SAME change via the sanctioned command (see below), since the 201 response schema changes.
4. `tests/integration/test_contradiction_surfacing.py` — new test file.

## Gotchas already investigated for you (use these; don't rediscover)
- **The store surface is confirmed present:** `store.find_live_edges(conn, src=..., edge_type="cites")` (store.py ~L1275, has `edge_type` param) for evidence lookup; node `status` column uses the literal `'live'`; `NodeType = Literal["entity","definition","claim","relation","proof","evidence","task"]` (kernel/model.py) — so `type == "claim"` and the evidence node types are real. `store.find_open_reviews(conn)` (~L297) is what your read-only proof test should snapshot before/after. `store.search(conn, q)` (~L1366) shows the `nodes_fts MATCH ... ORDER BY rank` pattern but passes `q` RAW — do NOT reuse it directly with a full body (see next bullet).
- **FTS5 syntax safety (critical):** feeding a raw claim body to `nodes_fts MATCH` throws `sqlite3.OperationalError` on quotes/operators/colons/hyphens. The spec already dictates the safe recipe: tokenize the **canonicalized** body to alphanumeric terms, FTS5-quote each term, OR-join them; an empty term set ⇒ return `[]` (no MATCH issued). Your FTS5-hostile-body test (a body of only punctuation / embedded quotes / FTS operators) must prove a 201 with a sane list, never a 500.
- **Self-exclusion:** the just-created node is already in `nodes_fts` (create_node inserts it). Exclude its own id from candidates.
- **Ranking:** bm25 rank; a byte-equal canonical body (exact duplicate) ranks first; cap 5; filter to `type='claim' AND status='live'`.
- **Evidence shape:** per the spec paragraph, each candidate is `{node_id, body, created_at, evidence: [{node_id, body}]}` where `evidence` lists the Evidence-type dst nodes of the candidate's live `cites` edges. Follow the spec paragraph's exact wording for what "Evidence-type" means; if it's ambiguous between `"evidence"` only vs `{"evidence","proof"}`, take the **narrowest** reading (literal `"evidence"`) and add a `# SPEC-QUESTION:` comment noting it — do not widen silently.
- **OpenAPI regen command:** `uv run python -m tests.integration.test_openapi_snapshot` (documented in that module's docstring). Run it after changing the route, then confirm `tests/integration/test_openapi_snapshot.py` passes. Diff must be additive-only.

## Required test coverage (make it non-vacuous — assert real content, not "no crash")
- Exact-duplicate claim ranks first and carries the existing claim's `body` text + `created_at` + its evidence (seed a claim with a live `cites` edge to an evidence node; assert the evidence body text appears).
- A near-duplicate (shares terms, not byte-equal) surfaces.
- Non-claim node create ⇒ `contradiction_candidates == []`.
- Agent-token create ⇒ still 202 proposal, no candidates field leakage.
- **Read-only proof:** `store.find_open_reviews(conn)` and node/commit state are byte-identical before vs after the 201 create's surfacing computation (mirror T10.2's read-only-gate test discipline — this proves the surfacing writes nothing).
- FTS5-syntax-hostile body ⇒ 201, sane candidate list, no 500.

## Non-negotiable rules (root CLAUDE.md)
Never invent schema/endpoints/grammar beyond `docs/mvp-spec.md` — here the field + heuristic are ALREADY spec-sanctioned by today's ruling, so implement exactly §4.11's paragraph, nothing more. All persistent writes go through `store.py` — this task adds ZERO writes (entirely read-only surfacing on top of the existing create). No new schema, index, or `cause_kind` (the enum is frozen). No `pickle`/`eval`/`exec`. Never edit golden files/fixtures. Touch only the 4 Files above (the OpenAPI snapshot is generated, not hand-edited).

Run the Verify command yourself before reporting DONE: `uv run pytest tests/integration/test_contradiction_surfacing.py tests/integration/test_openapi_snapshot.py`. Also run the full gate (`uv run ruff check src tests && uv run pyright src && uv run pytest tests/unit tests/property tests/integration -q`) and report its real result.

If you hit a genuine NEW ambiguity not covered by the ruling/gotchas, follow the standard procedure: `# SPEC-QUESTION:` comment, draft (don't write) a `docs/spec-questions.md` entry, and return `status: "BLOCKED"` with the draft in `spec_questions`. Do not guess past it.

If you have not reached a terminal status (DONE or BLOCKED) within ~30 tool calls, stop immediately and report `status: "BLOCKED"`, `blocked_reason: "possible hang — exceeded tool-call budget"`.

`files_changed` must be the real output of `git diff --name-only` + untracked files from `git status --porcelain`, not a guess. End your reply with a fenced ```json block containing exactly: status, files_changed, verify_command, verify_exit_code, verify_stdout_tail, spec_questions (array, empty if none), blocked_reason (only if BLOCKED).
