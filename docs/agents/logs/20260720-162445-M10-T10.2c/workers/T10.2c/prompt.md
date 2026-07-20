You own exactly one build-plan task in the akasha repo: T10.2c — Wire §4.10 trigger evaluation into the commit path (story 8).

Goal: in store.commit_node, after the existing invalidate call, INSIDE the same `with conn:` transaction, evaluate the `all_subtasks_closed` condition for the committed node's parent supertask(s), via a function-body deferred import of tms/triggers.py (mirroring the existing invalidate import pattern).

Scope narrowing (do NOT widen): wire ONLY all_subtasks_closed. Do not touch facet_interface_changed (already live via T7.2), evidence_retracted (covered by T7.2b), or recheck_after (no persisted schedule — out of scope, no migration).

Preserve idempotence via the existing find_open_reviews gate in triggers.py; never write task_state from this path.

New test in tests/integration/test_tms.py driving the scenario through the REAL commit/API path (not calling evaluate() directly): assert fires exactly once on last subtask close, not before, no duplicate on re-commit, task_state never auto-closed.

Files allowed: src/akasha/kernel/store.py, tests/integration/test_tms.py only.
Verify: uv run pytest tests/integration/test_tms.py.
Also required green: make check (ruff/pyright/unit/property), no regression in T7.1-T7.5 tests.
After landing: update docs/acceptance.md row 8 (PARTIAL→GREEN) with freshly re-run counts, and flip T10.2c TODO→DONE in docs/agents/task-status.md with a detailed note.

Full task detail (ground truth): docs/build-plan.md "### T10.2c" block; docs/agents/task-status.md T10.2c row.
