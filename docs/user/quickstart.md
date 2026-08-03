# Quickstart

Run from a source checkout via [`uv`](https://docs.astral.sh/uv/), or on Windows use one of the options below — including a real (unsigned) Windows installer.

**Windows, one command:** `powershell -ExecutionPolicy Bypass -File scripts\windows\setup.ps1` from a checkout does steps 1-3 below for you (uv sync, mint the first token, start the daemon, open the web UI) — see the script's own `-?`/comment header for what it does and does not touch. `scripts\windows\build-exe.ps1` (task T12.5) goes one step further and packages a standalone `akasha.exe` (every CLI verb below plus `akasha.exe tray`, a system-tray-hosted daemon) via PyInstaller, for testing/distributing without a Python/`uv` install on the target machine — vision.md §7.9's "packaged single executable... tray presence". `scripts\windows\akasha.iss` compiles (via Inno Setup) into a zero-elevation installer that places `akasha.exe`, registers Start Menu shortcuts, and offers an autostart option backed by a crash-recovering supervisor loop (see [`ops/autostart.md`](ops/autostart.md)) — it is not code-signed, so Windows SmartScreen may warn on first run. Building either of these yourself from source is covered in [`../dev/windows-packaging.md`](../dev/windows-packaging.md), not here.

## 1. Install

```bash
git clone <this-repo> akasha && cd akasha
uv sync
```

## 2. Mint your first token

`POST /v1/tokens` is human-only and itself requires an existing bearer token, so a brand-new database has no way to authenticate the call that would mint its first token. `akasha init` closes that gap: it talks to the store directly (the same "not a pure HTTP client" exception `daemon` already has, see `cli.md`) rather than a new HTTP endpoint, and mints exactly one `human`-class token.

If you have Git Bash/WSL, `scripts/dogfood/init.sh <name>` does this step plus starting the daemon and registering a sync root, all in one command — see `docs/dogfood/README.md`.

```bash
uv run akasha init --name me
```

Save the printed `<token_id>.<secret>` string — it is shown once and not recoverable. Running `akasha init` again once a token already exists is a clean, documented no-op-with-error (exit code 4) — it will not overwrite or add a second token; use `token create` (below, once the daemon is running) for additional tokens.

```bash
export AKASHA_TOKEN='<paste the bearer value here>'
```

## 3. Start the daemon

```bash
uv run akasha daemon
```

Serves `http://127.0.0.1:7433` (config/db at `~/.config/tm-daemon/` on Linux/macOS, `%APPDATA%\tm-daemon\` on Windows — see [`../mvp-spec.md`](../mvp-spec.md) §3). Leave this running; open a second terminal for the next steps. To keep it running across reboots, see [`ops/autostart.md`](ops/autostart.md).

## 4. Create and read a node

```bash
uv run akasha --token "$AKASHA_TOKEN" new claim "caffeine impairs sleep"
uv run akasha --token "$AKASHA_TOKEN" search caffeine
uv run akasha --token "$AKASHA_TOKEN" get <id-from-above>
```

## 5. Register a vault to sync

```bash
uv run akasha --token "$AKASHA_TOKEN" sync add <path-to-your-notes>
```

`--name` defaults to the path's basename; see [`cli.md`](cli.md) for the full `sync add` reference.

## Next steps

- Full verb/endpoint reference: [`cli.md`](cli.md), [`api.md`](api.md)
- Browser UI at `http://127.0.0.1:7433/`: [`web-ui.md`](web-ui.md)
- Sync an Obsidian vault: [`obsidian.md`](obsidian.md)

**Status of the original onboarding-friction list** (full audit:
[`../onboarding-ux-report.md`](../onboarding-ux-report.md)): minting the first token used to
require a raw Python one-liner — fixed by `akasha init` (T12.1). Registering a sync root used to
require a hand-built `curl`/`Invoke-RestMethod` call — fixed by `akasha sync add` (T12.2, step 5
above). The web UI used to need a devtools console command to authenticate — fixed by the in-page
auth bar plus a `?token=` bootstrap link (T12.3, see [`web-ui.md`](web-ui.md)). A packaged,
autostart-capable installer now exists too (T12.5, see [`ops/autostart.md`](ops/autostart.md)) — it
isn't code-signed, and the onboarding docs still describe the from-source path as the default
rather than the installer-first framing T12.6 calls for.
