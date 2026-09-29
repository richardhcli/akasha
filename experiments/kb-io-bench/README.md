# kb-io-bench: writing into and reading back from a large personal knowledge base

<!-- SUMMARY: filled in when every milestone is done -->

Question: when an agent has to **file** new facts into a personal knowledge base, **update** a
fact that is copied in several notes, and **answer** questions from it later with a fresh
context, does the akasha stack help? The benchmark separates two things: the harness (the tools
the agent gets) and the knowledge system (what stores the data).

| id | harness | knowledge system |
|---|---|---|
| A | akasha harness: node search, get, neighborhood, read note, write atom, transclude, edit-once | akasha vault + scratch akasha daemon |
| B | basic-memory MCP tools (the SOTA baseline) | the same akasha vault + daemon |
| C | basic-memory MCP tools | the regular vault, no daemon |
| C′ (`Cp`) | plain `list_dir`/`grep`/`read_file`/`write_file`/`edit_file`, in the style of Claude Code | the regular vault |
| CB | closed book: no tools, no knowledge base | none |

A vs B is the harness effect with the knowledge system fixed. B vs C is the knowledge-system
effect with the harness fixed. A vs C is the whole stack.

## Where things live

This is the same split as `experiments/concepts-retrieval`: personal-vault content never goes
into git.

- **Tracked (`experiments/kb-io-bench/`)**: `kbio/` (the package), `config/` (tiers, Wikipedia
  overrides), `tests/`, `wiki-manifest.json`, `results/` (aggregates only, ids and numbers), and
  `PLAN.md`/`STATUS.md`. Two submodules are pinned under `third_party/`:
  - basic-memory at v0.23.2 (AGPL-3.0; run as a separate process, never vendored)
  - MemoryAgentBench
- **Local only (`data/experiments/kb-io-bench/`)**:
  - the corpora for each tier
  - fetched Wikipedia text and perturbations
  - the tasks (they quote personal notes) and the hand audit
  - every result JSON (transcripts, vault diffs), the LLM cache and the per-question reports
  - the scratch HOME and logs

## Pipeline (`uv run kbio <command>`)

1. `wiki`: map the 432 concept notes to English Wikipedia by exact lookup, not search.
   - A note matches when its title with any "(concept)" suffix removed *is* a page, or redirects
     to one, and that page is not a disambiguation page.
   - `config/wiki-overrides.toml` holds 37 hand fixes.
   - Pages are fetched pinned to a revision id, at most 1 request/s, and every response is
     cached.
2. `corpus <tier>`: build the regular and the akasha vault of a tier (see below).
3. `gate <tier>`: the akasha compatibility gate, run through the real CLI. It uses a scratch HOME
   and port 7534, never the real daemon.
4. `tasks S`: generate and verify the tasks. They are written on tier S and stay answerable at
   every larger tier, because the tiers are nested.
5. `run --tier T --conditions A,B,C,Cp,CB --model M --tasks read,write,update`: resumable at
   task granularity.
6. `analyze --tiers S,M,L`: offline and deterministic. It reads only the result JSONs.

## Corpus

- **Concept notes**: `clean_note` from concepts-retrieval, the same text in both vaults.
- **Wikipedia pages**: `Wikipedia/<Title>.md`. Each has a source line (URL, revision, CC BY-SA),
  the lead sentence as its own paragraph, and `## Related pages` wikilinks to the other pages in
  the tier that it links to.
- **Augmentation**: each mapped concept note gets a `## Reference` section with the page's lead
  sentence, **pasted**, and a wikilink to the page.
  - A note that links to a mapped concept also gets that concept's lead under
    `## Linked definitions`. There are at most 2 per note, and at most 2 linked copies of each
    lead.
  - So most leads exist in **4 copies**: the page, the concept note, and two linking notes.
