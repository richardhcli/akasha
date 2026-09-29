# M13 plan: akasha as AI memory, on an external benchmark with SOTA baselines

Status: **CONFIRMED by the user 2026-09-28 23:47; IN PROGRESS.** Rulings: see §7a (end of file).

Naming: this is kb-io-bench **M13**. `docs/build-plan.md` already uses M13–M21 for product
milestones, so the akasha code changes proposed here are a new build-plan milestone, **M22**
(§5).

## 1. Question

Is akasha competitive with SOTA memory systems at storing text an agent has been told, and at
answering from it later? Does its truth-maintenance machinery make it better where memory goes
stale?

Two claims, tested separately:
- **Competitive** (non-inferiority): akasha answers as well as the benchmark's own baselines, on a
  benchmark someone else built.
- **Better** (superiority): only where akasha offers something the others don't. That is
  conflicting and updated facts.

**Why not continue M12.**
- M12's file-tools half was measured, not predicted. Ap and Cp were both 35/35: grep plus edit
  keeps identical pasted copies consistent.
- The basic-memory half was unpredictable, and it was stopped at B 35/55, C 0.
- So M12 ends with **no verdict**. It also showed our own tasks were too easy to distinguish the
  KBs, which is why M13 uses someone else's benchmark.

## 2. Benchmark: MemoryAgentBench, Conflict Resolution (FactConsolidation)

- **Source:** Hu, Wang, McAuley, ICLR 2026. It is already a submodule
  (`third_party/MemoryAgentBench`); the data is public on HuggingFace (`ai-hyz/MemoryAgentBench`).
- **Why this one:** it is external, ships SOTA memory baselines (mem0, Letta, Cognee, Zep,
  HippoRAG-v2, RAPTOR, GraphRAG, BM25 and embedding RAG, long context), and its Conflict
  Resolution competence is the benchmark closest to truth maintenance.
- **The data:** 8 contexts, 100 questions each, 800 in total.
  - Single-hop (`sh`) and multi-hop (`mh`) at 6k, 32k, 64k and 262k tokens (26k to 1.1M chars).
  - Each context is a numbered list of atomic facts, e.g. "1. The chairperson of Fatah is Mahmoud
    Abbas." Later facts override earlier ones, and the larger serial number wins.
  - Answers are counterfactual (e.g. "The New York Times was written in Ancient Greek"), so the
    model can't answer from its own knowledge.
- **Metric:** `substring_exact_match`, the benchmark's own, scored by code.
- **Fit with the akasha vision:** each fact is one single-predicate claim, the content pillar 1
  admits. The prose-memory competencies (Accurate Retrieval, LongMemEval) raise the pillar-1/F2
  question (§7d) and come later.
- **Prior art to beat or match: `Knowl`** (`methods/knowl.py`, already in the submodule), a
  local-first SQLite memory engine.
  - It resolves conflicts at write time: a fact matching an earlier one's subject and relation
    retires the earlier record.
  - It has a published supersede-on/off ablation, with every other setting copied from the
    baselines (retrieve 10, temperature 0.7).
  - The akasha arm follows the same design. Knowl's and the paper's published numbers are copied
    from their tables during the pilot, not from memory. They used other models, so they are
    context only.

## 3. How akasha stores memory (no schema change)

The benchmark's memorize step delivers chunks of about 4k tokens.

| Benchmark input | akasha node | Why |
|---|---|---|
| a memorized chunk (the verbatim record of what the agent was told) | one **Evidence** node | Evidence is "external warrant: source, observation, quote-span" (§7.1); the raw record is kept, with provenance |
| each numbered fact in the chunk | one **Claim** node, the fact text including its serial number | single-predicate atom |
| fact → the chunk it came from | `cites` edge, claim → evidence, binding `*` | provenance; every answer can cite its source |
| a newer fact that conflicts with an older one | `contradicts` edge, newer → older, binding `*` | the conflict is recorded, not guessed away (F3) |
| supersession (arm K2 only) | older claim deleted with `redirect_to` [newer]: status `tombstone`, history kept | the edge above lifts the old claim to S1, so it is tombstoned, not hard-deleted |

