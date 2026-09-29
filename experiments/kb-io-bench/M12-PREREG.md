# M12 pre-registration: does the akasha knowledge base keep facts true better than a plain vault?

Written 2026-09-28 19:47 EDT, before any M12 data exists. The only earlier M12 runs are the 2-task
`apsmoke` validity check of the Ap condition; it is excluded from all results.

The sha256 of this file is recorded in STATUS.md (M12) before the job starts. Any later change to
this file is an amendment: log it in STATUS with the reason, and report it next to the results.

## Claim under test

Hold the model, the prompts and the agent's tools fixed. Change only the knowledge base:

- the **akasha vault**: the same markdown, with anchored blocks and pasted copies transcluded;
  the scratch akasha daemon keeps the copies in sync;
- the **plain vault**: the same markdown, with pasted copies and no daemon. This is the
  traditional wiki/markdown knowledge base.

Is knowledge IO (update, then read back) more truthful on the akasha vault?

The akasha-aware harness (condition A) is **not** part of the claim, because we built and tuned
it. Both harnesses in the claim know nothing about akasha:

| harness | akasha vault + daemon | plain vault |
|---|---|---|
| basic-memory MCP tools (SOTA, as shipped) | B | C |
| plain file tools (`list_dir`, `grep`, `read_file`, `write_file`, `edit_file`) | Ap | Cp |

The model is gpt-oss:120b (Purdue GenAI) at temperature 0. The prompts are unchanged from v1. The
UPDATE prompt says the fact "may appear in several notes", so the plain vault is told what to look
for. The step caps, the 2,000-token result cap and the loop are all as in v1.

## Tasks (`data/.../tasks/S-kb.json`, sha256 `a2826795…3b88`)

Tier S: 122 files, about 232k words.

- **35 multi-copy UPDATEs.** A lead definition pasted into 2–4 notes has one phrase changed.
  - 15 are v1's tasks, unchanged (`seen_in_v1`).
  - 20 are new, drafted with the same checks.
  - 5 of the 40 candidates were rejected before any run, because no valid rewrite came out in 3
    drafting attempts.
- **20 single-copy UPDATEs.** A planted fact in one Wikipedia page gets a new value of the same
  format.
- **Verified in code:** every old sentence occurs in exactly its scored copies in both vaults,
  and no new value occurs anywhere.
- **Hand-audited:** 10 tasks (6 new multi-copy, 4 single-copy), all valid.

READ (60) and WRITE (15) are v1's tasks. For Ap they are copied verbatim from `S.json` into
`S-kbrw.json` (sha256 `901f1d6e…e0de`). For B, C and Cp, the v1 tier-S records are reused.

## Endpoints

- **Primary: KB consistency after a multi-copy UPDATE.**
  - Per task, a binary check made on the vault files after the writer finishes and the vault
    settles: every scored copy contains the new sentence and none contains the old one
    (`update_score.copies_updated_rate == 1`).
  - It is checked by code. No judge and no reader are involved.
  - A task whose writer FAILED (API error) counts as not consistent.
- **Secondary: stale reads.**
  - A fresh reader answers both follow-ups: q1 (generic) and q2 (names one pasted copy).
  - "Stale" means the answer contains the old phrase in context (`update_stale`).
  - Reported as the stale-read rate per condition, over q1 and q2 separately and pooled.

## Hypotheses and tests

**H1 (primary).** In each harness stratum, the akasha vault leaves the KB consistent in more
multi-copy UPDATE tasks than the plain vault.
- H1a: B > C. H1b: Ap > Cp.
- **Test:** one-sided exact sign test on the discordant tasks (McNemar), over the 35 paired tasks.
- **Correction:** Holm across H1a and H1b, with family-wise α = 0.05.
- **Effect size:** the paired difference in the consistent rate, with a 95% bootstrap CI over
  tasks (2,000 resamples, seed 0).

**H1-unseen (sensitivity; required for the "proven" wording).** The same tests on the 20 tasks not
seen in v1 must each give p < 0.05, one-sided, uncorrected. The 15 v1 tasks generated the
hypothesis, and Cp's writer requests on them may replay from cache.

