Independently verify build-plan task T11.1 ("Dogfood smoke test") in the akasha repo (/home/richardhcli/projects/personal-projects/akasha). A fleet-worker claims DONE. Re-check its claim against real on-disk/live state — do not trust its report at face value.

## What the worker claims it did
- Copied 5 real files verbatim from `data/(10) Concepts/(1) Universal/` into a scratch dir OUTSIDE the repo: `$HOME/.local/share/akasha-dogfood/vault-1/`.
- Scratch config at `$HOME/.local/share/akasha-dogfood/config.toml`, scratch DB at `$HOME/.local/share/akasha-dogfood/store.db`.
- Started `akasha daemon --config <scratch config>` on port 7433, backgrounded, PID 24677 (may or may not still be alive — restart it yourself from the scratch config if it's dead, don't treat "not currently listening" alone as a contradiction).
- Bootstrapped one throwaway human token directly via `store.create_token` (documented as a spec-question, since `POST /v1/tokens` is `require_human` and a fresh DB has no other way to mint the first token), then created a real `dogfood-smoke` token over HTTP using that bootstrap token, then `POST /v1/sync/roots {"name": "dogfood-smoke", "root_path": "<vault dir>"}`.
- Wrote `docs/dogfood/README.md` (the only file in T11.1's Files list) documenting all this, generalized with no literal personal file names/note content.

## This task has NO pytest Verify command — do not invent one. Verify these three live/on-disk assertions directly:
(a) `GET /v1/sync/roots` against the scratch daemon (restart via `akasha daemon --config $HOME/.local/share/akasha-dogfood/config.toml` if nothing is listening on its configured port) includes a root named `dogfood-smoke`. You'll need a valid bearer token — if the worker's token is gone, mint a fresh one yourself the same way it describes in docs/dogfood/README.md (direct `store.create_token` bootstrap → HTTP-create a human token), scoped to the scratch DB only.
(b) `GET /v1/sync/status` shows 0 violations for that root. **0 violations is the EXPECTED PASS, not a bug** — the 5 copied files have no `^tm-` anchors yet, so 0 managed blocks is correct.
(c) `git status --porcelain` from the repo root shows NOTHING related to the scratch path (`akasha-dogfood`) or `data/` contents. Do NOT treat this repo's pre-existing unrelated dirty/untracked files (there are several, unrelated to this task — e.g. modified docs/agent files from other in-flight work) as a contradiction; only check that nothing scratch-path- or `data/`-related leaked into git tracking. Also independently run `git check-ignore -v data/` and confirm it reports `data/` as ignored.

## Additional checks specific to this task
- Read `docs/dogfood/README.md` yourself in full. Confirm it contains ZERO literal personal note titles, file names, or note content — only generalized path patterns and command shapes. This is the one irreversible failure mode (it's git-tracked and about to be committed/pushed), so scrutinize this carefully — grep it against the actual filenames in `data/(10) Concepts/(1) Universal/` if useful, but do not yourself copy note content into your verdict/report.
- Confirm the DB file (`store.db`) is genuinely under the scratch tree, not the default `~/.config/tm-daemon/` location — i.e. this could never collide with/pollute a real production DB.
- Confirm no new/modified files exist anywhere in the repo except `docs/dogfood/README.md` (T11.1's sole allowed file).
- Note: the worker also flagged a second, out-of-scope spec gap (sync `Watcher` has no production call site, so newly-registered roots aren't auto-scanned) — this has already been logged to `docs/spec-questions.md` by the dispatcher, not the worker. You don't need to verify that finding's technical accuracy in depth, just confirm the README's "Known limitation" section states it honestly rather than overclaiming.

## Report
Return your verdict as one of: CONFIRMED_DONE, CONTRADICTS_CLAIM, or BLOCKED (with reason), plus the literal output of each check (a)/(b)/(c) and the README leak-check, per your standard fleet-verifier report format.