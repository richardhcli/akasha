You are a fleet-worker executing build-plan task T11.4.

Goal: Extend T11.1's mechanical-plumbing precedent to increasing scale, to answer "does the sync/reconcile pipeline hold up against something closer to the founder's real vault size" — **without** making any content-curation decision. This task is explicitly **content-blind**: it appends one fixed, deterministic managed block (a single mechanically-generated `claim`-type anchor, same literal wording template for every file, no reading or interpretation of the note's real content) to each copied file, purely so discovery → anchor-mint → write-back → node-creation → review-queue behavior actually exercises at scale instead of the T11.1 result (0 anchors, 0 nodes at any scale). **This is not T11.2.** Deciding which real spans the user actually wants tracked as claims/entities stays exclusively human (`docs/vision.md` PRD §5 F-list, R9) — this task creates zero nodes derived from real note *meaning*, only from a fixed, content-independent template, and must say so plainly in its report so a green result is never mistaken for "the vault is now usable," which remains T11.2's human call.

Depends on: T11.3
Files you may create or edit: docs/dogfood/scaled-smoke-report.md
Spec reference: §4.7/§4.8 (anchor grammar, reconcile), §4.11 `GET /sync/status`, `GET /dashboard`/§4.9 metrics (`rss_bytes`, `sync_cycle_ms`)
Steps: (1) Create three scratch vaults under `$HOME/.local/share/akasha-dogfood/` (e.g. `vault-scale-1`, `vault-scale-10`, `vault-scale-100`), each a **fresh copy** (not cumulative) of the first N files (by sorted path, mechanical selection — no content judgment) under `data/(10) Concepts/`, N ∈ {1, 10, 100} (confirmed 2026-07-25: `find "data/(10) Concepts" -name '*.md' | wc -l` = 432, so all three scales are satisfiable from real files — no need to pad or substitute). (2) For each copied file, mechanically append one fixed managed block using the exact `^tm-new` anchor grammar (§4.7) around a fixed placeholder claim body — identical template string for every file, substituting nothing from the real note text. (3) For each scale, on its own scratch daemon/DB (never reused across scales, never the default config dir): register the vault as a sync root, call `POST /v1/sync/rescan` (now functional per T11.3), and record: files reconciled, nodes minted, wall-clock time for the rescan call, daemon RSS before/after (`GET /dashboard` or `ps`). **Catalogue every review-queue violation/linter code that fires, and on how many files, at each scale — do not assume or report "none".** The appended blocks are well-formed by construction, but the reconciler parses the *whole* file (real YAML frontmatter, wikilinks, native Obsidian `^block-id`s, meta-bind embeds it was never tested against), and whether that messy real content trips the linter is the one genuinely new signal this content-blind test can produce — a bare "0 violations" is only meaningful if the report shows it was actually counted, not assumed. (4) Confirm zero nodes were derived from real note content at any scale (spot check: the minted node bodies are all the one fixed template string, not vault text). (5) Write `docs/dogfood/scaled-smoke-report.md` with a table of the three scales' results (including the violation catalogue from step 3) and one explicit closing line: "This validates sync/reconcile mechanics and daemon health at scale; it makes no claim about content usability — that is T11.2, still open, still human-only."
Verify command: N/A (manual/live-daemon leg, same framing as T11.2 and M9/M10's human/CI-pending legs). A fleet-verifier picking this up independently re-checks the report's row counts (files reconciled, nodes minted) against `store.list_sync_files`/a live node query in each scratch DB — it does not need to re-run all three scales from scratch to confirm the numbers are real. DoD is the completed, dated report with all three scales' rows filled in from real runs, not estimated.
Definition of done: `docs/dogfood/scaled-smoke-report.md` exists with real (not projected) results at N=1, 10, and 100 files, a real (possibly empty, but actually-checked) violation catalogue per scale, no crashes or unbounded RSS growth across the three runs, and the report's closing line explicitly disclaims any content-usability conclusion, leaving that call to T11.2.

Non-negotiable rules (root CLAUDE.md): never invent schema/endpoints/
grammar beyond the spec (narrowest reading + # SPEC-QUESTION: comment on
ambiguity); never edit golden files/fixtures to make tests pass; all
persistent writes go through src/akasha/kernel/store.py; no pickle/eval/
exec anywhere; touch only the Files listed above.

Additional instructions for this dispatch:
- Use pure-Claude execution only. Do NOT invoke scripts/fleet/cursor_bridge.py or any Cursor subprocess under any circumstance — edit/run directly only.
- All scratch vaults/config/DBs must live outside the repo tree under $HOME/.local/share/akasha-dogfood/, one fresh daemon+DB per scale, never the default ~/.config/tm-daemon/ location. Check port 7433 (or whichever port you bind) is free before starting each scratch daemon; there may be leftover scratch daemons from prior T11.1/T11.3 runs — do not disturb the repo's default config.
- `data/(10) Concepts/` is gitignored real personal content. Reading it is explicitly authorized for this task, but docs/dogfood/scaled-smoke-report.md must contain only counts/timings/observations — never real note text or real filenames, same discipline as docs/dogfood/README.md.
- Do not touch src/akasha/sync/watcher.py or any other src/tests file — this task's only allowed file is docs/dogfood/scaled-smoke-report.md.
- The step-5 closing line is a verbatim literal string requirement: "This validates sync/reconcile mechanics and daemon health at scale; it makes no claim about content usability — that is T11.2, still open, still human-only."

Run the Verify command yourself via Bash and report its REAL exit code
and output tail — do not estimate or guess these values. Since Verify is
N/A (manual leg), instead actually run the three live scratch-daemon
scales for real and report real observed numbers — never estimated or
projected ones.

If you get stuck or exceed a reasonable tool-call budget without making
progress, self-report status BLOCKED with reason "possible hang —
exceeded tool-call budget" rather than continuing indefinitely.

Return your result via the required structured schema. files_changed
must be the actual output of `git diff --name-only` plus untracked
files you created (check with `git status --porcelain`), not a guess.
End your reply with a fenced ```json block with these fields: status
("DONE" or "BLOCKED: <reason>"), files_changed (array of paths),
verify_command (string), verify_exit_code (integer or null if N/A),
verify_stdout_tail (string), spec_questions (array, possibly empty).
