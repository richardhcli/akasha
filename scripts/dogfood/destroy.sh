#!/usr/bin/env bash
# Stop (if running) and permanently delete a dogfood scratch instance.
#
# Usage: scripts/dogfood/destroy.sh <name>
#
# Safety: refuses to run unless the resolved target directory is a real,
# existing descendant of $HOME/.local/share/akasha-dogfood -- this makes it
# structurally impossible for this script to ever touch a real
# ~/.config/tm-daemon or %APPDATA%/tm-daemon store.db, in this environment
# or a real one.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../.."
source scripts/dogfood/lib.sh

NAME="${1:?usage: destroy.sh <name>}"
SCRATCH="$(scratch_dir "$NAME")"

if [ ! -d "$SCRATCH" ]; then
  echo "'$SCRATCH' does not exist -- nothing to destroy" >&2
  exit 0
fi

require_under_dogfood_root "$SCRATCH"

scripts/dogfood/deinit.sh "$NAME" || true

rm -rf "$SCRATCH"
echo "destroyed $SCRATCH"
