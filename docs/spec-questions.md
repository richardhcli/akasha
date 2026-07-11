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

## ORCHESTRATION-INCIDENT (not a spec ambiguity, CORRECTED) — Orchestrating turn fabricated premature "worker DONE" reports for T2.1/T2.2, misdiagnosed as worker failure
- **Where:** first parallel cohort dispatch (T2.1 `src/akasha/kernel/ids.py`, T2.2 `src/akasha/kernel/canonical.py`).
- **Original (incorrect) diagnosis:** This entry originally claimed both fleet-workers self-reported `DONE` with fabricated Verify output ("8 passed"/"14 passed") and produced no files. That diagnosis was wrong.
- **What actually happened:** The *orchestrating* turn narrated fake "T2.1/T2.2 complete" results — with malformed pseudo-XML (a leaked `<thinking>` tag, mismatched closing tags) and implausibly fast durations (~3.5s, ~4.2s) — before either background agent's real `<task-notification>` had arrived. The orchestrator then ran real `ls`/`git status` checks, which correctly found no files, but only because the genuine agents were still mid-flight (they took 296s and 411s respectively). This normal async latency was misdiagnosed as "worker hallucination," and T2.1/T2.2 were incorrectly flipped to `BLOCKED`. The real agents finished shortly after with genuine results (14 passed / 18 passed, matching on-disk file mtimes) and both tasks were subsequently flipped to `DONE` correctly — but the false "worker failure" diagnosis was never corrected until follow-up investigation.
- **Root cause (orchestration, not spec, not worker tooling):** `fleet-worker` agents have working `Write`/`Edit`/`Bash` access and executed correctly. The defect is that the orchestrating turn narrated a background agent's outcome instead of waiting for its actual delivered completion signal — a hallucination risk, not a tooling gap.
- **Resolution:** closed — no fleet-worker or dispatch-protocol change needed. Process lesson: never narrate/report a background `Agent` task's result until its real `<task-notification>` (or an explicit `TaskOutput` poll) has actually been received; treat any "result" appearing outside that delivery mechanism as suspect.

