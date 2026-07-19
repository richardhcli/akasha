You are a fleet-worker executing build-plan task **T10.3 — Acceptance mapping (`docs/acceptance.md`)**. This is the FINAL task in the build plan. **Read these authoritative sources in full first:**
- `docs/build-plan.md`, the `### T10.3 — Acceptance mapping` entry — its story→verifier table was **corrected today** (rows 1/2/3/5) and is now accurate; use it, not any older memory.
- `docs/mvp-spec.md` §9 (acceptance table) and the M10 DoD line.
- `docs/vision.md` §8 (the 9 acceptance stories) — the source of truth for what each story requires.
- `docs/spec-questions.md` — the two `## T10.3` entries + the `## T10.2b` entry (context on the row-1 manual-script leg and the story-2 verifier).

## What this task is
Author a NEW file `docs/acceptance.md` mapping each of PRD §8 stories **1–9** to its verifying test or checked manual script, confirming each is green. **This is `docs/acceptance.md` ONLY — it is the single file in T10.3's Files list (rule 0.8). Do not touch any other file.**

## Critical framing: this is a CODE-COMPLETE deliverable, worded HONESTLY (this is the whole point of the task)
The M10 DoD says "all nine rows green on **Windows CI**" — that Windows-CI attestation **cannot run from this Linux box** and is explicitly a pending leg (same "code-complete, CI-leg pending first push" framing M0/M6/M8/M9 all use — read those milestone headers in `docs/agents/task-status.md` for the exact tone). Your job is to author the mapping and attest the **locally-runnable** greenness truthfully, marking every external leg as pending. Per PRD vision invariant 5 ("flagged, never guessed") and the fable ruling in `docs/spec-questions.md`:
- **Every row must state:** (a) the verifier by its REAL on-disk name (module::test or script path), (b) exactly what you ran and where/when — e.g. "`tests/battery` — 47 passed, ubuntu/Linux, 2026-07-19, `make battery`", and (c) an explicit per-leg status: **GREEN (local Linux)**, or **pending first CI push** (the Windows-CI attestation), or **pending first scheduled nightly run** (the 24h soak), or **pending manual execution** (an unrun manual script).
- **No bare checkmarks. No unqualified "green" for anything you did not personally run to completion.** An unchecked manual script must never be represented as checked.

## The 9 rows (use the corrected build-plan/§9 citations; VERIFY each test name exists and run it)
1. **capture ≤3s (syntax path)** — no automated timing test exists (grep-verified). This row rests on the M10 DoD's alternative leg: a **checked manual script**. No such script has been run. State honestly: the syntax capture path itself is functionally exercised green by `tests/integration/test_api.py` / `test_cli*.py` (T4.4/T4.8) — cite the real ones you find — but the **≤3s timing assertion is pending manual execution**; reference where the manual capture-timing check would run (the `akasha add`/`POST /nodes` path). Do NOT invent a passing timing number.
2. **contradiction surface (non-LLM)** — `tests/integration/test_contradiction_surfacing.py` (T10.2b, landed today). Run it; cite the real pass count.
3. **invalidation on major edit** — `tests/integration/test_tms.py::test_review_revised_reclassifies_and_cascades` + `::test_s1_node_retraction_flags_dependents`, and `tests/unit/tms/test_invalidate.py` (the `*`-binding case). Run them; confirm the names exist.
4. **split/merge zero dangling** — the split/merge property test (find its real path under `tests/property/` or `tests/integration/` — the build-plan says `test_split_merge.py`; confirm the actual path/name).
5. **as-of time travel** — `tests/integration/test_api.py::test_nodes_get_as_of_returns_earlier_body` (T4.4).
6. **review economy (cap, dashboard)** — the dashboard + metrics assertions (T10.1/T9.2): `tests/integration/test_ui_dashboard.py` + `tests/unit/test_metrics.py` (confirm real names).
7. **contract sync losslessness** — battery E01–E20 (`tests/battery/test_edit_battery.py`, T5.8). Note the Windows leg of the battery is pending-CI (T9.1's lock/retry runs for real only on Windows).
8. **tasks + supertask trigger + S0 lifecycle** — `tests/integration/test_tms.py::test_supertask_flag` (T7.4) + battery E06/E08.
9. **daemon residency** — the soak (`tests/battery/soak.py`, T9.5) + crash-recovery (`test_crash_recovery_idempotent`, T5.6). The soak's **literal 24h/Windows leg is pending first scheduled run**; the accelerated proxy runs locally (see below).

For every row, actually RUN the cited test(s) and record the real pass count. If a cited test does not exist under the stated name, do not paper over it — find the real one (grep) and cite that, or if genuinely absent, flag it as a `# SPEC-QUESTION:`-style gap in the doc (do not silently pass).

## Running the legs (record real results in the doc)
- `make check` (ruff + pyright + unit/property) — run it, record result.
- `make battery` (`tests/battery`) — run it, record result.
- **Soak:** do NOT run the literal 24h default (`soak.py` defaults to `--hours 24.0`). Run the accelerated proxy `uv run python tests/battery/soak.py --hours 0.05` (~3 min, the exact in-session proxy T9.5 established) for the local-green attestation, and word the literal 24h nightly-Windows run as pending.

## Non-negotiable rules
Touch ONLY `docs/acceptance.md`. Never edit golden files/fixtures/tests to make anything "pass". Never invent a passing result, timing number, or CI attestation you did not produce — honesty is the entire deliverable here. If a story genuinely has no green verifier, that is a gap to flag in the doc, not a silent pass (T10.3 Step 2).

If you hit a genuine ambiguity, add it as a clearly-marked gap note in the doc and continue with the honest status; do not block on trivialities, but do not fabricate.

If you have not finished within ~30 tool calls, stop and report `status: "BLOCKED"`, `blocked_reason: "possible hang — exceeded tool-call budget"`.

`files_changed` must be the real `git status --porcelain` output (expect exactly `docs/acceptance.md` as a new file). End your reply with a fenced ```json block containing exactly: status, files_changed, verify_command, verify_exit_code, verify_stdout_tail, spec_questions (array, empty if none), blocked_reason (only if BLOCKED). For `verify_command` use `make check && make battery && uv run python tests/battery/soak.py --hours 0.05` and report its real combined result; note in your prose that the literal-24h/Windows-CI legs are the pending attestation, per the code-complete framing.