**H2 (secondary).** The stale-read rate is lower on the akasha vault, in each stratum.
- Same test, on per-task stale indicators: a task counts as stale if any of its follow-ups is
  stale.
- Reported with Holm across the two strata. It is not required for the claim.

**Specificity control S.** With single-copy UPDATEs there is nothing to keep in sync, so the
vaults should not differ.
- **Prediction:** the lower bound of the 95% bootstrap CI of (akasha − plain), for the rate of
  correct single-copy updates, is > −0.15 in each stratum.
- "Correct" means consistent (the check above) and the follow-up correct.
- If the control shows a large akasha advantage, that suggests something other than copy sync
  (for example, the task being easier to edit), and it is reported as such.

**Non-inferiority N (secondary; no penalty elsewhere).** For akasha minus plain, in each stratum,
the task-clustered 95% bootstrap CI lower bound must be > −0.10 for:
- N-read: READ correct (60 questions; `analyze.correct`);
- N-write: WRITE follow-up correct (55 questions, 15 tasks). With 15 tasks this is expected to be
  inconclusive, and will be reported as inconclusive if the CI crosses −0.10.

**Cost (descriptive).** Tokens and tool calls per UPDATE task.

## Allowed wording, by outcome

- **H1a, H1b and H1-unseen all reject:** "On tier S, for facts held in several notes, the akasha
  knowledge base kept the knowledge base consistent more often than a plain markdown vault,
  replicated with two harnesses that know nothing about akasha (effect sizes …)." Add the result
  of S and N as observed.
  - The scope sentence is mandatory: it concerns duplicated facts. How much it matters depends on
    how often facts are duplicated in a real vault.
- **Only one stratum rejects:** "shown with <harness>, not replicated with <harness>".
- **Neither rejects:** "no KB effect shown".
- **Never claim:** that akasha is better for knowledge IO in general, anything at scale beyond
  122 files, or anything about other models.

## Run order and conduct

Job `kb`, via the launcher, runs sequentially (one daemon, fixed work paths):
1. UPDATE Cp → Ap
2. UPDATE B → C
3. READ + WRITE Ap and Cp (Cp is rerun because of Amendment 1)
4. UPDATE A, descriptive only and outside the claim.

Interim looks check validity only: 0 FAILED, the gateway gets `gpt-oss:120b`, and records are
complete. No decision about stopping or changing the run depends on an interim p-value.

- **Outage rule (v1):** an API-outage FAILED record is moved to `outage/` and rerun once.
- **Other FAILED records** count as failures, as above.

Analysis: `uv run python -m kbio.m12` writes `results/m12-S.md`. The script is written after this
file and must implement exactly the tests above.

## Amendment 1 (2026-09-28 19:54 EDT, after 4 Cp records; no hypothesis test was looked at)

**What went wrong.** The startup validity check found Cp mc-5177 had all 4 copies stale. The
writer typed `Universal/Truth.md` for `(1) Universal/Truth.md`, which is the known v1 threat of the
model dropping the "(1) " prefix. Every edit then failed with "file not found".
- On the plain vault this leaves a stale copy.
- On the akasha vault, editing any other copy syncs it.
- So 9 of the 35 multi-copy tasks, those with a copy under `(1) Universal/`, could credit akasha
  for a path-typing quirk instead of for truth maintenance. None of the single-copy controls has
  such a copy, so the control would not have caught it.

**Changes.**
1. **The file tools resolve a missing path component** to the one sibling whose name matches once
   a leading "(n) " is stripped (`FilesHarness._tolerant`, with a test). This is identical for Ap
   and Cp, and it makes the plain-vault baseline stronger. basic-memory (B/C) is used as shipped
   and is unchanged.
2. **The 4 Cp records made before the change** are moved to `data/.../trash/m12-pre-amendment1/`
   and rerun.
3. **Cp READ and WRITE are rerun** under `S-kbrw` with the new tools, so the N comparison uses the
   same file tools on both sides. B and C READ/WRITE still reuse the v1 records.
4. **New sensitivity analysis, H1-noprefix.** H1a and H1b are repeated on the 26 multi-copy tasks
   with no copy under a "(n) " folder. Each must give p < 0.05, one-sided, uncorrected, for the
   "proven" wording, in addition to H1-unseen.
