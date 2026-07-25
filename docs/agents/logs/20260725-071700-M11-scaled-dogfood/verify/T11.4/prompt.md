You are an independent verifier for build-plan task T11.4.
You did NOT do the work. Your job is to catch a worker that claims
success without having actually done it — do not trust anything below
except as a claim to check.

The worker claims: status=DONE, files_changed=["docs/dogfood/scaled-smoke-report.md"], verify_exit_code=0.

Task background (build-plan T11.4): a content-blind scaled dogfood smoke
test that copies the first N files (N in {1, 10, 100}, sorted path order)
from data/(10) Concepts/ into three FRESH (non-cumulative) scratch vaults
under $HOME/.local/share/akasha-dogfood/, appends one fixed identical
template ^tm-new managed block to each file (no real content read for
meaning), registers each vault as a sync root on its OWN scratch
daemon/DB (never the default ~/.config/tm-daemon/, never reused across
scales), calls POST /v1/sync/rescan, and records real (not
estimated/projected) counts: files reconciled, nodes minted, wall-clock
rescan time, daemon RSS before/after, and a catalogue of every
review-queue violation/linter code that actually fired at each scale
(never assumed "none" without checking). DoD requires:
  - docs/dogfood/scaled-smoke-report.md exists with a table of real
    results at N=1, 10, 100.
  - A real (possibly empty, but ACTUALLY CHECKED) violation catalogue
    per scale.
  - No crashes or unbounded RSS growth across the three runs.
  - The report's closing line is EXACTLY (verbatim):
    "This validates sync/reconcile mechanics and daemon health at scale; it makes no claim about content usability — that is T11.2, still open, still human-only."
  - No real note filenames or real note body text appear anywhere in the
    report (same no-leak discipline as docs/dogfood/README.md).
  - Only docs/dogfood/scaled-smoke-report.md was created/modified in the
    repo; no src/ or tests/ file touched (this task's Files list is
    exactly that one file).
  - Node bodies minted at each scale must all be the fixed template
    string, not real vault content (spot-checkable against the scratch
    DB if it still exists on disk).

Verify command to re-run yourself: N/A (manual/live-daemon leg — there is
no pytest command). Instead: read the report file directly, check the
scratch DBs/vault dirs on disk if they still exist under
$HOME/.local/share/akasha-dogfood/ (worker claims they were left in place
for you, named daemon-scale-{1,10,100} and vault-scale-{1,10,100}), and
cross-check the report's claimed numbers (files reconciled, nodes minted,
violation counts) against real sqlite queries against
daemon-scale-{1,10,100}/store.db if present, rather than trusting the
report's prose alone. You do NOT need to re-run all three scales from
scratch — DoD explicitly says re-checking the existing artifacts is
sufficient.

Steps:
1. Read docs/dogfood/scaled-smoke-report.md in full. Confirm the exact
   verbatim closing line is present, confirm a results table exists for
   all three scales with real (not obviously placeholder) numbers, and
   grep it for any real filename/content leak from data/(10) Concepts/.
2. Check `git status --porcelain` and `git diff --name-only` in the repo
   — confirm the ONLY changed/new repo file is
   docs/dogfood/scaled-smoke-report.md (no src/, tests/, or other docs/
   file touched).
3. If the scratch DBs still exist under $HOME/.local/share/akasha-dogfood/,
   open daemon-scale-1/store.db, daemon-scale-10/store.db, and
   daemon-scale-100/store.db (read-only sqlite3 CLI queries) and confirm:
   node counts of type='claim' match N at each scale, node bodies are the
   one fixed template string (not varying per file), and query the
   review_queue table (or equivalent) for open items to sanity check the
   report's violation catalogue claim.
4. If the scratch artifacts are missing entirely (cleaned up), note that
   explicitly — the report's claimed numbers cannot be independently
   re-confirmed against live state, which weakens (but does not
   automatically fail) the claim; use judgment on whether the report's
   internal detail (specific counts, timings, RSS deltas, a real log-grep
   description) is credible as genuine executed output versus fabricated
   placeholder text, and say which you concluded and why.
5. Set verdict:
   - CONFIRMED_DONE only if the worker claimed DONE, the report exists
     with the exact required closing line, a real per-scale results table,
     no repo-file scope violations, no content leak, and (if scratch
     artifacts exist) the DB cross-check is consistent with the report's
     claimed numbers.
   - CONTRADICTS_CLAIM if the worker claimed DONE but any of the above
     checks fail (missing closing line, wrong file scope, content leak,
     DB numbers inconsistent with the report, or report numbers appear
     fabricated/placeholder rather than real).
   - CONFIRMED_BLOCKED if the worker claimed BLOCKED (not applicable here
     since the worker claims DONE, but included for schema completeness).

If you get stuck or exceed a reasonable tool-call budget without making
progress, self-report status BLOCKED with reason "possible hang —
exceeded tool-call budget" rather than continuing indefinitely.

Return your result as a fenced ```json block matching this schema:
files_exist (bool), verify_exit_code (integer or null), verify_stdout_tail
(string), git_status_matches_claim (bool), verdict ("CONFIRMED_DONE" |
"CONTRADICTS_CLAIM" | "CONFIRMED_BLOCKED"), notes (string, explain your
reasoning including the disk-artifact check outcome).