**Detecting conflicts** uses akasha's own capture path.
1. `POST /v1/nodes` returns `contradiction_candidates`: live claims, bm25-ranked, up to 5.
2. One decision rule then accepts a candidate. The rule is fixed before any answer is seen and
   doesn't depend on this dataset: two claims conflict iff their normalized token sequences are
   identical except for one contiguous span at the end (the object slot), and that span differs.
3. Its precision is reported on a hand-audited sample of 100 accepted pairs. Recall is audited on
   100 random facts from the 6k context.
4. There is no LLM in the ingest path.

The adapter lives in `experiments/kb-io-bench/kbio/mab/`. It talks only to the scratch daemon (HOME
`data/.../scratch-home`, port 7534) over HTTP, with one fresh akasha store per context.

## 4. Conditions

All conditions use the same model: gpt-oss:120b on Purdue. Every call goes through `kbio.llm`, for
its throttle, cache and call log. MemoryAgentBench's `main.py` is not run, because it bypasses all
three. Its loader, chunking, templates, truncation and metric are imported, and the submodule is
not edited. Settings are copied from the benchmark's baselines, as Knowl did: retrieve 10,
temperature 0.7, buffer 200, the `factconsolidation` templates. `max_tokens` is set for gpt-oss
(their code sets it only for gpt-4 models), and this is checked in the pilot.

**Primary: single-shot retrieve-then-answer, 1 call per question.** This is how the benchmark scores
memory systems.

| id | memory | retrieval | role |
|---|---|---|---|
| LC | none: the whole context in the prompt, truncated like the benchmark | – | benchmark baseline |
| R-bm25 | plain text, their 4k chunks | BM25, top 10 chunks (their `Simple_rag_bm25`) | the benchmark's plaintext baseline |
| R-facts | plain text, one fact per line | BM25, top k facts | controls for storage granularity |
| **R-rule** | R-facts plus the **same conflict rule**, superseded facts dropped | BM25, top k facts | **decisive control**: the policy without akasha |
| K0 | akasha claims (§3), no conflict handling | akasha `/search`, any-term bm25, top k | akasha as a store |
| K1 | akasha plus `contradicts` edges | as K0, each hit shown with its newer contradicting claims | vision-faithful: conflicts surfaced with provenance, the reader decides |
| K2 | akasha plus supersession (tombstone and redirect) | as K0, live claims only | same as Knowl's published supersede-on arm |

k (facts) is set so the retrieved context has the same token budget as R-bm25's 10 chunks, and it
is fixed in the pre-registration.

**Secondary: agentic, on a subset** of 20 questions × 8 contexts = 160.
- P-agent: the kbio agent loop with grep/read over the facts as a plain markdown file.
- K2-agent: the same loop with akasha tools (search, get node, neighborhood).

**Optional, costed separately (§7e):** mem0, Letta or Cognee on the same model. They need an
embedding model and extra calls at ingest.

## 5. Proposed akasha changes: build-plan M22 (needs your approval, §7a)

akasha can already **store** memory text (`POST /v1/nodes`, any node type). What blocks it as a
memory backend is **retrieval**. Verified in the code, 2026-09-28:
1. **All-terms search.** `/v1/search` joins every term with AND (`_fts5_safe_match_query`), so a
   natural-language question ("Which sport is goaltender associated with?") matches nothing.
2. **Unbounded results.** It returns every match as a full node dump, with no limit or filter. At
   20k claims that makes the payload grow without bound.
3. **Tombstoned nodes are returned.** `delete_node` keeps their `nodes_fts` rows, and search has no
   status filter.
