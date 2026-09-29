# M13 pre-registration: akasha as AI memory on MemoryAgentBench Conflict Resolution

Written 2026-09-29 00:08 EDT, after the pilots and before the full run. The plan and the user's
rulings are in `M13-PLAN.md` §7a. The sha256 of this file is recorded in STATUS.md (M13) before
the job starts. Any later change is an amendment, logged in STATUS with its reason and reported
with the results.

## Pilot disclosure

The pilots were validity checks. Their outputs were visible in the job logs, and they did not set
any hypothesis.

- **Baselines** (LC, R-bm25, R-facts, R-rule): the first 20 questions of `sh_6k` and `mh_6k`.
- **akasha** (K0, K1, K2): the same 40 questions, plus the three 6k ingests.
- **One long-context call** on `sh_262k` q0. It found Purdue's real context limit, below.

Pilot responses sit in the response cache, and the full run replays them. They are included in
the analysis, and a sensitivity analysis excludes them (the `sensitivity_nopilot` block).

Changes made because of the pilots:
1. **Long-context limit is 62,000 tokens.** Purdue serves gpt-oss:120b with a 65,536-token limit
   (HTTP 400 at 120,252 tokens). 62,000 leaves room for the system prompt, the query and
   `max_tokens`. Truncation follows the benchmark's own rule (keep the last tokens).
2. **Fact-to-chunk mapping.** The benchmark's sentence splitter can break a fact across two
   memorize chunks. akasha therefore assigns each fact to the chunk where it starts. The chunk
   text is unchanged.
3. **The conflict rule was tightened** after a precision audit, before any answer was scored.
   - First sample: 24 of 25 accepted pairs were correct.
   - The false positive was "Hun Sen is a citizen of …" vs "Hun Sen is married to …".
   - Rule added: the shared prefix must be at least half of the longer fact.
   - Second sample (seed 7): 50 of 50 correct.
   - Pairs accepted: 156 / 804 / 1,614 / 6,720 at 6k / 32k / 64k / 262k.
4. **akasha's own capture candidates** (top-5 live claims) found 154 (K1) and 155 (K2) of the
   plaintext rule's 156 pairs at 6k.
5. **akasha retrieval text is stripped** of its canonical trailing newline, so the K prompts
   match the R-facts prompts byte for byte in layout. K pilot responses are therefore not
   replayed; they are re-asked in the full run.

## Fixed inputs

**Data.** MemoryAgentBench `Conflict_Resolution` (HuggingFace `ai-hyz/MemoryAgentBench`): 8
sub-datasets (`factconsolidation_{sh,mh}_{6k,32k,64k,262k}`) × 100 questions = 800. Exported with
the benchmark's own `ConversationCreator` (chunk size 4,096, its templates) by `kbio/mab/mab_export.py`,
with one change: a fixed memorize timestamp. sha256 prefixes of `data/.../mab/export/`:

| file | sha256 prefix |
|---|---|
| mh_262k | 8739b4b888d7fb48 |
| mh_32k | 3663b89dc5de0864 |
| mh_64k | bb113117570a5266 |
| mh_6k | 8a9ec6501b518070 |
| sh_262k | 63ac6cd3fad46fa6 |
| sh_32k | 7ede68c24984b20e |
| sh_64k | f335862259d57b09 |
| sh_6k | d02bed84455fa994 |

**Code** (sha256 prefix):

| file | sha256 prefix |
|---|---|
| `kbio/mab/run.py` | 54be3a647614794e |
| `conflict.py` | 19e08aa1386a04c6 |
| `akasha_mem.py` | cee2399d35e5b62f |
| `analyze.py` | 074655540df1ed29 |
| `mab_score.py` | 7759f76d71a86ab8 |
| `mab_export.py` | 02913256faa97e50 |

**akasha.** Build-plan M22 (T22.1–T22.5) is not committed: `git diff -- src` sha256 prefix
0dd806554b520bd0, 6 files, +242/−56. `make check` passes 897 tests and `make battery` 58. The
scratch daemon runs this working tree (editable install).

**Model and settings:**
- **Model:** gpt-oss:120b on Purdue GenAI, through `kbio.llm` (throttle, cache, call log).
- **Copied from the benchmark's baselines:** temperature 0.7, retrieve 10, its system message and
  query templates, its RAG prompt ("Memory i:" blocks), and BM25 as LangChain's (`BM25Okapi` over
  `text.split()`, `get_top_n`).
- **Our choice:** `max_tokens` 2,000, because gpt-oss reasons before it answers.
- **One call per question, no retry.** An empty answer scores 0; in the pilot, empties were
  reasoning-only answers on multi-hop questions.
- **Outages:** a call that fails after the client's 7 retries is FAILED and scores 0. The v1
  outage rule applies: an outage artefact is moved aside and rerun once.
- **Metric:** the benchmark's own `default_post_process` (`substring_exact_match`, maximum of the
  raw and the parsed output), run by `mab_score.py` inside the benchmark's code.

## Conditions (1 call per question; 7 × 800 = 5,600 calls)

