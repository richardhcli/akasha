# Spec questions

Log of ambiguities hit while implementing `docs/build-plan.md`. Per build-plan
rule 0.2 / rule 2: never invent schema, endpoints, ID formats, or grammar
beyond `docs/mvp-spec.md`. When something is ambiguous, implement the
narrowest reading, add a `# SPEC-QUESTION:` comment at the site, and log an
entry here so a human can resolve it.

Entry format:

```
## <task ID> — <one-line question>
- **Where:** <file:line>
- **Narrowest reading taken:** <what was implemented in the meantime>
- **Resolution:** <filled in once a human answers; leave "open" until then>
```

This file holds **open** questions only. Resolved entries are moved to
`docs/archived-questions.md` in a batch when the milestone that raised them
closes (context-size optimization — an agent scanning for outstanding
ambiguities shouldn't have to read past closed ones). See that file for the
full resolved history: M1 (T1.3/T1.5/T1.6/T1.7), M3 (T3.1/T3.2/T3.5/T3.6×2),
M4 (13 entries, 2026-07-12), M5 (10 entries: T5.1/T5.5/T5.8-*, 2026-07-13),
M6 (1 entry: T6.5, 2026-07-14), M8 (4 entries: T8.0/T8.1/T8.3/T8.5b,
2026-07-18 via fable rulings), and the **pre-dogfood triage** (11 entries,
2026-07-20/21 via a fable ruling: T7.1, T7.7, T7.3, T7.5×2, T7.6, T9.2×3,
T9.3, T10.2b — see that file's "Pre-dogfood spec-question triage" section
for the full ruling on each), and 2026-07-26 (2 entries: T9.6, T11.1's
sync-roots/watcher half — both closed by the same-day T9.6 live-watcher fix).

## D5 — Spec §4.13 names four views (+Dashboard, M10) but no fifth "settings"/auth affordance. Is adding a minimal shared token-entry UI in scope?
- **Where:** `src/akasha/ui/static/app.js` (`initAuthBar`), all six `src/akasha/ui/templates/*.html`.
- **Narrowest reading taken:** Same precedent T8.3's inline revise-textarea already set for "spec silent on a UI affordance": implement the smallest thing that closes a real, empirically-found gap (no in-page way to ever set the bearer token — see `docs/mvp-debug-plan.md` D5) rather than block on a spec amendment. One always-visible `#tm-auth-bar` bar per view, writing to the same `localStorage.tm_token` key every view already reads — no new persistence mechanism, no new endpoint, no schema change.
- **Resolution:** resolved 2026-07-31 — the user directed improving the UI's general UX as part of this session's dogfood pass; this is read as in-scope authorization for exactly this kind of minimal affordance. Implemented, tested (`tests/integration/test_ui_auth_bar.py` + shell-test updates), full gate green.

**Open questions: 1.** Every entry open as of M10's first code-complete
milestone (2026-07-19) has been triaged, resolved, and archived — see
`docs/archived-questions.md`. New ambiguities encountered during the
one-month dogfood gate or any future work should be logged here per the
entry format above.

## D4 — What origin(s) should the daemon's CORS policy allow for browser-embedded clients (the Obsidian plugin, `app://obsidian.md`)?
- **Where:** `src/akasha/api/app.py` (`create_app`, `_CORS_ALLOWED_ORIGINS`); see `docs/mvp-debug-plan.md`'s D4 entry for the full empirical finding (first live Obsidian-vault dogfood run: every plugin→daemon fetch failed CORS preflight, status bar stuck on `TM: offline`) and fix writeup.
- **Narrowest reading taken:** Spec §4.11/§3 document the API surface and the `127.0.0.1`-only bind but say nothing about CORS/allowed origins, so there is no documented default to fall back to. Allow exactly `app://obsidian.md` (the plugin's fixed Electron origin), not a wildcard `*` — this daemon carries bearer tokens, and wildcard-plus-credentials would be a real weakening of the localhost-only security posture spec §3 establishes, unspec'd by anything in `mvp-spec.md`. `allow_credentials=False` since auth is a bearer token header, never a cookie.
- **Resolution:** resolved 2026-07-31 — the user, acting as the human this entry asked to adjudicate it, explicitly directed implementing this exact narrowest reading. `CORSMiddleware` registered with `allow_origins=["app://obsidian.md"]` only; guarded by a test (`tests/integration/test_cors.py::test_no_wildcard_origin_is_ever_configured`) that fails if this is ever loosened to `*`. Full gate green (see D4 in `docs/mvp-debug-plan.md`).

## D6 — Is a first-run/onboarding UX overhaul (bootstrap token, sync CLI verb, web-UI login link, Windows packaging/tray) in scope now, ahead of `docs/user/quickstart.md`'s "no packaged installer yet (Phase 4+)" framing?
- **Where:** `docs/onboarding-ux-report.md` (new, this entry's full writeup); no `src/` files touched by this entry.
- **Narrowest reading taken:** `docs/vision.md` §7.9 already names a packaged single executable (PyInstaller/Nuitka), tray presence, and Task-Scheduler/NSSM autostart as the intended Windows-first distribution ("a later polish step," not a Phase-5 deferred item) — this is confirmation, not invention, that the class of work is in scope. The open T11.1 entry below independently already flags the bootstrap-token gap as "a real first-run UX gap." Rather than edit `src/` directly, produced a full audit + a proposed M12 task breakdown (`docs/onboarding-ux-report.md`), because this session runs without a Windows host or an established `make check`/`make battery`/Playwright gate (rule 0.9) to close any implementation task against — the project's own history shows several real bugs (CRLF write-back, RSS-sampler ctypes truncation, `winerror` handling) were only catchable on a real Windows run.
- **Resolution:** resolved 2026-08-02 — the user directed this UX audit directly in this session (same authorization pattern as D4/D5). `docs/build-plan.md` M12 (T12.1–T12.6) now carries the proposed tasks; Tier 0 (T12.1–T12.3) is implementable and verifiable in a Linux sandbox via `make check` and was dispatched the same session. Tier 1/2 (T12.4/T12.5, Windows packaging) are folded in as `TODO` gated on a real Windows host per rule 0.9 — not started here. T12.1's own sub-decision (bootstrap-token transport) is resolved in the entry immediately above: CLI verb, not endpoint.

## T12.5 — `MIGRATIONS_DIR`'s repo-root-relative resolution breaks inside a PyInstaller-frozen build; is touching `kernel/store.py` (outside T12.5's original Files list) authorized to fix it?
- **Where:** `src/akasha/kernel/store.py` (`_migrations_dir`/`MIGRATIONS_DIR`).
- **Narrowest reading taken:** T12.5 (packaged Windows executable, `docs/onboarding-ux-report.md`) surfaced this live while building the exe: `MIGRATIONS_DIR = Path(__file__).resolve().parents[3] / "migrations"` walks up to the repo root, which does not exist inside a PyInstaller bundle (`sys._MEIPASS` is the only real filesystem root there), so a frozen `akasha.exe`'s very first `run_migrations` call would silently find zero `.sql` files. Same recurring precedent as T9.2/T9.3b/T10.2/T10.2b's minor Files-list completions: fixed with a `getattr(sys, "frozen", False)` branch that resolves from `sys._MEIPASS` instead, guarded so the non-frozen path (every existing test, the CLI, `uv run akasha daemon`) is byte-for-byte unchanged — confirmed by re-running the full gate after the change (see task-status.md M12/T12.5 for the pass counts). No schema/endpoint/grammar change; `app.py`'s `_UI_DIR` needed no equivalent fix since it was already package-relative, not repo-root-relative.
- **Resolution:** resolved 2026-08-02, same session — self-resolved (mechanical Files-list completion, zero behavior change for any non-frozen caller, full gate re-verified green on the real Windows host before and after).

## T11.1 — How does the very first human token get minted on a fresh DB, given `POST /v1/tokens` is `require_human`?
- **Where:** `src/akasha/api/routes/tokens.py` (`create_token`, `require_human`); `src/akasha/api/deps.py` (`require_human`); `docs/dogfood/README.md` step 6.
- **Narrowest reading taken:** Spec §4.11/§4.12 mark the whole `/tokens` row human-only, and there is no documented bootstrap endpoint or CLI flag for a brand-new DB with zero existing tokens. Treated this as a one-time pre-daemon operator/test-harness bootstrap step (an "embedded caller" per `store.connect`'s own docstring): mint one throwaway bootstrap token via a direct call into `kernel/store.py`'s `create_token` (never a second write path — still routed through `store.py` per rule 0.4, same pattern `tests/battery/soak.py:243` already uses), used solely to authorize the real `dogfood-smoke` token creation over genuine HTTP. This does not block T11.1 but is a real first-run UX gap for anyone standing up a fresh daemon without the test harness's direct-DB shortcut.
- **Resolution:** resolved 2026-08-02 — the user, asked directly (`AskUserQuestion`, transport choice: CLI verb vs. `POST /v1/bootstrap` endpoint), ruled **(a)**: a new `akasha init` CLI verb that talks to `kernel/store.py` directly, same "embedded caller" precedent this entry already used, rather than a new authless HTTP endpoint. Tracked as `docs/build-plan.md` T12.1 (M12).

