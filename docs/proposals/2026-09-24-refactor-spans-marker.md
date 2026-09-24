# Plan: transclusion spans, marker-less files, join rule, and code refinement

> **Superseded 2026-09-24** by `docs/build-plan.md` M20/M21 and `docs/spec-questions.md` M20-A…G, which carry the final rulings (padding is per file and never part of the body; no pause at all, per the M20-G table). Kept as the evidence and reasoning behind them.

Status: **plan with the user's rulings of 2026-09-24 applied.** Only item F1 (glued anchor) is implemented;
everything else below is scheduled work. Per `CLAUDE.md` rule 2 this becomes build-plan tasks (M20, M21) once
task T20.1 lands the spec amendments; M20-F (glued anchor) is already logged in `docs/spec-questions.md`; M20-A…E are logged by T20.1.

Evidence base: the user's `sandbox/init` run (4 pauses, 2 conflicts, 6 open reviews), `make check` = 843 passed,
and an AST scan of `src/` (hotspots below).

---

## 0. Rulings received and how each is applied

| # | Ruling | Applied as |
|---|---|---|
| 1 | Braces acceptable; id is `{tm-…}`, the same id text as `^tm-…` | Span = `{ TEXT }{tm-<id8>}`. The id inside the braces is byte-identical to the `^` form (`tm-` + id8 with checksum), so `kernel.ids` is unchanged. Tokens are constants in `contract/grammar.py`. |
| 2 | Multi-line spans in scope for the first spans milestone, same syntax | The span text may contain newlines; the open token can be on an earlier line than `}{tm-…}`. T20.6. |
| 3 | No backward compatibility; remove `tm: 1` and all front-matter edits; push back only if critical | Not critical, so **no push-back**. The daemon never reads, writes or interprets front matter again. Four consequences, all handled in T20.3 (see §3). |
| 4 | On a join, a paste with new changes wins, "based on timestamps in the database" | Implemented as your rule plus one guard, because timestamps alone cannot tell a *stale* paste from an edit (see §4). T20.7. |
| 5 | Elaborate (M19-D, relaying a propagated cycle's own edit) | §2. Still **open**: I have not marked it resolved. |
| 6 | Sandbox is ephemeral; make the glued anchor a silent repair generally | **Done** (§1). Sandbox restart is done by the assistant separately. |

---

## 1. Done: glued anchor is a silent repair (F1, M20-F)

**Cause.** Typing at the end of `…tet 1111 ^tm-yuqpxpaz` puts the cursor before the anchor and eats its space
(`…22222^tm-yuqpxpaz`). The grammar demanded whitespace before the anchor, so the block lost its anchor; one lost
block in a 2-block file is 50 % > 25 %, so `pause_and_diff` stopped the whole file (zero writes) — the four
pauses in the sandbox.

**Fix (general, in the grammar).** The space before an end-of-line anchor or `^tm-new` is optional on read
(`grammar.py`: `ANCHOR_EOL_RE`, `MANAGED_PAR_RE`, `TASK_LINE_RE`, `NEW_LINE_RE`, `NEW_MARKER_EOL_RE`: `\s+` → `\s*`).
`render` always writes the canonical single space, so the file is re-spaced on the next write-back with no
violation, no pause, no review item. Spec §4.7 EBNF and prose amended (`[SP]`). A line that is only an anchor is
still not a block.

**Tests.** `tests/unit/contract/test_parser.py` (glued paragraph, glued nested task, glued `^tm-new`, anchor-only
line), `tests/unit/sync/test_reconcile.py` (glued edit is repaired *and* still propagates to the mirror; glued
with unchanged text only re-spaces with no commit; glued `^tm-new` is minted). No protected test needed changing.

Not done here: F3b (auto-resolve stale pause reviews) and F3 (minimum block count for the pause ratio) — F3
changes a spec number and F3b was not covered by your rulings; both are listed as T20.9.

---

## 2. M19-D elaborated: relaying an edit that a propagated cycle commits

**The situation.** A and B both show two mirrored lines X and Y. Inside one 500 ms debounce window the user
saves A with X edited and B with Y edited (two windows, an agent, or a `git pull` do the same).

**What the spec sentence did.** §4.8 said "the propagated cycle does not itself propagate further" (to prevent
A→B→A ping-pong). Trace: the watcher fires A first. `on_change(A)` commits X, then runs a three-way cycle on
B to bring it up to date. B's file holds an *unsaved-to-hub* edit (Y) *and* a stale X, so that cycle writes
the hub's X into B **and commits Y**. Under the literal sentence Y was then never sent to A. B's own event
arrives 500 ms later but B now equals its base, so it is a quiet cycle. Result: A shows old Y, B shows new Y,
the hub has new Y, no error, no review item, permanently. I reproduced exactly this (5 ms apart) in the
end-to-end run; after 12 s it had not converged.

**The change.** `on_change` keeps a work list. A propagated cycle that returns *committed node ids* has those
relayed to those nodes' other owners, same three-way way; a hub→file rewrite commits nothing and is never relayed.
A per-call cap (`MAX_PROPAGATION_CYCLES = 1000`) is a runaway guard.

**Why it cannot ping-pong.** A relay needs a *commit*; a commit needs a real vault-side edit that differs from the
file's base; the daemon's own write-backs make file == base. Each user edit is committed once. The existing test
`test_mirror_propagation_does_not_recurse_or_ping_pong` (source once, mirror once) is unchanged and green.

**Alternatives rejected.**
1. Keep the literal sentence: strands edits silently (the bug).
2. Re-queue the other file's path in the debouncer instead of relaying inline: its cycle is already consumed, so
   it would be quiet; nothing would propagate.
3. Skip propagating into a file that has pending edits and let its own event run first: order-dependent, and
   the cycle for that event may already have been coalesced away.

**Cost / risk.** Propagation latency is additive per hop (milliseconds). A very large mirror set fans out
in one `on_change` call (capped). Same-line concurrent edits are unchanged (M19-C/E22: one commit, one conflict,
nothing lost). **Ruling wanted:** accept M19-D (the spec §4.8 text is already amended to match)? Yes/no.

---

## 3. M20 — task plan (spans, marker-less files, join rule)

Order: **T20.1 → T20.2 → T20.3 → T20.5 → T20.6 → T20.7 → T20.8**; T20.9 anytime. One task = one focused change;
each ends with `make check` and `make battery` green.

### T20.1 — Rulings and spec (docs only)
Log M20-A…F in `docs/spec-questions.md` (resolved, with the ruling text); amend `docs/mvp-spec.md` §4.7 (span
grammar, no marker, glued anchor — the last is already amended), §4.8 (join rule), §5/§6 where they name the
marker; edit the vision F-row wording only if it names the marker. Add M20/M21 to `docs/build-plan.md` and
`docs/agents/task-status.md`. **Verify:** `grep -rn "tm: 1" docs/mvp-spec.md` names only the retired-marker note.

### T20.2 — Stage the reconcile cycle (pure refactor, was R2)
Split `Reconciler._cycle` (283 lines, 41 branches) into named stages returning `Quiet | HubOnly | Paused |
Applied`; keep the metrics `try/finally` in one wrapper. **Files:** `sync/reconcile.py`. **Verify:** every test
in `tests/unit/sync`, `tests/battery`, `tests/golden` passes **unmodified**. **Why first:** T20.3, T20.6, T20.7
all edit this function, and D12 hid in it.

### T20.3 — Remove the `tm: 1` marker
* **Model.** `BlockSet` loses `managed`, `contract_version`, `front_matter`. `parse(text)` no longer looks for
  `tm:`. An initial `---…---` block is skipped as **raw lines** (so a YAML line can never be mis-read as a
  construct) but never interpreted or edited. `render` emits raw lines plus blocks and no header.
* **Reconcile.** Delete `adopt_unmanaged`; `_cycle` parses the file directly. A file with no construct (no block,
  `^tm-new`, span, embed, ref) is untouched and untracked, exactly as now. Deleting all constructs from a tracked
  file now runs the ordinary deletion rules (previously a file without the marker was silently ignored).
* **Linter.** `W_UNMANAGED_ANCHOR` and the `not current.managed` early return are removed.
* **DB.** `sync_files.contract_version` (NOT NULL) becomes meaningless: migration `003_drop_sync_files_contract_version.sql`
  (forward-only), `store.upsert_sync_file`/listings and `api/routes/sync.py` drop the field. `/health`'s
  `contract_version` stays (the grammar version is now hub-owned, `grammar.CONTRACT_VERSION`).
* **Existing files.** A note that already contains `tm: 1` keeps it as an ordinary YAML key; nothing reads it.
* **Authorized protected-test changes (the only ones, by name; goldens change only by this task):**
  1. every `tests/golden/**` fixture: the 3-line `---\ntm: 1\n---` block is stripped by a committed, idempotent
     transform script whose diff must show *only* those lines removed;
  2. golden case `contract_w_unmanaged_anchor` and the unit tests for `W_UNMANAGED_ANCHOR` are retired;
  3. `tests/unit/contract/test_parser.py` "unmanaged" tests (5) become "no construct ⇒ empty BlockSet";
  4. `test_render.py::test_front_matter_absent_when_unmanaged`; the `_managed()` test helper returns its body.
* **Files:** `contract/{parser,render,linter,grammar}.py`, `sync/reconcile.py`, `kernel/store.py`,
  `api/routes/sync.py`, `migrations/003…`, `docs/mvp-spec.md`, the tests above, `docs/user/*` wording.
* **Verify:** `make check`, `make battery`; `grep -rn "tm: " src` finds no writer; a real-CLI test that a note
  gains **no** front matter through `setup`, a mint, an edit and a mirror.
* **DoD:** the daemon never writes a header; a tracked file's bytes outside its constructs never change.
* **Push-back check (none critical).** Risks accepted: (a) a note that *documents* akasha and has a line ending in
  ` ^tm-xxxxxxxx` is adopted — already true under M18-B, mitigated by fences and `.tmignore`; (b) no per-file
  grammar version, so a future token change needs a hub-driven migration (§3.T20.5 note); (c) one-way door for
  existing vaults — none exist besides ephemeral sandboxes.

### T20.5 — Span grammar, single-line (depends T20.3)
* **Constants** (`grammar.py`, everything else built from them with `re.escape`):
  `SPAN_OPEN = "{"`, `SPAN_CLOSE = "}"`, `SPAN_ID_OPEN = "{"`, `SPAN_ID_CLOSE = "}"`.
* **EBNF.** `span := SPAN_OPEN text SPAN_CLOSE SPAN_ID_OPEN "tm-" id8 SPAN_ID_CLOSE` ; `span_new := … "tm-new" …`.
  Whole-line `text ^tm-id` stays the default form and is unchanged.
* **Scan rule (balanced braces).** From each `{` outside a code fence, scan forward counting `{`/`}`. The span
  closes at the first `}` that returns depth to 0 **and** is immediately followed by `{tm-<valid id8>}`. If depth
  reaches 0 without that suffix, this `{` is literal prose and scanning resumes at the next `{`. So
  `use {x} then {shared}{tm-…}` yields one span (`shared`), and `{ a {b} c }{tm-…}` yields `a {b} c`.
  Braces inside a span must therefore balance (like LaTeX); unbalanced text cannot be a span. Scan length is
  capped (proposed 200 lines / 64 KiB) so a stray `{` cannot make parsing quadratic.
* **Canonical form.** Stored text is trimmed; `render` writes `{ text }{tm-id}` (one space of padding), so
  `{text}{tm-id}` typed by hand is re-padded silently, like the glued anchor.
* **Model.** `Block(kind="span")` with `text`, `line_no` (open) and `end_line_no`, `prefix` and `suffix` (text
  outside the braces on the first/last line). Everything outside the span is per-file, like indentation today.
* **Hub.** Node body = span text. `hub_state_for` substitutes the span text into the file's own prefix/suffix;
  the newline guard `E_UNPROJECTABLE_BODY` applies to non-span blocks only. Mirrors, propagation, relay and the
  join rule are text-based and need no change.
* **Minting.** `{ text }{tm-new}` mints an id like `^tm-new`; the daemon rewrites it in place.
* **Lint.** `E_NESTED_SPAN` (a second `}{tm-…}` terminator, an anchor line, or `^tm-new` inside a span text) is a
  review item, never a guess. Damaged span (terminator edited so it no longer matches) goes through the existing
  lost-anchor path: fuzzy match to the base text ⇒ review, never a silent hard delete of an S1+ node.
* **Task lines.** The checkbox stays a whole-line-anchor feature; a span inside a task line shares text only.
* **Tokens are constants, but existing spans are not:** changing them later turns every span into plain text; a
  hub-driven rewrite (`akasha migrate-spans`) is the way, and is **not** built now (no compatibility needed).
* **Files:** `contract/{grammar,parser,render,linter}.py`, `sync/reconcile.py`, `docs/mvp-spec.md`, tests, goldens
  (new fixtures only). **Verify:** parser/render round trip properties extended with spans; battery E26–E28.

### T20.6 — Multi-line spans (depends T20.5)
Parser state machine across lines within the scan cap; `Block.end_line_no`; `raw_lines` excludes the range; `render`
emits the range as one unit; diff/base-snapshot comparison works on the joined text; mirror rewrite replaces
the whole range. Fences inside a span: the fence is part of the text (not a boundary) but an unbalanced fence
line ends the scan (literal). **Verify:** battery E29–E31 (edit either copy, span with blank lines, span with
inner braces), property test `parse(render(G)) == G` including multi-line spans, real-CLI test.

### T20.7 — Join rule (ruling 4; revises M19-C; depends T20.2)
Where a file joins a node another file already shows with **differing text** (today: hub wins, one conflict
review). New rule, in order:
1. text equals the hub head ⇒ quiet;
2. text equals an **earlier version in the node's history** ⇒ *stale paste*: hub wins, file rewritten, **no
   review** (timestamps cannot detect this case: yesterday's text saved now has a fresh mtime);
3. text is **new** and the file was changed **after** the hub head's commit `ts` (`commits.ts` vs the file's
   mtime) ⇒ *your* new change wins: committed as a sync edit, propagated to every mirror;
4. text is new but the hub head is **newer** than the file, or the timestamp is unreliable (mtime in the future,
   or a conservative/cloud root whose sync client sets mtimes) ⇒ genuine conflict: today's behaviour (hub wins,
   version kept as a conflict branch, one review).
