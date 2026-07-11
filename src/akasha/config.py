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


def default_config_dir() -> Path:
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        base = Path(appdata) if appdata else Path.home() / "AppData" / "Roaming"
        return base / NEUTRAL_DIR_NAME
    return Path.home() / ".config" / NEUTRAL_DIR_NAME


def default_config_path() -> Path:
    return default_config_dir() / "config.toml"


@dataclass(frozen=True)
class Config:
    port: int = DEFAULT_PORT
    bind: str = DEFAULT_BIND
    path: Path | None = None


def load_config(config_path: str | Path | None = None) -> Config:
    """Load config from config_path, or the default per-OS location if unset.

    Missing files are not an error — defaults apply.
    """
    path = Path(config_path) if config_path is not None else default_config_path()
    if not path.exists():
        return Config(path=path)

    data = tomllib.loads(path.read_text(encoding="utf-8"))
    return Config(
        port=data.get("port", DEFAULT_PORT),
        bind=data.get("bind", DEFAULT_BIND),
        path=path,
    )
