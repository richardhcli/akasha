You own exactly one build-plan task in the akasha repo (/home/richardhcli/projects/personal-projects/akasha): T11.1 — "Dogfood smoke test" (milestone M11). Use pure-Claude workers only — do not invoke scripts/fleet/cursor_bridge.py or any Cursor subprocess under any circumstance; edit/execute directly only.

## Context
T11.1 is pure mechanical plumbing: stand up a small, disposable, never-git-tracked copy of real notes as a genuine sync root on a real running daemon instance. No judgment about note content is required — just plumbing.

## Spec references
§4.11 `GET/POST /sync/roots` (existing endpoint from T4.10 — do NOT add a new CLI verb for this; the literal §4.12 CLI verb list has no sync-root verb, so register the root via direct HTTP, e.g. curl/httpx, exactly as an Obsidian-plugin-less human would today). §4.12 `akasha daemon [--config PATH]` / `akasha token create`.

## Steps
1. Confirm `/data/` is present in the repo's `.gitignore` (it already is, around line 17) — verify only, do not re-add.
2. Create a scratch directory OUTSIDE the repo, e.g. `$HOME/.local/share/akasha-dogfood/vault-1/`.
3. Copy exactly 5 real files verbatim, unmodified, from `data/(10) Concepts/(1) Universal/` into that scratch dir. Pick small, self-contained concept notes; avoid anything under any `Personal Workflow/` subdirectory.
4. Write a scratch `config.toml` next to it with its own `db_path` inside the SAME scratch tree — never the default `~/.config/tm-daemon/` location, so this can never collide with or pollute a future real production DB.
5. Before starting the daemon, check whether port 7433 is already bound (e.g. `lsof -i :7433` or `ss -ltnp | grep 7433`). If it's already in use by another process, pick a different port and set it explicitly in the scratch config.toml rather than colliding with a possibly-real daemon.
6. Start `akasha daemon --config <scratch config.toml>` (backgrounded, e.g. via nohup + `run_in_background`-style shell backgrounding, so it survives your own steps — do not rely on a foreground process you then lose).
7. `akasha token create dogfood-smoke --class human` against that daemon; capture the token (do not print/log the raw token value anywhere that will be committed — keep it out of the run log and out of docs/dogfood/README.md).
8. `POST /v1/sync/roots {"name": "dogfood-smoke", "root_path": "<scratch vault dir>"}` via direct HTTP against the running daemon, using the human-class token (the endpoint is human-only per §4.11).
9. Write `docs/dogfood/README.md` (this is the ONLY file you should create/modify — it is git-tracked) documenting steps 2–8 as copy-pasteable, GENERALIZED commands: path patterns and command shapes only. Do NOT include any literal personal file names, note titles, or note content anywhere in this file — that is the one irreversible failure mode for this task, since the file will be committed and pushed.

## Verify (this task has no pytest command — do not invent one; verify with these live assertions and report the actual output of each)
(a) `GET /v1/sync/roots` against your scratch daemon includes the new root by name.
(b) `GET /v1/sync/status` shows 0 violations for it — this is the EXPECTED PASS result, since the 5 copied files have no `^tm-` anchors yet (0 managed blocks is correct, not a bug).
(c) `git status --porcelain` run from the repo root shows nothing under the scratch path (it's outside the repo, so it shouldn't appear at all) and confirms `data/` itself stays untracked/ignored. Note: this repo's git status has pre-existing unrelated dirty/untracked files from other work — `git status --porcelain` will NOT be empty overall. That's fine and expected; only check that nothing related to the scratch path or `data/` contents appears.

## Definition of done
A real personal-note directory (5 files, copied verbatim, never git-tracked) is a live, watched sync root on a real running daemon instance backed by a throwaway DB; `docs/dogfood/README.md` exists, its commands are copy-pasteable, and it contains zero personal note content or literal personal file names.

## Report back
In your final report, include: the scratch paths you used (config, db, vault dir), the port you used, confirmation the daemon is still running (or how to restart it), the human-class token creation command shape (not the raw token), and the literal output of verify checks (a), (b), (c). State clearly whether you completed all steps or hit a blocker, and if you hit a blocker, describe it precisely rather than guessing past it.