Needs a `store.node_versions(conn, node_id)` read helper (uses `commits` + `objects`, both already exist).
**Authorized protected-test changes:** `tests/unit/sync/test_reconcile.py::test_mirror_join_with_differing_text_hub_wins_and_is_reviewed`
(split into the four cases above) and the demo selftest line "a differing copy in D.md is rewritten…". E05/E21–E24
are unaffected. **Verify:** unit tests per case + a real-watcher test of "paste then edit within one window".

### T20.8 — End-to-end, docs, plugin
Real-CLI e2e (`setup`, spans, multi-line spans, no header, join rule) as a permanent integration test; quickstart
and `cli.md`; optional Obsidian plugin decoration that dims the visible `}{tm-…}` (braces are shown in reading
view, unlike `^blockid`). **DoD:** the sandbox flow of this session passes unattended.

### T20.9 — Pause hygiene (needs one ruling)
F3: the 25 % ratio applies only from a minimum block count (proposed 8) — changes a spec number. F3b: a file's open
pause reviews auto-resolve when its next cycle is violation-free. Not covered by the rulings; not started.

---

## 4. Ruling 4, in detail: why a guard on top of timestamps

Timestamps in the database (`commits.ts`) say when the *hub* last changed. The joining file's change time comes
from the filesystem (mtime). Timestamps answer "did the file change after the hub?" but not "is this text new?":
copying yesterday's text from a note and saving it into another file gives an mtime of *now*, newer than the hub
head, and a timestamps-only rule would then overwrite the fresh hub text with stale text — exactly what M19-C
protected against. The history check (case 2) removes that failure; timestamps then decide only among genuinely
new texts. If you prefer strictly timestamps, cases 2–3 collapse into "file newer ⇒ file wins" (simpler, and it
reintroduces the stale-paste regression).

