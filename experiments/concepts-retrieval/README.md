# Concepts retrieval experiment: regular vault vs akasha-format vault

Question: with the same retriever and the same context budget, does an **akasha-compatible**
version of a notes subset retrieve correct, cited information more accurately and with fewer
tokens than the **regular** notes?

## Where things live

The experiment is split in two, because the repo's `.gitignore` keeps personal vault content out
of git history.

- **Tracked here (`experiments/concepts-retrieval/`):** the scripts, this README, and the
  aggregate results. None of these quote the notes.
  - `results/summary.json`: every metric per condition and budget.
  - `results/tokens_to_gold.json`: tokens needed to reach the evidence, per question id.
  - `results/audit-overrides.json`: the hand-graded judge corrections.
- **Local only (`data/experiments/concepts-retrieval/`, git-ignored):** everything that
  contains or quotes the vault.
  - `datasets/regular`, `datasets/clean`, `datasets/akasha`: the three datasets.
  - `questions.json`: the gold questions, answers and spans.
  - `units/`: the retrieval units per condition.
  - `results/retrieval.json`: the packed contexts.
  - `results/cache/`: every raw model answer and judgment.
  - `results/per-question.md`: the per-question evidence report.

  The scripts rebuild the datasets and units from `data/(10) Concepts`. The questions and cached
  answers exist only there.

## Datasets (`data/experiments/concepts-retrieval/datasets/`)

| dataset | what it is | files | bytes |
|---|---|---:|---:|
| `regular/` | 60 notes copied verbatim from `data/(10) Concepts` | 60 | 187,747 |
| `clean/` | `regular` minus template scaffolding: front matter (aliases kept), dataview/code fences, `INPUT[...]`, empty `[[]]` link slots, image embeds, empty headings. Content lines are byte-for-byte. | 60 | 135,518 |
| `akasha/` | `clean` in akasha contract grammar v1: every content block is an atom with a checksummed `^tm-<id8>` anchor (one line) or a `{...}{tm-<id8>}` span (multi-line). A note that wikilinks a subset note with a `Definition:` atom gets that atom mirrored byte-for-byte (same id) under `## Linked definitions`, which is akasha transclusion. | 60 | 165,076 |

Subset rule (`scripts/select_subset.py`): every note in `Techniques/` and `Personal Workflow/`
with at least 250 non-space content characters, plus the notes those seeds link to most, capped
at 60. 158 of 339 resolved links stay inside the subset. See `datasets/subset-manifest.json`.

Akasha build stats (`datasets/build-stats.json`): 850 atoms (603 single-line, 247 spans),
32 notes with a definition atom, 80 mirrored definition copies. The subset has one
`![[...#^block]]` embed, and it points outside the subset, so 0 embeds were transcluded.

**Compatibility, checked with the real CLI.** Current `main` was installed with `uv tool install`
into a scratch HOME on port 7533, isolated from the real daemon. Then `akasha setup <copy>` ran.

- 60 files tracked.
- 850 nodes, one per unique atom: the 80 mirrored copies map to their definitions' nodes.
- 0 conflicts, 0 pauses, 0 reviews.
- `diff -r` against `datasets/akasha` shows no changes, so no id was rewritten or repaired.
- Editing one mirrored definition line reached all 3 copies.

## Questions (`questions.json`)

33 questions, written from the raw notes before the akasha set was built:

- 22 single-note questions.
- 8 multi-hop questions, which need two linked notes.
- 3 unanswerable questions.

Each answerable question has one or more verbatim gold spans with their source files. The
build asserts that every span survives inside a single retrieval unit in every condition.

## Conditions (all use the same BM25 over "title › heading path + body", greedy-packed to a cl100k token budget)

| condition | unit | citation shown to the model |
|---|---|---|
| `raw` | heading section of the regular note (split at blank lines past 400 tokens) | `path` |
| `clean` | heading section of the clean note | `path` |
| `clean-atoms` | the akasha atoms without ids: a granularity control | `path` |
| `akasha` | akasha atom (one node per id) | `path#^tm-id` |
| `akasha-1hop` | `akasha`, and after each retrieved atom, the mirrored definition atoms of the notes it links to | `path#^tm-id` |

## Pipeline

```bash
cd scripts
python3 select_subset.py                    # regular dataset
python3 build_datasets.py                   # clean + akasha datasets, units/*.json
uv run --no-project --with tiktoken python retrieve.py               # retrieval-only recall sweep
uv run --no-project --with requests --with tiktoken python run_llm.py --budgets 500 1000
uv run --no-project --with tiktoken python tokens_to_gold.py         # tokens needed to reach the evidence
python3 analyze.py                          # results/summary.json + data/.../results/per-question.md
```