| id | memory | retrieval |
|---|---|---|
| LC | the memorize-wrapped context in the prompt, truncated to 62,000 tokens (benchmark rule) | – |
| R-bm25 | plain text, the benchmark's 4k chunks | BM25, top 10 chunks (the benchmark's `Simple_rag_bm25`) |
| R-facts | plain text, one fact per document | BM25, top 10 facts |
| R-rule | R-facts after the plaintext conflict rule retires superseded facts | BM25, top 10 facts |
| K0 | akasha: `journal` per chunk, `claim` per fact, claim `cites` journal | `/v1/search?mode=any&limit=10&type=claim&status=live` |
| K1 | K0 plus a `contradicts` edge (new → old) for each capture candidate the rule accepts, which flags the old claim for review | as K0; each hit is followed by `[contradicted by newer: …]` listing its live contradictors |
| K2 | K0 plus `supersede` (old tombstoned with a redirect to new) for the same pairs | as K0 (live claims only) |

Every condition gets the same query as its retrieval query: the benchmark's RAG query text after
"Now Answer the Question:". The retrieved items fill the same "Memory i:" prompt.

## Hypotheses and tests (`kbio/mab/analyze.py`)

- **Unit:** the question. Each test is a paired exact sign test on discordant questions.
- **Primary family:** single-hop, pooled over the 4 sizes (400 questions). Holm correction across
  H1–H3, α = 0.05.

| test | claim | test | role |
|---|---|---|---|
| **H1** | K2 > R-bm25 | one-sided | akasha memory beats the benchmark's plaintext RAG baseline |
| **H2** | K1 > K0 | one-sided | surfacing contradictions with provenance helps the reader (the vision's claim) |
| **H3** | K2 > LC | one-sided | beats putting the whole history in the prompt |
| **H1c** | K2 vs R-rule | two-sided, not in the Holm family | the **decisive control** |
| **N1** | K0 vs R-facts | non-inferiority | the lower bound of the 95% bootstrap CI of the difference is > −0.05 |

**Pre-registered reading of H1c:**
- **K2 ≈ R-rule** (p ≥ 0.05): "akasha is a correct host for conflict handling, providing it at
  capture with history, provenance and review. It is not better than plaintext running the same
  rule."
- **K2 > R-rule:** akasha adds value beyond the rule.
- **K2 < R-rule:** plaintext with the same rule does better.

The verdict line printed by `analyze.py` follows exactly these branches.

**Multi-hop** is reported with the same comparisons as descriptive results only; no claim is made.
Single-shot retrieval is expected to fail it for every system.

**Reported alongside, descriptive only:**
- per-size accuracy per condition;
- ingest statistics (conflicts found, reviews opened, supersessions, ingest seconds);
- published numbers from the MemoryAgentBench paper and Knowl, which used other models, so they
  are context only.

## Allowed wording

- **H1 fails:** "akasha did not beat the benchmark's BM25 baseline."
- **H1 holds:** "akasha memory beat the benchmark's plaintext RAG baseline on single-hop Conflict
  Resolution (gpt-oss:120b)." The H1c reading must follow in the same paragraph, whichever branch
  it is.
- **Never claim:** results for other models or other benchmark competencies, or "better than
  plaintext" unless H1c shows K2 > R-rule.

## Secondary (not part of the claim)

The agentic subset from the plan: P-agent (grep/read over the facts as a markdown file) vs
K2-agent (akasha search, get node and neighborhood), same loop, 20 questions × 8 sub-datasets.
Its tool descriptions will be fixed by an amendment before it runs. It is reported descriptively.

## Conduct

- One launcher job, `mab`: baselines and K conditions on single-hop, then multi-hop. LC runs last
  in each half.
- One scratch daemon on port 7534. akasha stores are built once per (condition, size) and restored
  for querying.
- Interim looks check validity only: 0 FAILED, the model id sent, completeness.

## Amendment 1 (2026-09-29 00:19 EDT, before any agentic run; the primary run is unaffected)

This fixes the secondary agentic subset. It is still descriptive only.

- **Code:** `kbio/mab/agentic.py`, sha256 prefix 1dd31af7b20354f3.
- **Loop:** the kbio agent loop (`run_agent`): 12-step cap, 2,000-token tool-result cap,
  temperature 0 (the kbio loop default), `max_tokens` 2,000.
- **Messages:** the benchmark's system message. The user message is the benchmark's RAG query
  plus "The knowledge pool is not in this message: use your tools to look facts up. When you are
  done, reply with only the answer."
- **A-P (plaintext):** `list_dir`, `grep` and `read_file` (kbio FilesHarness, including its
  number-prefix path tolerance) over one file, `facts.md`, holding the benchmark's fact list
  verbatim.
- **A-K2 (akasha K2 store):** two tools.
  - `search(query)`: "Search the memory for facts matching any of the words in `query`;
    returns up to 10 current facts, best match first, as `id: text`." It calls
    `/v1/search?mode=any&limit=10&type=claim&status=live`.
  - `neighborhood(id)`: "The nodes linked to a fact `id`: the journal entry it was learned
    from, and any facts that contradict it." It shows each edge type, node type, status and
    up to 300 characters of the body.
- **Questions:** 20 per sub-dataset, `random.Random(13).sample(range(100), 20)`, 160 in total.
- **Scoring:** the benchmark metric via `mab_score.py`.
- **Report:** accuracy per condition and sub-dataset, and a paired sign test of A-K2 vs A-P
  (two-sided), descriptive.
- **When it runs:** after job `mab` exits 0, as job `mabagent`. It needs the daemon with the K2
  store, so it can't run alongside `mab`.
