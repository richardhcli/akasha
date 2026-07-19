You are an independent verifier for build-plan task **T10.3 — Acceptance mapping (`docs/acceptance.md`)**, the final build-plan task. You did NOT do the work. This deliverable's entire value is HONESTY, so your job is specifically to catch fabrication: a cited test that doesn't exist, a "green" that isn't, a checkmark on an unrun manual/CI/soak leg, or an invented timing number.

## Authoritative context
`docs/build-plan.md` T10.3 entry (its story→verifier table was corrected today), `docs/mvp-spec.md` §9, `docs/vision.md` §8 (the 9 stories). The task is CODE-COMPLETE by design: the "all nine rows green on **Windows CI**" DoD leg cannot run on this Linux box and must be marked *pending*, per the M0/M6/M8/M9 framing. Local (Linux) legs must be genuinely green; external legs (Windows CI, 24h soak, manual timing script) must be marked pending — never as done.

## The worker's claim
- status=DONE; files_changed=["docs/acceptance.md"] (new file, 264 lines).
- Maps PRD §8 stories 1–9 to real on-disk verifiers with per-leg honest status.
- Claims local legs green: `make check` (ruff clean, pyright 0, 408 unit+property), `make battery` (47), accelerated soak `soak.py --hours 0.05` (passed, 90/90 ticks, max RSS 62.52MB, 0 unhandled exceptions).
- Claims story 1's ≤3s timing = pending manual execution (no automated timing test); story 7's Windows-FS leg + story 9's 24h/real-OS leg = pending first CI push / first scheduled nightly run.
- Claims each story-specific cited test was run (contradiction 9, TMS/invalidation, split/merge property 5, as-of 1, dashboard+metrics 28, daily-cap 1, supertask+E06/E08, crash-recovery 1).

## Steps
1. `git status --porcelain` — confirm the ONLY change is `docs/acceptance.md` (new). (Ignore unrelated pre-existing items: a deleted `docs/agents/temp/handoff-*.md` and an untracked `.cursor/*` file — not this task's.) If any OTHER file changed, that violates the single-file scope → CONTRADICTS_CLAIM.
2. Read `docs/acceptance.md` in full. Confirm all 9 PRD §8 stories are present and each row names a concrete verifier (a `module::test` id, a test file path, or a named manual script).
3. **For every test the doc cites as green, confirm it EXISTS and PASSES** — run them yourself (batch where convenient), e.g.:
   - `uv run pytest tests/integration/test_contradiction_surfacing.py -q` (story 2)
   - the story-3 tests: `uv run pytest "tests/integration/test_tms.py::test_review_revised_reclassifies_and_cascades" "tests/integration/test_tms.py::test_s1_node_retraction_flags_dependents" tests/unit/tms/test_invalidate.py -q`
   - story 5: `uv run pytest "tests/integration/test_api.py::test_nodes_get_as_of_returns_earlier_body" -q`
   - story 8: `uv run pytest "tests/integration/test_tms.py::test_supertask_flag" -q`
   - the split/merge property test (story 4), the dashboard/metrics tests (story 6), and crash-recovery (story 9) — run whatever names the doc actually cites.
   If ANY cited test does not exist under the cited name or does not pass, that's a fabrication → CONTRADICTS_CLAIM.
4. **Honesty audit (the core check):** confirm the doc does NOT mark as done/green/✓ any of: story 1's ≤3s timing assertion, story 7's Windows-filesystem battery leg, story 9's literal 24h soak, or any Windows-CI attestation. Each of those MUST be explicitly worded as pending (pending manual execution / pending first CI push / pending first scheduled nightly run). Confirm no invented timing number appears for story 1. If the doc overclaims any external leg as completed, that's the exact failure this task exists to prevent → CONTRADICTS_CLAIM.
5. Re-run the local legs yourself to confirm the headline claim: `make check`, `make battery`, and `uv run python tests/battery/soak.py --hours 0.05`. Record real results. (The soak proxy takes ~3 min; that's expected.)
6. Verdict:
   - **CONFIRMED_DONE** only if: the sole changed file is `docs/acceptance.md`; all 9 stories are mapped to real, existing, passing local verifiers (or honestly-pending external ones); every external/manual leg is explicitly marked pending (no overclaim, no fabricated number); and your own `make check`/`make battery`/accelerated-soak runs are green.
   - **CONTRADICTS_CLAIM** if any cited test is missing/failing, any external leg is overclaimed as done, a timing number is fabricated, another file was changed, or a local leg you re-ran is not actually green.

If you have not reached a terminal verdict within ~25 tool calls, stop and report notes.

End your reply with a fenced ```json block containing exactly: files_exist (array of {path, exists, nonempty}), verify_exit_code, verify_stdout_tail, git_status_matches_claim (boolean), verdict, notes.
