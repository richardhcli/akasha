# kb-io-bench v2: plan for a scalable, harness-agnostic information-IO testbench

Status: **proposal, not started.** It needs the user's go-ahead. v1 tier M/L runs are on hold
until then (STATUS "Next step" 3).

## 1. The question

After information has been **input** into a knowledge system, how well does an agent get it back
**out**: correct, cited, current, and cheap? How does that change as the store grows toward "all
of Wikipedia"? Any LLM should be able to wear any harness, and a default harness with standard
CRUD tools is the yardstick.

## 2. Closest existing benchmarks (checked 2026-09-27)

No single benchmark covers **agent write → fresh-agent read, at scale, with any model × any
harness**. The closest one on each axis:

| axis | closest existing | what it gives | what it lacks for us |
|---|---|---|---|
| any memory system × any benchmark × any judge, ingest→search→answer pipeline | **supermemory/memorybench** (MIT, TypeScript/bun, ~320 stars) | a provider interface `initialize / ingest / awaitIndexing / search / clear`; checkpointed stages; MemScore = accuracy / latency / context tokens | no tool-using agent: reading is one programmatic `search`, with results pasted into the answer prompt; some providers (filesystem = the MEMORY.md style, mem0) run an LLM *extraction* step at ingest, but nothing writes through tools; conversational data only (LoCoMo, LongMemEval, ConvoMem); ingest is bulk through the provider API, so the agent never writes; no update/delete probes; no scale ladder |
| any LLM × any retriever, agentic, fixed corpus | **BrowseComp-Plus** (ACL 2026, MIT) | about 100K human-verified documents; the retriever is a tool (a local tool, or a hosted MCP server); separates retriever from agent; accuracy, recall, search calls, calibration | read-only; one fixed corpus size |
| inject-then-query protocol | **MemoryAgentBench** (ICLR 2026, MIT; already a submodule) | chunked injection, then queries; adapters for Mem0, Letta, Cognee, HippoRAG, RAG, long-context; 4 competencies, including conflict resolution | text streams, not a knowledge base the agent edits; no harness × model factorial |
| scale of the memory itself | **BEAM** (ICLR 2026) | 128K → 10M-token conversations; 2,000 questions over 10 abilities, including knowledge update and contradiction | conversational; no agent writes; no tool harness |
| "all of Wikipedia" with provenance | **KILT** (MIT) | a fixed 2019-08-01 snapshot: 5,903,530 pages, 34.76 GiB, stable page ids and paragraph provenance; 11 datasets | a static read-only retrieval benchmark |
| edit payloads that defeat parametric memory | **MQuAKE-CF / CounterFact** | counterfactual (subject, relation, new object) edits, with multi-hop questions whose answers depend on the edit | built for model editing, not for external stores |

Related evaluation lessons: MemDelta (arXiv 2606.29914) shows that rankings flip with the answer
model and the embedder, so vary one factor at a time. The earlier kb-io-bench v1 review showed
that every harness miss was "the answer was never shown", so result presentation must be
controlled and reported.

**Recommendation:** build v2 as our own driver, and reuse the pieces above:
- KILT's snapshot as the scale corpus;
- MQuAKE-CF-style counterfactual edits as the probe facts;
- memorybench's provider stages as the bulk-ingest contract;
- BrowseComp-Plus's "the retriever is a tool" design, generalised to MCP.

Optional: a memorybench bridge (M8 below), so memorybench providers (Mem0, Zep, supermemory) can
be run as v2 harnesses with no rewrite.

## 3. How v2 differs from the current benchmark (v1)

| | v1 (current) | v2 |
|---|---|---|
| corpus | the personal vault plus mapped Wikipedia; tiers S (122 files), M (668), L (about 3–5k) | the **KILT Wikipedia** background in a nested ladder of 1e2 → 1e3 → 1e4 → 1e5 → 1e6 → 5.9M pages; the personal vault stays as an optional background |
| harness interface | Python adapters written for this experiment (akasha, basic-memory, files) | **any MCP server** (stdio or HTTP) plus a small `harness.toml` manifest; v1's adapters are wrapped as MCP servers |
| model interface | the Purdue endpoint only | **any OpenAI-compatible chat endpoint with native tool calls** (Purdue, vLLM, OpenRouter, a LiteLLM proxy for Anthropic/Gemini), set in `models.toml` |
| baseline harness | basic-memory, a grep file agent | a **default CRUD harness** (§5) as the yardstick; basic-memory, the MCP filesystem server and akasha as contenders |
| input path | agent writes only, into a small vault | **two paths**: harness-native bulk ingest for the background (no LLM, so scale is feasible), and agent-mediated CREATE/UPDATE/DELETE for a fixed probe set |
| operations | READ, WRITE, UPDATE | **CREATE, READ, UPDATE, DELETE** (retract, then the reader must abstain), plus multi-hop and aggregate reads |
| scoring | partly white-box (reads vault files: "landed", "copies updated") | **black-box first**: a fresh reader agent probes every operation; white-box only through an optional `export` tool |
| leakage control | perturbed Wikipedia facts; text-referential "unperturbed" questions | counterfactual and fictional probes only; a closed-book filter drops any probe a model answers without tools |
| statistics | pooled questions | the **task** is the unit; paired per family; cluster bootstrap |
| presentation | each harness shows results its own way | every harness gets the same result-size cap; the CRUD yardstick uses a match-window snippet format; schema tokens are reported separately; a "shown vs never shown" split is recorded for every miss |

