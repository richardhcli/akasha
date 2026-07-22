# Fleet architecture retrospective

**Date:** 2026-07-21. **Scope:** an evidence-based review of the
`fleet-orchestrator` / `fleet-worker` / `fleet-verifier` architecture
(`.claude/agents/*.md`, `docs/agents/fleet-architecture.md`,
`docs/agents/runbook.md`) as actually exercised across this project's build,
not as designed on paper. **Why this document exists:** the user intends to
carry this same architecture into later phases of akasha and into other
projects, so it needs a refinement pass grounded in what really happened,
not a restatement of its own design intent. **Method:** every claim below is
checked against a primary source — `docs/agents/logs/*/manifest.json` (12
run logs), `docs/agents/task-status.md`'s per-task notes, and
`docs/archived-questions.md`'s two incident writeups
(`ORCHESTRATION-INCIDENT`, `WORKFLOW-TOOL-HEADLESS-GAP`) — not from the
architecture document's own description of itself.

---

## Verdict, up front

The three findings below matter more than anything in the "worked well"
section further down, because they are gaps between what the design
document claims and what the run logs show. A reader who only takes one
section from this document should take this one.

### 1. The headline safety guarantee was never actually built — it's carried by discipline, the same category of thing that failed before

`fleet-architecture.md` frames Path A (a deterministic `Workflow` script)
as closing the `ORCHESTRATION-INCIDENT` failure mode **"by construction"**:
a script's `agent()` call is a real blocking await, so an orchestrator
literally cannot narrate a result before receiving it. That is a genuinely
strong design. **It has never run.** `WORKFLOW-TOOL-HEADLESS-GAP`
(`docs/archived-questions.md`) found the `Workflow` tool does not exist in
headless `claude -p` sessions at all — the only mode every real dispatch in
this project's history has used — and it remains unverified even
interactively. Every one of the 12 logged runs used **Path B**, where the
guarantee is "never write down a result you have not actually received" —
a prompt-level rule enforced by the dispatching session's own discipline.
That is precisely the category of thing that produced the original
incident (an orchestrating turn narrating a fabricated result before the
real one arrived). The project has been running on the fallback path for
its entire history, and the fallback path's safety property is "the same
kind of agent that caused the bug promises not to cause it again," not a
structural guarantee. This isn't a hypothetical risk — the incident write-up
confirms it happened once already, was caught, and got documented as
resolved by adopting a rule, not a mechanism.

**What actually holds the line today is something the design doc
undersells: the independent verifier re-running Verify from scratch,
plus mechanical, schema-validated logging (`log_run.py`) that refuses to
accept a malformed or duplicate result.** Those two things are real,
tool-enforced boundaries — not prompts — and they deserve to be described
as the actual safety mechanism, with Path A relabeled as an unrealized
aspiration rather than "the mechanism, with Path B as fallback."

### 2. The three-tier cost model collapsed to two tiers after M3, and the savings claim was never re-measured

