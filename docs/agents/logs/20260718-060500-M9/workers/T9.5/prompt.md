Repo root: /home/richardhcli/projects/personal-projects/akasha. Run id: 20260718-060500-M9. Task id: T9.5.

You are a fleet-worker per your persona in `.claude/agents/fleet-worker.md` (read it first, and read `docs/build-plan.md`'s T9.5 entry verbatim yourself before doing anything -- the task object below is orchestrator guidance, not a replacement for the spec).

## Task object (from fleet-orchestrator)
```json
{
  "task_id": "T9.5",
  "goal": "Prove residency: RSS < 150 MB, idle CPU ~ 0%, zero unhandled exceptions.",
  "depends_on": ["T9.1", "T9.2", "T9.3", "T9.4"],
  "files": ["tests/battery/soak.py", ".github/workflows/ci.yml"],
  "spec_ref": "M9 DoD, \u00a79 story 9",
  "steps": "Build-plan Steps (verbatim): (1) Drive realistic edit traffic over 24 h (OR a scaled proxy in CI with a full run nightly on `main`). (2) Sample RSS/CPU into metrics. (3) Assert zero unhandled exceptions. ORCHESTRATOR GUIDANCE: deliver tests/battery/soak.py as a real soak harness with an INJECTABLE clock, reuse T9.2 metrics.py helpers, add nightly CI job. SCOPE GUARD: only tests/battery/soak.py and .github/workflows/ci.yml. Do NOT touch metrics.py/reconcile.py.",
  "verify_cmd": "uv run python tests/battery/soak.py --hours 0.05",
  "verify_cmd_build_plan_literal": "uv run python tests/battery/soak.py --hours 24 (nightly Windows)",
  "dod": "RSS < 150 MB throughout; idle CPU ~ 0%; zero unhandled exceptions in logs."
}
```

Full prompt included hard constraints: touch only Files list, no pickle/eval/exec, all DB access via store.py, never edit tests/golden/**, run ruff+pyright+unit+property+battery, run literal verify_cmd, don't modify task-status.md/build-plan.md, don't commit/push.
