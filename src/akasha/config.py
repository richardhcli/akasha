"""Config loading: paths, ports, budgets.

Config dir uses the neutral name ``tm-daemon`` everywhere on disk (build-plan
rule 0.6 — the product name never appears in on-disk formats or paths).
"""

from __future__ import annotations

import os
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

DEFAULT_PORT = 7433
DEFAULT_BIND = "127.0.0.1"
NEUTRAL_DIR_NAME = "tm-daemon"
# vision.md §14 A7: "S0 default GC retention 30 days (configurable)".
DEFAULT_S0_GC_RETENTION_DAYS = 30


def default_config_dir() -> Path:
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        base = Path(appdata) if appdata else Path.home() / "AppData" / "Roaming"
        return base / NEUTRAL_DIR_NAME
    return Path.home() / ".config" / NEUTRAL_DIR_NAME


def default_config_path() -> Path:
    return default_config_dir() / "config.toml"


# build-plan T18.9 (ruling M18-A), neutral name per rule 6: the human token that
# `akasha init`/`akasha setup` mint is saved beside config.toml so every verb
# can authenticate without a flag. Only ever the human token -- never an
# agent-class one.
TOKEN_FILE_NAME = "tm-token"


def default_token_path(config_dir: Path | None = None) -> Path:
    return (config_dir if config_dir is not None else default_config_dir()) / TOKEN_FILE_NAME


def read_token(path: Path) -> str | None:
    """The saved token, or None if the file is missing, unreadable or empty."""
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return text or None


def write_token(path: Path, token: str) -> None:
    """Save ``token`` to ``path`` with mode 0600 set AT CREATION, then atomically in place.

    The temporary file is created with ``os.open(..., 0o600)`` so the secret is never,
    even briefly, world-readable; ``os.replace`` then makes the swap atomic. On
    Windows the POSIX mode bits are not meaningful: the file relies on the per-user
    ``%APPDATA%`` ACL (no ACL code here).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(token + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def default_db_path() -> Path:
    """Default SQLite database file location.

    Lives alongside ``config.toml`` in the neutral ``tm-daemon`` dir.
    ``store.db`` is the spec-fixed, product-name-free filename (rule 0.6).
    """
    return default_config_dir() / "store.db"


@dataclass(frozen=True)
class Config:
    port: int = DEFAULT_PORT
    bind: str = DEFAULT_BIND
    path: Path | None = None
    db_path: Path | None = None
    # T9.3b / vision.md §14 A7: S0 node-retention-by-age GC threshold, in
    # days. Not a DB column -- purely a scheduler input consumed by
    # ``daemon.GcScheduler`` (see ``store.list_expired_s0_node_ids``).
    s0_gc_retention_days: int = DEFAULT_S0_GC_RETENTION_DAYS


def load_config(config_path: str | Path | None = None) -> Config:
    """Load config from config_path, or the default per-OS location if unset.

    Missing files are not an error — defaults apply.
    """
    path = Path(config_path) if config_path is not None else default_config_path()
    if not path.exists():
        return Config(path=path, db_path=default_db_path())

    data = tomllib.loads(path.read_text(encoding="utf-8"))
    db = data.get("db_path")
    return Config(
        port=data.get("port", DEFAULT_PORT),
        bind=data.get("bind", DEFAULT_BIND),
        path=path,
        db_path=Path(db) if db else default_db_path(),
        s0_gc_retention_days=data.get(
            "s0_gc_retention_days", DEFAULT_S0_GC_RETENTION_DAYS
        ),
    )