`fleet-architecture.md` proposes Tier 3 (Cursor) as the cost optimization:
"cutting 70–90% of token spend versus Opus-everywhere" by routing
mechanical/spec-verbatim work to the cheapest tier. Checking
`docs/agents/task-status.md`'s own per-task notes: Cursor delegation
happened for **T3.3, T3.5, T3.6, T3.7 only** — four tasks in one milestone
(M3, 2026-07-11). Every task from M4 onward (T4.1 through T10.2c and the
final T9.2c/T9.3b pair — roughly 35+ tasks) was a **direct Sonnet or Opus
edit**, worker-owned, with no Cursor delegation at all. The bridge's
"target contract" for Cursor running its own local Verify loop
(`fleet-architecture.md` §"Future Enhancements" #1) is explicitly flagged
in that same document as possibly not landed in code — and since nothing
exercised it after M3, whether it actually works today is simply unknown,
not confirmed-and-then-abandoned. The stated 70–90% savings figure is a
projection based on a usage pattern that stopped in the project's first
week. Carrying that number forward into cost planning for a new project
would be planning against a number nobody re-checked.

This is not necessarily wrong as a decision — it's plausible that
judgment-heavy tasks (which dominated M4 onward: auth design, transaction
wiring, spec interpretation) genuinely aren't a good fit for Tier 3, and
fleet-workers correctly self-selected direct edits for them per their own
decision tree. But that means the 3-tier *architecture* is now better
described as a place a 2-tier system *could* drop to Tier 3 for the right
task shape, not a load-bearing part of this project's actual cost profile.

### 3. The verifier's real catches were shallow, because worker and verifier are the same model family — the deepest catches came from a different mechanism entirely

Across roughly 30 task dispatches with recorded verifier verdicts (a
9-task M4 cohort predates consistent Path B logging and has no per-task
verdicts on record — its own small gap, noted below), there is exactly
**one** genuine `CONTRADICTS_CLAIM` in production use: T10.3's story-6 test
count (the doc claimed 28, the verifier reproduced 27). That's a real
catch — the pipeline correctly refused to flip the task to `DONE` on a
false claim — but it's a **shallow** catch: a re-run producing a different
number, not a semantic or architectural misunderstanding caught by
independent reasoning. The other standout catch, T9.5's verifier finding a
CI YAML gating bug the task's own `Verify` command didn't even test for,
is a better example of real value — but it's still an artifact of the
verifier reading code carefully, not of it disagreeing with the worker's
reasoning.

**Every deep catch in this project's history — the kind that changes what
gets built, not just whether a number matches — came from a *different*
model auditing the same ground truth, never from the Sonnet-worker /
Sonnet-verifier pair:** fable found the unbuilt story-2 contradiction
surfacing and the unwired story-8 trigger (both real, shipped-gap findings,
both driving new build-plan tasks); Opus-as-advisor caught this session's
own prose overclaims (a stale "no producer wired" comment left in
`metrics.py`, an imprecise citation about which files import `triggers.py`)
and, in the pre-dogfood triage, distinguished entailed-schema-changes from
merely-mentioned ones. The worker/verifier pair, being the same model
family reading the same code, is structurally prone to correlated blind
spots — this is the well-documented "LLM-as-judge" self-verification
weakness (a judge sharing the generator's training and failure modes tends
to agree with it on the things it would have gotten wrong anyway). The
architecture's two real defenses against *deep* error were heterogeneous
model consultation (fable, Opus-advisor) and human checkpoints
(`AskUserQuestion`), not the homogeneous verifier stage — and the
architecture doesn't currently name that distinction anywhere.

**One reassuring nuance:** the original `ORCHESTRATION-INCIDENT` failed
*safe*, not *open*. The fabricated result led to two tasks being
incorrectly marked `BLOCKED`, not incorrectly marked `DONE` — a
conservative failure direction. No fabricated `DONE` has ever reached
`docs/agents/task-status.md` in this project's recorded history. That's
real evidence the discipline holds under the one adversarial condition it's
actually been tested against, even though the underlying guarantee is a
rule rather than a mechanism (finding #1).

---

## What worked well (with evidence, not just design intent)

- **File-disjoint parallelism was genuinely exploited and never caused a
  conflict.** T9.2c/T9.3b (this week) and the original T2.x cohort both
  ran two-plus workers concurrently against disjoint `Files` lists with
  zero merge conflicts across 12 runs — the partitioning rule in
  `fleet-architecture.md` §"File-Disjoint Parallelism" is simple and it
  held.
- **Mechanical, schema-validated logging (`log_run.py`) is a real trust
  boundary, not a prompt.** It refuses malformed or duplicate entries
  without `--force`, and every log inspected for this review was
  internally consistent (prompt/result pairs present, JSON well-formed).
  This is arguably the single most load-bearing piece of the whole
  architecture, and it's a plain Python script with no model in the loop —
  worth explicitly protecting in any redesign.
- **The retraction culture is real, not aspirational.** M10 was declared
  "code-complete" prematurely twice in this project's history (once at
  T10.3's landing, once the day T9.2c/T9.3b were discovered) and both times
  the tracker was corrected in place with an honest timeline rather than
  silently overwritten. That's evidence the discipline the architecture
  is supposed to produce — "never write down a result you haven't
  verified" — actually generalizes past dispatch logging into the whole
  project's status-tracking culture.
- **`AskUserQuestion` checkpoints landed at genuine forks, not everywhere.**
  Across this project's sessions, human checkpoints were used for real
  judgment calls with more than one defensible answer (build-vs-document a
  discovered gap; prepare-vs-launch the overnight runner) rather than
  peppered through routine dispatch — a good instinct that should be kept
  explicit as a design principle, not left implicit.
- **Verify-first, never-weaken-the-test discipline held under real
  pressure.** T10.3's CONTRADICTS_CLAIM was not resolved by loosening the
  assertion or picking a number that "sounded plausible" — the worker was
  resumed, found the real cause (two different batch compositions being
  conflated), and fixed the documentation with freshly-observed evidence.

## Quantitative summary

| Metric | Value | Source |
|---|---|---|
| Logged fleet runs | 12 (10 real cohorts + 2 synthetic red-team tests) | `docs/agents/logs/*/manifest.json` |
| Real task dispatches with recorded verifier verdicts | ~30 (a ~9-task M4 cohort predates consistent per-task verdict logging) | manifests, cross-checked against `task-status.md` |
| Genuine `CONTRADICTS_CLAIM` in production | 1 (T10.3, a count mismatch — shallow catch) | `20260719-152301-M10` |
| Verifier catch beyond the stated Verify command | 1 (T9.5, a CI YAML gating bug) | `20260718-060500-M9` |
| Synthetic adversarial tests, correct outcome | 2 / 2 (one fabrication correctly declined by the worker itself; one weak-Verify-but-hollow-file correctly caught by the verifier's independent content check) | `20260711-172304-verification-test{,-2}` |
| Fabricated `DONE` ever reaching `task-status.md` | 0 (one fabricated result reached a false `BLOCKED`, self-corrected) | `ORCHESTRATION-INCIDENT` |
| Cursor (Tier 3) delegations | 4, all in M3, none since | `task-status.md` T3.3/T3.5/T3.6/T3.7 notes |
| Per-task-pair token cost, early run (4-task parallel cohort) | ~52k tokens/task all-in (8 agents, 91 tool calls, 207,386 tokens, ~192s wall clock) | `20260711-173459-M1-M2-mixed` |
| Per-task-pair token cost, this session's runs | 39k–147k tokens per single agent call (worker or verifier alone), 100–1,180s duration | this session's `Agent` call usage stats |
| Unattended/overnight runs actually executed | 0 (the runner is built and was verified "ready" in a prior session, but has never been launched against a real cohort) | this session's process/pid checks |

**The cost trend is worth flagging on its own:** per-task cost in later runs
ran noticeably higher than the early-run baseline. The likely cause is that
every dispatched agent — orchestrator, worker, verifier — re-reads large,
growing project documents (`task-status.md`, `build-plan.md`,
`spec-questions.md`) from scratch on every call, with no shared memory
across dispatches. As those documents grow (this project's `task-status.md`
is now ~370 lines with dense per-task notes), the fixed reading cost per
dispatch grows with it. This will compound in "next stages of the project"
if the same pattern is reused verbatim against an even larger doc set.

## Comparison to established patterns

Four comparisons, chosen for how directly they bear on the findings above —
not an exhaustive survey.

**LLM-as-judge / self-verification (the central weakness).** A large body
of evaluation research shows a judge model correlates with the generator's
own blind spots when they share a family or training lineage — it tends to
agree with mistakes it would have made itself and disagree on style, not
substance. This project's own evidence supports it directly: the
Sonnet-worker/Sonnet-verifier pair's one real catch was a shallow count
mismatch, while every deep, direction-changing catch came from a
differently-sourced check (fable, Opus-as-advisor, or a human). The
practical lesson translates cleanly: **route the verifier to a model
different from the worker's**, not as a nice-to-have but as the fix for a
specific, evidenced weakness.

**CI/CD independent-rerun (what the verifier is modeled on, and where it's
weaker).** The verifier's "re-run Verify yourself, don't trust the
reported exit code" pattern is exactly the philosophy behind CI systems
that re-run tests in a clean environment rather than trusting a
developer's local "it passed for me." The difference that matters: a CI
runner is hermetic and deterministic — it has zero judgment but also zero
correlated bias with the code author. This project's verifier adds real
value CI can't (reading whether a test is *non-vacuous*, whether a
docstring is honest, whether scope was actually held) precisely because
it has judgment — but that same judgment is what reintroduces the
same-family bias problem above. The honest framing is "a judgment-capable
CI, with CI's determinism traded away in exchange for interpretive
checks" — not "as trustworthy as CI, plus more."

**Multi-agent orchestration frameworks (CrewAI, AutoGen, LangGraph
supervisor patterns) — where this project deliberately diverges.** The
default pattern in these frameworks is an orchestrator/supervisor agent
that freely spawns workers and often synthesizes or trusts their outputs
directly, sometimes with a single "critic" agent for review. This
project's `fleet-orchestrator` is explicitly stripped of the `Agent` tool
after the incident — it can compute a cohort but cannot spawn anything,
specifically to remove the capability that caused the failure. That is a
stricter, more conservative design than the industry-default pattern, and
the incident-driven reasoning for it (documented in
`docs/archived-questions.md`) is worth preserving verbatim in any future
project's onboarding material — it's a real lesson, not a defensive
generality.

**Human-approval gates (Terraform plan/apply, PR review) — mapped onto
`AskUserQuestion`.** The pattern of "an automated system proposes, a human
approves before anything irreversible happens" is standard in
infrastructure-as-code tooling specifically because plan/apply mistakes
are expensive to reverse. This project's use of `AskUserQuestion` at real
forks (build-vs-document a gap, launch-vs-prepare the overnight runner)
maps onto that same discipline and, per the quantitative section above, was
used at genuine decision points rather than diluted through routine
dispatch — this is a strength worth keeping explicit, not diminishing in
future iterations for the sake of "more autonomy."

## Recommendations, in priority order

1. **Make the verifier model different from the worker model.** This is
   the single highest-leverage change the evidence supports. Concretely:
   dispatch `fleet-worker` on Sonnet (as now) but `fleet-verifier` on Opus
   or route it through a fable-style spec-grounded pass for any task with
   real judgment content (not just a mechanical Verify re-run) — reserve
   same-model verification for the cheapest, most mechanical tasks where a
   shallow catch is all that's needed.
2. **Stop describing Path A as "the mechanism, Path B as fallback."**
   Either invest in one real interactive-session test to see if Path A
   is genuinely reachable there, or rewrite `fleet-architecture.md` and
   `docs/agents/runbook.md` to state plainly that the *actual* safety
   mechanism in production is the independent-verifier-plus-mechanical-log
   combination, with Path B's prompt discipline as a necessary but
   structurally weaker backstop. Documentation that overstates a
   guarantee is worse than documentation that's honest about a weaker one,
   because it invites future sessions to under-invest in the parts that
   are actually load-bearing.
3. **Decide Tier 3's fate on purpose, not by drift.** Either finish and
   re-test the Cursor local-Verify-loop contract on a batch of genuinely
   mechanical tasks in the next project phase (golden fixtures, boilerplate
   scaffolding — the shape of task it was actually used for in M3), or
   drop the 3-tier cost framing and its unverified savings figure from
   planning documents. Don't let a stale number stand unchallenged into a
   new project's budget assumptions.
4. **Address the growing-document re-read cost before it compounds
   further.** Consider having the caller (not the dispatched agent) extract
   just the relevant task block, spec citation, and prior-art excerpt
   before dispatch, rather than asking each worker/verifier to
   grep/re-read the full, ever-growing `task-status.md`/`build-plan.md`
   from scratch. `fleet-orchestrator` already does a version of this for
   the cohort description — extend the same discipline to what workers and
   verifiers receive.
5. **Actually run the overnight/unattended path once, at low stakes,
   before depending on it.** It is fully built and was independently
   verified "ready" in a prior session, but has zero real executions.
   An architecture intended for "next stages of this project and other
   projects" should not carry an entirely untested capability forward as
   if it were proven.
6. **Track catch rate and catch depth as an explicit metric going
   forward**, not just as prose buried in manifest notes. A simple
   per-run tally (verdict, and whether the catch was shallow/re-run-based
   or deep/reasoning-based) would make the pattern in finding #3 visible in
   real time instead of requiring a retrospective like this one to surface
   it.

## What to carry forward unchanged

- The never-write-down-a-result-you-haven't-received rule, as a floor —
  it should stay in force even after recommendation #1 lands, since
  heterogeneous verification reduces but doesn't eliminate the narration
  risk.
- Independent file-existence and `git status`/`git diff` cross-checking in
  the verifier role — cheap, mechanical, and it caught the synthetic
  hollow-file case exactly as designed.
- `log_run.py`'s refusal to accept malformed or duplicate entries without
  `--force` — the strongest real trust boundary in the whole system, and
  the cheapest one to keep.
- File-disjoint cohort partitioning — simple, held up under real
  concurrent dispatch, no redesign needed.
- Fable-for-spec-rulings as a distinct, valuable heterogeneous consultation
  channel — this is the one part of the "layered verification" story that
  actually worked as intended, and the reason is exactly the mechanism
  recommendation #1 wants to generalize.
- `AskUserQuestion` reserved for genuine forks rather than routine
  confirmation — keep this discipline explicit in onboarding material for
  future projects, since it's easy to drift toward over-asking or
  under-asking without a stated principle.