The last two steps and the dataset build are offline and deterministic. Re-running them after
this directory moved to `experiments/` reproduced `results/summary.json` and `retrieval.json`
exactly.

`run_llm.py` imports `PurdueGenAIStudio` from
`akasha-wikipedia-codegraph/scripts/run_purdue_experiment.py`, which reads `PURDUE_GENAI_API_KEY`
from that repo's `.env`.

- Answering model: `llama3.3:70b`, temperature 0. It must cite `[S#]` or reply `NOT FOUND IN SOURCES`.
- Judge: `qwen2.5:72b`, a different model family. It grades 0/1/2 against the gold answer and
  never sees sources, file paths or ids, so it cannot tell which condition produced an answer.
- Citation scores are computed in code against the gold spans, not by the judge.
- Every call is cached in `data/experiments/concepts-retrieval/results/cache/<condition>@<budget>/<qid>.json`, so reruns resume.

## Results (run 2026-09-26; generated from `results/summary.json` and `results/tokens_to_gold.json`)

All 330 answer and judge jobs completed with 0 failures.

### 1. Tokens needed to retrieve the evidence (no LLM)

The ranked list has no budget cap here. The measure is how many context tokens are consumed before every gold span of a question is present.

Only the 29 answerable questions that every condition eventually retrieves are counted. q27 is never fully retrieved in the top 60 by the atom conditions.

| condition | median tokens | mean tokens | vs raw (median / mean) |
|---|---:|---:|---:|
| raw | 278 | 602 | +0% / +0% |
| clean | 266 | 552 | -4% / -8% |
| clean-atoms | 201 | 445 | -28% / -26% |
| akasha | 210 | 510 | -24% / -15% |
| akasha-1hop | 210 | 552 | -24% / -8% |

### 2. Retrieval recall at a fixed budget (no LLM)

Share of gold spans present in the packed context:

| condition | 250 | 500 | 1000 | 2000 | 4000 |
|---|---:|---:|---:|---:|---:|
| raw | 0.70 | 0.77 | 0.95 | 0.97 | 0.98 |
| clean | 0.73 | 0.80 | 0.95 | 0.97 | 0.98 |
| clean-atoms | 0.70 | 0.87 | 0.93 | 0.95 | 0.98 |
| akasha | 0.67 | 0.83 | 0.92 | 0.93 | 0.98 |
| akasha-1hop | 0.67 | 0.78 | 0.92 | 0.93 | 0.98 |

### 3. Answer correctness with cited sources

llama3.3:70b answers; qwen2.5:72b judges; then a hand audit.

Columns:

- **Score**: 0–2, averaged over 33 questions, after the hand audit. The raw judge mean is in brackets.
- **Cited span recall**: the share of gold spans inside the chunks the answer cited. Computed in code over the 30 answerable questions.
- **Cited source precision**: the share of cited chunks that come from a gold source file.
- **Tokens per citation**: how much text a reader must read to check one citation.

| condition @ budget | score (judge) | fully correct | single | multi-hop | unanswerable abstained | cited span recall | cited source precision | tokens per citation | API prompt tokens |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| raw @500 | 1.61 (1.73) | 73% | 1.77 | 1.00 | 3/3 | 0.77 | 0.93 | 168 | 607 |
| clean @500 | 1.67 (1.70) | 76% | 1.86 | 1.00 | 3/3 | 0.78 | 0.96 | 155 | 606 |
| clean-atoms @500 | 1.70 (1.73) | 82% | 1.91 | 1.00 | 3/3 | 0.87 | 0.94 | 67 | 613 |
| akasha @500 | 1.64 (1.73) | 76% | 1.77 | 1.12 | 3/3 | 0.83 | 0.95 | 80 | 606 |
| akasha-1hop @500 | 1.51 (1.51) | 73% | 1.73 | 0.75 | 3/3 | 0.78 | 0.94 | 80 | 603 |
| raw @1000 | 1.88 (1.94) | 91% | 2.00 | 1.50 | 3/3 | 0.95 | 0.94 | 201 | 1106 |
| clean @1000 | 1.91 (1.97) | 94% | 2.00 | 1.62 | 3/3 | 0.95 | 0.96 | 194 | 1102 |
| clean-atoms @1000 | 1.85 (1.91) | 91% | 1.91 | 1.62 | 3/3 | 0.93 | 0.97 | 71 | 1094 |
| akasha @1000 | 1.79 (1.79) | 85% | 1.86 | 1.50 | 3/3 | 0.92 | 0.97 | 81 | 1094 |
| akasha-1hop @1000 | 1.76 (1.76) | 85% | 1.86 | 1.38 | 3/3 | 0.90 | 0.97 | 84 | 1096 |

Paired against `raw` on the same questions (after the audit), no condition differs significantly:

