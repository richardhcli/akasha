# Scaled dogfood ingestion smoke test (T11.4)

**Date run:** 2026-07-25
**Depends on:** T11.1 (scratch-vault plumbing precedent), T11.3 (filesystem
discovery for newly registered sync roots — the fix that makes a fresh
`POST /v1/sync/rescan` actually pick up never-before-seen files).

## What this is, and what it is not

This is a **content-blind mechanical scale test**. For every copied file,
one fixed, identical, content-independent placeholder template string was
mechanically appended (Python string literal, substituting nothing from
the real note's text) around a plain (non-task) `^tm-new` anchor, per the
§4.7 contract grammar's `new_line` production. Per `reconcile.py`'s
existing default for a non-task `^tm-new` paragraph mint
(`PARAGRAPH_NODE_TYPE = "claim"`), every minted node is `node_type="claim"`.

**This task makes zero decisions about which real spans in these notes are
worth tracking.** No real note text was read for meaning, only copied
byte-for-byte (aside from the mechanical frontmatter/anchor-line append
described below) and passed through the pipeline. It answers one question
only: *does discovery → anchor-mint → write-back → node-creation → review-
queue behavior hold up mechanically as file count grows (1 → 10 → 100)?*
It does **not** answer "is the vault usable" or "are these claims worth
anything" — that judgment is exclusively T11.2's, and remains open.

## Method

For each scale N ∈ {1, 10, 100}:

1. **Fresh (non-cumulative) copy.** The first N `.md` files under
   `data/(10) Concepts/` by sorted path (mechanical selection, no content
   judgment) were copied into a scratch vault directory
   `$HOME/.local/share/akasha-dogfood/vault-scale-N/`, freshly re-staged
   from scratch each run (old contents removed first, so N=10's directory
   is never a superset carried over from a prior N=1 run).
2. **Mechanical per-file transform** (identical for every file, same
   Python script for all three scales):
   - If the file has no YAML frontmatter, or malformed/unclosed
     frontmatter, a fresh `---\ntm: 1\n---` block is prepended (spec §4.7:
     "files without [`tm: 1`] are never parsed for management" — this is
     the mechanical step that makes the file eligible for management at
     all, not a content decision).
   - If the file has well-formed frontmatter, `tm: 1` is inserted as the
     second line of the existing block (no other frontmatter keys
     touched). **All 100 files in the largest sample had well-formed
     frontmatter** (confirmed by a pre-check before staging), so only
     this branch was ever exercised in this run; the no-frontmatter
     prepend branch exists in the staging script but was not exercised
     by this particular 432-file corpus's first 100 sorted files. A
     verifier diffing a staged file against its source should expect
     exactly one inserted line (`tm: 1`) plus one appended line (the
     template + `^tm-new`), nothing else changed.
   - Exactly one fixed line is appended at end-of-file: the literal
     template string below, followed by a space and `^tm-new` (a plain,
     non-task `new_line` per §4.7's EBNF — never a `task_form`, so it
     always mints `node_type="claim"`, never `"task"`):

     > `T11.4 scaled dogfood smoke test placeholder claim (fixed content-independent template; not derived from this note's real text). ^tm-new`

   This is the **only** edit made to each file's content; nothing else in
   the note (real body text, existing structure) is read for meaning or
   altered.
3. **One scratch daemon + DB per scale**, never reused across scales,
   never the default `~/.config/tm-daemon/` location:
   `$HOME/.local/share/akasha-dogfood/daemon-scale-N/{config.toml,store.db}`,
   bound to `127.0.0.1:7434` (checked free before each run; each scale's
   daemon is fully started, exercised, and stopped before the next scale's
   daemon starts, so the port is never contended). This is a distinct
   scratch tree from T11.1/T11.3's pre-existing `vault-1`/`store.db` under
   the same parent directory (bound to port 7433, left completely
   untouched — confirmed still healthy after this run via `GET /health`)
   and from the repo's real default config dir, which was never touched.
4. **Live HTTP sequence per scale:** bootstrap a first human token
   directly via `store.create_token` (same one-time-bootstrap pattern as
   T11.1, logged there as a SPEC-QUESTION — no HTTP-only path exists to
   mint the very first token on a fresh DB), start the daemon, mint a real
   `dogfood-scale-N` human token over genuine HTTP using the bootstrap
   token, `POST /v1/sync/roots` to register the scratch vault, capture
   `GET /v1/metrics` before, time a `POST /v1/sync/rescan` call
   wall-clock, capture `GET /v1/metrics` after, `GET /v1/sync/status`,
   `GET /v1/review?status=open`, then stop the daemon.
5. **Ground truth cross-check with the daemon stopped**, read-only SQLite
   against the scratch DB (`mode=ro`, no writer contention): live `claim`
   node count, `sync_files` row count, `review_queue` open-row count
   grouped by `cause_kind`, and every `review_queue` row's `cause_ref` JSON
   parsed for its violation/linter `code` field (catches anything that
   might diverge from the `GET /v1/review` HTTP response shape). Also
   spot-checks every minted claim node's body against the fixed template
   string byte-for-byte (after normalizing a trailing newline the object
   store adds on write). A third path was also checked: each scale's
   `daemon-scale-N/daemon.out` (stdout/stderr) and `daemon.log`
   (structured JSON log) were grepped for `error|traceback|exception|warn|
   W_|E_` after the run — the one place a caught-but-logged exception or
   an advisory lint could show up without appearing in either `/v1/review`
   or `sync_status.violations`.

All three scratch vault directories and all three scratch DBs
(`daemon-scale-1/store.db`, `daemon-scale-10/store.db`,
`daemon-scale-100/store.db`, all under
`$HOME/.local/share/akasha-dogfood/`) were left in place after this run —
not deleted — specifically so an independent verifier can re-query them
directly (`store.list_sync_files`, a live node query, `review_queue`)
without needing to re-run any scale from scratch.

## Results

| Scale (N) | Files reconciled (`rescan` response) | `sync_files` rows (DB) | Claim nodes minted (DB) | Claim bodies == fixed template (spot check) | Rescan wall-clock | `sync_cycle_ms` after (p50 / p95) | RSS before → after | Open review items (HTTP `?status=open`) | Open review items (DB, all `cause_kind`) |
|---|---|---|---|---|---|---|---|---|---|
| 1   | 1   | 1   | 1   | 1/1     | 13 ms  | 1.84 / 1.84 ms | 65,204,224 → 66,039,808 bytes (+816.0 KiB) | 0 | 0 |
| 10  | 10  | 10  | 10  | 10/10   | 29 ms  | 1.53 / 2.67 ms | 65,224,704 → 66,244,608 bytes (+996.0 KiB) | 0 | 0 |
| 100 | 100 | 100 | 100 | 100/100 | 158 ms | 1.19 / 2.34 ms | 65,175,552 → 67,031,040 bytes (+1,812.0 KiB) | 0 | 0 |

At every scale: `files_missing: 0`, `files_still_tm_new_grep: 0` (every
appended `^tm-new` anchor was successfully converted to a real minted
`^tm-<id8>` anchor — none silently dropped or left unconverted, e.g. by
landing inside an unclosed code fence per §4.7's "anything inside fenced
code blocks is ignored entirely" rule; the source files at these three
scales all happened to have well-formed, closed fences, confirmed by a
pre-check: an even fenced-code-block count in all first-100 sorted files),
`sync_status.violations: []`, `sync_status.pauses: []`,
`sync_status.conflicts: []`, `auto_repairs_after: {}` (no certain-repairs
silently applied either — nothing to repair, since every file arrived
clean the first time), `violation_rate_after: 0.0`. No crash, no
non-zero exit from any daemon process, no daemon left running or any port
left bound after each scale's run, and (per the log-file check in the
next section) no exception or advisory lint logged to `daemon.out`/
`daemon.log` at any scale either.

