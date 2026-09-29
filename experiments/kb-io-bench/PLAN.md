# kb-io-bench: plan

This benchmark measures how well an agent **writes into** and **reads back from** a large personal
knowledge base. It compares three setups:

| id | harness (the tools the agent gets) | knowledge system (what stores the data) |
|---|---|---|
| **A** | akasha harness: node search, get, neighborhood, anchored-atom write, transclude, edit-once | akasha vault with the akasha daemon running |
| **B** | regular SOTA harness: basic-memory MCP tools | the same akasha vault with the daemon running |
| **C** | regular SOTA harness: basic-memory MCP tools | the regular vault, with no daemon |
| C′ | a plain grep/read/write file agent, in the style of Claude Code (a cheap second baseline) | the regular vault |
| CB | closed-book: no knowledge base (the floor that measures what the model already knows) | none |

The comparisons are:
- A vs B: the harness effect, with the knowledge system held fixed.
- B vs C: the knowledge-system effect, with the harness held fixed.
- A vs C: the whole stack.

The corpus is the `data/(10) Concepts` vault (432 notes) plus Wikipedia pages on the same
concepts, arranged in nested tiers that can keep growing.

This is experiment code, outside the build-plan and spec chain. It must **not** touch `src/`,
`docs/build-plan.md`, `docs/agents/task-status.md`, or the golden tests. The rules from `CLAUDE.md`
that still apply: no pickle/eval/exec, and `ruff` must be clean on this directory. The earlier
study in `experiments/concepts-retrieval/` is the model for layout and style; read its README first.

## 0. Prior-art answer (settled while planning, 2026-09-27)

**Does this testbench already exist? Partly.**

- **MemoryAgentBench** (HUST-AI-HYZ, MIT licence, ICLR 2026) is the closest match. It injects text
  in chunks, then queries it through adapters for long-context, RAG, Mem0, Letta, Cognee and
  HippoRAG agents. It covers four competencies: accurate retrieval, test-time learning,
  long-range understanding, and conflict resolution.
  - It does **not** cover markdown vaults, transclusion, or agent-authored writes to a file
    knowledge base.
  - It does not cover edit-once-propagate updates, or harness × knowledge-system factorial designs.
  - https://github.com/HUST-AI-HYZ/MemoryAgentBench, arXiv 2507.05257
- Other work, for reference only:
  - LongMemEval, LoCoMo and BEAM are conversation-memory benchmarks.
  - AgentMemoryBench (s010m00n) is a continual-memory benchmark.
  - BEIR and KILT are static retrieval benchmarks.

**Decision:**
- Add MemoryAgentBench as a **git submodule**. Use it for its inject-then-query protocol and
  metric definitions. As a stretch goal (M9), run our three harnesses on a small slice of its
  Accurate Retrieval split.
- **Build the rest**: the corpus, the write, read and update tasks, and the A/B/C factorial.

**Which SOTA harness to use: basic-memory** (basicmachines-co/basic-memory, AGPL-3.0, used as a
submodule only, never vendored).
- It is a markdown-file knowledge base with SQLite search and MCP read/write tools: `write_note`,
  `read_note`, `edit_note`, `search_notes`, `build_context`.
- It indexes files **without calling an LLM**.
- Systems that call an LLM for every chunk at ingestion (LightRAG, Mem0, Graphiti, HippoRAG) are
  ruled out at scale by the Purdue rate limit (§6). Leave the adapter interface open for them.
- If basic-memory supports hybrid or semantic search with a local embedder, turn it on and
  record that, so the baseline is at full strength.

## 1. Where things live

The tracked/local split is the same as in concepts-retrieval, because `.gitignore` says
personal-vault content never belongs in history.

