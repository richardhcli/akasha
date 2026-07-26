# Spec questions

Log of ambiguities hit while implementing `docs/build-plan.md`. Per build-plan
rule 0.2 / rule 2: never invent schema, endpoints, ID formats, or grammar
beyond `docs/mvp-spec.md`. When something is ambiguous, implement the
narrowest reading, add a `# SPEC-QUESTION:` comment at the site, and log an
entry here so a human can resolve it.

Entry format:

```
## <task ID> — <one-line question>
- **Where:** <file:line>
- **Narrowest reading taken:** <what was implemented in the meantime>
- **Resolution:** <filled in once a human answers; leave "open" until then>
```

This file holds **open** questions only. Resolved entries are moved to
`docs/archived-questions.md` in a batch when the milestone that raised them
closes (context-size optimization — an agent scanning for outstanding
ambiguities shouldn't have to read past closed ones). See that file for the
full resolved history: M1 (T1.3/T1.5/T1.6/T1.7), M3 (T3.1/T3.2/T3.5/T3.6×2),
M4 (13 entries, 2026-07-12), M5 (10 entries: T5.1/T5.5/T5.8-*, 2026-07-13),
M6 (1 entry: T6.5, 2026-07-14), M8 (4 entries: T8.0/T8.1/T8.3/T8.5b,
2026-07-18 via fable rulings), and the **pre-dogfood triage** (11 entries,
2026-07-20/21 via a fable ruling: T7.1, T7.7, T7.3, T7.5×2, T7.6, T9.2×3,
T9.3, T10.2b — see that file's "Pre-dogfood spec-question triage" section
for the full ruling on each), and 2026-07-26 (2 entries: T9.6, T11.1's
sync-roots/watcher half — both closed by the same-day T9.6 live-watcher fix).

**Open questions: 1.** Every entry open as of M10's first code-complete
milestone (2026-07-19) has been triaged, resolved, and archived — see
`docs/archived-questions.md`. New ambiguities encountered during the
one-month dogfood gate or any future work should be logged here per the
entry format above.

## T11.1 — How does the very first human token get minted on a fresh DB, given `POST /v1/tokens` is `require_human`?
- **Where:** `src/akasha/api/routes/tokens.py` (`create_token`, `require_human`); `src/akasha/api/deps.py` (`require_human`); `docs/dogfood/README.md` step 6.
- **Narrowest reading taken:** Spec §4.11/§4.12 mark the whole `/tokens` row human-only, and there is no documented bootstrap endpoint or CLI flag for a brand-new DB with zero existing tokens. Treated this as a one-time pre-daemon operator/test-harness bootstrap step (an "embedded caller" per `store.connect`'s own docstring): mint one throwaway bootstrap token via a direct call into `kernel/store.py`'s `create_token` (never a second write path — still routed through `store.py` per rule 0.4, same pattern `tests/battery/soak.py:243` already uses), used solely to authorize the real `dogfood-smoke` token creation over genuine HTTP. This does not block T11.1 but is a real first-run UX gap for anyone standing up a fresh daemon without the test harness's direct-DB shortcut.
- **Resolution:** open.