### Violation/linter-code catalogue (Step 3 — actually checked, not assumed)

The full §4.7 violation-code set is `E_ID_CHECKSUM`, `E_DUP_ID`,
`E_LOST_ANCHOR`, `E_DELETED_S1`, plus the advisory `W_UNMANAGED_ANCHOR`,
plus the pause&diff (formatter-storm) review item and `cause_kind="conflict"`
review items reconcile.py can separately enqueue. Every one of these was
checked for at every scale via **two independent paths** that would
disagree if either had a bug: (a) the live `GET /v1/review?status=open`
HTTP response, and (b) a direct read-only SQL query against
`review_queue` (all rows, not just `cause_kind="violation"`) with each
row's `cause_ref` JSON parsed for its `code` field. A third path was also checked: `daemon-scale-{1,10,100}/daemon.out` and
`daemon.log` were grepped for `error|traceback|exception|warn|W_|E_`
after each run — all six files matched zero times (each `daemon.log`
contains only three `INFO`-level structured startup lines: daemon start,
startup reconcile, GC tick; no exception, no advisory lint, nothing
caught-and-logged). All three paths agreed at all three scales: **zero
violations of any code, zero conflicts, zero pause-and-diff events, zero
certain-auto-repairs, at N=1, N=10, and N=100.** This is a real, counted
zero, not an assumed one — every file in this fixed-template test was
well-formed by construction (a single clean `^tm-new` mint against an
empty base, first ever sync of each file), which is exactly the condition
under which zero violations is the *expected* outcome for this run;
positive demonstration that the linter machinery itself is reachable and
correct is what the existing unit/golden test suites already cover, not
this run. The one genuinely new signal this content-blind run could have
surfaced — real vault content
(actual YAML frontmatter shapes, native Obsidian `^block-id`s, wikilinks,
fenced code) tripping a violation code the reconciler had never been
exercised against at scale — did not occur in this particular 1/10/100
sample of `data/(10) Concepts/`; it remains untested whether a different
432-file sample, or the full 432, would surface one, since this run only
exercises the first 1/10/100 files by sorted path, not the whole
directory.

