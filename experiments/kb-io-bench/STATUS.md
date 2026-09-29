# kb-io-bench status

The agent updates this file after every milestone. The scheduled runner stops at the terminal
line described in agent/prompt.txt step 7, or at a line that starts with the blocked marker from step 6.

| id | state | verification / notes |
|---|---|---|
| M0 | DONE | `git submodule status`: MemoryAgentBench 5380260 (main, 2026-09-25), basic-memory c0bd87c (tag v0.23.2); `uv run ruff check .` clean. Own uv project (prerelease=allow for basic-memory's fastmcp 4.0.0b1); basic-memory is an editable path dep. |
| M1 | DONE | `uv run python -m kbio.sources.wikipedia`: 432 notes, 243 matched (56.2% of all, 60.6% of the 401 eligible; 142 exact, 70 redirect, 31 override; 27 disambiguation + 131 missing left unmatched; 25 Personal Workflow + 6 override skips). 236 unique pages, 236/236 with revid, pinned in `wiki-manifest.json`. Rerun: `http_requests: 0`, 408 cache hits. |
| M2 | DONE | `python -m kbio.corpus S`: 122 files (82 concept notes = 60 subset + 22 fill, 40 wiki pages), 1.53 MB regular / 1.59 MB akasha, 4,454 atoms, 149 lead copies (38 of 40 leads have 3+ copies). Perturbations: 685 facts over 236 pages (213 counterfactual, 472 fictional); tier S holds 114 (34 + 80). `python -m kbio.gate S` (real CLI, scratch HOME, :7534): setup 2.3 s, 122 files, 0 violations/pauses/conflicts/reviews; `diff -r` 0 changed files; an edit to a transcluded lead copy reached 4/4 copies via the watcher. GATE PASS. |
| M3 | DONE | `python -m kbio.tasks S` -> data/.../tasks/S.json: 90 tasks (READ 60 = 15 perturbed + 10 multi-hop + 10 unperturbed + 15 personal + 5 aggregate + 5 unanswerable; WRITE 15 memos of 3-4 facts; UPDATE 15 lead-definition changes, each lead in 4 copies). Verification: 72/72 gold spans verbatim in both vaults, 0 failures; 41 qwen2.5:72b drafting calls (cached; a rerun makes 0). Hand audit of 18/90 (20%) in data/.../audit.md: UPDATE follow-ups switched to a neutral code-scored question after 2 of 4 drafted ones leaked the change. |
| M4 | DONE | `uv run pytest tests`: 6 passed (fake model: tool loop, step cap forces a tool-less final turn, bad calls reported not raised, write/edit, closed book, truncation marker). Real call: gpt-oss:120b + files harness on tier S answered a planted fact (1925) correctly in 2 turns, 1 grep, 1,220 tokens, usage from the API, cited the right file + verbatim span. `kbio/llm.py` throttle is a cross-process file lock (4.0 s between request starts). |
| M5 | DONE | `python -m kbio.checks m5` on a fresh tier-S snapshot with a fresh store (setup 2.3 s): search (9 ms) -> get_node -> neighborhood -> read_note -> write_atom (new node indexed in 0.96 s) -> transclude -> edit_node: the new node reached 2/2 copies, a lead edit reached 4/4 copies (0.9 s, via the watcher); after rescan 0 violations/conflicts/reviews. Log: data/.../logs/m5-check.json. |
| M6 | DONE | `python -m kbio.checks m6` (log data/.../logs/m6-check.json). basic-memory v0.23.2 over MCP stdio, config + SQLite under the scratch HOME, semantic search on (fastembed bge-small, hybrid default); indexing tier S takes 40-90 s. Scripted write_note/search_notes/read_note/build_context/edit_note work on both vaults. **Non-interference:** the first index adds title/type/permalink frontmatter to all 122 files (nothing else changes); kept as a recorded threat, because turning it off leaves pre-existing notes without permalinks and `build_context` returns nothing. In B, after the index and after the scripted writes, akasha shows 0 violations/conflicts/reviews, and file hashes are identical across 3 rescans, so the two watchers do not rewrite each other. A bm `edit_note` on one lead copy reached **4/4** copies in B (the akasha daemon propagated it) and 1/4 in C. **Token fairness:** exposed subsets match the akasha adapter; tool-schema tokens READ/all: akasha 337/643, basic-memory 2,254/4,097, files 253/420, reported separately. |
| M7 | DONE | Pilot report (tier S, gpt-oss:120b, loop v2 + tasks v2; 10 READ + 3 WRITE + 3 UPDATE per condition, CB READ only; `kbio analyze --tiers S`, frozen copy in data/.../results-pilot/). **Gate: all pass**; failing tasks A 0/16, B 0/16, C 0/16, C′ 2/16 (12.5%: write-01/-03 writers hit the 20-step cap with 0 write calls), CB 0/10. 0 FAILED runs after the one outage artefact (C update-01, rerun ok). Reported counts: claimed-DONE-but-wrote-nothing A0 B0 C0 C′2; writes-with-no-effect C1 (C write-02: write calls, 0 landed); malformed_calls C′1. Step-cap rate A .03, B .16, C .24, C′ .39. Tokens/run mean A 17k, B 82k, C 99k (incl. 3.6k bm follow-up), C′ 41k, CB 0.4k. READ headline (perturbed, n=5) A .80, B/C/C′ .60, CB 0. WRITE landed A .92, B .83, C .58, C′ .25; follow-up correct A .75, B .42, C .33, C′ .17; 0 duplicates, 0 violations/reviews. UPDATE copies updated A 1.00, B 1.00, C .42, C′ 1.00 (C stale-read .67). Calls: 1,001 real calls in 96 min (911 gpt-oss + 90 judge) ≈ **625/h**; 2 ReadTimeouts + 1 gateway 400. Calls/task: READ ≈6.5 (CB 2), WRITE A 31 / B 54 / C 63 / C′ 67, UPDATE 9-15. **Budget restated:** a full tier ≈ 5,660 calls ≈ 9 h per model (PLAN §6 said 3,100 / 3.5 h); M/L also pay basic-memory indexing per snapshot. |
| M8 | DONE | **Scope: tier S (decision 2026-09-27).** Done 2026-09-27 21:47 (interactive). `fullS` exit 0 at 19:35: 1,490 calls in 8,027 s, 420 records (A/B/C/Cp 90, CB 60), 0 FAILED, so no outage reruns. The only traceback in `logs/full-S.log` is the user's Ctrl-C during C write-14 (17:21). That task restarted from a fresh snapshot (archive `work/S/C/archive/20260927-172131-write-14` interrupted, `-172433-write-14` the rerun), so no partial edits reached later tasks. `uv run kbio analyze --tiers S` run twice: `results/summary.json` and `results/gpt-oss_120b-S.md` are `cmp` identical. README section **Results: v1, tier S** has the headline table, the task-clustered tests and the threats (review items 2-6). Tier S = 122 files, about 232k words; the tier-M corpus (668 files, about 1.3M words) is built but not run. Earlier notes: **Scope reduced to tier S by decision (2026-09-27).** The full tier-S run resumed as launcher job `fullS`, then comes the S analysis and README (Next step 2). The tier-M corpus is built and verified but will not be run. **18:28: Review items 2-6 applied in `analyze.py`** (offline; analyze is not imported by the job): `followup_wrong_split` (stale / other definition), `landed_relaxed` (from the stored diffs; strict implies relaxed), `paired_by_family` (task-clustered: per-unit mean, sign test, bootstrap CI of the mean difference; the pooled table is relabelled as overstated), `tokens_net_mean` (minus schema × tool-bearing requests), `misses_perturbed`/`misses_all` (shown / never shown / shown-then-abstained), `unexposed_tool_calls`. Ran twice on the partial S data (A/B/C 90, Cp 68, CB 10): `cmp` identical. Final run + README after fullS. |
| M9 | TODO (stretch) | Skip unless time allows after V7; v2 V8 covers MemoryAgentBench. |
| M10 | SKIPPED | The user decided on 2026-09-27 to end v1 at tier S; scale moves to v2 (the KILT ladder). |
| M11 | STOPPED | **Context-window sweep (user, 2026-09-28 19:09): scale pressure without a bigger KB.** A real small-window model isn't available: Purdue ignores `num_ctx` (top-level and `options`), and gpt-oss/qwen3:8b/llama3.1 all accepted a 21-24k prompt; the smallest OpenRouter free window is 64k, above every tier-S peak (max 40k). So the same gpt-oss:120b runs under a client-side window: model label `gpt-oss:120b@ctx8k` (`agent.parse_model`; the API still gets `gpt-oss:120b`). Before a request with prompt + max_tokens (2000) > window, the oldest tool results, then the oldest carried reasoning, are replaced by a placeholder (`agent.fit_window`, the OpenAI `truncation:"auto"` / Anthropic clear-tool-results pattern). The system prompt, task and newest results are kept; if it still can't fit, the status is `context_overflow`, scored wrong. Gateway/estimate ratio learned per run. Runs affected in the baseline (peak > window - 2000): 8k A 23 / B 82 / C 81 / Cp 42 of 160; 16k 5 / 30 / 32 / 12. **Noise:** A (and Cp) replay the baseline from the response cache until the first eviction, but basic-memory results carry fresh UUIDs (`external_id`, `project_id`) and score jitter per index, so B/C never replay. Hence the control `@ctx128k` (unbound; native gpt-oss window). Tests: `tests/test_agent.py` +3 (parse, eviction keeps ids/system/task/newest and pre-eviction requests are identical, overflow); 72 pass, ruff clean; the baseline `analyze` output is `cmp` identical. Smoke (6 READ, A+B, 15 real calls): baseline files untouched (sha of path/mtime/size), A 3/3 full cache replays, B evictions 0/3/2, API prompt max 5,158 <= 6,144. Job `ctx` (launcher): 8k A,B,Cp -> 128k B,A,Cp -> 8k C -> 16k A,B,Cp -> 128k C -> 16k C. Est. about 14 h at 625 calls/h. Analysis: `kbio analyze --model gpt-oss:120b@ctx8k` (new rows: context_overflow_rate, evicted_run_rate, max_api_prompt_tokens, which must be <= window - 2000). **Stopped by the user 2026-09-28 19:38** (job `ctx` exit 143) to refocus on M12. Partial: `@ctx8k` A READ 60/60 + WRITE 8/15 kept as is; early A signal READ unchanged, WRITE follow-ups 0.91 -> 0.74 on 5 tasks (not significant). Sweep report: `uv run python -m kbio.window` -> `results/context-window-S.md` (per condition per label, paired vs the control, A-minus-others per label, the window check). Rerun it as stages finish; DONE when the chain exits 0 and the window check is clean. |
| M12 | STOPPED | **KB-effect test, pre-registered (user, 2026-09-28 19:40): is the akasha KB better for knowledge IO than a plain vault?** Pre-registration `M12-PREREG.md`, sha256 `45929b963821b0b4ba5350af79a3f4859477b0c3e750094b260baf96e404e293`, written 19:47 before any M12 data; **Amendment 1** (19:54, after 4 Cp records, no test looked at) -> sha256 `2d3b86f5ed167964db5b774693f465ee2d1d4b0fbd7fa97899785d5b0bfe1c0a`: file tools resolve a dropped "(1) " folder prefix (Cp and Ap alike; `(1) Universal` stalled Cp mc-5177 and would have credited akasha sync for a path quirk on 9/35 tasks); the 4 records are in `trash/m12-pre-amendment1/`; Cp READ/WRITE rerun under `S-kbrw`; new sensitivity H1-noprefix (26 tasks). 2×2: harness {basic-memory, files} × KB {akasha vault + daemon, plain vault} = B/C and new **Ap** (files tools on the akasha vault) / Cp; A is descriptive only. Tasks `kbio/tasks_kb.py` -> `tasks/S-kb.json`: 35 multi-copy UPDATEs (15 v1 + 20 new; 5 candidates had no valid rewrite) with 2 follow-ups (generic + one naming a pasted copy by path), 20 single-copy controls (planted facts, new value of the same shape). Verified in code; rebuild is byte-identical; 10 hand-audited OK. `run.py`: `--taskset` (results under `S-<name>/`), condition `Ap`, `update_followups`, `copies_scored`. Baseline S report `cmp` identical; 72 tests pass; ruff clean. Ap smoke (`apsmoke`, 2 v1 UPDATEs, excluded): the agent edited 2 of 4 copies and the daemon synced all 4; a second task edited all 4 with 0 conflicts; violations/reviews 0. Primary: white-box all-copies-consistent, one-sided exact sign test per stratum, Holm; the 20 unseen tasks must agree. Job `kb` order: UPDATE Cp, Ap -> B, C -> READ/WRITE Ap, Cp (`S-kbrw`) -> UPDATE A. Analysis `python -m kbio.m12` -> `results/m12-S.md`/`.json`, written 19:58 during the run before any test was looked at, sha256 `9d40daff65785ed55f3ea8109773ce0cfda3f091f69179768eca36209b36ccd3` (rev. 20:00: `complete` also requires 75 READ/WRITE records for Ap and Cp in `S-kbrw`) (tests/test_m12.py, 3; 76 tests pass). It prints `INCOMPLETE` until all 4 strata conditions have 55 records. Startup check 19:57: 0 label leaks, all records `ok`, baseline `S/` unchanged (path/mtime/size sha), the path fix works (Cp mc-5177 edits `(1) Universal/Truth.md` via `Universal/Truth.md`). |
| M13 | DONE | **akasha as AI memory on MemoryAgentBench Conflict Resolution (FactConsolidation, 800 questions, external SOTA baselines).** Plan: `M13-PLAN.md` (2026-09-28 23:19). Memory = Evidence (chunk) + Claims (facts) + `contradicts` / tombstone-redirect; conditions LC, R-bm25, R-facts, **R-rule** (the decisive plaintext control), K0/K1/K2; prior art Knowl (supersede ablation, in the submodule). Proposed akasha changes = build-plan **M22** (search `limit`/`mode=any`/`type`/`status`, Unicode terms, CLI parity); verified gaps: AND-only search, unbounded results, tombstones returned, ASCII-only terms. Decisions (a)-(f) in plan §7. Nothing started: no `src/` edits, no jobs, no LLM calls (the CR parquet, 1.5 MB, was fetched to the scratchpad for inspection). **2026-09-29 00:15: M22 done in akasha** (search `mode=any`/`limit`/`type`/`status`, Unicode terms, CLI parity, `journal` node type, `contradicts` → `contradiction` review, `supersede` override; `make check` 897 passed, `make battery` 58; not committed). Adapter `kbio/mab/` (export with the benchmark's own code, conflict rule, runner, akasha memory, scorer, analysis). Pilots: baselines 160 calls, K 118 calls, 0 FAILED; Purdue's gpt-oss limit is **65,536 tokens** (LC set to 62,000). **Pre-registration `M13-PREREG.md` sha256 `aaa08f3c42ff6248a50c27f38d4546688175631795beffdcff68e014890d731a`**, written before the full run; **Amendment 1** (00:19, the secondary agentic subset fixed before it runs) -> `dba946e60445c9c984c71f294d13eb214753bebddcf8980b1921c404e7138356`. Job `mab` launched. |
| V0 | DONE | The user decided on 2026-09-27 17:20 EDT: finish v1 tier S, then build v2 per `V2-PLAN.md`. v1 tier M/L runs are dropped; the tier-M corpus stays built but unused. |
| V1 | DONE | `kbio/crud_kb/` (store.py, server.py, `python -m kbio.crud_kb serve|ingest|export`), stdlib only. `uv run pytest tests`: 61 passed after the 17:45 fix (35 in `tests/test_crud_kb.py`: CRUD, tombstones (a deleted id reads exactly like a missing one; re-ingesting *other* ids leaves it deleted, but re-ingesting the same id revives it, since ingest is an upsert), patch rules, FTS5 sanitising (`55-Q`, U+2011, `AND`, `NEAR(`, `*`, all-punctuation -> `[]`), hyphenated codes as phrases, AND-then-OR fill, ±150-char snippet on a 10k-char single-line paragraph with the match at the end, read/list pagination with no gaps, server `read` pages concatenate exactly and each stays ≤ 2,000 cl100k tokens (dense numbers, prose, CJK), result caps, JSON-RPC, `integrity-check` after every mutation, md + KILT-jsonl ingest). **MCP round trip:** the real mcp 2.2 client against the stdio server, driven by `run_agent` with a fake model: create -> search -> read -> patch -> search new/old -> bad patch -> delete -> read/search gone. `ruff check .` clean. Scale smoke test: 100k synthetic 300-word docs ingest in 34.5 s (397 MB); search 4-10 ms, 228 ms for an all-stopword query. |
| V2 | IN PROGRESS | Done offline (53 tests pass, ruff clean): `kbio/harnesses/mcp.py` (any MCP stdio server from `config/harnesses/<name>.toml`: command placeholders, allowlists, reset/ingest steps with timings), `config/harnesses/crud-kb.toml`, `kbio/textmode.py` (text tool-call `ChatFn` wrapper + `pick_tool_mode` probe), `config/models.toml`; scripted CRUD sequence passes on crud-kb; text-mode agent over crud-kb passes with a fake model. **Left (after `fullS`):** akasha-mcp + basic-memory manifests/wrappers and the same CRUD sequence on them; ingest-time table at 1e2/1e3/1e4; `llm.py` generalised to `models.toml` endpoints; real llama3.3:70b text-mode READ task on crud-kb; `crud-kb` script entry in pyproject. **Also:** (a) `McpHarness.setup` must refuse a non-empty `store/` (or move it to trash), so DELETE/UPDATE state can't leak between tasks; (b) ingest once per rung into a template DB and copy it into each WRITE/UPDATE/DELETE snapshot (`ingest=False`), as v1's bm template does, since re-ingesting 1e6 takes ~6 min per task. **18:32 (still IN PROGRESS):** v2 driver `kbio/run2.py` + `kbio/prompts2.py` (`python -m kbio.run2 --model M --harness H --rung N --ops read,create,update,delete,control [--limit k] [--prune]`): one template store per (harness, rung, arm) built by the manifest's ingest step (stats + store bytes + an `{id: hash}` export index kept); READ on the template with read-only tools; each CREATE/UPDATE/DELETE copies the template store into a fresh per-task store (so ingest runs once per rung, item (b)), sessions opened by the driver never run reset/ingest (item (a) handled in the driver, `mcp.py` untouched while fullS runs), white-box export diff by id, follow-ups on the task store with read-only tools, per-task dir archived to trash (or pruned by file at big rungs). `crud-kb.toml` gained `corpus = "jsonl"` + `[export]`. `tests/test_run2.py`: synthetic 3-page rung, oracle fake model, crud-kb over MCP: READ, UPDATE (2 copies patched), DELETE, CREATE, template untouched, control arm, then analyze2 over the records. **crud-kb ingest (base arm, KILT rungs):** 1e2 0.07 s / 4.5 MB; 1e3 0.19 s / 10.8 MB; 1e4 1.37 s / 53 MB; 1e5 14.7 s / 476 MB; **1e6 198 s / 4.7 GB** (job `crudtpl`, max RSS 0.46 GB). At 1e5+ run WRITE-like ops with `--prune` (a per-task store copy is the full template). **18:36:** `kbio/mcp_wrap.py` serves any v1 harness class over MCP stdio (crud-kb's JSON-RPC loop); `config/harnesses/files.toml` = v1 C′ tools on an md vault (`corpus = "md"`: the corpus is copied into `{store}/vault`; `[export] mode = "vault"` maps md names back to page ids via the corpus's `.ids.json`); `tests/test_run2.py::test_run2_files_harness_md_vault` passes (READ/UPDATE/DELETE/CREATE + analyze2). Left: akasha-mcp + basic-memory manifests/wrappers and their ingest times + the scripted CRUD on them (scratch HOME), `llm.py` → `models.toml`, llama3.3 text-mode READ on crud-kb, pyproject script entry. |
| V3 | ON HOLD | **ON HOLD: awaiting human approval of M8 and V1 (user, 2026-09-27 21:15).** Don't start or continue it: no code edits, jobs, downloads or LLM calls for it. Only the human lifts a hold, by editing this cell. State before the hold: DONE. `kbio/kilt.py` + 2 tests. Jobs `kilt` (download, 37,318,876,722 bytes, md5 d1dca62a… = S3 metadata; `KILT download verified`) -> `kiltrungs` (exit 0, 17:51): `index` (5 workers) 5,903,530 pages, 5,889,297 eligible; `plan` 26 s / 1.0 GB RSS; `verify` -> `nested: true`, rungs 1e2 100 / 1e3 1,000 / 1e4 10,000 / 1e5 100,000 / 1e6 1,000,000 / all 5,889,297, `core_pages_in_1e2: 100` (50 core + 50 linked); `build --upto 1e6` -> pages.jsonl 2.94 GB in 7:57, 237 MB RSS. Manifest (per-rung id-list sha256, 50 core pairs; public data) tracked at `results/v2/kilt-rungs.json`. Cosmetic: KILT nested sections use `.:` (e.g. `## History.:Early years`), not `:::`, so sub-headings are one flat level; text unaffected, left as is. |
| V4 | ON HOLD | **ON HOLD: awaiting human approval of M8 and V1 (user, 2026-09-27 21:15).** Don't start or continue it: no code edits, jobs, downloads or LLM calls for it. Only the human lifts a hold, by editing this cell. State before the hold: IN PROGRESS. `kbio/probes.py` + `tests/test_probes.py` (3 tests). Offline part done 18:25: `probes build` (5 s, no LLM) plants 293 facts on the 100 rung-1e2 pages (198 fictional, 95 counterfactual; `perturb_page` unchanged, seed 20260928) and builds 130 tasks: READ 30 single fictional + 10 page→probe hops + 10 probe→probe hops + 10 aggregates (2-4 facts, `answer_mode: all`) + 10 near-miss unanswerables; CREATE 20 memos (3-4 facts; control arm bulk-inserts them); UPDATE 20 (each fact in 3 copies); DELETE 20 (10 single, 10 in 3 copies). Scan job `kiltscan` (5 min, `LC_ALL=C grep -F -o -w -i` over the full 37 GB source, 940 candidate markers/towns/persons, 87 found): 72 values excluded before drawing, 4 facts with a marker found in KILT never probed. `probes verify`: 130 tasks, 185 questions, 192 gold spans, **0 failures** (spans verbatim in the patched base/control arms, md names = `write_md` rule, UPDATE/DELETE copies exactly as recorded, aggregate markers only on gold pages, unanswerables unanswerable, values unique across planted+created+updated). `probes corpus 1000 base|control [--md]` writes a patched rung (tested: 1,000 pages both formats). Self-audit 26/130 (20%) OK: `data/.../v2/audit.md`. **Left (LLM, after fullS):** `probes draft` (20 counterfactual READ questions, qwen2.5:72b), `probes cb gpt-oss:120b` and `probes cb llama3.3:70b` (drop any leaked probe), `probes verify --exposure` (cf exposure per rung), audit 4 cf tasks. |
| V5 | ON HOLD | **ON HOLD: awaiting human approval of M8 and V1 (user, 2026-09-27 21:15).** Don't start or continue it: no code edits, jobs, downloads or LLM calls for it. Only the human lifts a hold, by editing this cell. State before the hold: IN PROGRESS. `kbio/score2.py` (verdicts per kind: single/hop/create = value and no abstention, aggregate = all values (`partial`), unanswerable = abstain, UPDATE = new and not old (`stale`), DELETE = abstain and no retracted value (`zombie`), counterfactual = planted and not real year; citations by id / md name / unique title, precision over all gold ids, recall over distinct gold spans; shown/never-shown; white-box CREATE landed/relaxed, UPDATE copies updated/stale, DELETE copies removed + collateral) and `kbio/analyze2.py` (per harness × rung × op; task-unit bootstrap CIs; task-clustered paired sign tests between harnesses; reader/writer cost incl. net-of-schema tokens, malformed and unexposed calls; ingest cost; results/v2/summary.json + md). `tests/test_score2.py` (3) + analyze2 in test_run2; 68 tests pass, ruff clean. **Regression (`python -m kbio.score2`) on the v1 S records so far: 358/358 code-scored questions get the same verdict as `analyze.correct`, 0 different** (131 judge-only or v1 UPDATE-phrase questions skipped by design). DONE after the rerun on the complete S data. |
| V6 | ON HOLD | **ON HOLD: awaiting human approval of M8 and V1 (user, 2026-09-27 21:15).** Don't start or continue it: no code edits, jobs, downloads or LLM calls for it. Only the human lifts a hold, by editing this cell. State before the hold: TODO. pilot at rung 1e3 |
| V7 | ON HOLD | **ON HOLD: awaiting human approval of M8 and V1 (user, 2026-09-27 21:15).** Don't start or continue it: no code edits, jobs, downloads or LLM calls for it. Only the human lifts a hold, by editing this cell. State before the hold: TODO. ladder run + analysis + README |
| V8 | TODO (optional) | memorybench bridge / MemoryAgentBench AR slice |

## Background jobs

Check with `python3 experiments/kb-io-bench/agent/runner.py status`, which reads
`data/experiments/kb-io-bench/jobs/*.json`.

- **Nothing is running (checked 2026-09-27, unattended session after 21:47).**
- `fullS` exited 0 at 19:35. S records are complete: A/B/C/Cp 90 each, CB 60, 0 FAILED.
- Also finished with exit 0: `kilt`, `kiltrungs`, `kiltscan`, `crudtpl`; earlier: `pilot`,
  `rerun`, `writetest*`.
- The scratch daemon (`daemon` job) is stopped (exit 143 = stopped by `akasha_ctl.stop()`).
  `akasha_ctl.start()` restarts it on demand as the launcher job `daemon` (tmux
  `kbio-job-daemon`), and a pane Ctrl-C does not stop it.

## Next step

**Approval gate (user, 2026-09-27 21:15).** V3, V4, V5, V6 and V7 are ON HOLD until a human
approves M8 and V1. Allowed work: finishing M8, and V2 (not held). Nothing else.

1. **Finish M8: DONE (21:47, interactive).** See the M8 row and the README results section.
2. **Approval request: DONE** (see `## Approval request` below). **Write the approval request first** (the human is waiting on it). Add a section `## Approval request` to this file
   covering:
   - M8: the headline tier-S results table, where the README section is, and the known threats;
   - V1: what `crud-kb` does, its tests, and the MCP round trip;
   - the exact commands a human can run to check both;
   - what V3–V7 would do next, and their estimated cost.

   Don't add the BLOCKED line yet.
3. **Optional: continue V2: SKIPPED this session** (its verifiable remainder needs KILT rungs; see Log). **Optional: continue V2** (not held). Stop at the first point where it would need V3–V7 work,
   such as KILT rungs, probes or the pilot.
4. **Stop.** Add this exact line, which stops the runner:
   `BLOCKED: awaiting human approval of M8 and V1 (V3-V7 on hold)`

BLOCKED: awaiting human approval of M8 and V1 (V3-V7 on hold)

**To lift the hold (human):**
- Remove the `BLOCKED:` line.
- Change the V3–V7 states from ON HOLD back to TODO or IN PROGRESS.
- Run `experiments/kb-io-bench/agent/start.sh now`.

## Approval request

Written 2026-09-27 (unattended session). Everything below was re-checked in this session:
`uv run pytest tests` 69 passed, `uv run ruff check .` clean, and a fresh
`uv run kbio analyze --tiers S` gave `results/summary.json` and `results/gpt-oss_120b-S.md`
byte-identical (`cmp`) to the stored copies.

### M8: v1 tier-S results (please approve)

Full section: `README.md`, "Results: v1, tier S, gpt-oss:120b (M8)"; full tables in
`results/gpt-oss_120b-S.md`. Run `fullS`: 1,490 calls, 420 records, 0 FAILED, exit 0.
A = akasha stack, B = basic-memory tools on the akasha KB, C = basic-memory stack,
C′ = grep/files, CB = closed book.

| metric | A | B | C | C′ | CB |
|---|---:|---:|---:|---:|---:|
| READ perturbed, correct (n=25) | 0.84 | 0.52 | 0.48 | 0.96 | 0.00 |
| WRITE landed strict / relaxed | 0.88 / 0.92 | 0.89 / 0.95 | 0.84 / 0.93 | 0.37 / 0.37 | – |
| WRITE follow-up correct (n=55) | 0.91 | 0.73 | 0.78 | 0.36 | – |
| UPDATE all copies updated | 1.00 | 1.00 | 0.13 | 0.60 | – |
| UPDATE stale reads | 0.00 | 0.00 | 0.20 | 0.27 | – |
| tokens/run, net of schemas (mean) | 18.0k | 43.9k | 43.5k | 38.0k | 0.5k |
| tasks failing the gate | 0 | 0 | 0 | 9/90 | 0 |

Task-clustered sign tests: A vs B READ perturbed +0.32 (9/1, p 0.02); A vs C UPDATE +0.47
(7/0, p 0.02); B vs C UPDATE +0.27 (4/0, p 0.13); A vs C′ WRITE +0.54 (10/1, p 0.01);
A vs C′ READ perturbed −0.12 (0/3, p 0.25).

In short: consistency under UPDATE comes from the akasha KB (A and B both 15/15); the akasha
harness reads better and costs about 2.4× fewer net tokens than basic-memory tools; plain grep
reads best at this size but fails at writing.

Known threats (README "Threats to validity"): A-vs-B is our tuned adapter vs basic-memory as
shipped; one model, one tier, n 5-25 task units per family (wide CIs); the UPDATE follow-up is
ambiguous (stale-read rate is the primary metric); `landed` strict penalises entity notes
(relaxed reported); basic-memory rewrites frontmatter on first index; unperturbed and personal
questions don't discriminate; the judge is qwen2.5:72b (headline metrics are code-checked).
One C write task was Ctrl-C'd and rerun from a fresh snapshot (both archived).

### V1: `crud-kb` (please approve)

`kbio/crud_kb/` is a stdlib-only MCP stdio server and CLI (`python -m kbio.crud_kb
serve|ingest|export`) over SQLite + FTS5. It is the harness-neutral yardstick for v2.
- Tools: search (FTS5, sanitised queries, hyphenated codes as phrases, AND-then-OR fill,
  ±150-char snippet around the match), read (paginated, each page ≤ 2,000 cl100k tokens),
  list, create, patch (exact-string replace), delete (tombstones: a deleted id reads exactly
  like a missing one).
- Tests: 35 in `tests/test_crud_kb.py` (CRUD, tombstones, patch rules, FTS sanitising,
  snippets, pagination, caps, JSON-RPC, `integrity-check` after every mutation, md and
  KILT-jsonl ingest).
- MCP round trip: `test_mcp_round_trip_through_agent_loop` drives the real mcp client against
  the stdio server through v1's `run_agent` with a fake model: create, search, read, patch,
  search new/old, bad patch, delete, read/search gone.
- Scale smoke test: 100k synthetic docs ingest in 34.5 s; search 4-10 ms.

### Commands to check both

```
cd experiments/kb-io-bench
uv run pytest tests -q                         # 69 passed
uv run pytest tests/test_crud_kb.py -q         # V1: 35 passed, incl. the MCP round trip
uv run ruff check .
cp results/summary.json /tmp/sum.json          # M8: offline, no LLM calls
uv run kbio analyze --tiers S                  # regenerates results/summary.json + md
cmp results/summary.json /tmp/sum.json && echo identical
```

### What V3-V7 would do next, and the cost

- **V3 (KILT rungs):** already built before the hold (download md5-checked, nested rungs
  1e2-1e6 + all 5.9M verified). Needs only a DONE mark: 0 calls.
- **V4 (probes):** offline part done and verified. Left: `probes draft` (20 questions,
  qwen2.5:72b), the closed-book filter with gpt-oss:120b and llama3.3:70b (about 185 questions
  each), `probes verify --exposure`. About 400 calls, under 1 h.
- **V5 (scoring):** code done; regression on the v1 S records is 358/358 identical. Left: rerun
  on the complete S data. 0 calls, minutes.
- **V2 remainder (not held, but it cannot reach DONE while the hold stands):** akasha-mcp and
  basic-memory manifests, ingest times at 1e2/1e3/1e4, a llama3.3 text-mode READ. The ingest
  table needs the KILT rungs (V3 artifacts), so V2 stays IN PROGRESS until the hold lifts.
  basic-memory ingest is about 0.5 s/file (1e4 ≈ 1.4 h).
- **Costs below are ranges, because V2-PLAN §7 is optimistic.** It assumes about 15 calls per
  CREATE task, but v1 measured 31 / 54 / 63 / 67 calls per WRITE task (A / B / C / C′). v1's
  own plan was also about 1.8× low (5,660 calls per tier measured, 3,100 planned). Re-measuring
  calls per op and calls per hour is part of V6's purpose.
- **V6 (pilot, rung 1e3, crud-kb + akasha-mcp + basic-memory, 10 tasks per op).** Per harness:
  READ 10 × 6.5 = 65, CREATE 10 × 15-60 = 150-600, UPDATE 10 × 16-21 = 160-210,
  DELETE 10 × 14 = 140, judge about 50. That is about 565-1,065 calls per harness, so
  **1.7k-3.2k calls, about 2.7-5 h** at 625 calls/h.
- **V7 (ladder, the trimmed design of V2-PLAN §7: all ops at 1e3/1e5/5.9M, READ-only on a
  subsample at 1e2/1e4/1e6).** About **30-55 h** of endpoint time for one model; a second model
  roughly doubles that. Ingest comes on top: akasha about 33 h at 5.9M (extrapolated, and
  millions of atoms is an untested store size); basic-memory probably drops off above 1e5.
  Disk is fine (196 GB free), with one full-size store at a time.

## Review items (interactive review 2026-09-27 14:45, apply in M8 analysis/README)

1. **Fixed: exact-match missed Unicode hyphens.** gpt-oss writes U+2011 ("97‑W"); NFKC folds
   it to U+2010, not "-", so `exact_hit` failed. 23 of 320 scored S questions flipped to correct
   (A 10, B 8, C 1, Cp 2) and 9 citation recalls went 0 -> 1; every change has a dash in the text
   (checked). `score.norm` now maps U+2010/2011/2012/2212 to "-" and drops U+00AD.
   `score.rescore()` recomputes the text-only fields from each record's stored answer and
   citations, and `analyze.questions()` applies it, so records written before and after the fix
   score alike. Stored records are untouched. The running job had already imported `score`, so it
   is unaffected. 17 tests pass, ruff clean, analyze is deterministic (run twice, `cmp`).
   WRITE `landed` is not affected (0 writer diffs contain U+2010/2011).
2. **UPDATE follow-up is ambiguous.** "What is the lead definition of X in the knowledge base?"
   Concept notes also carry the user's own `Definition:` line. All 5 failures (A update-05; B
   update-07/09/10/12) quote a different definition, not the old one: stale 0, copies 4/4.
   Don't change the tasks (that would break S/M/L comparability). Report `stale_read_rate` as the
   primary UPDATE read metric. Split non-correct follow-ups into stale (the old phrase) and
   other-definition.
3. **WRITE `landed` penalises entity-note writes.** Landed means marker and answer in one
   paragraph. basic-memory agents often create a new entity note (e.g. `Societies/Straughan
   Lexicon Society.md`, `Places/Pellamfield.md`), with the marker as the title and the answer in a
   bullet. Seen in B write-01/-12 and C write-01. Add `landed_relaxed`: the answer newly appears
   in a file whose path/title or paragraph contains the marker. Report both.
4. **Paired tests pool clustered questions.** The 55 write follow-ups come from 15 tasks, and
   "all questions" mixes families, so A-vs-B p=0.0002 is overstated. Report paired tests per
   family, plus a task-clustered version (follow-ups aggregated per task). Don't headline the
   pooled p.
5. **Report tokens net of tool schemas** (schema tokens × requests). Estimated per READ run:
   A 12.2k total / 10.2k net; B 40.2k / 25.9k; C (pilot) 29.9k / 17.9k; Cp 13.7k / 12.3k. The
   schema is 35-54% of B/C tokens, but A is still about 2.5x cheaper than B net.
6. **Threats for the README.**
   - (a) **The A-vs-B harness gap is "our adapter vs basic-memory as shipped".** The akasha
     harness was built for this experiment and tuned during the pilot (match-window snippets).
     basic-memory `search_notes` returns the head of the note, not the matched passage. In B
     read-fi-02 the right note (Truth) was the top hit for "Straughan", but the agent concluded
     "search is not returning" and answered NOT FOUND; cf-06, fi-04 and fi-07 are similar. Count
     these "top hit had it, answered NOT FOUND" cases per condition.
     Measured at 14:55 on the S data so far: of the failed perturbed READs (single-hop and
     multi-hop), **every** miss is "the answer value never appeared in any tool result shown to the
     agent". That is A 4/4, B 12/12, C 4/4, Cp 1/1, and 0 cases where the answer was shown and then
     answered wrongly. So A's lead is about what each harness *shows*: atom-level hits plus the
     match window, versus the note head plus the 2k-token cap on `read_note`. It is not about
     reasoning. Add this split to analyze.py as a metric.
   - (b) **The unperturbed questions don't measure leakage.** They are text-referential ("mentioned
     in the text", "according to the paragraph"), so CB = 0 there by construction. The leakage
     control rests on the perturbed facts only.
   - (c) **Personal questions are at ceiling** (1.00 in every KB condition) and judge-scored. CB
     scores 0.5 from generic knowledge (the PARA question). So they don't discriminate between
     conditions.
   - (d) **B's agent calls tools that aren't exposed** (`list_memory_projects`, `find_in_note`),
     from gpt-oss priors about basic-memory. Report the count.
7. **Scheduler.** The `kbio` runner tmux session (run_scheduled.sh) died between 14:09 and 14:39
   with no log line and no STOP file. The cause is unknown; nothing in kbio kills processes.
   `kbio-job-fullS` is unaffected. Re-arming is the user's call.
8. **tmux targets must be exact.** `-t name` prefix-matches, so with `kbio` gone,
   `tmux kill-session -t kbio` kills `kbio-job-fullS` (tested on a private socket). PLAN,
   prompt and `akasha_ctl.py` now use `-t '=name'`; use that form everywhere.
9. **Re-verified tasks with the new `norm`:** `python -m kbio.tasks S --verify-only` and
   `M --verify-only` both report 72 gold spans and 0 failures. The corpora have U+2010-2012 only
   in natural Wikipedia text; the tasks, facts and UPDATE sentences have none, so S (scored with
   the old norm) and M/L (the new norm) score WRITE/UPDATE the same way.

## Log

- 2026-09-27 00:30 EDT: plan written. Implementation run scheduled for about 05:25 EDT through
  `agent/run_scheduled.sh` in tmux session `kbio`.
- 2026-09-27 05:40 EDT: M0 done. No ripgrep on this host; the C′ `grep` tool is pure Python.
- 2026-09-27 06:10 EDT: M1. Wikimedia returns HTTP 429 to User-Agents without a contact URL; the UA now
  carries the repo URL (not the user's email). Cleaning: MathML fallbacks folded to `[math]`, braces
  -> parens (span grammar), reference-type sections dropped, one paragraph per line.
- Tier S coverage: only 18 of the 60 concepts-retrieval notes map to a wiki page (after skipping the
  wrong-sense "Energy (mental)"). Narrowest fix, applied in M2: tier S = the 60 notes + their pages +
  the mapped concept notes (from M) most linked from the 60, until S holds 40 wiki pages.
- 2026-09-27 06:05 EDT: M2 design choices. Both vaults use `clean_note` text for concept notes (so B vs C
  differs only in format, not content). Perturbations never touch the lead sentence (it is the copied
  definition, owned by UPDATE tasks). Planted values are unique corpus-wide. The akasha daemon runs from
  the repo `.venv` (current `main`), not the older uv-tool install. Per-task snapshots: a fresh copy
  registered as a new sync root costs ~2.3 s at tier S, so every task gets its own copy + root.
- 2026-09-27 06:10 EDT: M5 write path settled: **file-based** writes that the daemon's watcher ingests (the
  real user path; agent tokens would turn API mutations into review proposals, spec §4.11). The harness
  mints checksummed ids, and `edit_node` waits (≤15 s) until every mirror shows the new text.
- Design fix: the daemon's search and mirroring span *every* registered sync root, so per-task snapshot
  copies cannot share one store (identical ids in two copies would be mirrored across tasks). Each
  snapshot gets a fresh store in the same scratch HOME (`akasha_ctl.reset_store`: stop daemon, move
  store.db + token to trash, `akasha setup`). READ tasks get read-only tool subsets in every condition
  (no writes possible), so one snapshot per (tier, condition) serves all READ tasks; WRITE/UPDATE tasks
  each get a fresh snapshot.
- 2026-09-27 06:20 EDT: M6 observations for the README: basic-memory text search misses a hyphenated
  code ("55-Q"), and its hybrid search did not surface a note written seconds earlier in C (embedding
  lag). Both are real harness properties, so they are left as they are. Indexing cost (embeddings) grows
  with the tier. For M/L the runner should index once per (tier, condition) and copy the index into each
  WRITE/UPDATE snapshot.
- 2026-09-27 06:40 EDT: runner smoke test found a C′ harness bug. `grep` printed the first 300 characters of
  a matching line, but Wikipedia paragraphs are single long lines, so the match was invisible; the agent
  grepped 12 times and ran out of steps. Now grep shows a window around the match. The same blind spot
  existed in the akasha `search` snippet (first 200 characters of a node); it now shows a 200-character
  window around the first query term when the term would otherwise be cut off. basic-memory's own
  `match` snippets are left as they are. `llm.chat` now fails fast on non-429 4xx errors, and every
  real request is logged to `logs/llm-calls.log` (for calls per hour).
- 2026-09-27 07:00 EDT: `## Related pages` lists are no longer capped at 15, because in bigger tiers a cap
  could push a wiki->wiki hop-gold link out. No S page has more than 12 in-tier links, so tier S is
  byte-identical (re-verify with a rebuild + `diff -r` once the pilot is done; the pilot copies from
  corpora/S, so S must not be rebuilt while it runs). Distractor pages (L) are fetched without link
  lists; they are only needed as text. Titles containing "(identifier)" are excluded.
- 2026-09-27 07:10 EDT: session end. `kbio/analyze.py` is written (lint clean, not yet run on results);
  a README draft covers design, corpus, tasks, harness fairness and threats. Early pilot throughput:
  about 360 real calls/hour (latency-bound, not the 900/hour of the 4 s throttle). If that holds, a full
  tier is about 9 h per model; decide in M7 whether to trim (e.g. drop C′ from WRITE/UPDATE) or to
  run two processes. Only CB can run in parallel safely: every other condition shares the scratch
  daemon/basic-memory HOME, and `Env` for C/C′ stops the daemon.
- 2026-09-27 08:10 EDT: **pilot stopped** (`tmux kill-session`) after A WRITE + B write-01 and before C, C' or CB had
  run. Reason: WRITE was at a floor. In 4 WRITE tasks, 1 of 16 facts landed. Each writer spent
  all 20 steps searching for the (new, fictional) facts, never called a write tool (A made 1
  `write_atom` in total, on step 19), then claimed "DONE". B write-01 took 37 min, mostly on 504s.
  The READ (A, B) and UPDATE (A: 3/3, 4/4 copies) results are kept: their requests are unchanged.
  The WRITE results moved to `data/.../results-pilot-v1/`. Fixes, each identical across conditions:
  (1) `prompts.WRITE` now says the facts are new, so searching for them finds nothing, and to use
  a few calls to find where each belongs and then write. `SYSTEM` is untouched, so READ cache keys
  are unchanged. (2) `agent.run_agent`: a turn with no tool call and empty content (gpt-oss did
  this on 10-20% of forced finals, and once on a `length` cut) gets one re-ask without tools
  (`reasked_empty` is recorded). (3) `llm.chat` timeout 300 -> 150 s (p99 latency 40 s; the
  gateway returns 504 only after about 300 s, and 8 of those cost about 40 min in the pilot).
  Client-side exceptions are now logged in `llm-calls.log`. Unit tests: 14 pass (a new re-ask
  test).
- 2026-09-27 08:13-08:28 EDT: the single-task write test hit a gpt-oss outage. 4 consecutive 150 s
  ReadTimeouts on a tiny first request; a direct probe also timed out for gpt-oss, while qwen2.5:72b
  answered in 7 s. The test was killed before it could record a spurious FAILED result (no result file
  written). A new `kbio/probe.py` waits for model health, and the retest is chained behind it in
  `kbio-job-writetest`.
- 2026-09-27 08:59 EDT: visit. `kbio-job-writetest` still probing; gpt-oss:120b has not answered since 08:13 (every probe times out, last 08:58). No result file yet. Nothing else to do that does not depend on it; session ended.
- 2026-09-27 09:30 EDT: gpt-oss recovered (after 48 min). The A write-01 retest with the "facts are new" prompt still failed: 0 write calls, 20 steps of repeated searches ("Lexicon Society" x4, "first discussed" x3), then a final that **claimed** four facts were added (0 landed). Record "claimed DONE but wrote nothing" as a reported behaviour in M7/M8. Result moved to `data/.../results-pilot-v2/`.
- Likely task confound: WRITE memos use the same templates as the planted READ perturbations, so the first search returns look-alikes (Pemberton/Debugging), and the writer seems to keep checking for duplicates. Narrowest prompt fix: the false "searching will find nothing" sentence was replaced by "none of them is in the knowledge base yet (similar-looking facts about other topics are not duplicates)". Backstop: `run_agent(warn_left=4)` for WRITE/UPDATE only (identical in every condition) adds one user message "You have 4 tool calls left..." 4 steps before the cap. READ requests are unchanged. The kept A UPDATE pilot results predate the warning, so rerun them with the pilot. 15 unit tests pass; ruff clean.
- Cross-model check: llama3.3:70b on A write-01 printed a tool schema as plain text on step 1 (no native tool call), so it says nothing about the task. It also suggests llama is not a drop-in PLAN §6 swap for the tool-heavy harnesses.
- Reasoning pass-back: the endpoint (LiteLLM-style gateway) returns gpt-oss reasoning as `message.reasoning_content`, and `llm.chat` drops it. A/B probes show that passing it back as `reasoning_content`, `reasoning` or `thinking` has **no effect** on the next step (identical output with and without), so the gateway strips it. Native pass-back is not possible; the loop is left as it is. `python -m kbio.probe <model> --fields` prints the returned fields.
- Retest with prompt fix + warning (A write-01, gpt-oss): 1/4 facts landed (1 `write_atom` at step 15, linked, 0 duplicates, 0 violations). Still looping on the same searches after the write and after the warning.
- 2026-09-27 09:40 EDT: **task confound confirmed.** 14 of 55 WRITE facts (25%) had the same template kind as a fact already planted on their target page (write-09: 4/4). `tasks.py` now redraws such facts with a separate rng. Tasks v2: all 75 READ/UPDATE tasks are byte-identical (checked), 10 WRITE tasks changed, 0/55 collisions; 72/72 gold spans verified, 0 LLM calls; v1 in `trash/tasks-v1/`; note in `audit.md`. Retest under loop v1: 0/4, still looping, and the final was a leaked raw harmony tool call (`<|start|>assistant<|channel|>commentary to=functions...<|call|>`).
- 2026-09-27 09:48 EDT: **loop v2** (the actual fix, identical in every condition). The gateway strips passed-back reasoning, so gpt-oss re-planned from zero at every step. Now: (1) `llm.chat` keeps `reasoning_content` as `message.reasoning`, and `run_agent` carries it back as the visible content of that tool-call turn (a probe showed that content is seen). For models that return no reasoning, nothing changes. (2) Content containing harmony markers and no parsed tool call is answered as a malformed call (it counts as a step, tools stay available, `malformed_calls` is recorded), not taken as the final answer. (3) Tool-bearing requests use a cache namespace (`_kbio_loop: 2`), so v1 chains cannot replay; judge/drafting keys are unchanged. 17 unit tests pass; ruff clean. **Retest A write-01: 4/4 landed, 4/4 linked, 0 duplicates, 0 violations, 12 steps, no loop** (prompt tokens 104k, which is the cost of carried reasoning; report it). GATE PASS. All loop-v1 pilot results (A/B READ, A UPDATE) moved to `results-pilot-v3/loop1/`, so the whole pilot reruns under v2.
- 2026-09-27 10:24 EDT: visit. `kbio-job-pilot` alive (log: 65 lines, B write started; READ done for A/B/C/Cp/CB, A write-02/03 and update-01..03 ok). Faster than estimated. While it runs: `analyze.py` now computes the M7 gate per condition (`gate` block + a "Pilot gate" table: failing tasks = any run not ok or with an empty final, or a writer with 0 write calls; plus `writers_zero_write_calls`, `claimed_done_wrote_nothing` (writer final non-empty but 0 landed / 0 copies updated), `malformed_calls`, `pass` at <=20%). analyze.py is not imported by run.py, so the running chain is unaffected. A dry run on partial results: all conditions 0 failing so far. ruff clean; 17 tests pass. Session ended.
- 2026-09-27 10:55 EDT: visit. `kbio-job-pilot` alive (log 75 lines): B write 3/3 and update 3/3 ok (all follow-ups ok), C write-01 ok; about 11 writer tasks left (C, Cp). Gateway answering (last calls ok). Session ended.
- 2026-09-27 11:25 EDT: visit. `kbio-job-pilot` alive (log 89 lines): C write 3/3, C update 2/3, Cp write 3/3, Cp update 2/3 ok; Cp update-03 is the last task. C update-01 writer FAILED on step 1 with `HTTP 400 {"detail":"Open WebUI: Server Connection Error"}` (`llm.chat` fails fast on non-429 4xx). `llm-calls.log` shows one isolated http-400 at 11:07:03 between runs of ok calls, so this is a gateway outage artefact even though it is a 4xx. Narrowest extension of the outage rule: a 4xx whose body is a gateway connection error counts as an outage. Moved to `results-pilot-v3/outage/C/update-01.json`; rerun after the chain ends. The `kbio-daemon` tmux session is not currently listed (C/Cp stop it; `akasha_ctl.start()` restarts it). Session ended.
- 2026-09-27 11:56 EDT: pilot chain ended 11:26 (848 calls, 5,132 s). Reran C update-01 (outage artefact) in `kbio-job-rerun`: ok (13 calls). `analyze.py`: the gate's "claimed DONE but wrote nothing" now counts only writers with 0 write calls; writers whose write calls had no effect are a separate `writes_no_effect` count (C write-02 was mislabelled before). 17 tests pass; ruff clean. **M7 DONE**, all conditions pass (report in the table).
- 2026-09-27 12:00 EDT: open 07:00 item closed: tier S rebuilt from scratch (old copy in `trash/corpora-S-pilot/`) is **byte-identical** (`diff -r` empty, manifest included). Pilot results **copied** (not moved) to `data/.../results-pilot/` as a frozen record and left in `results/` so the full S run resumes past them: same code, tasks and loop, and WRITE/UPDATE reruns would not replay from the cache anyway (minted ids, watcher timing). Deliberate change from the old Next step 2. No trimming at S (full design, so S is the reference); trims at M/L are done by splitting `kbio run` commands, no code change.
- 2026-09-27 12:10 EDT: tier M corpus built (`python -m kbio.corpus M`, 1 s, no daemon). The old `tasks.verify` checked UPDATE copies against the S copy list, which fails at M (the 2 "linked definition" copies per lead go to different notes when all 432 notes exist). run.py already scores against the tier manifest's list, so only the verifier was wrong. `tasks.verify(tier)` now checks: gold spans; each UPDATE old sentence occurs as a whole line in exactly the tier manifest's copies; no WRITE fact (marker + answer in one paragraph) already in the tier; planted unanswerables not answerable (positive control: 8/8 planted READ questions detected as answerable at S). S: 0 failures; M: 0 failures (update-12: `Wikipedia/Philosophy.md` has Logic's lead inside a paragraph; not a copy, left as a threat).
- 2026-09-27 12:20 EDT: more M checks (no daemon). The concepts-retrieval unanswerables (q31 Pomodoro intervals, q32 daily caffeine limit, q33 Kanban WIP limit) stay unanswerable at M: no "Pomodoro" anywhere; "caffeine" only in Wikipedia/Mind, Sleep, Cognition as generic mentions (no limit); "work-in-progress" only in Wikipedia/Hackathon prose (no Kanban). `gate.py` is tier-generic (it reads the tier's manifest; the lead is the first one with 3+ copies). `analyze.calls_per_hour()` is not written into `summary.json` or the tracked `.md`, so later appends to `llm-calls.log` do not break the offline re-analysis check. Session ended; `kbio-job-fullS` at A read (≈15/60 done).
- 2026-09-27 12:35 EDT: visit. `kbio-job-fullS` alive (log 56 lines, 0 FAILED): A read 50/50 ok, A write 4/12 done (write-04..07 ok, all follow-ups ok). ≈2,235 calls logged in total. Running ahead of the 20:00 ETA. B read not started yet, so the bm index-copy change must still wait. Session ended.
- 2026-09-27 13:05 EDT: visit. `kbio-job-fullS` alive (log 83 lines, 0 FAILED): A read/write/update all done (50+12+12 new, all ok), B read started (≈5/50). ≈2,665 calls logged in total. Since `basic_memory.py` is now loaded by the job, wrote the bm index-template code (`run.py`: `corpus_key`, `bm_template`, `Env(template=)`, `--bm-template`; `basic_memory.py`: `backup_db`, `restore_db=`). Opt-in only; ruff clean, 17 tests pass, imports ok; **not run** (needs the scratch HOME). Verification plan in Next step 3. Session ended.
- 2026-09-27 13:45 EDT: visit. `kbio-job-fullS` alive (log 131 lines, 0 FAILED): A done; B read 50/50 ok; B write 2/12 done (write-04/05 ok, all follow-ups ok). ≈3,100 calls logged. Wrote the index-template verification as `kbio.checks bmtpl` (builds B/C templates, then per condition one restored and one fresh `Env`: restored `bm_index_seconds` < 15, restored file hashes = template files, entity/vector counts = template, same permalinks for 3 `search_notes` queries (plain `habit`, text `Achterberg Index 97-A`, a semantic phrase), B akasha 0 violations/conflicts/reviews; also records exact-output equality and setup timings). ruff clean, imports ok; **not run** (needs the scratch HOME). Session ended.
- 2026-09-27 14:09 EDT: visit. `kbio-job-fullS` alive (log 139 lines, 0 FAILED): A done; B read 50/50; B write 10/12 done (write-04..13 ok, all follow-ups ok). ≈3,400 calls logged. C/C′/CB and B update still to run. Nothing to do without the scratch HOME; session ended.
- 2026-09-27 14:45 EDT: interactive review of partial tier-S results; see "Review items". Scoring fix applied in `score.py`/`analyze.py` (offline only; the running job is unaffected).
- 2026-09-27 15:45 EDT: the user stopped all tmux terminals (Ctrl-C); fullS was interrupted during
  C write-14. The runner was rebuilt as `agent/runner.py` plus `agent/start.sh` (PLAN §2 rule 5,
  §8a), and all 4 acceptance tests passed. `run_scheduled.sh` was moved to
  `data/.../trash/run_scheduled.sh.v1`. Tier M/L runs are on hold pending the v2 decision
  (`V2-PLAN.md`), so no hours are spent on tiers that v2 might redo.
- 2026-09-27 16:05 EDT: more runner hardening.
  - STOP is renamed `STOP.consumed-<ts>` when honored, so the next arm doesn't exit at once.
  - Rate limits are detected from the structured event, and from the limit text with its exact
    reset time. Tested with fixtures.
  - A job started from a headless `claude -p` outlives that session (tested; exit 0 recorded).
  - `job stop` waits for the process to exit, and `job start` removes a stale viewer pane.
  - The daemon moved to the launcher (above).
  - `V2-PLAN.md` written.
- 2026-09-27 17:20 EDT: the user decided to finish v1 tier S, then build v2. `fullS` was resumed
  through the launcher (job `fullS`), and the runner was re-armed with `agent/start.sh now`.
- 2026-09-27 17:35 EDT: **V1 done** (in parallel with `fullS`; new files only, no scratch HOME,
  no port 7534, no LLM calls). Design choices:
  - `read` pages are in **characters, default 6,000**, not the plan's `limit=2000`. At 2,000
    characters one page is a quarter of the shared 2,000-token cap, so the yardstick would need
    about 4x the read calls that basic-memory needs, which biases the calls and tokens metrics.
    The server also caps every result at 8,000 characters (search drops whole hits, so the JSON
    stays valid).
  - Search runs AND over the content words (stopwords dropped unless the query is all stopwords),
    then fills to `k` with OR. A multi-token word (`41-C`) is an adjacent phrase. Raw text never
    reaches MATCH. Porter stemming. bm25 weights are title 2, body 1, tags 1; scores are negated
    and rounded to 3 places.
  - Ids: ingest keeps the source id (the `.md` path relative to the ingest root without `.md`; a
    KILT `wikipedia_id`). `create` mints `n<k>` from a counter, skipping taken ids and never
    reusing one. No timestamps or uuids are visible to agents, so the LLM cache keys stay stable.
  - FTS5 is external-content over a view of the live rows, so bodies are stored once and a
    `rebuild` never resurrects tombstones. Ingest is one transaction with `synchronous=OFF`,
    then `rebuild` + `optimize`. It upserts (a re-ingested id is replaced).
  - `export` is registered only with `--white-box`, so it is never listed to agents.
  - Deferred: the optional fastembed flag (not stdlib; V1's check doesn't need it), and the
    `crud-kb` console-script entry in `pyproject.toml`. Editing pyproject would re-sync the venv
    that `fullS` is using, so it waits for V2. The 100k smoke-test DB was left in `/tmp`.
- 2026-09-27 17:34 EDT: V2 offline parts written (no scratch HOME, no LLM calls). The KILT download
  started early as launcher job `kilt`: it needs no LLM, scratch HOME or port, so it cannot
  disturb `fullS`. Source: the official URL in the facebookresearch/KILT README
  (`http://dl.fbaipublicfiles.com/KILT/kilt_knowledgesource.json`, 34.76 GiB =
  37,318,876,722 bytes, checked against the HTTP Content-Length). KILT publishes no checksum, so
  the md5 comes from the S3 object's upload metadata (`x-amz-meta-s3cmd-attrs`,
  md5 `d1dca62aa6ba889d2e842182e3114af5`).
- 2026-09-27 17:37 EDT: V3 design (`kbio/kilt.py`). Rung order is seeded (key =
  sha256(`20260927:<id>`)). Rung 1e2 is 50 "core" pages (the lowest keys with at least 2,000
  characters, not a list or disambiguation page), each followed by the first page it links to
  with at least 1,000 characters. So probe → background multi-hop questions stay inside rung 1e2,
  and hence inside every rung.
  - All other pages with at least one content paragraph follow in key order. Rung N is the first
    N pages, so nesting holds by construction, and `verify` checks it through per-rung id-list
    hashes.
  - `plan` streams the index and uses an external `sort`, because 5.9M rows in Python lists would
    take about 4 GB and the machine has 15 GB with `fullS` running.
  - `pages.jsonl` holds compact markdown text: `# Title`, `Section::::` lines become headings,
    `BULLET::::` lines become list items, plus links and the revid. It is built only up to 1e6 for
    now. The "all" rung can be built later, or ingested straight from the KILT file.
- 2026-09-27 17:45 EDT: **V1 bug fixed** (found in the advisor review).
  - The server's result cap cut the tail of every full `read` page (body plus header was over
    8,000 characters), while `next_offset` skipped past the cut. That text was never shown, which
    is exactly the failure v2 measures.
  - Now `read` shrinks the page until header + body fit both the character cap and a 1,900-token
    budget. The budget is estimated stdlib-only: pieces (letter runs of up to 8, 3-digit groups,
    single symbols or CJK characters) × 1.35, or chars/3.2 if larger. The 1.35 comes from the
    first 2,000 KILT pages: 1.09 tokens per piece on average, 1.32 at worst.
  - A real KILT page: 6,000 characters = 1,352 tokens. Regression test added.
  - The same review also led to:
    - `snippet()` is now an O(n) sliding window (it was O(hits²)).
    - Text mode replaces run_agent's MALFORMED retry ("tool-calling interface") with a
      fenced-block instruction.
- 2026-09-27 17:45 EDT: **timing overlap.** Job `kiltrungs` (the md5 check from 17:40, then
  `index` with 5 workers) overlaps `fullS`'s C update tasks. v1 `wall_s` for C/Cp tasks between
  17:40 and the end of `index` (see `logs/kilt-rungs.log`) may be inflated. Flag that window in
  the M8 analysis; it doesn't affect correctness or tokens.
- 2026-09-27 18:25 EDT: **V3 DONE** (verify `nested: true`; manifest in `results/v2/kilt-rungs.json`).
  **V4 offline part done** (no LLM, no scratch HOME). Design decisions:
  - **Hops use visible mentions, not links.** KILT anchors are kept only in `pages.jsonl`'s
    `links` field; the page text the harnesses ingest has no link markup, and only 30 of the 50
    core→linked titles occur in the core page's text. A hop is therefore "page C mentions a
    topic (title occurs in C, whole word, case-insensitive) whose page mentions <kind of fact>",
    kept only when exactly one mentioned probe-carrying page has a fact of that kind (after
    redundant copies are placed). The corpus is unchanged, the same for every harness and rung.
  - **Planted ≈ 300, probed ≈ 150** (deviation from V2-PLAN "about 150 facts"): every rung-1e2
    page gets `perturb_page`'s 1 counterfactual + 2 fictional facts; unprobed ones stay as
    distractors.
  - **Counterfactual confound at scale:** shifting a year on page P does not touch other KILT
    pages that repeat the real year. `probes verify --exposure` counts, per cf task and rung, the
    pages that contain both the title and the real year; cf is reported as its own family with
    `stale_values = [real year]`.
  - Uniqueness is checked against the full KILT source (`v2/scan.sh`): marker phrases
    (`<Surname> Index/Lexicon Society/Prize/Reading Room`, archivist names), towns and surveyor
    names; plus `used` across planted, created and updated values.
  - Job `kiltscan` (18:16-18:21) read 37 GB while `fullS` ran Cp read/write; flag that window's
    `wall_s` in M8 like the `kiltrungs` window.
  - Gold records carry both the KILT id and the `write_md` file name, so V5 can score citations
    on id-based (crud-kb) and file-based (akasha, basic-memory) harnesses.
- 2026-09-27 18:41 EDT: advisor review of the V4/V5 code; fixed offline:
  - `probes verify` now checks every hop (exactly one mentioned probe page carries the answer's
    kind, C carries none, a probe hop's clue sits on C only); 20/20 pass, and a tampered answer
    page or duplicated clue is caught.
  - CB filter: v2 closed-book prompt pair (`prompts2`, the NOT FOUND line; the answers double as
    the CB floor of V2-PLAN §4.7), max_tokens 2000, empty answers and errors are not stored (a
    rerun retries), leak match with perturb_page's token boundaries ("14" no longer matches
    "2014"), CB entries survive a rebuild only if the question text is unchanged, and `verify`
    fails unless every kept question has a non-empty answer from each of `CB_MODELS`
    (gpt-oss:120b, llama3.3:70b); `--no-cb` for offline checks.
  - Freezing: corpora carry `.meta.json` and templates `ingest.json` with `patches_sha`; run2
    refuses a mismatch; records carry `probes_sha` (hash of the task list); analyze2 refuses a
    mix. Existing crud-kb corpora/templates (1e2-1e6, all built after the current patches by
    mtime) were backfilled with the sha (`backfilled: true`). Current: probes_sha
    a951af3af55842a9 (before `draft`), patches_sha 6d089fbbf0df0423.
  - score2 precision counts unresolved citations as wrong (same rule for every harness);
    analyze2 skips CB-dropped questions and run2 no longer asks dropped follow-ups; analyze2 has
    the V6 gate block (as v1 M7).
  - 69 tests pass, ruff clean.
- 2026-09-27 21:15 EDT: the user put V3-V7 on hold pending human approval of M8 and V1. The
  runner was already stopped (STOP honoured after attempt 4, which had hit the 5-hour usage
  limit). `fullS` finished at 19:35, exit 0.
- 2026-09-27 21:47 EDT: M8 finished interactively; the user wants the M8 metrics as fast as
  possible. Every input was already on disk (fullS complete, 0 FAILED), so M8 needed no LLM calls:
  analyze twice (1 s, `cmp` identical), the write-14 snapshot check, and the README results
  section. Next step reordered so the approval request comes before optional V2. Runner
  restarted by the user's request.
- 2026-09-27 (unattended session after 21:47): wrote `## Approval request` (M8 table, threats,
  V1 summary, check commands, V3-V7 cost). Re-checked it: 69 tests pass, ruff clean,
  `kbio analyze --tiers S` rerun is `cmp` identical. The advisor reviewed it, and the costs were
  then restated as ranges (the plan's 15 calls per CREATE vs 31-67 measured in v1). Updated
  Background jobs, which was stale since 15:45. Optional V2 skipped: its remaining verifiable
  part needs KILT rungs (held), and the human is waiting. No code edits, jobs or LLM calls this
  session. Added the BLOCKED line.
- 2026-09-28 19:09 EDT: user asked for a smaller-context model instead of tier M. No real
  small-window model is available (see M11), so M11 = client-side window sweep on gpt-oss:120b.
  Not part of V3-V7; the hold and the BLOCKED line stay, and the runner stays down. Job `ctx` started
  interactively.
- 2026-09-28 19:40 EDT: user stopped M11 and asked for the strongest test of "the akasha KB is
  better for knowledge IO than a traditional (plain) KB". M12 designed (advisor-reviewed),
  pre-registered and hashed before launch. The hold on V3-V7 and the BLOCKED line stay.
- 2026-09-28 19:54 EDT: M12 Amendment 1 (see the M12 row and M12-PREREG.md). Job `kb` relaunched.
- 2026-09-28 20:50 EDT: M12 validity check. Cp UPDATE 55/55 done (19:54-20:47). Ap started
  20:47: its first 3 records have writer and follow-ups ok, daemon status 0 violations, 0 conflicts
  and 0 reviews, 0 path errors, and every scored copy present. No FAILED in the log. No test
  results were looked at.
- 2026-09-28 22:53 EDT: M12 status. The files stratum is complete (Cp 55, Ap 55; 0 FAILED; 0 label
  leaks). Read out as the completed stratum; no run decision depends on it. **H1b: Ap 35/35 and Cp
  35/35 all copies consistent. No difference (0/0 discordant, p=1), and the same on the unseen and
  no-prefix subsets; H2 has no stale reads in either.** Control S: 0.80 vs 0.75, CI [-0.10, 0.20],
  so the prediction holds. Cost: Ap 49.7k vs Cp 41.1k tokens per UPDATE task. So with fixed file
  tools, grep+edit updates byte-identical pasted copies perfectly; v1's C′ UPDATE failures were the
  "(1) " path quirk. The pre-registered verdict can now be at most "shown with basic-memory, not
  replicated with files". B is at 28/55 (not looked at). The run continues unchanged.
- 2026-09-28 23:05 EDT: descriptive and post hoc (not pre-registered). How the files stratum
  reached the same outcome. Cp agents edited every copy themselves (133 ok edits, 3 failed). Ap
  agents made 40 successful edits (about 1 copy per task, 31% of copies). The daemon synced the
  rest in all 35 tasks. Their later edits to other copies then failed 60 times ("`old` occurs 0
  times": already synced), and they re-read more (206 vs 130 read_file). Ap doesn't tell the agent
  that the vault syncs, so the safety net shows up as confusion and +21% tokens, not as savings.
- 2026-09-28 23:12 EDT: the user stopped and skipped M12 ("we can predict the output"). Final
  counts: Cp 55/55, Ap 55/55, B 35/55, C 0, S-kbrw 0, A 0. **No pre-registered verdict was
  reached**, so the claim is untested; the records stay under `results/.../S-kb/`. What stands:
  the files-stratum readout (Ap = Cp = 35/35 all copies consistent, control within the margin) and
  the post-hoc mechanism note. The basic-memory stratum (B vs C) was not complete and was never
  looked at. Next: M13 plan (akasha stores "memory" text for external memory benchmarks), awaiting
  human confirmation.
- 2026-09-28 23:19 EDT: M13 plan written (`M13-PLAN.md`), awaiting human confirmation of §7 (a)-(f).
- 2026-09-28 23:47 EDT: user confirmed M13 with rulings (a)-(f); recorded in M13-PLAN.md §7a. Started.
- 2026-09-29 00:15 EDT: M13 pre-registered (sha256 aaa08f3c42ff6248…) and job `mab` launched (5,600 single-shot calls; est. 7–9 h).
- 2026-09-29 00:19 EDT: M13 Amendment 1 (agentic subset: `kbio/mab/agentic.py`, tools and sample fixed); job `mabagent` queued after `mab`.
- 2026-09-29 00:21 EDT: the user set the goal "metrics for knowledge base management comparing
  akasha to SOTA methods"; M13 is that measurement. Queued chain: `mab` (primary, 5,600 calls) ->
  `mabagent` (agentic subset, after mab exits 0) -> `mabscore` (benchmark metric via
  `mab_score.py`, then `kbio.mab.analyze` -> `results/m13-mab.md`). The paper's published numbers
  (other backbones, context only; the length aggregation is unspecified) are in
  `results/m13-published-context.md`. Knowl has no published numbers in the submodule.
- 2026-09-29 09:54 EDT: **M13 DONE.**
  - Jobs `mab` (5,600 calls), `mabagent` (160 agentic questions) and `mabscore` all exited 0;
    0 FAILED; 5,920 scored records.
  - **Pre-registered verdict:** "H1 shown, and K2 > R-rule".
    - Single-hop pooled: K2 0.970 vs R-bm25 0.705 (+0.265, Holm p < 0.001) and vs LC 0.670
      (+0.300).
    - H2 (flagging helps) not shown: +0.005, p = 0.31.
    - H1c: K2 vs R-rule +0.058 (24/1).
    - The results hold without the pilot questions.
  - **Post-hoc diagnostic** (zero LLM calls, labelled exploratory): the edge over plaintext is
    the benchmark BM25's whitespace tokenizer. A lowercase, punctuation-stripped plaintext BM25
    puts the gold answer in the top 10 almost as often as akasha (262k: 0.96 vs 0.97; benchmark
    tokenizer 0.88). K0, with no conflict handling, already scores 0.965.
  - **Multi-hop:** fact-level stores fail (0.03–0.18); chunks and long context reach about 0.30.
    Agentic multi-hop: akasha 0.71 vs grep 0.55.
  - Write-up in README "M13", tables in `results/m13-mab.md`.
  - Not committed.