- **Tracked, `experiments/kb-io-bench/`:**
  - `PLAN.md` and `STATUS.md`
  - `README.md`, written at the end
  - `pyproject.toml` (its own uv project, so the root dependencies stay untouched) and `ruff.toml`
  - `kbio/` (the package)
  - `config/`
  - `third_party/` (submodules)
  - `results/`: aggregate results only
  - `wiki-manifest.json`: page id, **revision id**, URL and sha256 for each page, plus CC BY-SA
    attribution
  - Question sets derived only from Wikipedia may be tracked, in `results/` or `config/`.
- **Local only, `data/experiments/kb-io-bench/`:**
  - built corpora for each tier and condition
  - fetched Wikipedia text
  - questions that quote personal notes
  - transcripts, the LLM cache, the per-question report
  - the scratch HOME and all logs
- **Scheduler files:** `agent/prompt.txt` and `agent/run_scheduled.sh` are tracked. Their logs
  go under `data/experiments/kb-io-bench/agent-logs/`.

## 2. Hard safety rules for the unattended agent

1. **Never touch the real daemon**, `~/.config/tm-daemon`, or the real vault.
   - Every akasha process runs with `HOME=data/experiments/kb-io-bench/scratch-home` on port
     **7534**, from `uv tool install`-ed current `main` or `uv run` against the repo. This is the
     same method as the concepts-retrieval compatibility check.
   - basic-memory also runs under that scratch HOME, so its config and SQLite stay there.
2. **Never print, log or commit `PURDUE_GENAI_API_KEY`.** Load it from
   `../akasha-wikipedia-codegraph/.env` and pass it only through the environment.
3. **Don't commit and don't push.** The user has not asked for it. Leave the work in the working
   tree, and note in `STATUS.md` what a commit would contain.
   - `git submodule add` stages `.gitmodules`. That is fine, but leave it uncommitted.
4. **Don't `rm -rf`.** Use fresh directories or `mv` into `data/experiments/kb-io-bench/trash/`.
5. **Run long jobs only through `agent/runner.py job start <name> [--cwd D] [--log P] -- CMD`**, with
   every call cached.
   - The launcher gives each job its own tmux session `kbio-job-<name>`. Inside it, the job runs
     in its own process session, so a pane Ctrl-C or a `tmux kill-session` cannot stop it.
   - Output is line-timestamped and appended to the log by path.
   - `data/.../jobs/<name>.json` and `<name>.exit` record the command, the pid, and the exit code
     or the signal.
   - Stop a job with `runner.py job stop <name>`. List jobs with `runner.py job list` or
     `runner.py status`.
   - Raw `nohup`/`setsid` from a `claude -p` Bash call dies when that session exits (tested
     2026-09-27). Target tmux sessions exactly with `-t '=name'`, because a bare `-t` prefix-matches.
   - Don't watch jobs from Claude. Record the job in STATUS, check once, update STATUS, and end the
     session. The runner comes back in 30 minutes.
   - **Humans watch read-only:** `tmux attach -r -t '=kbio-view'` for the live rendered agent
     stream, `tmux attach -r -t '=kbio-job-<name>'` for a job. The runner is `agent/start.sh`
     (§8a).
6. After finishing each milestone, update `STATUS.md`:
   - the milestone state;
   - the verification you ran;
   - the next concrete step.

## 3. Corpus and augmentation (M1–M2)

Wikipedia is the "information augmentation" source.

**Map concepts to pages** (`kbio/sources/wikipedia.py`).
- For each note in `(10) Concepts`, strip "(concept)" and similar suffixes from the title. Look it
  up with the MediaWiki `opensearch` and `query` APIs.
- Keep a match only if the page title, after normalizing, matches or is a redirect target.
- Hand-fix ambiguous matches in `config/wiki-overrides.toml`: `title -> page | skip`.
- Personal-workflow notes with no encyclopedic match get `skip`.
- Record the match rate.

**Fetch.** Get plaintext with `prop=extracts&explaintext=1` plus `prop=revisions` (revid and
timestamp). Pin every page to its revid, and cache pages under `data/.../sources/wiki/`. Send a
descriptive User-Agent, and make at most 1 request per second.

