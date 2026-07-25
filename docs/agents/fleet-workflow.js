export const meta = {
  name: 'fleet-dispatch',
  description: 'Dispatch eligible build-plan tasks to fleet-worker agents with independent verification',
  phases: [
    { title: 'Dispatch', detail: 'one fleet-worker agent per task, file-disjoint tasks in parallel via pipeline' },
    { title: 'Verify', detail: 'independent re-run of each task Verify command, git status cross-check' },
  ],
}

// args: { run_id: string, cohort: [{task_id, goal, depends_on, files, spec_ref, steps, verify_cmd, dod}, ...] }
//
// The caller (outer session, or a scanner agent invoked by it) owns
// eligibility scanning, dependency-order checks, and file-disjointness
// partitioning per docs/agents/fleet-architecture.md — this script only
// dispatches whatever cohort it's handed and independently verifies the
// result. It does NOT read task-status.md or build-plan.md itself (no
// filesystem access is available inside a workflow script).
//
// Why pipeline(), not parallel()+barrier: task A's verifier can start the
// moment task A's worker returns, while task B's worker is still running.
// No barrier is needed between dispatch and verify because verification
// is per-task, not cross-task (unlike a dedup-before-verify pattern).

const WORKER_SCHEMA = {
  type: 'object',
  required: ['status', 'files_changed', 'verify_command', 'verify_exit_code', 'verify_stdout_tail', 'spec_questions'],
  properties: {
    status: { enum: ['DONE', 'BLOCKED'] },
    files_changed: { type: 'array', items: { type: 'string' } },
    verify_command: { type: 'string' },
    verify_exit_code: { type: 'number' },
    verify_stdout_tail: { type: 'string' },
    spec_questions: { type: 'array', items: { type: 'string' } },
    blocked_reason: { type: 'string' },
    cursor_task_json: { type: 'string' },
    cursor_response_json: { type: 'string' },
  },
}

const VERIFY_SCHEMA = {
  type: 'object',
  required: ['files_exist', 'verify_exit_code', 'verify_stdout_tail', 'git_status_matches_claim', 'verdict'],
  properties: {
    files_exist: {
      type: 'array',
      items: {
        type: 'object',
        properties: { path: { type: 'string' }, exists: { type: 'boolean' }, nonempty: { type: 'boolean' } },
      },
    },
    verify_exit_code: { type: 'number' },
    verify_stdout_tail: { type: 'string' },
    git_status_matches_claim: { type: 'boolean' },
    verdict: { enum: ['CONFIRMED_DONE', 'CONTRADICTS_CLAIM', 'CONFIRMED_BLOCKED'] },
    notes: { type: 'string' },
  },
}

// args is documented to arrive verbatim as whatever was passed to the
// Workflow call, but has been observed arriving as a JSON-encoded string
// rather than a native object — normalize defensively so `.cohort` always
// resolves to a real array instead of throwing deep inside pipeline().
const _args = typeof args === 'string' ? JSON.parse(args) : args
if (!Array.isArray(_args.cohort)) {
  throw new Error(
    `fleet-workflow: args.cohort must be an array, got ${typeof _args.cohort} ` +
      `(args was ${typeof args}). Check the Workflow call passed a native ` +
      `array for cohort, not a stringified one.`,
  )
}

const HANG_GUARD =
  'If you have not reached a terminal status (DONE or BLOCKED) within ' +
  'roughly 20 tool calls, stop immediately and report status BLOCKED with ' +
  'blocked_reason "possible hang — exceeded tool-call budget". Do not ' +
  'continue indefinitely.'

function buildWorkerPrompt(task) {
  return [
    `You are a fleet-worker executing build-plan task ${task.task_id}.`,
    '',
    `Goal: ${task.goal}`,
    `Depends on: ${(task.depends_on || []).join(', ') || 'none'}`,
    `Files you may create or edit: ${task.files.join(', ')}`,
    `Spec reference: ${task.spec_ref}`,
    `Steps: ${task.steps}`,
    `Verify command: ${task.verify_cmd}`,
    `Definition of done: ${task.dod}`,
    '',
    'Non-negotiable rules (root CLAUDE.md): never invent schema/endpoints/',
    'grammar beyond the spec (narrowest reading + # SPEC-QUESTION: comment on',
    'ambiguity); never edit golden files/fixtures to make tests pass; all',
    'persistent writes go through src/akasha/kernel/store.py; no pickle/eval/',
    'exec anywhere; touch only the Files listed above.',
    '',
    'Run the Verify command yourself via Bash and report its REAL exit code',
    'and output tail — do not estimate or guess these values.',
    '',
    HANG_GUARD,
    '',
    'Return your result via the required structured schema. files_changed',
    'must be the actual output of `git diff --name-only` plus untracked',
    'files you created (check with `git status --porcelain`), not a guess.',
  ].join('\n')
}

function buildVerifierPrompt(task, workerResult) {
  return [
    `You are an independent verifier for build-plan task ${task.task_id}.`,
    'You did NOT do the work. Your job is to catch a worker that claims',
    'success without having actually done it — do not trust anything below',
    'except as a claim to check.',
    '',
    `The worker claims: status=${workerResult.status}, ` +
      `files_changed=${JSON.stringify(workerResult.files_changed)}, ` +
      `verify_exit_code=${workerResult.verify_exit_code}.`,
    '',
    `Verify command to re-run yourself: ${task.verify_cmd}`,
    '',
    'Steps:',
    '1. Run the verify command yourself via Bash. Record the REAL exit code',
    '   and output tail.',
    '2. For every path in files_changed, check it exists on disk and is',
    '   non-empty.',
    '3. Run `git status --porcelain` and `git diff --name-only` and confirm',
    '   they are consistent with the claimed files_changed list.',
    '4. Set verdict:',
    '   - CONFIRMED_DONE only if the worker claimed DONE, your own verify run',
    '     exits 0, and every claimed file exists and is non-empty.',
    '   - CONTRADICTS_CLAIM if the worker claimed DONE but any of the above',
    '     checks fail.',
    '   - CONFIRMED_BLOCKED if the worker claimed BLOCKED (no further claim',
    '     to contradict, just confirm the verify command still fails).',
    '',
    HANG_GUARD,
  ].join('\n')
}

const results = await pipeline(
  _args.cohort,
  (task) =>
    agent(buildWorkerPrompt(task), {
      // Orchestrator stamps worker_agent_type per docs/agents/fleet-architecture.md
      // §"Worker Mode Selection"; default preserves old hybrid behavior for
      // callers/cohorts that don't opt into pure-Claude workers.
      agentType: task.worker_agent_type || 'fleet-worker',
      phase: 'Dispatch',
      label: task.task_id,
      schema: WORKER_SCHEMA,
    }),
  (workerResult, task) => {
    if (!workerResult) return null
    return agent(buildVerifierPrompt(task, workerResult), {
      phase: 'Verify',
      label: `verify:${task.task_id}`,
      schema: VERIFY_SCHEMA,
    }).then((verification) => ({
      task_id: task.task_id,
      worker_prompt: buildWorkerPrompt(task),
      worker_result: workerResult,
      verify_prompt: buildVerifierPrompt(task, workerResult),
      verification,
    }))
  },
)

return { run_id: _args.run_id, results: results.filter(Boolean) }