4. **Non-ASCII terms can't match.** Query terms are `[A-Za-z0-9]+`, so "pesäpallo" becomes "pes"
   and "pallo". The `unicode61` index holds "pesapallo" as one token, so it never matches. This
   also affects `find_contradiction_candidates`.

Each task below follows `docs/build-plan.md`'s template. Every task's Verify is `make check` and
`make battery`, and its Definition of Done is: the named test passes, the OpenAPI snapshot is
deliberately updated in the same change, and the spec text matches.

| task | change | files | test |
|---|---|---|---|
| T22.1 | `GET /v1/search`: `limit`, `mode=all\|any` (any = the OR-joined bm25 query `find_contradiction_candidates` already builds), `type=`, `status=live\|all`. Defaults keep today's behaviour. | `src/akasha/kernel/store.py`, `src/akasha/api/routes/search.py`, `docs/mvp-spec.md` §4.11, `docs/api-snapshot/openapi.json` | new `tests/integration/test_search_params.py` |
| T22.2 | Unicode query terms: tokenize like FTS5 `unicode61`, so accented words match. Fixes search and contradiction candidates. | `src/akasha/kernel/store.py` | new `tests/unit/test_fts_unicode.py` |
| T22.3 | CLI parity: `akasha search Q [--limit N] [--any] [--type T] [--live]` (API-first parity) | `src/akasha/cli/main.py` | extend the CLI search test |
| T22.4 | only if the pilot shows ingest is too slow: batch create | – | – |

- **No schema change.** Supersession uses the existing `contradicts` edge plus tombstone and redirect.
- **One SPEC-QUESTION** is logged: "is newer-fact supersession a legitimate use of
  tombstone/redirect, which the spec defines for refactors?"
- **Your ruling needed:** whether a dedicated `supersedes` edge type is ever wanted. The schema is
  frozen and additions are "guilty until proven necessary".
- **If M22 is not approved,** the adapter works around items 1–4 on the client side: keyword
  queries, slicing and status filtering done in the adapter, and ASCII folding. akasha is then
  measured as it is today, and item 1 will likely sink K0–K2 on natural-language questions.

## 6. Steps and budget

Measured throughput: 625–890 calls per hour. Long-context calls near 128k tokens are slower, and
may hit the 150 s timeout (checked in the pilot).

| step | work | LLM calls | time |
|---|---|---|---|
| 0 | M22 tasks T22.1–T22.3 (if approved), one focused commit each | 0 | about 2–3 h |
| 1 | Adapter plus ingest for all 8 contexts. Rule audit (100 pairs + 100 facts). Knowl and paper numbers copied from their tables. | 0 | about 3 h |
| 2 | Pilot: 6k sh + mh, 20 questions each, all 7 primary conditions. Checks: empty finals, `max_tokens`, timeouts, payload sizes, ingest time. | about 280 | about 30 min |
| 3 | `M13-PREREG.md`, hashed before the full run (as M12) | 0 | – |
| 4 | Full primary run: 7 conditions × 800 questions | about 5,600 | about 7–9 h |
| 5 | Secondary agentic subset: 2 × 160 questions × about 6 calls | about 1,900 | about 2.5–3 h |
| 6 | Analysis and README section | 0 | – |

Every job runs through the launcher (`runner.py job start`) with timestamped logs. At most one
daemon runs, on scratch port 7534.

**Pre-registration outline (step 3):**
- **Primary H1:** K2 > R-bm25 on single-hop Conflict Resolution, pooled over the 4 sizes. The test
  is a paired exact sign test per question, one-sided.
- **Decisive control H1c:** K2 vs R-rule. **Pre-registered reading:** if K2 ≈ R-rule, the claim is
  "akasha is a correct host for conflict handling, with history, provenance and redirects, and
  provides it at capture". It is not "akasha beats plaintext". Plaintext with the same custom rule
  does as well.
- **Multi-hop** is reported separately. Single-shot retrieval will probably fail it for every
  system.
