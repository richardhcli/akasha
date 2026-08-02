# Onboarding / UX audit — is a packaged Windows app feasible, and what should land first

**Date:** 2026-08-02. **Origin:** user-directed audit (Cowork session), same authorization
pattern as `docs/spec-questions.md` D4/D5 — the user asked directly "how do we make the
UI/UX as easy as possible to use, does a Windows executable work?" This is that answer,
plus a task breakdown sized for a CLI-capable agent to pick up and verify per
`docs/build-plan.md` rule 0.7 (`make check`/`make battery`).

**What this session changed directly:** documentation only — this file and one
`docs/spec-questions.md` entry (D6). No `src/` files were touched. See "Why nothing in
`src/` changed here" below.

---

## 1. Where the product actually is today

This is **not** a bare-CLI MVP. M0–M10 are DONE/code-complete (`docs/agents/task-status.md`):
a resident FastAPI daemon, a full HTTP API, a `typer` CLI (`new/get/set/rm/search/review/
token/export/daemon`), a daemon-served htmx web UI (dashboard/node/review/search/sync
views), and a TypeScript Obsidian plugin with bijective vault sync. M11 (real-vault dogfood)
is in progress; its one remaining step, T11.2, is deliberately human-only (deciding which
real notes become tracked claims — `vision.md`'s human-in-the-loop invariant) and out of
scope for this audit.

The actual complaint is accurate anyway: **first-run and every-day-startup UX is genuinely
rough**, not because features are missing but because every step that exists still requires
knowing internal implementation detail. Concretely, today:

1. **No bootstrap path for the first token.** `POST /v1/tokens` is `require_human`, so a
   brand-new database has a chicken-and-egg problem. The only documented fix
   (`docs/user/quickstart.md` §2, `docs/user/dogfood-windows.md` §3) is a multi-line
   `uv run python -c "..."` heredoc that imports `akasha.kernel.store` directly and calls
   `create_token` — i.e. the *documented* onboarding path requires reading and copying
   internal module code. This is a logged, open gap: `docs/spec-questions.md` T11.1.
2. **No CLI verb to register a vault.** `POST /v1/sync/roots` has never had a CLI verb
   (confirmed in `build-plan.md` T11.1's own Steps, by grep against `cli/main.py`) — the
   documented path is a raw `curl`/`Invoke-RestMethod` call with a hand-built JSON body.
3. **The web UI has no login screen.** `docs/user/web-ui.md` step 3, verbatim: "open the
   browser console and run `localStorage.setItem('tm_token', '<your token>')`, then
   reload." A minimal always-visible auth bar was added since (D5, resolved 2026-07-31),
   which helps, but there is still no link/flow that carries a freshly-minted token from
   "I just ran `token create`" into the browser without hand-typing it somewhere.
4. **No installer at all.** Every guide (`quickstart.md`, `dogfood-windows.md`) starts from
   `git clone` + `uv sync`. The user needs Python 3.12+, `uv`, and (for the Obsidian plugin)
   Node 20+ already on their machine, and needs to know what a virtualenv is.
5. **No autostart by default**, and what exists is manual and has a documented sharp edge:
   Task Scheduler's own `RestartOnFailure` was empirically verified (`docs/dogfood/
   windows-service.md`) **not to reliably restart a killed daemon** on this project's own
   Windows 11 Enterprise Evaluation host — the real fix is a generated `.bat` supervisor
   loop, which is currently only wired up inside the *dogfood-scratch* scripts
   (`scripts/windows-service/init.ps1`), not offered as something a real end user runs
   against their production config.
6. **No visible process at all.** The daemon is a bare foreground console window (or an
   invisible service). There is no tray icon, no "is it running" affordance, no click-to-open
   for the web UI.
7. **The Obsidian plugin isn't installable normally.** No Community Plugins listing; the
   documented path is `npm ci && npm run build` plus manually creating a filesystem
   junction into the vault's plugin folder (`docs/user/dogfood-windows.md` §6–7).

None of this is a bug in the sense the codebase's own tests would catch — it's a **coverage
gap between "the API/CLI/UI exist and work" and "a non-technical human can reach them
without reading source."**

---

## 2. Does a Windows executable make sense?

**Yes — and it's already the plan, not a new idea.** `docs/vision.md` §7.9 says this
explicitly (this audit didn't invent it):

> "Platform order — Windows first. Distribution: `pipx` / `uv tool install` for the
> technical early audience (clean, antivirus-friendly), **packaged single executable
> (PyInstaller/Nuitka) as a later polish step** with its known Windows costs (AV false
> positives, bundle size) accepted deliberately... **tray presence; autostart via Task
> Scheduler or an NSSM-wrapped Windows service.**"

So the question isn't "is this in scope" — vision.md already named PyInstaller/Nuitka, a
tray icon, and Task-Scheduler/NSSM autostart as the intended MVP-adjacent Windows
distribution. The open question is only **sequencing**: `docs/user/quickstart.md` currently
frames "no packaged installer" as a Phase-4+ thing, which is a stricter reading than §7.9's
"later polish step" — this audit treats that gap as worth closing now given the user's
direct request, not as a reason to block.

**Why it's technically straightforward for this codebase specifically:**

- The whole product is already one Python console-script entry point (`akasha`, from
  `cli/main.py`) that either runs the daemon in-process (`akasha daemon`) or speaks HTTP to
  it (every other verb). PyInstaller bundling a single `typer` app with FastAPI/uvicorn/
  SQLite (stdlib `sqlite3`, no native extension) is a well-trodden path — no exotic
  dependencies, no GUI toolkit to bundle for the daemon itself.
- The web UI needs **zero build step** already (`docs/user/web-ui.md`: "static HTML shells +
  vanilla JS calling `/v1`") — it's served by the same daemon process, so the packaged exe's
  "UI" is just "open a browser tab," no Electron/Tauri (vision.md is explicit that this was
  deliberately avoided).
- A tray icon is an *additive* small module (e.g. `pystray` + `Pillow` for the icon), not a
  rearchitecture — it would wrap the existing `daemon.serve()` call, not replace it.
- The Obsidian plugin is a separate TypeScript artifact; packaging the daemon doesn't touch
  it directly, though the installer is the natural place to also drop `main.js`/`manifest.json`
  into a vault's plugin folder if the user picks one during setup.
- **Known, already-accepted costs** (vision.md says so, not new information): PyInstaller/
  Nuitka executables trip AV heuristics more than a `pip`/`uv`-installed script, and bundle
  size is larger than a venv. §7.12 also already frames PyInstaller as a *stopgap*: the
  eventual Rust migration produces a true static single binary that "permanently solves
  packaging" — so packaging work now should be scoped as a bridge, not a rewrite, and kept
  cheap to throw away.

**Recommendation: yes, build it**, but sequence the cheap, code-adjacent wins first (below) —
several of the worst friction points (bootstrap token, sync CLI verb, web-UI login link) are
one-endpoint/one-verb changes that help *today*, before any packaging work lands, and a
packaged exe without them just moves the same friction one layer down (a shiny installer that
still ends with "now go run a Python heredoc to mint a token").

---

## 3. Recommended sequencing

**Tier 0 — cheap, no packaging, biggest per-hour impact:**

1. A real bootstrap path for the first token (endpoint or CLI verb — needs one narrow design
   decision, see T12.1 below).
2. `akasha sync add <path> [--name NAME]` CLI verb wrapping the existing `POST /v1/sync/roots`
   (endpoint already exists; this is pure CLI-client work, same shape as every other verb in
   `cli/main.py`).
3. A one-time web-UI bootstrap link (e.g. `/?token=...` or `/setup?token=...`) that seeds
   `localStorage.tm_token` and redirects, so "copy-paste into devtools console" stops being
   the documented flow.

**Tier 1 — bridge, before packaging lands:**

4. A single "one-command Windows setup" script that chains 1–3 plus `uv sync` and daemon
   start, modeled on the *safety conventions* `scripts/dogfood/*.sh`/`scripts/windows-service/
   *.ps1` already established (refuse-if-exists checks, no silent overwrite of a real config),
   but pointed at the user's **real** config location instead of a disposable scratch tree.

**Tier 2 — the packaged executable:**

5. PyInstaller build of the `akasha` entry point → single `akasha.exe`; `pystray`-based tray
   icon (start/stop, open web UI, open logs, quit) wrapping `daemon.serve()`; an installer
   (Inno Setup is the standard lightweight choice for this) that unpacks the exe, runs the
   Tier-0/1 first-run flow once, and registers autostart using the **already-proven**
   supervisor-loop pattern from `scripts/windows-service/lib.ps1`'s `New-DaemonWrapperScript`
   (plain Task Scheduler `RestartOnFailure` is empirically unreliable on this project's own
   Windows host — don't re-litigate that, reuse the fix that's already validated).

**Tier 3 — not now:** the Rust migration (§7.12) is the durable fix (one static binary, no AV
noise, no PyInstaller bundle-size cost) but it's gated on Phase-4 market-traction triggers per
the vision doc — Tier 2 is explicitly a stopgap to bridge to it, not a substitute for it.

---

## 4. Why nothing in `src/` changed in this Cowork session

Build-plan rule 0.9: *"A task is not `DONE` until its `Verify` command passes locally... do
not weaken the test or move on."* Rule 0.7 requires `make check` (ruff + pyright --strict +
the full unit/property/integration suite, including headless-Chromium Playwright tests) and,
for M5+ work, `make battery` — and the project's own history (`docs/agents/task-status.md`'s
Windows-verification callouts) shows several real bugs were **only** catchable by running the
suite on an actual Windows host (CRLF write-back corruption, a Windows RSS-sampler ctypes
truncation, a `winerror`-vs-`PermissionError` mismatch). This session runs in a Linux sandbox
with no Windows host and no established `make check`/`make battery` gate — any code edit here
would be unverifiable by this session's own standard, which is exactly what rule 0.9 exists to
prevent. So this audit is deliberately scoped to what's honest to ship from here: analysis,
and a task breakdown a CLI-capable agent (ideally running the gate on a real Windows host, per
the project's own established practice) can execute and verify properly.

`docs/spec-questions.md` entry D6 logs this scope decision using the same "user directed it,
treat as authorization" pattern already established for D4/D5.

---

## 5. Proposed tasks for a CLI-capable agent (ready to fold into `docs/build-plan.md` as M12)

These follow the existing build-plan task template (Goal/Depends on/Files/Spec/Steps/Verify/
DoD) so they can be pasted in with minimal editing. Suggested milestone framing, mirroring
M11's own header: **"M12 — Onboarding & Windows packaging UX (Depends on: M10) — post-MVP
addendum, added at user request, not derived from `mvp-spec.md`'s milestone list."** Runs
independently of M11 (file-disjoint; T11.2's human-only leg is untouched by any of this).

### T12.1 — Bootstrap path for the first human token on a fresh DB
- **Goal:** close `docs/spec-questions.md` T11.1. One narrow design decision needed first
  (log it as its own dated sub-entry, same as T10.2's transport ruling): either (a) a new
  `akasha init` CLI verb that talks to `store.py` directly and only works against a DB with
  zero existing tokens (mirrors `daemon`'s own "not a pure HTTP client" exception, already
  precedented in `cli/main.py`'s module docstring), or (b) a `POST /v1/bootstrap` endpoint
  that 403s once any token exists. Recommend (a) — no new authless HTTP surface to defend.
- **Depends on:** M10 (milestone gate).
- **Files:** `src/akasha/cli/main.py`, `tests/integration/test_cli_*.py` (new or extended),
  `docs/user/quickstart.md`, `docs/user/dogfood-windows.md`, `docs/spec-questions.md` (T11.1
  resolution).
- **Verify:** new test proving `akasha init` fails cleanly against a DB with an existing
  token (exit 4, not a crash) and succeeds against a fresh one; full `make check`.

### T12.2 — `akasha sync add <path>` CLI verb
- **Goal:** wrap the existing `POST /v1/sync/roots` the same way every other verb wraps its
  endpoint — pure HTTP client, no new server-side logic.
- **Depends on:** M10.
- **Files:** `src/akasha/cli/main.py`, `tests/integration/test_cli_*.py`, `docs/user/cli.md`.
- **Verify:** CLI integration test round-tripping a real registration; `make check`.

### T12.3 — Web-UI one-time bootstrap link
- **Goal:** extend D5's auth-bar work so a freshly minted token can reach the browser without
  devtools. Narrowest version: a `?token=` query param, handled client-side only (seed
  `localStorage`, strip the param via `history.replaceState`, never logged server-side).
- **Depends on:** D5 (already DONE).
- **Files:** `src/akasha/ui/static/app.js`, `tests/integration/test_ui_auth_bar.py`,
  `docs/user/web-ui.md`.
- **Verify:** Playwright test confirming the token lands in `localStorage` and the param is
  stripped from the visible URL; `make check`.

### T12.4 — One-command Windows setup script (bridge, pre-packaging)
- **Goal:** a `scripts/windows/setup.ps1` chaining `uv sync` → T12.1's `akasha init` → config
  write → daemon start → prints the T12.3 bootstrap URL — targeting the user's **real**
  default config path (`%APPDATA%\tm-daemon`), not a scratch tree. Reuse the safety
  conventions already proven in `scripts/dogfood/lib.sh`/`scripts/windows-service/lib.ps1`
  (refuse to silently overwrite existing config; explicit confirmation before touching a
  real, non-scratch path).
- **Depends on:** T12.1, T12.2, T12.3.
- **Files:** `scripts/windows/setup.ps1` (new), `docs/user/quickstart.md`.
- **Verify:** manual/live leg (same framing as T11.1/T11.4) run on a real Windows host —
  fresh machine, script produces a running daemon + working browser session in one pass.

### T12.5 — PyInstaller executable + tray icon + installer
- **Goal:** package the `akasha` entry point as `akasha.exe`; add an opt-in tray module
  (`pystray`) wrapping `daemon.serve()` with start/stop/open-UI/open-logs/quit; produce an
  Inno Setup installer that runs T12.4's flow once and registers autostart via the
  **already-validated** supervisor-loop pattern (`scripts/windows-service/lib.ps1`'s
  `New-DaemonWrapperScript` — do not re-derive; Task Scheduler's native `RestartOnFailure` is
  empirically unreliable, per `docs/dogfood/windows-service.md`).
- **Depends on:** T12.4.
- **Files:** `pyproject.toml` (PyInstaller as a packaging-only dev dep), `scripts/windows/
  build-exe.ps1` (new), `scripts/windows/akasha.iss` (new, Inno Setup script), a new
  `src/akasha/tray.py`, `docs/user/ops/autostart.md`.
- **Verify:** manual/live leg on a real Windows host — build the exe, run the installer on a
  clean VM/user profile, confirm autostart survives logoff/logon and a `taskkill /F` (reuse
  the exact live kill-and-poll test `docs/dogfood/windows-service.md` already describes).

### T12.6 — Rewrite onboarding docs around the new flow
- **Goal:** once T12.1–T12.5 land, `docs/user/quickstart.md`, `web-ui.md`, `dogfood-windows.md`,
  and `ops/autostart.md` should describe the installer-first path as the default, with the
  current from-source path demoted to a "developer setup" appendix.
- **Depends on:** T12.1–T12.5.
- **Files:** the four docs above.
- **Verify:** doc-only; DoD is a fresh-eyes read-through with no step requiring reading
  source code.

---

## 6. Closing note

Everything above is additive: it doesn't touch the kernel, contract, sync, or TMS layers, and
it doesn't reopen anything M0–M10 already closed. It also doesn't block or get blocked by
M11's remaining human-only leg (T11.2). The one real judgment call left for a human is T12.1's
(a)-vs-(b) transport decision — flag it and get a one-line ruling before implementing, same
pattern as T10.2's CLI-export transport question.