### Content-blindness spot check (Step 4)

For every scale, 100% of minted `claim` node bodies were confirmed
byte-identical (after normalizing the object store's trailing newline) to
the single fixed template string above — never to any excerpt of the
real note text. Confirmed directly against each scratch DB's `objects`
table (`nodes.head_hash` → `objects.bytes` → JSON `body` field), not
inferred from the HTTP layer.

## Daemon health across scales

Three independent daemon processes (one per scale, never overlapping —
each was fully stopped before the next started), same host, same scratch
parent directory, different scratch DB and port-reuse-safe sequential
7434 binding each time. RSS delta (before rescan → after rescan) grew
from +816.0 KiB (N=1) to +996.0 KiB (N=10) to +1,812.0 KiB (N=100) —
sub-linear-to-roughly-linear in file count and small in absolute terms;
there is no superlinear or runaway growth across this 100x file-count
range. This is evidence against unbounded growth **within a single
rescan cycle on a freshly started process** at this scale; it says
nothing about long-running-process memory behavior over many cycles
(each scale here is a fresh process measured once, not a soak test).

## Known limitations of this run

- Only 1/10/100 files (by sorted path) out of the real 432-file
  `data/(10) Concepts/` corpus were exercised — not the full directory.
  A different or larger sample could still surface a violation code this
  run did not.
- This is a single rescan cycle per scale on a freshly started daemon,
  not a soak/longevity test; RSS growth over many cycles on one
  long-lived process is a different (untested here) question.
- The fixed template's own text was never checked for accidentally
  colliding with `E_LOST_ANCHOR`'s ≥0.9 fuzzy-match heuristic across
  files (identical bodies across many files is exactly the kind of input
  that heuristic exists to be robust against); no evidence of a problem
  was observed (zero `E_LOST_ANCHOR` at any scale), but this run doesn't
  specifically stress that interaction beyond what naturally occurred.

## Closing line

This validates sync/reconcile mechanics and daemon health at scale; it makes no claim about content usability — that is T11.2, still open, still human-only.