- **akasha vault**: the same text with every block anchored (contract grammar v1). Every copy of
  a lead carries the same id, which is akasha transclusion.
  - Gate: `akasha setup` reports 0 conflicts and 0 reviews, and `diff -r` shows no rewritten ids.
  - An edit to one copy reaches all copies.
- **Leakage control**: the models already know Wikipedia. So every mapped page gets planted facts,
  seeded and made with templates, no LLM:
  - one **counterfactual** (a year in a real sentence shifted by 3–19);
  - two **fictional attributes** (e.g. "In the 1874 Marlow Index, the topic of X is catalogued as
    entry 41-C.").

  Planted values are unique across the corpus. Lead sentences are never perturbed.
- **Tiers** (`config/tiers.toml`), nested:

| tier | contents | files |
|---|---|---:|
| S | the 60-note concepts-retrieval subset + 22 fill notes, 40 wiki pages | 122 |
| M | all 432 notes + 236 wiki pages | (M8) |
| L | M + ~3.8k one-hop link targets of the mapped pages (distractors) | (M8) |

## Tasks (tier S, 90)

- **READ (60)**:
  - 15 on planted facts (7 counterfactual, 8 fictional);
  - 10 multi-hop planted facts, where the question gives no page title:
    - 6 note → page ("My note 'Choices' references a Wikipedia page…");
    - 4 wiki → wiki (through a page's related pages);
  - 10 unperturbed Wikipedia facts, reported next to closed book;
  - 15 personal-note questions and 5 aggregate ones, reused from concepts-retrieval;
  - 5 unanswerable (3 from concepts-retrieval, and 2 near-misses shaped like planted facts).
- **WRITE (15)**:
  1. Agent 1 files a memo of 3–4 new planted-style facts about concepts in the knowledge base
     "the way this knowledge base expects".
  2. The write is scored in code:
     - **landed**: a paragraph now holds the fact's distinctive name *and* its value, and none
       did before;
     - **linked**: that paragraph is in the concept's note or page, or in a file that wikilinks
       to them;
     - **duplicates**: distinct copies, where akasha mirrors of one node count once;
     - akasha violations and conflicts.
  3. A **fresh** agent 2 answers one question per fact.
- **UPDATE (15)**:
  1. Agent 1 is told that a lead definition changed (one phrase, drafted by qwen2.5:72b and
     verified in code).
  2. Score **copies updated / copies total** over all of that lead's copies.
  3. A fresh agent 2 is asked to quote the current definition. **Stale read**: its answer holds
     the old wording.

All gold data is checked in code: every gold span is verbatim in both vaults. A 20% hand audit
is in `data/.../audit.md`.

## Harnesses and fairness

- **Shared across conditions**: one agent loop (`kbio/agent.py`) with native tool calls on the
  Purdue GenAI endpoint. Everything is identical across conditions:
  - model, system prompt and temperature 0;
  - step caps: 12 for READ, 20 for WRITE/UPDATE;
  - tool results capped at 2,000 cl100k tokens.
- **Tokens** are counted over all turns, from the API `usage` field.
- **Tool subsets**: READ tasks get read-only subsets.
  - akasha: `search`, `get_node`, `neighborhood`, `read_note`;
  - basic-memory: `search_notes`, `read_note`, `build_context`;
  - files: `list_dir`, `grep`, `read_file`.

  WRITE and UPDATE tasks add the write tools:
  - akasha: `write_atom`, `transclude`, `edit_node`;
  - basic-memory: `write_note`, `edit_note`;
  - files: `write_file`, `edit_file`.

  Tool-schema tokens differ a lot and are reported separately. For READ / all tools: akasha
  337 / 643, basic-memory 2,254 / 4,097, files 253 / 420.
- **akasha writes are file-based** (the real user path): the daemon's watcher ingests them.
  `edit_node` edits one copy and waits for the daemon to propagate it.
- **basic-memory** runs with its defaults:
  - hybrid semantic search with local fastembed;
  - frontmatter and permalinks on. Turning those off breaks `build_context`.
- **Isolation**:
  - Every WRITE/UPDATE task runs on a fresh vault copy, with a fresh akasha store and a fresh
    basic-memory index.
  - The daemon's search and mirroring span every registered root, so two copies must never share
    a store.
  - READ tasks share one read-only snapshot per condition.

<!-- RESULTS: filled in at M8 -->

## Results: v1, tier S, gpt-oss:120b (M8)

Scope:
- 122 files, about 232k words, so roughly 300k tokens. That is more than the agent's 128k
  context, so every KB condition has to retrieve.
- 90 tasks per KB condition (60 READ, 15 WRITE, 15 UPDATE), 130 scored questions per condition,
  and 60 closed-book READ runs.
- Run `fullS`, 2026-09-27: 1,490 LLM calls in 8,027 s, 0 FAILED records, exit 0.
- One write task was interrupted by Ctrl-C and rerun from a fresh snapshot. The interrupted copy
  is archived.
- Full tables: `results/gpt-oss_120b-S.md`. Per-run data: `results/summary.json`. Both are
  regenerated by `uv run kbio analyze --tiers S`, which is deterministic (two runs, `cmp`
  identical).

**Tier S is a small-scale result.** It measures quality and cost when the KB is just above
context size. It does not show how those change as the KB grows. The tier-M corpus (668 files,
about 1.3M words) is built and verified but has not been run. The v2 KILT ladder (1e2 to 5.9M
pages) is the scale test, and it is on hold pending approval.

| metric | A (akasha stack) | B (bm tools, akasha KB) | C (bm stack) | C′ (grep/files) | CB |
|---|---:|---:|---:|---:|---:|
| READ perturbed facts, correct (n=25) | **0.84** | 0.52 | 0.48 | **0.96** | 0.00 |
| WRITE facts landed (strict / relaxed) | 0.88 / 0.92 | 0.89 / 0.95 | 0.84 / 0.93 | 0.37 / 0.37 | – |
| WRITE follow-up correct (n=55) | **0.91** | 0.73 | 0.78 | 0.36 | – |
| UPDATE all copies updated | **1.00** | **1.00** | 0.13 | 0.60 | – |
| UPDATE stale reads | **0.00** | **0.00** | 0.20 | 0.27 | – |
| UPDATE follow-up correct (n=15) | **0.93** | 0.73 | 0.47 | 0.60 | – |
| tokens per run, net of tool schemas (mean) | **18.0k** | 43.9k | 43.5k | 38.0k | 0.5k |
| tokens per run (median) | **3.8k** | 21.4k | 20.4k | 16.2k | 0.4k |
| tokens per UPDATE task | **13k** | 104k | 100k | 68k | – |
| step-cap rate | **0.09** | 0.13 | 0.13 | 0.31 | – |
| tasks failing the gate | 0 | 0 | 0 | 9 of 90 (wrote nothing, claimed done) | 0 |

Headline tests (task-clustered sign tests, from `Paired tests per family`). The pooled
per-question McNemar p values in the report are overstated, because questions are clustered by
task.

| comparison | family | mean diff [95% CI] | wins / losses | p |
|---|---|---:|---:|---:|
| A vs B (harness effect) | READ perturbed | +0.32 [0.12, 0.52] | 9 / 1 | 0.02 |
| A vs B | WRITE | +0.17 [0.02, 0.32] | 7 / 1 | 0.07 |
| A vs C (whole stack) | READ perturbed | +0.36 [0.16, 0.60] | 10 / 1 | 0.01 |
| A vs C | UPDATE | +0.47 [0.20, 0.73] | 7 / 0 | 0.02 |
| B vs C (KB effect) | UPDATE | +0.27 [0.07, 0.47] | 4 / 0 | 0.13 |
| A vs C′ | WRITE | +0.54 [0.27, 0.78] | 10 / 1 | 0.01 |
| A vs C′ | READ perturbed | −0.12 [−0.24, 0.00] | 0 / 3 | 0.25 |

What tier S supports:
1. **Consistency comes from the knowledge system.** With the akasha KB (A and B), one edit
   reached every copy of a duplicated fact in all 15 UPDATE tasks, with 0 stale reads. The same
   harness on a plain vault (C) updated every copy in 2 of 15 tasks. This is the akasha
   transclusion guarantee, and it holds whatever the harness.
2. **The akasha harness finds more and costs less.** A beats the same model on the same KB with
   basic-memory tools on perturbed READ (+0.32) and WRITE follow-ups. It uses about 2.4× fewer
   net tokens per run, and 7.8× fewer per UPDATE.
   - Every A and B miss on perturbed READ is "the answer never appeared in a tool result". The
     gap is what each harness shows (atom hits with a match window, versus the note head), not
     how the model reasons.
3. **Plain grep is still competitive for reading at this size.** C′ answers perturbed READs
   best (0.96). At 122 files, an exact-string grep finds planted values directly.
   - C′ fails at writing: 9 of its 30 WRITE/UPDATE writers (8 WRITE, 1 UPDATE) made no write
     call and claimed to be done.
   - It also left stale copies in UPDATE (stale-read rate 0.27).
   - Whether grep reading holds up as the corpus grows is exactly the open scale question.

Where A loses: aggregate READ (0.80 vs 1.00 for B, C and C′; n=5), and multi-hop perturbed READ
against C′ (0.60 vs 0.90).

## M13: akasha vs SOTA memory baselines on MemoryAgentBench (Conflict Resolution)

**Question:** is akasha competitive with SOTA memory systems at answering from facts it was told,
and better where facts go stale? Plan: `M13-PLAN.md`. Pre-registration (hypotheses, tests,
allowed wording, hashed before the run): `M13-PREREG.md`.

**Benchmark:** MemoryAgentBench (Hu, Wang, McAuley, ICLR 2026), Conflict Resolution /
FactConsolidation.
- 8 sub-datasets: single-hop and multi-hop at 6k/32k/64k/262k tokens, 100 questions each (800).
- Each context is a numbered fact list in which later facts override earlier ones.
- Answers are counterfactual, so the model can't answer from what it already knows.
- Inputs and the metric (`substring_exact_match`) come from the benchmark's own code
  (`kbio/mab/mab_export.py`, `mab_score.py`). Its settings are copied: temperature 0.7, retrieve
  10, its templates.

**Model:** gpt-oss:120b on Purdue for every condition. Purdue serves it with a 65,536-token limit,
so long context is truncated to 62,000 tokens by the benchmark's own rule.

**akasha as memory** (build-plan M22, `src/`):
- each memorized chunk is a `journal` node, and each fact a `claim` that `cites` it;
- a newer conflicting fact either:
  - gets a `contradicts` edge, which flags the old fact for review (K1), or
  - replaces it through the confident-agent override `supersede` (K2);
- retrieval is `/v1/search?mode=any&limit=10&type=claim&status=live`.

**Conditions** (single-shot, 1 call per question):

| id | memory | retrieval |
|---|---|---|
| LC | the context in the prompt | – |
| R-bm25 | the benchmark's 4k chunks | BM25 top 10 |
| R-facts | one fact per document | BM25 top 10 |
| R-rule | R-facts, with the same conflict rule retiring superseded facts | BM25 top 10 |
| K0 / K1 / K2 | akasha store / flag conflicts / supersede | akasha search top 10 |

R-rule is the decisive control. If K2 ≈ R-rule, the pre-registered reading is "akasha is a correct
host for conflict handling (at capture, with history, provenance and review), not better than
plaintext running the same rule."

**Published SOTA numbers** (other backbones, context only): `results/m13-published-context.md`.
For example, on single-hop with GPT-4o-mini: BM25 48, HippoRAG-v2 54, Mem0 18, Zep 7, Cognee 28;
every method scores ≤ 28 on multi-hop.

**Results** (full tables: `results/m13-mab.md`):
- **Run:** 2026-09-29, 5,600 primary calls plus the 160-question agentic subset, 0 FAILED.
- **Scoring:** the benchmark's own scorer.
- **Pre-registered verdict:** "H1 shown, and K2 > R-rule".

| accuracy | sh_6k | sh_32k | sh_64k | sh_262k | mh_6k | mh_32k | mh_64k | mh_262k |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| LC (whole context) | 0.95 | 0.71 | 0.67 | 0.35 | 0.75 | 0.28 | 0.21 | 0.05 |
| R-bm25 (benchmark RAG) | 0.95 | 0.63 | 0.67 | 0.57 | 0.72 | 0.23 | 0.15 | 0.08 |
| R-facts | 0.97 | 0.93 | 0.87 | 0.85 | 0.11 | 0.05 | 0.03 | 0.04 |
| R-rule (plaintext + conflict rule) | 0.96 | 0.93 | 0.91 | 0.85 | 0.17 | 0.06 | 0.02 | 0.06 |
| K0 akasha (store only) | 0.99 | 0.97 | 0.97 | 0.93 | 0.08 | 0.03 | 0.04 | 0.04 |
| K1 akasha (flag conflicts) | 0.99 | 0.98 | 0.98 | 0.93 | 0.12 | 0.04 | 0.04 | 0.06 |
| K2 akasha (supersede) | 0.98 | 0.97 | 0.98 | 0.95 | 0.18 | 0.04 | 0.05 | 0.05 |

Single-hop, pooled (400 questions), paired exact sign tests:

| comparison | accuracy | diff [95% CI] | wins / losses | p |
|---|---:|---:|---:|---:|
| H1: K2 vs R-bm25 | 0.970 vs 0.705 | +0.265 [0.223, 0.310] | 110 / 4 | Holm p < 0.001 |
| H3: K2 vs LC | 0.970 vs 0.670 | +0.300 | 123 / 3 | Holm p < 0.001 |
| H2: K1 vs K0 (flagging) | 0.970 vs 0.965 | +0.005 | 3 / 1 | 0.31, not shown |
| H1c (control): K2 vs R-rule | 0.970 vs 0.912 | +0.058 [0.035, 0.085] | 24 / 1 | p < 0.001 |

The results hold when the piloted questions are excluded.

What this shows, and what it doesn't:
1. **Single-hop: akasha memory beats the benchmark's own baselines** on the same model. It gains
   +0.27 over its BM25 RAG and +0.30 over long context. The gap grows with context size: at
   262k it is 0.95 vs 0.57 and 0.35.
2. **The edge over plaintext comes from akasha's search, not its conflict handling.**
   - K0, with no conflict handling at all, already scores 0.965.
   - Flagging (K1) and supersession (K2) add about +0.005: the reader resolves conflicts itself
     from the serial numbers in the retrieved facts.
   - A post-hoc, zero-cost retrieval diagnostic (not pre-registered) shows why. The benchmark's
     BM25 tokenizes on whitespace only, so "Vogue?" never matches "Vogue.". A plaintext BM25 with
     lowercase and punctuation stripping puts the gold answer in the top 10 almost as often as
     akasha (262k: 0.96 vs 0.97; the benchmark's tokenizer: 0.88).
   - The honest reading is therefore: akasha's built-in search is as good as a well-tokenized
     plaintext retriever and better than the benchmark's. It is not better than plaintext done
     well.
3. **Multi-hop: every fact-level store fails**, akasha included (0.03–0.18). Single-shot
   retrieval of 10 facts can't assemble a chain. Retrieving chunks (R-bm25) or putting the whole
   context in (LC) does better (about 0.30), and all conditions collapse at 262k. The paper
   reports the same for every method (≤ 0.28).
4. **Agentic subset** (descriptive, 20 questions × 8): both agents score 0.95 on single-hop. On
   multi-hop, akasha scores 0.71 vs 0.55 for the grep agent (19 better / 6 worse). akasha's
   search, supersession and neighborhood help an agent chain facts.
5. **Costs and TMS behaviour:**
   - akasha's capture-time conflict detection found 97–99% of the exhaustive plaintext pairs
     (6,529 of 6,720 at 262k).
   - K1 opened one review per contradiction (6,506 at 262k): correct per ruling M22-C, but far
     above a human's daily review budget (PRD F9).
   - Ingest grows faster than linearly: 3 s for 455 facts, 10 min for 18,332.

**Published SOTA numbers** (context only, other backbones, the paper's length aggregation
unspecified): on single-hop with GPT-4o-mini, BM25 48, HippoRAG-v2 54, Mem0 18, Zep 7, Cognee 28;
the best long context is GPT-5-mini at 78. With gpt-oss:120b, our BM25 RAG scores 0.705 and
akasha 0.970. Only the same-model comparisons above support a claim.

## Threats to validity (running list)

- **The A-vs-B gap is "our adapter vs basic-memory as shipped".** The akasha harness was built
  for this experiment and tuned during the pilot (match-window snippets). basic-memory
  `search_notes` returns the head of the note. B's agent sometimes had the right note as the top
  hit, but not the matched passage, and answered NOT FOUND.
- **The unperturbed questions don't measure leakage.** They refer to the text ("according to the
  paragraph"), so CB is 0 by construction. The leakage control rests on the perturbed facts
  (CB 0.00).
- **Personal questions are at ceiling** (1.00 in A, B and C) and judge-scored, so they don't
  discriminate between conditions.
- **The UPDATE follow-up is ambiguous.** It asks for "the lead definition", and concept notes
  also carry the user's own `Definition:` line. Most wrong answers quote that other definition,
  not the old value; see the stale / other split in the report. Stale-read rate is the primary
  UPDATE read metric.
- **WRITE `landed` penalises entity notes.** basic-memory agents often file a fact as a new
  entity note. `landed_relaxed` counts those.
- **basic-memory's agent calls unexposed tools** (13 in B, 14 in C), from gpt-oss priors about
  basic-memory.
- **v1's plain-vault UPDATE failures are partly a path quirk.** The model drops the "(1) "
  from `(1) Universal/…` paths, so C′'s edits to those files failed with "file not found"
  (found in M12). On the akasha vault, a synced copy covers the miss, so v1's C′ UPDATE numbers
  overstate the knowledge-base effect. C may be affected too; that is not measured. M12
  (`M12-PREREG.md`, Amendment 1) fixes the file tools and supersedes v1 on this question.
- **One model, one tier, small n** (5 to 25 task units per family). Most per-family CIs are
  wide.

- **basic-memory rewrites the vault on first index.** It adds title/type/permalink frontmatter
  to every file; nothing else changes. In B the akasha daemon tolerates this: 0 violations, and
  hashes are stable across rescans.
- **basic-memory search quirks** (real harness properties, left as they are):
  - text search misses hyphenated codes such as "55-Q";
  - hybrid search can miss a note written seconds earlier, while its embedding is pending.
- **Harness bugs fixed during the smoke test and the pilot**, applied to every condition before
  any reported run:
  - The file `grep` showed the first 300 characters of a matching line. Wikipedia paragraphs are
    single long lines, so the match was hidden. It now shows a window around the match.
  - akasha `search` snippets now show a window around the first query term in the same way.
- **The folder name `(1) Universal`** trips models: they drop the "(1) " prefix. This hits every
  condition.
- **The judge** is qwen2.5:72b and blind: node ids that are present in the vault, paths,
  permalinks and the SOURCES block are stripped. The headline metrics are code-checked (exact
  planted values) and do not depend on the judge.