**Synthetic and perturbed facts, the leakage control.** This matters: llama and gpt-oss already
know Wikipedia, so a correct answer does not prove retrieval.
- Inject 2–3 facts into each augmented page:
  - A **counterfactual edit**: a changed year, number or named person in a real sentence. Record
    the original and the edited version.
  - A **fictional attribute**: "In the 1874 Marlow Index the term is catalogued as entry 41-C."
- Generate them with deterministic templates plus seeded randomness. An LLM is not required.
- Record them in `data/.../sources/perturbations.json`.
- The **headline accuracy is scored on these facts.** Unperturbed Wikipedia questions are
  reported separately, next to the closed-book (CB) score.

**How the augmentation looks in each knowledge-base format.**
- **Regular vault:**
  - Each Wikipedia page becomes a note `Wikipedia/<Title>.md` with a source line: URL, revid,
    licence.
  - The matching concept note gets a `## Reference` section containing `[[Wikipedia/<Title>]]`.
  - The page's lead sentence is **copied** into the concept note. This is the realistic
    "pasted definition" that goes stale.
- **akasha vault:**
  - The same files, in contract grammar v1: atoms with `^tm-<id8>` or `{...}{tm-id}` spans. Reuse
    `build_datasets.py` from concepts-retrieval, or import `akasha.contract` for id minting.
  - The lead sentence goes into the concept note as a **transclusion**: the same id and
    byte-identical text.
  - Gate: `akasha setup` must report **0 conflicts and 0 reviews**, and `diff -r` must show no
    rewritten ids.

**Tiers** (`config/tiers.toml`). The tiers are nested, so every question is answerable at every
tier.

| tier | contents | rough size |
|---|---|---|
| S | the 60-note concepts-retrieval subset + their mapped wiki pages | about 100 files |
| M | all 432 notes + their mapped wiki pages | about 700 files |
| L | M + a 1-hop link expansion of the mapped wiki pages (distractors, same domain) | about 3–5k files |
| XL | L + a 2-hop expansion, capped by `max_pages` | configurable, 10k+ |

- New tiers are added by config alone: `sources = [...]`, `expand_hops`, `max_pages`, `seed`.
- Sources are plugins with one interface, `iter_documents() -> Document(id, title, text, meta)`,
  so another corpus can be dropped in.

## 4. Tasks (M3)

All gold data is machine-checkable. Each task has one or more **verbatim gold spans**, or an
exact expected value.

**READ** (~60 core). A fresh agent answers a question from the knowledge base and must cite its
sources: a node id or a file plus a quoted span.
- 25 on perturbed or synthetic Wikipedia facts. 10 of those are multi-hop: note → wiki page, or
  wiki → wiki through a link.
- 15 on personal notes. Reuse and extend the concepts-retrieval questions; they stay local.
- 10 on unperturbed Wikipedia facts, reported next to CB.
- 5 unanswerable (the agent should abstain).
- 5 "aggregate" questions that need two or more notes.

**WRITE** (~15).
1. Agent 1 gets a short new source text: a synthetic memo of 3–5 facts about concepts already in
   the knowledge base. It is told to file those facts into the knowledge base "the way this
   knowledge base expects".
2. Score the write directly:
   - Did each fact land? Check the exact value is present in the files or nodes.
   - Is it linked to the right concept note?
   - Count duplicates.
   - For A and B: count akasha conflicts and reviews after `rescan`.
3. Then a **fresh** agent 2 gets READ questions about those facts. This is the "input data, then
   extract it with a new agent" round trip.

