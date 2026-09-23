# Build Utilities & Scripts

Development-time scripts and utilities. Not part of the akasha product itself.

## Directory Structure

- **`dev/`** — Local development helpers.
  - `seed_and_run.py` — seeds a throwaway, fully-isolated akasha graph (rich
    node/facet/review state, incl. an open stale badge) and serves the web UI
    against it for manual browser testing. `uv run python scripts/dev/seed_and_run.py`
    (or `make dev-ui`); `--seed-only` seeds and exits without serving (used by
    the `make dev-ui` verify step). Never touches the real default DB.
  - `transclusion_demo.py` — builds a mirror (transclusion) playground from scratch:
    a real, isolated daemon (own config/DB/lock/port/token), a vault with `A.md`/`B.md`/`C.md`
    sharing `^tm-id` anchors, a `./ak` CLI wrapper, a cheat-sheet and a live change monitor.
    `make demo-transclusion` to play; `make demo-transclusion-check` (`--selftest`) proves
    edit-in-A→B/C, edit-in-C→A/B, checkbox, hub-side `akasha set`, delete-one-copy and
    detach-with-`^tm-new` with measured latency, then tears down (exit 0/1). Needs the M19
    mirror support in `sync/reconcile.py`. Never touches the real default DB.

- **`dogfood/`** (Bash / Git Bash / MSYS — needs `cygpath`) — stand up a
  disposable dogfood vault + daemon instance: scratch DB, bootstrap token, a
  real human token minted over HTTP, a registered sync root, and an initial
  rescan, all in one command.
  - `init.sh <name> [port]` creates it under `$HOME/.local/share/akasha-dogfood/<name>/`;
    `deinit.sh <name>` stops the daemon and keeps the data; `destroy.sh <name>`
    stops it and deletes the scratch tree (refuses to run outside the scratch root).
  - See `docs/dogfood/README.md` for the full walkthrough and what each step does.

- **`fleet/`** — Agent fleet orchestration utilities for parallel multi-agent builds.
  - See `fleet/README.md` for usage and architecture overview.
  - Key file: `cursor_bridge.py` (Cursor Agent executor subprocess).

- **`windows-service/`** (PowerShell) — register the daemon as a real Windows
  Task Scheduler task, for a human dogfooding day-to-day with a persistent,
  autostarting, crash-recovering daemon (as opposed to `dogfood/`'s
  foreground/backgrounded instance). Also the vehicle for verifying the
  vision/mvp-spec "real-OS residency" requirement (T4.9).
  - `init.ps1` registers + starts the scheduled task; `deinit.ps1` stops it
    (task stays registered); `destroy.ps1` stops it, unregisters the task,
    and deletes the scratch tree. Requests UAC elevation only if a specific
    operation is denied, never up front.
  - See `docs/dogfood/windows-service.md` for the full explanation
    (supervisor-wrapper crash recovery, privilege model, safety guards) —
    read it before running these scripts if you haven't used them before.

## Onboarding in a new environment

For a human setting up akasha from a fresh checkout to actually use it (not
just run the test suite), start at [`../docs/user/README.md`](../docs/user/README.md) —
it covers the manual quickstart and links to `dogfood/` and `windows-service/`
above as the scripted fast paths.

## Future Sections

Additional utility directories may be added here as the project grows (CI helpers, migration tools, etc.).
