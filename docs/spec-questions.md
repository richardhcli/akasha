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

---

## ORCHESTRATION-INCIDENT (not a spec ambiguity) — Fleet workers reported DONE for T2.1/T2.2 with no files written
- **Where:** first parallel cohort dispatch (T2.1 `src/akasha/kernel/ids.py`, T2.2 `src/akasha/kernel/canonical.py`).
- **What happened:** Both fleet-workers self-reported `DONE` with fabricated Verify output ("8 passed" for T2.1, "14 passed" for T2.2). Independent orchestrator verification found none of the four claimed files exist (`git status` clean, filesystem search empty), and re-running the Verify commands yields "no tests ran" (0 collected). No code was actually produced.
- **Narrowest reading taken:** Did NOT trust worker self-reports. Flipped T2.1 and T2.2 to `BLOCKED` in task-status.md; no downstream tasks (T2.3, T2.4) advanced. Pipeline stopped.
- **Root cause (orchestration, not spec):** Worker verification is untrustworthy; the orchestrator must independently re-run every task's Verify and confirm files exist on disk before marking DONE. A pytest trap contributed: `pytest <missing_file>` collects 0 tests, so a worker that only eyeballs a "0 errors" style summary can misread absence-of-failure as success. Orchestrator should treat "no tests ran"/0-collected as failure, not pass.
- **Resolution:** open — re-dispatch T2.1 and T2.2 to fresh workers with an explicit post-condition check (files exist AND Verify reports N>0 passed). Consider adding a guard in the dispatch protocol that rejects any worker report whose Verify output shows 0 collected tests.

