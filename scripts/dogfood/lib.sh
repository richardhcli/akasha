#!/usr/bin/env bash
# Shared helpers for scripts/dogfood/{init,deinit,destroy}.sh.
#
# Every path that crosses into `uv run python`, config.toml, or an HTTP
# request body (sqlite3, TOML, and the JSON `root_path` field all want
# Windows-form forward-slash paths, not Git Bash's /c/... MSYS form) is
# converted exactly once here and reused everywhere else, rather than
# re-converted ad hoc at each call site.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DOGFOOD_ROOT_POSIX="$HOME/.local/share/akasha-dogfood"
DOGFOOD_ROOT_WIN="$(cygpath -w "$DOGFOOD_ROOT_POSIX" | tr '\\' '/')"

# to_win_path <posix-path> -> Windows-form forward-slash path
to_win_path() {
  cygpath -w "$1" | tr '\\' '/'
}

# scratch_dir <name> -> POSIX-form path to this scratch instance's directory
scratch_dir() {
  echo "$DOGFOOD_ROOT_POSIX/$1"
}

# require_under_dogfood_root <posix-path> -- exits non-zero if the path is
# not (a resolved, symlink-free) descendant of $DOGFOOD_ROOT_POSIX. This is
# the guard that makes `destroy.sh` safe to hand to a real environment where
# a real ~/.config/tm-daemon or %APPDATA%/tm-daemon store.db exists: it is
# structurally impossible for this guard to pass for that path.
require_under_dogfood_root() {
  local target
  target="$(cd "$1" 2>/dev/null && pwd || true)"
  if [ -z "$target" ]; then
    echo "refusing: '$1' does not exist or is not a directory" >&2
    return 1
  fi
  case "$target" in
    "$DOGFOOD_ROOT_POSIX"/*) ;;
    *)
      echo "refusing: '$target' is not under the scratch root '$DOGFOOD_ROOT_POSIX' -- will not touch it" >&2
      return 1
      ;;
  esac
}