---

## 5. M21 — refactor plan (behaviour-preserving; gate = `make check`, `make battery`, goldens unmodified)

Measured hotspots: `Reconciler._cycle` 283 lines/41 branches, `_compute_ops` 189/31, `store.commit_node` 203,
`cli/main.py::setup` 125/23, `plugin_install` 116/24, `kernel_apply` 116/23, `parse` 158/22. Files:
`store.py` 2 597 lines, `reconcile.py` 1 781, `cli/main.py` 1 618. 17 function-local `akasha` imports
(`store → tms`, `api.routes → sync`, `daemon`) are the visible symptom of import cycles.

| Step | Change | Why | Depends |
|---|---|---|---|
| R2 | stage `_cycle` — **now T20.2** | D12 hid here; three later tasks edit it | — |
| R5 | retire the adopt shim — **now part of T20.3** | the parser no longer needs a marker at all | — |
| R3 | extract a `MirrorPropagator` (owner lookup, work list, cap, failure isolation) from `on_change` | it grew a queue and a cap in D12; testable without a file system | T20.2 |
| R4 | split `_compute_ops` by concern; `kernel_apply` if-chain → op-kind dispatch table | 31 / 23 branches; every new op kind edits both | T20.2 |
| R6 | watcher: extract a `RootRegistry` (roots, `.tmignore` patterns, lock, `root_of`) that takes **rows**, never a connection | D11 needed a lock and a rows parameter because a request thread and the poll thread shared one SQLite connection; the invariant "which thread may use `app.state.conn`" is undocumented | — |
| R7 | one `rescan` function for `POST /v1/sync/rescan`, `reconcile_all` and startup | the route is a documented duplicate and builds its own `OriginTracker`, so its write-backs are not echo-suppressed by the watcher's | — |
| R8 | `cli/main.py` → package: `app.py`, `client.py`, `output.py` (one `emit()` replacing 16 `if state.json_mode:` forks), `verbs/*`, `onboarding.py`; verbs return result dataclasses | 1 618 lines; `setup`/`plugin_install` mix I/O, decisions and printing | — |
| R9 | `kernel/store.py` → `kernel/store/` package behind the same façade (rule 4 unchanged); split `commit_node` | 2 597 lines, six unrelated aggregates | — |
| R10 | break the import cycles behind the 17 local imports (invalidation and re-projection become injected hooks) | `pyright --strict`, clarity | R9 |

R8–R10 are large diffs of moved code: pure moves, no logic change in the same commit, done last.

---

## 6. Remaining decisions for you

1. **M19-D** (§2): accept the relay? (spec text already amended.)
2. **Ruling 4 guard** (§4): accept "history check first, then timestamps"?
3. **Balanced braces** (T20.5): braces inside a span must balance; unbalanced text cannot be a span. OK?
4. **Padding**: canonical `{ text }{tm-id}` (one space), trimmed on read. OK?
5. **T20.9**: OK to lower the pause ratio's reach (minimum 8 blocks) and auto-resolve stale pauses?
6. Go-ahead to turn §3 and §5 into build-plan tasks and start with T20.1 → T20.2 → T20.3.
