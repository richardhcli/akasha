"""Scratch isolation. Every akasha / basic-memory subprocess gets its environment from here, so it
can never reach the real daemon (port 7433), ~/.config/tm-daemon, or the Purdue key."""

from __future__ import annotations

import os
from pathlib import Path

from kbio.paths import BASE_URL, DATA, PORT, PURDUE_ENV, REPO, SCRATCH_HOME

# The repo's own venv runs the current working tree of `main` (the uv-tool install is older).
AKASHA_BIN = REPO / ".venv" / "bin" / "akasha"
SECRET_KEYS = ("PURDUE_GENAI_API_KEY", "OPENROUTER_API_KEY", "OPENAI_API_KEY", "AKASHA_TOKEN")


def scratch_env(home: Path = SCRATCH_HOME, extra: dict[str, str] | None = None) -> dict[str, str]:
    home = home.resolve()
    assert str(home).startswith(str(DATA.resolve()) + "/"), f"refusing non-scratch HOME {home}"
    env = {k: v for k, v in os.environ.items() if k not in SECRET_KEYS}
    env.update(
        HOME=str(home),
        XDG_CONFIG_HOME=str(home / ".config"),
        XDG_DATA_HOME=str(home / ".local" / "share"),
        XDG_STATE_HOME=str(home / ".local" / "state"),
        XDG_CACHE_HOME=str(home / ".cache"),
        AKASHA_BASE_URL=BASE_URL,
    )
    env.update(extra or {})
    assert ":7433" not in env["AKASHA_BASE_URL"], "refusing the real daemon's port"
    return env


def write_scratch_config(home: Path = SCRATCH_HOME) -> Path:
    """The daemon reads ~/.config/tm-daemon/config.toml; pin it to the scratch port."""
    cfg = home / ".config" / "tm-daemon" / "config.toml"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(f'port = {PORT}\nbind = "127.0.0.1"\n')
    return cfg


def load_purdue_key() -> None:
    """Put PURDUE_GENAI_API_KEY into this process's environment only (never printed)."""
    if os.environ.get("PURDUE_GENAI_API_KEY"):
        return
    for line in PURDUE_ENV.read_text(encoding="utf-8").splitlines():
        key, sep, value = line.strip().partition("=")
        if sep and key.strip() == "PURDUE_GENAI_API_KEY":
            os.environ["PURDUE_GENAI_API_KEY"] = value.strip().strip("\"'")
