#!/usr/bin/env bash
# Stand up a fresh, disposable dogfood scratch vault + daemon instance for
# manual/browser verification -- never the default config dir, never
# git-tracked, never reused across runs without an explicit destroy first.
#
# Usage: scripts/dogfood/init.sh <name> [port]
#   name  scratch instance name, e.g. "vault-ui-verify" -- becomes
#         $HOME/.local/share/akasha-dogfood/<name>/
#   port  defaults to 7433
#
# Prints the scratch dir, the daemon PID, and the minted human bearer token
# (also written to <scratch>/.dogfood_token -- treat as a secret, never
# commit it). Pair with deinit.sh (stop, keep data) or destroy.sh (stop +
# delete everything).
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../.."
source scripts/dogfood/lib.sh

NAME="${1:?usage: init.sh <name> [port]}"
PORT="${2:-7433}"
SCRATCH="$(scratch_dir "$NAME")"

if [ -e "$SCRATCH" ]; then
  echo "refusing: '$SCRATCH' already exists -- run destroy.sh '$NAME' first" >&2
  exit 1
fi

mkdir -p "$SCRATCH/vault-1"
SCRATCH_WIN="$(to_win_path "$SCRATCH")"

cat > "$SCRATCH/config.toml" <<EOF
port = $PORT
bind = "127.0.0.1"
db_path = "$SCRATCH_WIN/store.db"
EOF

# 8 topically distinct blocks (never near-duplicates of each other) so a
# later deliberate mutation can't accidentally fuzzy-match onto a survivor
# via E_LOST_ANCHOR's >=0.9 similarity repair path.
cat > "$SCRATCH/vault-1/note1.md" <<'EOF'
---
tm: 1
---

Synthetic verification block about weather patterns in coastal regions. ^tm-new
Synthetic verification block about the history of mechanical clocks. ^tm-new
Synthetic verification block about sourdough bread fermentation times. ^tm-new
Synthetic verification block about migratory routes of arctic terns. ^tm-new
Synthetic verification block about the tensile strength of steel cables. ^tm-new
Synthetic verification block about volcanic rock classification systems. ^tm-new
Synthetic verification block about the etymology of nautical terminology. ^tm-new
Synthetic verification block about orbital mechanics of small satellites. ^tm-new
EOF

echo "== bootstrapping DB + first human token =="
uv run python - "$SCRATCH_WIN" <<'PYEOF'
import sys
from akasha.kernel import store
from akasha.api import auth

scratch_win = sys.argv[1]
db_path = f"{scratch_win}/store.db"
conn = store.connect(db_path, check_same_thread=True)
store.run_migrations(conn)

raw_secret = auth.mint_secret()
token = store.create_token(conn, "dogfood-bootstrap", "human", auth.hash_secret(raw_secret))
bearer = auth.format_bearer_token(token["id"], raw_secret)
conn.close()

with open(f"{scratch_win}/.bootstrap_token", "w") as fh:
    fh.write(bearer)
print("bootstrap token id:", token["id"])
PYEOF

echo "== starting daemon on port $PORT =="
nohup uv run akasha daemon --config "$SCRATCH/config.toml" \
  > "$SCRATCH/daemon.out" 2>&1 &
echo $! > "$SCRATCH/daemon.pid"

for _ in $(seq 1 30); do
  if curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$PORT/health" | grep -q '^200$'; then
    break
  fi
  sleep 0.5
done
curl -sf "http://127.0.0.1:$PORT/health" > /dev/null || {
  echo "daemon did not come up -- see $SCRATCH/daemon.out" >&2
  exit 1
}
echo "daemon up, pid $(cat "$SCRATCH/daemon.pid")"

echo "== minting real human token over HTTP =="
BOOTSTRAP="$(cat "$SCRATCH/.bootstrap_token")"
CREATE_OUT="$(curl -sf -X POST "http://127.0.0.1:$PORT/v1/tokens" \
  -H "Authorization: Bearer $BOOTSTRAP" \
  -H "Content-Type: application/json" \
  -d '{"name": "dogfood-ui-verify", "token_class": "human"}')"
echo "$CREATE_OUT" | uv run python -c "
import sys, json
d = json.load(sys.stdin)
sys.stdout.write(d['bearer_token'])
" > "$SCRATCH/.dogfood_token"

echo "== registering sync root =="
TOKEN="$(cat "$SCRATCH/.dogfood_token")"
curl -sf -X POST "http://127.0.0.1:$PORT/v1/sync/roots" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"name\": \"$NAME\", \"root_path\": \"$SCRATCH_WIN/vault-1\"}" > /dev/null

echo "== initial rescan =="
curl -sf -X POST "http://127.0.0.1:$PORT/v1/sync/rescan" \
  -H "Authorization: Bearer $TOKEN" > "$SCRATCH/.rescan_1.json"
cat "$SCRATCH/.rescan_1.json"
echo
echo "== status =="
curl -sf "http://127.0.0.1:$PORT/v1/sync/status" -H "Authorization: Bearer $TOKEN"
echo
echo
echo "scratch dir : $SCRATCH"
echo "port        : $PORT"
echo "token file  : $SCRATCH/.dogfood_token (secret -- never commit)"
echo "daemon pid  : $(cat "$SCRATCH/daemon.pid")"
