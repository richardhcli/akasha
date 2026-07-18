#!/usr/bin/env python3
"""
Mechanical, schema-checked writer for docs/agents/logs/<run_id>/.

This exists to close a real gap: after the first fleet-dispatch run
(20260712-M4-daemon-api), every subsequent cohort (T4.2 through the rest of
M4, and all of M5-M8) was dispatched and verified for real but never
persisted a durable log, because there was no `Workflow` tool available in
that session to force the "caller writes the literal returned object"
discipline described in docs/agents/runbook.md — the log-writing step was
just... skipped, silently, with no error. This script makes that step a
single deterministic command instead of "remember to call Write three times
with the right paths," and it *fails loudly* on a malformed or incomplete
result instead of silently producing a half-written or schema-violating log.

It is a pure, non-LLM file writer: it never talks to any model, never
narrates a result, and never invents a value. Whatever JSON you give it is
exactly what lands on disk (after required-field validation) — same trust
property as scripts/fleet/cursor_bridge.py's independent verify_cmd re-run.

Usage:
  # Persist a fleet-worker's result (schema mirrors WORKER_SCHEMA in
  # docs/agents/fleet-workflow.js -- keep both in sync if you change either):
  python scripts/fleet/log_run.py task \\
    --run-id 20260718-143000-M9 --task-id T9.1 --kind worker \\
    --prompt /tmp/t9.1-worker-prompt.md --result -   <<'JSON'
  {"status":"DONE","files_changed":["src/x.py"],"verify_command":"...",
   "verify_exit_code":0,"verify_stdout_tail":"...","spec_questions":[]}
  JSON

  # Persist the independent verifier's result (schema mirrors VERIFY_SCHEMA):
  python scripts/fleet/log_run.py task \\
    --run-id 20260718-143000-M9 --task-id T9.1 --kind verify \\
    --prompt /tmp/t9.1-verify-prompt.md --result /tmp/t9.1-verify-result.json

  # Create/update the run manifest:
  python scripts/fleet/log_run.py manifest \\
    --run-id 20260718-143000-M9 --cohort T9.1 T9.2 --status IN_PROGRESS

`--result` accepts a file path, or `-` to read JSON from stdin.
`--prompt` must be a path to a file containing the exact, verbatim prompt
text that was sent to the agent (never a paraphrase).

By default this refuses to overwrite an existing prompt.md/result.json for
a given (run_id, kind, task_id) -- once written, a log entry is treated the
same way the product treats history: append-only, not silently rewritten.
Pass --force to explicitly replace one (e.g. a genuine re-verification).
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).parent.parent.parent
LOGS_ROOT = REPO_ROOT / "docs" / "agents" / "logs"

# Kept in sync by hand with WORKER_SCHEMA / VERIFY_SCHEMA in
# docs/agents/fleet-workflow.js -- that file is the schema for the
# Workflow-tool dispatch path (Path A); this is the same contract enforced
# for the direct Task-tool dispatch path (Path B). If you change one,
# change the other, or Path A and Path B logs silently drift apart.
REQUIRED_FIELDS: dict[str, list[str]] = {
    "worker": [
        "status",
        "files_changed",
        "verify_command",
        "verify_exit_code",
        "verify_stdout_tail",
        "spec_questions",
    ],
    "verify": [
        "files_exist",
        "verify_exit_code",
        "verify_stdout_tail",
        "git_status_matches_claim",
        "verdict",
    ],
}

ENUM_FIELDS: dict[str, dict[str, list[str]]] = {
    "worker": {"status": ["DONE", "BLOCKED"]},
    "verify": {"verdict": ["CONFIRMED_DONE", "CONTRADICTS_CLAIM", "CONFIRMED_BLOCKED"]},
}

MANIFEST_STATUSES = ["IN_PROGRESS", "COMPLETE", "PARTIAL", "ABORTED"]

# workers/<task_id>/ or verify/<task_id>/ -- matches the layout documented
# in docs/agents/runbook.md and docs/agents/fleet-architecture.md.
KIND_DIRNAME = {"worker": "workers", "verify": "verify"}


class ValidationError(Exception):
    pass


def validate_result(kind: str, result: dict[str, Any]) -> None:
    missing = [f for f in REQUIRED_FIELDS[kind] if f not in result]
    if missing:
        raise ValidationError(
            f"result JSON for kind={kind!r} is missing required field(s): {missing} "
            f"(required: {REQUIRED_FIELDS[kind]})"
        )
    for field, allowed in ENUM_FIELDS.get(kind, {}).items():
        if result[field] not in allowed:
            raise ValidationError(
                f"field {field!r} = {result[field]!r} is not one of {allowed}"
            )
    if kind == "worker" and result["status"] == "BLOCKED" and not result.get("blocked_reason"):
        raise ValidationError(
            "status is BLOCKED but blocked_reason is missing/empty -- "
            "CLAUDE.md rule 9: a blocked task must record why, never silently move on"
        )


def read_result_json(spec: str) -> dict[str, Any]:
    text = sys.stdin.read() if spec == "-" else Path(spec).read_text()
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise ValidationError(f"--result did not parse as JSON: {e}") from e
    if not isinstance(data, dict):
        raise ValidationError(f"--result must be a JSON object, got {type(data).__name__}")
    return data


def cmd_task(args: argparse.Namespace) -> int:
    prompt_path = Path(args.prompt)
    if not prompt_path.is_file():
        print(
            f"error: --prompt path does not exist or is not a file: {prompt_path}",
            file=sys.stderr,
        )
        return 1
    prompt_text = prompt_path.read_text()
    if not prompt_text.strip():
        print(f"error: --prompt file is empty: {prompt_path}", file=sys.stderr)
        return 1

    try:
        result = read_result_json(args.result)
        validate_result(args.kind, result)
    except ValidationError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    if result.get("task_id") and result["task_id"] != args.task_id:
        print(
            f"error: result JSON's task_id ({result['task_id']!r}) does not match "
            f"--task-id ({args.task_id!r}) -- refusing to write a mismatched log entry",
            file=sys.stderr,
        )
        return 1

    out_dir = LOGS_ROOT / args.run_id / KIND_DIRNAME[args.kind] / args.task_id
    result_file = out_dir / "result.json"
    prompt_file = out_dir / "prompt.md"

    if not args.force:
        existing = [p for p in (result_file, prompt_file) if p.exists()]
        if existing:
            print(
                "error: refusing to overwrite existing log entry (append-only by "
                f"default, same as the product's own history): {[str(p) for p in existing]}\n"
                "       pass --force if this is a genuine re-verification.",
                file=sys.stderr,
            )
            return 1

    out_dir.mkdir(parents=True, exist_ok=True)
    prompt_file.write_text(prompt_text)
    result_file.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")

    print(f"wrote {prompt_file.relative_to(REPO_ROOT)}")
    print(f"wrote {result_file.relative_to(REPO_ROOT)}")
    return 0


def cmd_manifest(args: argparse.Namespace) -> int:
    if args.status not in MANIFEST_STATUSES:
        print(f"error: --status must be one of {MANIFEST_STATUSES}", file=sys.stderr)
        return 1

    run_dir = LOGS_ROOT / args.run_id
    manifest_file = run_dir / "manifest.json"

    cohort: list[str] = []
    if manifest_file.exists():
        try:
            existing = json.loads(manifest_file.read_text())
        except json.JSONDecodeError as e:
            print(f"error: existing manifest.json is not valid JSON: {e}", file=sys.stderr)
            return 1
        cohort = list(existing.get("cohort", []))

    for task_id in args.cohort:
        if task_id not in cohort:
            cohort.append(task_id)

    manifest = {
        "run_id": args.run_id,
        "cohort": cohort,
        "final_status": args.status,
        "notes": args.notes or "",
    }

    run_dir.mkdir(parents=True, exist_ok=True)
    manifest_file.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    print(f"wrote {manifest_file.relative_to(REPO_ROOT)}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_task = sub.add_parser("task", help="persist one worker or verify result")
    p_task.add_argument("--run-id", required=True)
    p_task.add_argument("--task-id", required=True)
    p_task.add_argument("--kind", required=True, choices=["worker", "verify"])
    p_task.add_argument(
        "--prompt", required=True, help="path to a file with the verbatim prompt text"
    )
    p_task.add_argument("--result", required=True, help="path to a JSON file, or - for stdin")
    p_task.add_argument("--force", action="store_true", help="overwrite an existing log entry")
    p_task.set_defaults(func=cmd_task)

    p_manifest = sub.add_parser("manifest", help="create or update a run's manifest.json")
    p_manifest.add_argument("--run-id", required=True)
    p_manifest.add_argument(
        "--cohort", required=True, nargs="+", help="task ids to merge into cohort"
    )
    p_manifest.add_argument("--status", required=True, choices=MANIFEST_STATUSES)
    p_manifest.add_argument("--notes", default="")
    p_manifest.set_defaults(func=cmd_manifest)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