**UPDATE and PROPAGATE** (~15).
1. Agent 1 is told that one fact changed ("the lead definition of X is now ...", or "the Marlow
   entry is 17-B").
2. Then a fresh agent 2 is asked a question whose answer appears in **several places**: the wiki
   page, the concept note's pasted or transcluded copy, and a linked note.
3. Score:
   - **stale-read rate**: the answer or a cited copy still has the old value;
   - **copies updated / copies total**.

This is where the akasha knowledge system (A and B) should differ from C, and it is the main
thing this benchmark adds over concepts-retrieval.

For every task, run from a knowledge-base **snapshot** copied fresh for that task and condition,
so writes never leak across tasks. The snapshot is a copy of the tier directory. For akasha,
either run `akasha setup` against a fresh scratch HOME per task, or snapshot the scratch store
too; choose whichever is fast enough and record why.

Questions are drafted by `qwen2.5:72b` from templates plus the gold spans, then **verified in
code**:
- every gold span occurs verbatim;
- the answer occurs in the span.

After that, the implementing agent reads a random 20% itself and writes its audit notes to
`data/.../audit.md`.

## 5. Harnesses (M4–M6)

There is one agent loop for all conditions (`kbio/agent.py`):
- It uses OpenAI-style native tool calls against the Purdue endpoint
  `https://genai.rcac.purdue.edu/api/chat/completions`. Tool calls were checked working on
  2026-09-27 for `gpt-oss:120b`, `qwen3:32b` and `llama3.3:70b`.
- Everything is the same across conditions:
  - model, system prompt skeleton, temperature 0;
  - max tool steps: **12** for READ, **20** for WRITE and UPDATE;
  - max tool-result size: 2,000 tokens, truncated with a marker.
- Reuse the throttle, backoff and caching from `concepts-retrieval/scripts/run_llm.py`
  (`MIN_INTERVAL=4.0`, 7 retries with `60*(n+1)` s backoff). A failed job is recorded as FAILED;
  it does not crash the run.
- Record for every turn: the prompt and completion tokens from the API `usage` field (fall back
  to tiktoken cl100k), tool names and arguments, the size of each tool result, and latency.
- Tokens are counted **over all turns**.

**Harness adapters** (`kbio/harnesses/<name>.py`). The interface is:
`setup(kb_dir, scratch) -> tools: list[ToolSpec]`, then `call(name, args) -> str`, then
`teardown()`.

- **akasha** (condition A). This is a client of the scratch daemon's HTTP API on 7534, using the
  token that `akasha setup` created in the scratch HOME.
  - `search(query)`: `GET /v1/search`. Returns node id, first 200 characters, and the files and
    headings where the node appears.
  - `get_node(id)`: the body, plus every location it is mirrored to.
  - `neighborhood(id, hops=1)`
  - `read_note(path)`: the file, rendered with ids kept.
  - `write_atom(note, text, after_heading?)`: appends an anchored atom. The harness mints a valid
    id8.
  - `transclude(id, into_note)`: inserts a byte-identical copy that carries the same id.
  - `edit_node(id, new_text)`: edits one copy; the daemon propagates the change.

  **First, settle how writes happen.** Agent-class tokens turn mutations into review proposals
  (`src/akasha/api/auth.py`, spec §4.11). So prefer **file-based writes** that the daemon ingests,
  which is the real user path. `PATCH /v1/nodes` with the scratch human token is the fallback.
  Record the choice and why in STATUS.

- **basic-memory** (conditions B and C). Start its MCP server over stdio from the submodule, with
  `uv run` in `experiments/kb-io-bench`. Use the Python `mcp` SDK. Convert its tool schemas to
  OpenAI tools unchanged, pointed at the tier directory.
  - For B, the akasha daemon also watches the same directory. Whatever basic-memory writes is
    what akasha sees, and anything out of contract counts in the violation metric.

- **files** (condition C′): `list_dir`, `grep(pattern)` (ripgrep, capped), `read_file(path,
  offset, limit)`, `write_file`, `edit_file(old, new)`.

- **closedbook** (condition CB): no tools.

## 6. Models and call budget

- **Agent models** (`config/models.toml`):
  - primary `gpt-oss:120b`, the strongest at tool calls on the endpoint;
  - secondary `llama3.3:70b`, for continuity with concepts-retrieval.
  - The pilot confirms both. If the primary is too slow or unreliable, swap them and record why.
- **Judge:** `qwen2.5:72b`, a different family from both agents. It is **blind**: ids, paths and
  the condition are stripped from answers. Scores are 0, 1 or 2.
- **Citations are checked in code** against the gold spans, as in concepts-retrieval.
- At a 4 s minimum interval the endpoint allows about **900 calls per hour**.
- Estimated calls for one model and one tier:
  - READ: 60 × (A, B, C) × ~6 steps ≈ 1,080. Add C′ ≈ 360, CB 60, judge ≈ 300. Total ≈ 1,800.
  - WRITE: 15 × 3 × (~8 writer + ~6 reader) ≈ 630, plus judge 45.
  - UPDATE: 15 × 3 × ~14 ≈ 630, plus judge 45.
  - **Total ≈ 3,100 calls, about 3.5 hours per tier per model.** S + M + L for the primary model
    is about 10–11 hours. The XL tier and the second model are optional extensions.
- If a 1-concurrency probe (M7) shows the limit is per-token rather than global, a parallelism of
  2 may be allowed. Measure before assuming.

## 7. Metrics and analysis (M8)

For each condition, tier and task type:
- judge score; fully-correct rate; accuracy on perturbed facts (the **headline**);
- CB-adjusted accuracy on unperturbed facts;
- cited-span recall and cited-source precision; abstention on unanswerable questions;
- **total tokens per task** (prompt + completion, all turns); tool calls; wall time; failure rate;
- WRITE: facts landed, correct linkage, duplicates, contract violations;
- UPDATE: stale-read rate, copies updated.

Statistics:
- Paired tests between A↔B, B↔C and A↔C on each task: McNemar on fully-correct, a sign test on
  scores.
- 95% bootstrap confidence intervals.
- Scale curves: metric against tier size, both files and tokens.

Outputs:
- `results/summary.json` (tracked);
- `results/*.md` tables and charts (tracked, no personal quotes);
- `data/.../per-question.md`: every transcript, with its cited sources, next to the gold answer
  (local).
- `README.md` in the style of concepts-retrieval: design, results, interpretation, and threats to
  validity. Threats include the judge's leniency (audit its "not found" cases by hand, as before)
  and basic-memory's version and settings.

## 8. Milestones

Each milestone has a verification step that must pass before it is marked done in STATUS.md.

| id | milestone | verification |
|---|---|---|
| M0 | Scaffold: `pyproject.toml` (deps: requests, tiktoken, mcp, pyyaml/tomllib) and `ruff.toml`. Add the submodules `third_party/MemoryAgentBench` and `third_party/basic-memory` (pinned). Write STATUS.md. | `git submodule status` shows both; `uv run ruff check .` is clean |
| M1 | Wikipedia mapper and fetcher, overrides, `wiki-manifest.json` | match rate recorded; every fetched page has a revid; re-running fetches nothing new |
| M2 | Corpus builder: regular and akasha vaults for tier S, perturbations | akasha tier S: `setup` gives 0 conflicts and 0 reviews, `diff -r` is clean, an edit to a transcluded copy reaches all copies (through the real CLI, scratch HOME, port 7534) |
| M3 | Task generator and verifier: READ, WRITE, UPDATE for tier S | every gold span verified; 20% hand-audited into `audit.md` |
| M4 | Agent loop + closedbook + files harness | a unit test with a fake model; one real call through Purdue |
| M5 | akasha harness (write path settled) | a scripted tool sequence (search → get → write_atom → edit_node) works against the scratch daemon, and an edit reaches all copies |
| M6 | basic-memory harness for B and C | A scripted `write_note`/`search_notes`/`read_note` works on both vaults. For B, read akasha `status` afterwards. **Non-interference:** once basic-memory has indexed a snapshot, `diff -r` it against the source; if basic-memory adds frontmatter or permalinks, turn that off or record it as a threat. In B, after a scripted write, file hashes stay stable across two rescans, so the two watchers are not rewriting each other. **Token fairness:** expose only the read/write subset of tools that matches the akasha adapter, or report schema tokens separately. |
| M7 | **Pilot**: 10 READ + 3 WRITE + 3 UPDATE × all conditions, tier S, primary model. Measure calls per hour and failure rate, then check whether the budget estimate still holds. | a pilot report in STATUS; fix any harness bugs; **stop and flag** if any condition fails more than 20% of the time |
| M8 | Full run for tiers S, then M (build M first, verified as in M2), then L. Run in the background, resumable. Then run the analysis. | `results/summary.json`, README results; offline re-analysis reproduces the summary exactly |
| M9 | *Stretch:* MemoryAgentBench adapter. Point its OpenAI client at Purdue through `OPENAI_BASE_URL` / `OPENAI_API_KEY` set only in the subprocess environment, and run A/B/C on ≤ 50 Accurate Retrieval questions. | a results table, or a written reason why it was skipped |
| M10 | *Extension:* XL tier and the second model, only if time allows | added to the scale curves |

The tier M and L corpora are built only after the pilot passes. That way a design flaw costs
tier-S prices.

## 9. What "augment-able" means here

- A tier is a config entry.
- A corpus source is a plugin.
- A harness is an adapter with the 3-method interface.
- A model is a line in `models.toml`.
- A task family is a generator module that emits the shared task JSON schema.

Adding any of these reruns only the missing cache entries. `kbio run --tier <t> --conditions
A,B,C --model <m> --tasks read,write,update` is the single entry point, and `kbio analyze` is
offline and deterministic.

## 8a. Unattended runner (`agent/runner.py`, `agent/start.sh`)

Rebuilt 2026-09-27. The first runner (`run_scheduled.sh`) had two problems:
- It wrote `claude -p` text output only when an attempt ended, and sent job output to files. The
  panes looked dead, so they were stopped with Ctrl-C.
- A Ctrl-C to a pane killed the work.

The rebuild follows prior art and adapts it:
- Ralph-loop runners (for example ralph-claude-code, and llm-loop, both MIT) render
  `--output-format stream-json --verbose` live, pause on quota, and stop on a file.
- The repo's own `scripts/fleet/overnight_runner.sh` logs by path on every line and polls STOP
  during sleeps.
- llm-loop was not adopted:
  - It is two days old with 2 stars, so too new to depend on.
  - It pipes Claude through its renderer in the same process.
  - It reads OAuth credentials to call an undocumented usage endpoint.

How the rebuild works:
- **Work and viewing are split.** Every attempt's raw stream-json goes straight to
  `agent-logs/attempt-<ts>.jsonl`. A rendered `.log` is written after the attempt. The viewer
  (`runner.py view`, session `kbio-view`) tails the newest raw file and prints timestamped tool
  calls, results and text, plus a runner status line every minute. Closing or Ctrl-C'ing the
  viewer changes nothing.
- **Signals.** Claude runs in its own process session. The first Ctrl-C or SIGTERM to the runner
  stops it after the current attempt; a second one ends the attempt. Both are logged. STOP is
  checked every 10 s, including during sleeps.
- **Rate limits.** The runner reads the structured `rate_limit_event` (`status`, `resetsAt`,
  5-hour utilization) and sleeps until the reset plus 2 minutes, rather than parsing text.
- **State.** `agent-logs/runner-state.json` records the phase, attempt, session id, last-event
  time, next wake and 5-hour usage. `runner.py status` flags a run as STALLED after 15 minutes
  with no event and prints `claude --resume <session>` so any attempt can be opened afterwards.
- **Acceptance tests** (2026-09-27, all passed):
  1. A dummy attempt's tool calls appeared in the viewer within seconds.
  2. A real `uv run` job survived two pane Ctrl-Cs and a `tmux kill-session`. `job stop` recorded
     "received SIGTERM" and exit 143, and a natural exit was recorded as its code.
  3. STOP ended a sleep within 10 s.
  4. A Ctrl-C to the runner stopped it cleanly, and the stop was logged.
