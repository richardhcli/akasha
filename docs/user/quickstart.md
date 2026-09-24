# Quickstart

From nothing to a live, syncing vault in two commands: install, then `akasha setup <folder-of-notes>`. From that moment a line shared between notes is kept identical in all of them — edit any copy and every other copy changes (see [Transclusion](#transclusion-the-same-line-in-several-notes)).

## 1. Install

You need [`uv`](https://docs.astral.sh/uv/) and Python 3.12+ (uv fetches Python if you have none).

```bash
git clone <this-repo> akasha && cd akasha
uv tool install .        # puts `akasha` on your PATH (run `uv tool update-shell` once if it isn't)
```

**Windows without Python/`uv`:** an unsigned Inno Setup installer packages a standalone `akasha.exe` (every verb below, plus `akasha.exe tray`, a system-tray-hosted daemon) with an optional autostart backed by a crash-recovering supervisor loop — see [`ops/autostart.md`](ops/autostart.md). Windows SmartScreen may warn on first run because it is not code-signed. Building it yourself is covered in [`../dev/windows-packaging.md`](../dev/windows-packaging.md). If `akasha` is not on your `PATH` after installing, run `akasha.exe` from its install folder.

## 2. Set up

```bash
akasha setup ~/notes        # any folder of Markdown files (or `cd ~/notes && akasha setup .`)
```

That one command:

1. creates the database and your first **human token** (on a fresh install),
2. starts the daemon in the background and waits until it is healthy,
3. registers the folder as a sync root and reconciles it once.

It then prints, once, your token, the `export AKASHA_TOKEN=...` line and a web-UI link (`http://127.0.0.1:7433/?token=...`). **The link and the token are secrets** — a token cannot be recovered afterwards. `setup` is safe to re-run.

```bash
akasha status               # one screen: is it running, is my token good, what is tracked, what is wrong
```

`status` names the classic first-run failures with a one-line fix each (no credential, no vault registered, a vault with nothing to sync yet).

## 3. Use it

**Every Markdown file in the folder is tracked** — there is no front matter to hand-add. A note becomes part of the system when it holds something to sync: write a task or a claim and ask for an id with `^tm-new`:

```markdown
- [ ] write the quickstart ^tm-new
```

Within a moment the daemon rewrites that line with a real id (`^tm-…`) and changes nothing else: the daemon never adds, edits or reads front matter, so a note gains no header. Prose-only notes are never modified.

**Leave things out** with a `.tmignore` file at the folder's root (gitignore-style: `# comments`, `*`, `?`, `**`, a trailing `/` for a folder, `!` to re-include). `.obsidian/`, `.git/`, `.trash/`, `node_modules/` and non-Markdown files are always skipped.

```bash
akasha new claim "caffeine impairs sleep"     # nodes can also be made directly
akasha search caffeine
```

### Transclusion: the same line in several notes

**Same id in two notes = one thing.** That is the whole mechanism, and it is on from the moment `setup` returns — nothing to enable per folder or per note.

1. In any note write a task or a claim with `^tm-new` (as above); the daemon gives it an id, e.g. `- [ ] renew passport ^tm-4cgfdxpi`.
2. Copy that line, id included, into any other notes, in any folder under the vault.
3. Edit or tick it in **any** of them: every other copy changes to match within about a second. Edits made while the daemon is off are picked up the next time it starts. To make an *independent* copy instead, replace the pasted id with `^tm-new`.

Each note keeps its own indentation, and the rest of each note is never touched. If two notes change the *same* line in the same instant, one version wins and the other is kept as a review item (`akasha review list`) — nothing is lost.

**By default the whole line is shared** (`text ^tm-id`). To share only *part* of a line, or *several lines*, wrap it in braces and follow it with the id:

```markdown
The launch is on {friday the 13th}{tm-new} unless it rains.

{ Bring the tent

and the stove }{tm-new}
```

The daemon replaces each `{tm-new}` with the real id (`{tm-4cgfdxpi}`); copy the braced part with its id into any other note and edit either copy. Only the braced text is shared: the rest of each line, and any spaces just inside the braces, stay each note's own (the daemon never adds or removes them). Braces inside the text must balance (`{ a {b} c }` is fine); a brace group with no `{tm-…}` after it is just text. Obsidian shows the braces and id in reading view. If you delete only the `{tm-…}` part, the daemon puts it back when the braced text is unchanged; if you break the text as well, that copy simply stops being shared.

**Damaged ids repair themselves.** Typing at the end of a line (which eats the space before its id) is fine. If a formatter or a typo breaks an id, the daemon never stops syncing the file: an exactly-restorable id comes back (a line that still reads exactly as before gets its id back even if the id itself was corrupted), otherwise the line simply gets a new node (an id it has never seen is adopted as it is, so a second machine or a reset database keeps your transclusions linked). The only thing that asks you anything is deleting a line whose node other things depend on (`akasha review list`).

`akasha render notes/B.md` prints a file with its `![[note#^tm-id]]` embeds replaced by the target's current text, without touching any file.

## 4. Obsidian (optional)

```bash
akasha plugin install ~/notes --from <path-to-a-built-plugin-obsidian>
```

`--from` is a `plugin-obsidian/` directory built with `npm ci && npm run build` (run from a source checkout with `uv run`, the checkout's own is used when you omit it). Obsidian's own consent step cannot be automated, so **you** still do three things once: turn off Restricted mode (Settings > Community plugins), enable "TM Hub", and paste your token in its settings (or pass `--with-token` to have it written for you — a secret in a file inside the vault, so it is opt-in). Details: [`obsidian.md`](obsidian.md).

## What to know

- **Your token is saved.** `setup` (and `init`) write your human token to `~/.config/tm-daemon/tm-token` (`%APPDATA%\tm-daemon\tm-token` on Windows), readable only by you, so `akasha` needs no flag afterwards. That means **anything that runs `akasha` as you — a script, an AI agent in your terminal — acts as you.** Delete the file to stop. Details in [`cli.md`](cli.md).
- **The daemon starts when needed.** A verb that finds no daemon starts one (and says so on stderr); `akasha up` / `akasha down` do it explicitly. There is no login-time autostart on Linux/macOS yet; on Windows the installer offers one ([`ops/autostart.md`](ops/autostart.md)).
- **Where things live:** config, database, `daemon.log` and the token file are in `~/.config/tm-daemon/` (Linux/macOS) or `%APPDATA%\tm-daemon\` (Windows).

## Next steps

- Full verb/endpoint reference: [`cli.md`](cli.md), [`api.md`](api.md)
- Browser UI at `http://127.0.0.1:7433/`: [`web-ui.md`](web-ui.md)
- Sync an Obsidian vault: [`obsidian.md`](obsidian.md)

## Developer appendix: from a source checkout

Running the daemon in the foreground under `uv run`, a scratch database, the test gates and the transclusion playground (`make demo-transclusion`) are covered in [`../dev/setup.md`](../dev/setup.md), not here. The manual sequence the commands above replace is `akasha init` → `akasha daemon` → `akasha sync add <folder>` → pass `--token` on every call; those verbs still exist and are documented in [`cli.md`](cli.md).