- **Non-inferiority:** K0 vs R-facts, margin −0.05.
- **Vision check:** K1 vs K0. Does surfacing conflicts with provenance help the reader?
- **Holm correction** across the H1 family.

## 7. Decisions needed from you

- **(a) `src/` edits.** Does M22 (T22.1–T22.3) lift the standing "don't edit `src/`" rule for this
  work, under the CLAUDE.md process: build-plan tasks, task-status rows, spec text, the OpenAPI
  snapshot, `make check` and `make battery`?
- **(b) Human token.** The adapter holds the human token, so its writes skip the propose-only
  review path (PRD §7.11). It is disclosed as "benchmark mode: all captures pre-approved".
  Acceptable?
- **(c) Machine adjudication.** K2 supersedes facts automatically, which is machine adjudication.
  The vision reserves that for the human. Run K2 as a disclosed benchmark policy next to K1, which
  is vision-faithful? Or K1 only?
- **(d) Scope.** Conflict Resolution only for now (atomic facts, pillar-1 safe)? Accurate Retrieval
  and LongMemEval (prose memory stored as Evidence) would come later, after a ruling on pillar 1/F2.
- **(e) Heavyweight baselines.** Also run mem0, Letta or Cognee on the same model? This needs a
  local embedding model and roughly +2,000–5,000 calls. The default is no, and the paper's and
  Knowl's published numbers are cited as context.
- **(f) Budget.** About 7,800 LLM calls, roughly 10–12 h of Purdue time.
- **Hold.** The V3–V7 hold and the BLOCKED line are unaffected.

## 8. Risks

- **Single-shot retrieval with a question as the query.** Even with any-term bm25, stop-words and
  paraphrase weaken ranking. That applies equally to R-facts and R-rule, which is why they are the
  controls.
- **The conflict rule.** False supersessions delete true facts. Precision is audited and reported,
  and K1, which deletes nothing, is the safeguard.
- **Long context at 262k exceeds gpt-oss's 128k window.** It is truncated as the benchmark does, and
  reported as such.
- **One model, and counterfactual answers.** Results say nothing about other models. Published
  numbers are context only.
- **Expected outcome, stated now:** K2 ≈ Knowl ≈ R-rule on single-hop, and above R-bm25 and LC. Most
  of the gain comes from the supersession policy, which any store can implement. akasha's specific
  value is providing it at capture, with history and provenance. H1c is there to measure exactly
  that.

## 7a. User rulings (2026-09-28 23:47)

- **(a) Modify akasha's code:** yes. The M22 tasks follow the CLAUDE.md process: build-plan and
  task-status rows, spec text, the OpenAPI snapshot, `make check` and `make battery`.
- **(b) Human review is not needed by default.**
  - Narrowest implementation for M13: the harness writes with the human token.
  - Changing the product default for agent tokens overturns PRD §7.11 and spec §4.11 (agent =
    propose-only). It is logged as a SPEC-QUESTION for a separate task, not changed silently here.
- **(c) Conflicts.** A contradiction is flagged for human review, but an agent that is absolutely
  confident in the new fact (for example, in a testbench) may override the old fact directly.
  - Implementation: creating a `contradicts` edge enqueues a `contradiction` review item on the
    older node.
  - A new atomic `supersede` operation (edge, tombstone and redirect, and closing that review) is
    the override.
  - This amends the T10.2b rule ("surfacing enqueues no review item"): it still holds for mere
    capture candidates, and only an explicit `contradicts` edge enqueues a review.
  - K1 = flag only. K2 = override.
- **(d) "Prose memory" is a new node type, `journal`**, so it fits the vision. Memorized chunks are
  stored as `journal` nodes, not `evidence`.
- **(e) No mem0, Letta or Cognee runs.** Published numbers are cited for context.
- **(f) Budget:** 10–12 h accepted. Start now.