- clean@500: 2 wins, 1 losses, 30 ties (sign test p = 1.0)
- clean-atoms@500: 6 wins, 3 losses, 24 ties (sign test p = 0.508)
- akasha@500: 5 wins, 3 losses, 25 ties (sign test p = 0.727)
- akasha-1hop@500: 4 wins, 5 losses, 24 ties (sign test p = 1.0)
- clean@1000: 1 wins, 0 losses, 32 ties (sign test p = 1.0)
- clean-atoms@1000: 2 wins, 3 losses, 28 ties (sign test p = 1.0)
- akasha@1000: 1 wins, 3 losses, 29 ties (sign test p = 0.625)
- akasha-1hop@1000: 1 wins, 4 losses, 28 ties (sign test p = 0.375)

### Interpretation

**Fewer tokens: the akasha format wins, because of how the notes are split, not because of the ids.**

- To reach the evidence, akasha needs a median of 24% fewer context tokens than the regular
  notes (15% fewer on average).
- At a tight 500-token budget it retrieves more of the correct passages (0.83 vs 0.77).
- The saving comes from splitting notes into small blocks. `clean-atoms` has the same blocks
  without ids and saves more (28% / 26%).
- The `^tm-` ids and `path#^tm-id` headers cost about 5–15%, which is 2 fewer blocks per
  1000-token context.
- Removing template scaffolding alone (`clean`) saves only 4–8%.

**Correctness: no reliable difference.** After the hand audit:

- All conditions score between 1.61 and 1.70 at 500 tokens, except akasha-1hop at 1.52.
- At 1000 tokens they score between 1.76 and 1.91.
- The paired tests all have p of 0.375 or more.
- The pattern is only a trend:
  - Small blocks help slightly at 500 tokens.
  - Whole sections help slightly at 1000 tokens, on single-note questions (2.00 for sections vs 1.86–1.91 for atoms). Multi-hop is level (1.50–1.63 for both).
- The atoms-vs-sections gap has a visible cause, which is fragmentation:
  - q12: a numbered list's items are separated from their lead-in, "The three most important things in college:".
  - q17: "Horizontal timeline..." is split from its child bullets.
  - q27: the gold line is buried in a long bullet group.

  Whole sections keep this parent/child context.
- The akasha-vs-clean-atoms gap is not fragmentation. Those two conditions use identical blocks.
  - On q12 and q17 at 1000 tokens, clean-atoms scored 0/2 and akasha scored 0/1 after the audit.
  - The akasha contexts hold about 2 fewer blocks because of the id overhead, and the answering
    model varies on near-identical contexts.
  - With n=33 that gap is noise plus overhead.

**Transcluded definitions (akasha-1hop) do not help.** Pulling in the linked notes'
`Definition:` blocks spends budget on generic definitions and pushes out ranked evidence. It is
the lowest condition at 500 tokens, on the score and on retrieval recall. A definition is rarely
the fact a multi-hop question needs.

**Checkable sources.**

- Citations point at the right files in every condition: cited source precision is 0.93–0.97.
- The difference is how much text a reader must read to check one citation: about 70–85
  tokens for a block versus 155–200 tokens for a section.
- Both block conditions have that advantage in this experiment. The only thing akasha adds is
  that its citation (`path#^tm-id`) is a stable node id. It survives edits, and every mirrored
  copy shares it.
- That stability is an argument from the design. It was **not measured** here.

**Every verdict can be checked.** The local `data/experiments/concepts-retrieval/results/per-question.md` shows, for every question, condition
and budget:

- the gold answer and gold source(s);
- the answer as given, the judge's score, and any audit override with its reason;
- the retrieved sources;
- the full text of every cited chunk.

### Threats to validity

- **Question vocabulary.** The questions were written from the notes and reuse their
  vocabulary, which makes BM25 easy in every condition. Paraphrased or embedding-based queries
  could reorder the conditions.
- **Sample size.** 33 questions, one answering model, temperature 0. Differences of 1–5
  questions are within noise.
- **The judge is imperfect.**
  - It gave full credit to 10 answers that claimed the information was unavailable, and scored
    some partial answers 0.
  - Every answerable-question answer containing "NOT FOUND" (29) was re-graded by hand. 15
    disagreements are overridden in `results/audit-overrides.json`, and the raw judge scores are
    kept alongside.
  - Answers that received a score but had no gold span in context were also checked. All of
    them were already in that set.
  - Other answers scored 2 were not audited.
- **Deterministic atom boundaries.** Blocks come from a parser: a top-level bullet with its
  children, or a paragraph. Curated atoms that keep list intros with their lists would likely
  close the multi-hop gap. That is a hypothesis, not a result.
- **Narrow scope.** One vault subset with one topic cluster. There is no link-following
  control for the regular notes.
