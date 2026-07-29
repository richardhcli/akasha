# Dogfood on Windows (no permanent install)

How to run this **in-progress** checkout as a temporary local stack on
Windows: daemon + CLI via `uv run`, Obsidian plugin via a vault junction.
Nothing is installed system-wide; teardown is deleting a folder (and
optionally removing the plugin junction).

**Current readiness (as of M6 code-complete):** daemon, HTTP API, CLI,
file sync, and the Obsidian thin client are usable for capture/sync
dogfood. The TMS review loop (M7) and web UI (M8) are **not** ready —
`akasha review` will fail gracefully until then.

For a lasting “start at logon” setup, use [`docs/autostart-windows.md`](../autostart-windows.md)
*after* you decide to keep a real install. This guide deliberately avoids
that.

---

## 0. What you need

| Tool | Why | Check |
|---|---|---|
| **Python 3.12+** | Runtime | `py -3.12 --version` |
| **[uv](https://docs.astral.sh/uv/)** | Env + `uv run` entry points | `uv --version` |
| **Node.js 20+** | Build the Obsidian plugin | `node --version` |
| **Obsidian desktop** | Spoke #1 | App installed |
| **A demo vault** | Prefer a path **not** under OneDrive/Dropbox (cloud paths force a conservative watcher profile) | e.g. `C:\Users\<you>\Documents\Obsidian\AkashaDogfood` |

Clone or open this repo. All commands below assume PowerShell with
cwd = the repo root.

---

## 1. One-time: sync the Python env (repo-local only)

```powershell
uv sync
```

This creates `.venv/` under the repo and installs the `akasha` console
script into that venv. You never need `pip install -e .` globally, and you
do not need Task Scheduler / NSSM for dogfood.

Sanity:

```powershell
uv run akasha --help
```

---

## 2. Isolated dogfood home (so APPDATA stays clean)

Default paths are `%APPDATA%\tm-daemon\` (neutral name — no product branding
on disk). For temporary dogfood, put **config + DB + lock + log** in a
throwaway directory.

```powershell
$Dogfood = Join-Path $env:TEMP "tm-daemon-dogfood"
New-Item -ItemType Directory -Force -Path $Dogfood | Out-Null

# Write UTF-8 *without* BOM — PowerShell 5.1's Set-Content -Encoding utf8
# adds a BOM that tomllib rejects.
$db = (Join-Path $Dogfood "store.db") -replace '\\', '/'
$configPath = Join-Path $Dogfood "config.toml"
[System.IO.File]::WriteAllText($configPath, @"
bind = "127.0.0.1"
port = 7433
db_path = "$db"
"@)
```

`db_path` must be set in the TOML: if the config file is missing, the
daemon still resolves the lock/log next to `--config`, but the SQLite file
falls back to `%APPDATA%\tm-daemon\store.db`.

Optional: keep `$Dogfood` somewhere durable under your user profile
(e.g. `%LOCALAPPDATA%\tm-daemon-dogfood`) if you want the store to survive
Temp cleanup but still stay out of Roaming.

---

## 3. Bootstrap the first human token

Every mutating API call requires a Bearer token. Token minting itself is
human-only and authenticated — so a **fresh** store has a chicken-and-egg
gap. Mint the first human token against the DB **before** (or with the
daemon stopped), using the store API:

```powershell
uv run python -c @"
from pathlib import Path
from akasha.api import auth
from akasha.config import load_config
from akasha.kernel import store

cfg = load_config(r'$Dogfood\config.toml')
db = Path(cfg.db_path)
db.parent.mkdir(parents=True, exist_ok=True)
conn = store.connect(db)
store.run_migrations(conn)
secret = auth.mint_secret()
tok = store.create_token(conn, 'dogfood', 'human', auth.hash_secret(secret))
print(auth.format_bearer_token(tok['id'], secret))
conn.close()
"@
```

Copy the printed `id.secret` value. Save it somewhere local (password
manager / env var). It is shown **once**.

```powershell
$env:AKASHA_TOKEN = "<paste bearer here>"   # current PowerShell session only
```

Later tokens (extra human or agent) go through the CLI once the daemon is
up:

```powershell
uv run akasha --token $env:AKASHA_TOKEN token create my-agent --class agent
```

---

## 4. Start the daemon (foreground)

Leave this terminal open:

```powershell
uv run akasha daemon --config "$Dogfood\config.toml"
```

Equivalent Makefile target (same idea, **default** config location —
skip if you are using the isolated `$Dogfood` layout above):

```powershell
# only if you intentionally want %APPDATA%\tm-daemon
uv run python -m akasha.cli.main daemon
```

Verify in a **second** terminal:

```powershell
Invoke-RestMethod http://127.0.0.1:7433/health
# expect: status=ok, version, contract_version
```

A second `uv run akasha daemon --config ...` should exit with code **4**
and a one-line lock message (`tm-daemon.lock` under `$Dogfood`) — that is
correct, not a crash.

Stop: `Ctrl+C` in the daemon terminal.

---

## 5. Register a demo vault as a sync root

There is no `akasha sync` CLI verb yet. Register via HTTP (human token
required):

```powershell
$Vault = "C:\Users\<you>\Documents\Obsidian\AkashaDogfood"  # adjust
$headers = @{ Authorization = "Bearer $env:AKASHA_TOKEN" }
$body = @{ name = "dogfood"; root_path = $Vault } | ConvertTo-Json

Invoke-RestMethod -Method Post -Uri http://127.0.0.1:7433/v1/sync/roots `
  -Headers $headers -ContentType "application/json" -Body $body
```

Confirm:

```powershell
Invoke-RestMethod http://127.0.0.1:7433/v1/sync/status -Headers $headers
```

Create at least one **managed** note under `$Vault` (Obsidian or any
editor). Front-matter `tm: 1` opts the file into the contract:

```markdown
---
tm: 1
---
First captured claim for dogfood
```

Save, wait a couple of seconds (500 ms debounce + reconcile), then either:

- append ` ^tm-new` at end of a line and save (daemon mints `^tm-<id8>`), or
- use the Obsidian command in §7.

CLI smoke (daemon must be running):

```powershell
uv run akasha --token $env:AKASHA_TOKEN new claim "CLI-created claim"
uv run akasha --token $env:AKASHA_TOKEN search claim
```

---

## 6. Build the Obsidian plugin (no Community Plugins publish)

```powershell
cd plugin-obsidian
npm ci
npm run build
cd ..
# expect: plugin-obsidian\main.js exists
```

Rebuild after any plugin edit; Obsidian may need a reload
(`Ctrl+R` in the vault window, or disable/enable the plugin).

---

## 7. Load the plugin without installing from the store

Point the vault’s plugin folder at the **repo** build output via a
Windows **junction** (survives rebuilds; no copy step):

```powershell
$Vault = "C:\Users\<you>\Documents\Obsidian\AkashaDogfood"  # same as §5
$PluginDir = Join-Path $Vault ".obsidian\plugins"
New-Item -ItemType Directory -Force -Path $PluginDir | Out-Null

$Link = Join-Path $PluginDir "tm-hub"   # must match manifest.json "id"
if (Test-Path $Link) { Remove-Item $Link -Force -Recurse }
New-Item -ItemType Junction -Path $Link -Target (Resolve-Path ".\plugin-obsidian")
```

Check: `$Link\manifest.json` and `$Link\main.js` resolve.

Then in Obsidian:

1. Open the demo vault.
2. Settings → Community plugins → turn **Safe mode** off.
3. Enable **TM Hub**.
4. Settings → TM Hub:
   - Daemon URL: `http://127.0.0.1:7433`
   - API token: the bearer from §3
5. Status bar (bottom) should show something like `TM: synced · 0 violations`
   within ~5s. Stopping the daemon should degrade to offline without
   console spam.

Full manual checklist (create-from-selection, cut/copy anchors, E04/E05):
**[`plugin-obsidian/TESTPLAN.md`](../../plugin-obsidian/TESTPLAN.md)**.

---

## 8. Suggested dogfood loop (what to actually try)

Keep the daemon terminal visible and work in Obsidian for a day:

| Action | What should happen |
|---|---|
| Edit text on a line ending in `^tm-<id8>`, save | Hub updates; no thrash/echo loop |
| Command **Create node from selection** | Line gets ` ^tm-new` → daemon rewrites to minted id once |
| Cut a managed block to another managed note | Cross-file move (battery E04) |
| Copy a managed block (duplicate anchor) | `E_DUP_ID` / review or certain-repair (E05) — not silent merge |
| Toggle a task checkbox on a managed task line | Checkbox sync |
| Stop daemon, edit vault, restart daemon | Startup reconcile converges (E11 class) |

Watch `$Dogfood\daemon.log` (JSON lines) if something looks stuck.

**Out of scope until later milestones:** daily review queue UI, invalidation /
staleness walk, web UI, `akasha export`, metrics dashboard, permanent
autostart.

---

## 9. Tear down (revert to “not installed”)

```powershell
# 1. Stop the daemon (Ctrl+C)

# 2. Remove the Obsidian plugin junction
$Vault = "C:\Users\<you>\Documents\Obsidian\AkashaDogfood"
Remove-Item (Join-Path $Vault ".obsidian\plugins\tm-hub") -Force -ErrorAction SilentlyContinue
# Disable TM Hub in Obsidian if it still appears

# 3. Delete the isolated dogfood home
Remove-Item -Recurse -Force $env:TEMP\tm-daemon-dogfood

# 4. Optional: remove the repo venv only (keeps source)
# Remove-Item -Recurse -Force .venv
```

If you ever ran the daemon **without** `--config` (default
`%APPDATA%\tm-daemon`), also delete that directory when you want a clean
slate.

---

## 10. Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `another akasha daemon instance is already running` | Lock held | Find the other process, or delete stale `$Dogfood\tm-daemon.lock` only if you are sure nothing is listening on `:7433` |
| `401` / auth errors from CLI or plugin | Missing/wrong `--token` / settings | Re-check bearer; mint a new human token (§3) if lost (old secret cannot be recovered) |
| Status bar stuck `offline` | Daemon down, wrong URL, firewall | `Invoke-RestMethod http://127.0.0.1:7433/health`; confirm plugin URL |
| Edits never mint / never sync | Vault not registered, or note lacks `tm: 1` | Re-run §5; confirm front-matter |
| Cloud-path warning / sluggish sync | Vault under OneDrive/Dropbox | Move demo vault to a local non-cloud path |
| Plugin missing after rebuild | Junction broken or Safe mode on | Re-create junction (§7); reload Obsidian |
| `akasha review …` fails | M7 not implemented yet | Expected; use `/v1/sync/status` for violation/conflict counts |

---

## Quick reference

```powershell
uv sync
# §2 create $Dogfood + config.toml
# §3 bootstrap bearer → $env:AKASHA_TOKEN
uv run akasha daemon --config "$Dogfood\config.toml"   # terminal A
# §5 register sync root; §6–7 build + junction plugin
uv run akasha --token $env:AKASHA_TOKEN search ""       # terminal B smoke
```
