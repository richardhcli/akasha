# Dogfood smoke test (T11.1)

This is a copy-pasteable runbook for staging a small, disposable, **never
git-tracked** copy of real personal notes as a genuine sync root on a real
running daemon instance. It documents mechanical plumbing only — no
judgment about note content. All paths below are placeholders; substitute
your own.

Everything this runbook creates lives **outside this repo's working
tree**, under a scratch directory such as `$HOME/.local/share/akasha-dogfood/`.
Nothing here is ever `git add`ed; `data/` (the personal-vault-content
staging area some contributors use locally) is also `.gitignore`d
(`/data/`, confirmed via `git check-ignore -v data/`) as defense in depth,
but the canonical scratch location for this runbook is outside the repo
entirely, so it can never be added regardless of `.gitignore`.

## 0. Prerequisites

- A working `uv run akasha` CLI (this repo's venv).
- A source vault directory containing your own real notes, e.g.
  `<VAULT_SRC>` (substitute your own path — never commit or reference the
  literal path in this file).

## 1. Confirm `data/` is ignored (verify only, do not re-add)

```sh
git check-ignore -v data/
# expect: .gitignore:<N>:/data/    data/
```

## 2. Create a scratch directory outside the repo

```sh
SCRATCH="$HOME/.local/share/akasha-dogfood"
mkdir -p "$SCRATCH/vault-1"
```

## 3. Copy a handful of real files verbatim into the scratch vault

Pick a small number (this runbook used 5) of small, self-contained notes.
Avoid anything under a "workflow"/task-tracking subdirectory of your vault
— prefer standalone concept/reference notes. Copy with `cp -p` (never
open-and-resave) so the bytes are unmodified:

```sh
for f in <FILE_1> <FILE_2> <FILE_3> <FILE_4> <FILE_5>; do
  cp -p "<VAULT_SRC>/$f" "$SCRATCH/vault-1/$f"
done

# Optional integrity check: confirm byte-identical copies.
for f in <FILE_1> <FILE_2> <FILE_3> <FILE_4> <FILE_5>; do
  diff <(sha256sum "<VAULT_SRC>/$f" | cut -d' ' -f1) \
       <(sha256sum "$SCRATCH/vault-1/$f" | cut -d' ' -f1)
done
```

## 4. Write a scratch `config.toml` with its own `db_path`

Never point `db_path` at the default `~/.config/tm-daemon/` (or
`%APPDATA%/tm-daemon/` on Windows) location — this must never collide with
or pollute a real production DB.

```sh
cat > "$SCRATCH/config.toml" <<EOF
port = 7433
bind = "127.0.0.1"
db_path = "$SCRATCH/store.db"
EOF
```

## 5. Check whether the default port is already bound

```sh
ss -ltnp 2>/dev/null | grep 7433 || echo "port free"
# or: lsof -i :7433
```

If it's already in use by another (possibly real) daemon, edit
`$SCRATCH/config.toml` to use a different `port` before continuing, and
substitute that port everywhere below.

## 6. Bootstrap the scratch DB and mint the first human token

`POST /v1/tokens` (spec §4.11, ``akasha token create``) is `require_human`
— it needs an *existing* human bearer token to authorize creating another
one. A brand-new database has no tokens at all, so there is no
HTTP/CLI-only path to mint the very first token on a fresh DB.

<!--
SPEC-QUESTION (T11.1): spec §4.11/§4.12 describe `POST /v1/tokens` as
human-only (`require_human`) with no documented bootstrap path for a
fresh database's first human token. The only way to obtain a first token
is a direct call into `kernel/store.py`'s `create_token` (the same
pattern `tests/battery/soak.py` already uses to seed a token for its own
harness) — never a second write path, never raw SQL, always through
`store.py` per rule 0.4. Narrowest reading taken here: treat this
one-time bootstrap as an operator/test-harness action (an "embedded
caller" in `store.connect`'s own terminology — see its docstring), run
BEFORE the daemon is started (avoids any question of a second writer
touching a live daemon's WAL connection), producing exactly one bootstrap
token whose only purpose is authorizing the *next* token creation over
real HTTP. Logged in `docs/spec-questions.md`.
-->

```sh
uv run python - <<'PYEOF'
from akasha.kernel import store
from akasha.api import auth

db_path = "REPLACE_WITH_YOUR_SCRATCH_DB_PATH"  # e.g. "$SCRATCH/store.db", expanded
conn = store.connect(db_path, check_same_thread=True)
store.run_migrations(conn)

raw_secret = auth.mint_secret()
token = store.create_token(conn, "dogfood-bootstrap", "human", auth.hash_secret(raw_secret))
bearer = auth.format_bearer_token(token["id"], raw_secret)
conn.close()

# Write the bearer to a scratch-only file; never print/log it anywhere
# that could end up committed.
with open("REPLACE_WITH_YOUR_SCRATCH_DIR/.bootstrap_token", "w") as fh:
    fh.write(bearer)
print("bootstrap token id:", token["id"])
PYEOF
```

## 7. Start the daemon, backgrounded

```sh
nohup uv run akasha daemon --config "$SCRATCH/config.toml" \
  > "$SCRATCH/daemon.out" 2>&1 &
echo $! > "$SCRATCH/daemon.pid"

# confirm it's up
curl -s http://127.0.0.1:7433/health
```

## 8. Create a real human token over genuine HTTP, using the bootstrap token

This is the real, spec-documented path (`akasha token create ... --class
human`) — the bootstrap token in step 6 exists solely to authorize this
call; do not reuse the bootstrap token for anything else.

```sh
BOOTSTRAP=$(cat "$SCRATCH/.bootstrap_token")
uv run akasha --base-url http://127.0.0.1:7433 --token "$BOOTSTRAP" --json \
  token create dogfood-smoke --class human > "$SCRATCH/.token_create_output.json"

# The --json envelope nests the payload under "data":
# {"schema":"cli/v1","ok":true,"data":{...,"bearer_token":"..."}}
# Extract just the bearer token into its own scratch-only file, then
# discard the envelope (it also contains the raw bearer value).
python3 -c "
import json
with open('$SCRATCH/.token_create_output.json') as f:
    d = json.load(f)
with open('$SCRATCH/.dogfood_smoke_token', 'w') as f:
    f.write(d['data']['bearer_token'])
"
rm -f "$SCRATCH/.token_create_output.json"
# Never print/log the raw bearer value anywhere that will be committed.
```

## 9. Register the scratch vault as a live sync root over direct HTTP

There is no `akasha` CLI verb for sync-root registration (§4.12's verb
list has none) — register it exactly as an Obsidian-plugin-less human
would today, via direct HTTP against the existing `POST /v1/sync/roots`
endpoint (§4.11, human-only):

```sh
TOKEN=$(cat "$SCRATCH/.dogfood_smoke_token")
curl -s -X POST http://127.0.0.1:7433/v1/sync/roots \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"name\": \"dogfood-smoke\", \"root_path\": \"$SCRATCH/vault-1\"}"
```

## 10. Verify

```sh
TOKEN=$(cat "$SCRATCH/.dogfood_smoke_token")

# (a) the new root is listed
curl -s http://127.0.0.1:7433/v1/sync/roots -H "Authorization: Bearer $TOKEN"

# (b) 0 violations for it — expected PASS: the copied files carry no
# `^tm-` anchors yet, so 0 managed blocks is correct, not a bug.
curl -s http://127.0.0.1:7433/v1/sync/status -H "Authorization: Bearer $TOKEN"
```

<!--
SPEC-QUESTION (T11.1): registering a sync root (`POST /v1/sync/roots`)
does no filesystem walk (`kernel/store.py`'s `register_sync_root` is a
pure DB upsert), and there is no wired file-discovery path for a brand
new root's on-disk files in this build: `sync/watcher.py`'s `Watcher`
class is never instantiated in `daemon.py`'s `serve()` or in
`api/app.py` (confirmed by grep — zero production call sites), and both
`reconcile.reconcile_all` (daemon startup) and `POST /v1/sync/rescan`
only iterate *already-known* `store.list_sync_files` rows, never the
filesystem. Empirically: after registering a root with 5 real `.md`
files on disk and calling `POST /v1/sync/rescan`, the response was
`{"files_reconciled": 0, "files_missing": 0, "reviews_open": 0}` and
`GET /v1/sync/status` continued to show `"files": []` for that root.
This does not block T11.1 (`GET /v1/sync/status` showing `violations: []`
is still the literally-specified, correct expected result either way),
but it means T11.2's step 2 ("let the daemon's watcher pick it up (or
`POST /v1/sync/rescan`)") will not actually pick up newly added `^tm-new`
anchors in this build until file discovery for new sync roots is wired
(out of scope for T11.1 — its Files list is this document only). Logged
in `docs/spec-questions.md`; a human running T11.2 needs to know this
before relying on either mechanism.
-->

```sh
# (c) confirm nothing under the scratch path leaks into this repo's git
# status, and that data/ stays untracked/ignored, run from repo root:
git status --porcelain | grep -i "akasha-dogfood" || echo "no matches (expected)"
git check-ignore -v data/
```

## Known limitation — read this before doing T11.2

**Registering a sync root does not scan its existing files, and there is
currently no wired mechanism that does.** `POST /v1/sync/roots` only
inserts a DB row; it does not walk the directory. In this build, the
filesystem `Watcher` (`sync/watcher.py`) has zero production call sites —
it is not started by the daemon (`daemon.py`'s `serve()`) or by
`api/app.py`. Both the daemon's own startup reconcile and
`POST /v1/sync/rescan` only re-process files *already* tracked in the
`sync_files` table — never files that have never been seen before.

Empirically: registering a root pointing at 5 real `.md` files on disk,
then calling `POST /v1/sync/rescan`, returned
`{"files_reconciled": 0, "files_missing": 0, "reviews_open": 0}`, and a
subsequent `GET /v1/sync/status` still showed `"files": []` for that
root.

**Practical consequence for T11.2:** its step 2 ("save; let the daemon's
watcher pick it up (or `POST /v1/sync/rescan`)") will not actually detect
a hand-added `^tm-new` anchor in a file under a sync root that has never
been reconciled before, in this build. Until file discovery for new sync
roots is wired (out of scope for this document — see the `# SPEC-QUESTION:`
comment above and `docs/spec-questions.md`), a human doing T11.2 should
expect this and treat it as a known gap, not user error.

## Cleanup

```sh
kill "$(cat "$SCRATCH/daemon.pid")"
# rm -rf "$SCRATCH"   # only if you want to discard the scratch vault/DB entirely
```