**Carried over from v1 unchanged:**
- the tool-calling agent loop, with reasoning carry-back and malformed-call handling;
- the cached, throttled LLM client;
- the blind judge;
- the dash-robust normaliser and `rescore`;
- the analysis skeleton;
- the perturbation templates;
- the unattended runner (`agent/runner.py`).

## 4. Protocol for one scale rung N

1. **Background build (no LLM).** `harness ingest <corpus-dir>` loads the first N KILT pages,
   nested so that rung k ⊂ rung k+1.
   - Record wall time, storage bytes, and any LLM tokens the harness spends while ingesting
     (for example Mem0-style fact extraction).
   - A harness **drops off the ladder** when ingest exceeds its budget (default 12 h per rung),
     and that is reported as a result.
2. **Probe set P**, fixed across rungs: about 150 facts on entities that appear in rung 1e2 (so
   every rung contains them).
   - Kinds: MQuAKE-CF-style counterfactual edits and fictional attributes. Each carries a unique
     planted value.
   - Some facts are stored redundantly, in k places, to test propagation.
3. **CREATE.** The writer agent, with the harness's write tools, gets memos of probe facts and
   files them.
   - Control arm: the same facts bulk-inserted. This separates write-path losses from read-path
     losses.
4. **READ.** A fresh reader agent, with read-only tools, answers questions and cites source ids:
   - single-hop;
   - multi-hop (probe → background, background → probe);
   - aggregate;
   - unanswerable.
5. **UPDATE.** The writer changes a subset of facts. A fresh reader answers, and we score stale
   reads and redundant copies left stale.
6. **DELETE.** The writer retracts a subset. A fresh reader must abstain; a "zombie" read counts
   as a failure.
7. **Closed-book (CB) floor** on every question.

**Metrics:**
- per operation: accuracy (code-scored against the planted values, judge as fallback), citation
  precision/recall by id, abstention;
- cost: total tokens, net-of-schema tokens, tool calls, wall time, failures, malformed calls;
- misses split into shown vs never-shown;
- ingest cost per rung;
- everything plotted against N.

## 5. Default harness: `crud-kb` (the yardstick)

An MCP stdio server of about 400 lines, stdlib only, SQLite FTS5 with BM25. Optional local
embeddings (fastembed with bge-small) behind a flag, so we can see whether vectors matter.

| tool | behaviour |
|---|---|
| `create(title, body, tags?)` | returns `id` |
| `read(id, offset=0, limit=2000)` | body page with `next_offset` |
| `update(id, body?, old?, new?)` | full replace, or an exact substring patch; errors if `old` is not unique |
| `delete(id)` | tombstones the document; excluded from search and read |
| `search(query, k=10)` | `[{id, title, score, snippet}]`, where the snippet is a **±150-character window around the best match** |
| `list(prefix?, cursor?)` | titles and ids |
| `export()` | *(white-box only; never shown to agents)* every live document |
| CLI `crud-kb ingest <dir>` | bulk-loads `*.md` / `*.jsonl`, with no LLM |

Every harness in a run gets the same result cap (2,000 tokens) and the same read-only subset for
READ tasks. That subset is set per harness in `harness.toml`, as in v1's `READ_TOOLS`.

## 6. Interfaces: any LLM wears any harness

- `config/models.toml`: `name`, `base_url`, `api_key_env`, `model`, `max_tokens`, and
  `tool_mode = native | text`. A probe call before the run picks the mode.
  - **native**: OpenAI-style `tools`.
  - **text**: for models without native tool calling, such as llama3.3 on Purdue, which printed
    tool schemas as text in v1. The tool list goes into the system prompt, and the model replies
    with a fenced block, `{"tool": ..., "arguments": {...}}`. The loop parses it, executes it and
    returns the result as a user turn.
  - So "any LLM" means any chat model. The mode is recorded per run and reported, because text
    mode costs more tokens.
- `config/harnesses/<name>.toml`:
  - `mcp.command` / `mcp.url`;
  - `env`;
  - `ingest.command`;
  - `tools.read` / `tools.write` allowlists;
  - `reset.command`, which gives a fresh store per task snapshot.
- Command: `kbio run --model <m> --harness <h> --rung <N> --ops create,read,update,delete`.
  Results are cached per `(model, harness, rung, op, task)`, so adding a model or a harness runs
  only its own cells.
- Planned harnesses:
  - `crud-kb`;
  - `akasha-mcp` (v1's adapter wrapped as MCP; under `experiments/`, never `src/`);
  - `basic-memory` (MCP native);
  - the reference `mcp-filesystem` server (plain files);
  - `memorybench-bridge` (optional, M8).

## 7. Budget: the binding constraint

- The Purdue endpoint gives about **625 calls/hour** (the v1 pilot measurement).
- One rung, one model, one harness:
  - CREATE: 20 memos × ~15 calls = 300
  - READ: 100 questions × ~6 = 600
  - UPDATE: 20 × (~10 writer + ~6 reader) = 320
  - DELETE: 20 × ~14 = 280
  - judge ≈ 150
  - **total ≈ 1,650 calls ≈ 2.6 h**
- The full grid (6 rungs × 4 harnesses × 1 model) is about **63 h**. The default design trims
  it:
  - all operations at rungs 1e3, 1e5 and 5.9M;
  - READ only, on a 40-question subsample, at 1e2, 1e4 and 1e6;
  - which comes to about **30 h** for one model.
  - A second model is about +30 h, or runs on a paid endpoint in parallel.

**Ingest feasibility.** The first two are extrapolations; measure them in V2 before promising
anything.
- basic-memory with embeddings took about 0.5 s per file, so 1e4 is about 1.4 h and 1e5 about
  14 h. It probably drops off above 1e5.
- akasha `setup` took about 20 ms per file, so 5.9M is about 33 h, and millions of atoms is an
  untested store size.
- crud-kb with FTS5 should load 1e6 documents in minutes.

**Disk:** 196 GB free. KILT is 34.76 GiB, plus about 2× that for indexes per harness, which is
fine for one harness at a time. Keep one full-size store at a time and archive the rest.

## 8. Milestones

**Approval gate (2026-09-27):** V3–V7 are on hold until a human approves M8 (the v1 tier-S
results) and V1 (`crud-kb`). V2 may proceed. See STATUS.md, "Next step".

Each has a verification step; STATUS.md tracks them, as in v1.

| id | milestone | verification |
|---|---|---|
| V0 | Decision record: this plan approved; v1 tier S finished and analysed as the v1 result | user go-ahead; v1 S README section |
| V1 | `crud-kb` MCP server + `ingest` CLI + unit tests (CRUD semantics, tombstones, snippet window, pagination, cap) | pytest; an MCP round-trip from the v1 agent loop with a fake model |
| V2 | MCP harness adapter in kbio (`harness.toml` → tools; allowlists; reset); wrap v1 akasha and basic-memory; ingest benchmarks at 1e2/1e3/1e4 for each harness; the text tool-call mode (§6) | the same scripted CRUD sequence passes on every harness; an ingest-time table; llama3.3:70b completes a crud-kb READ task in text mode |
| V3 | KILT download (checksum), nested rung slicer (seeded, keeps probe-entity pages), corpus manifests | rung k ⊂ rung k+1 (checked); page-id manifest per rung |
| V4 | Probe generator: counterfactual and fictional facts on rung-1e2 entities, redundancy groups, CREATE/READ/UPDATE/DELETE tasks; CB filter | planted values unique corpus-wide; CB answers 0 planted values; 20% self-audit |
| V5 | Scoring: black-box probes for all 4 operations, task-clustered statistics, shown/never-shown split, net-of-schema tokens | re-scoring v1 S records gives the same numbers (a regression check) |
| V6 | Pilot: rung 1e3, crud-kb + akasha-mcp + basic-memory, 10 tasks per operation | pilot gate (≤20% failing per condition, as in v1 M7); calls/hour re-measured; budget restated |
| V7 | Ladder run (the trimmed design in §7) through `runner.py job start`; analysis and README | `summary.json`; scale curves; the offline re-analysis is deterministic |
| V8 | *Optional:* memorybench bridge (its `Provider` wrapped as a harness) and MemoryAgentBench AR slice | a table, or a written reason for skipping |

## 9. Risks

- Some Purdue models emit tool calls as text (llama3.3 did in v1). The probe routes them to
  `tool_mode = text` (§6). Only a model that fails both modes on the probe is excluded, with the
  reason recorded.
- Store sizes of 1e6 and above may break harnesses. Treat that as a finding and report the rung
  where each one drops out.
- KILT is from 2019. That's fine for a fixed background, because probes are synthetic anyway.
- Personal-vault content stays under `data/`. The KILT corpus is public, but it is large, so it
  also stays under `data/`.
