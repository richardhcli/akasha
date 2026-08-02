"""Integration tests for ``akasha init`` (task T12.1, closing
``docs/spec-questions.md`` T11.1).

Unlike every other CLI verb (``tests/integration/test_cli.py``), ``init``
is not an HTTP client -- it talks to ``kernel/store.py`` directly, the
same "not a pure HTTP client" exception ``daemon`` already documents in
``cli/main.py``'s module docstring. These tests therefore invoke it
against a config file pointing at a scratch, on-disk SQLite path (no live
daemon needed for the ``init`` call itself), then separately stand up a
real daemon against that same DB to prove the minted bearer token
actually authenticates a live HTTP call -- closing the loop end to end.
"""

from __future__ import annotations

import re
import socket
import threading
import time
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
import uvicorn
from typer.testing import CliRunner

from akasha.api.app import create_app
from akasha.cli.main import app as cli_app
from akasha.kernel import store

runner = CliRunner()

# "{token_id}.{raw_secret}" -- see akasha/api/auth.py module docstring.
_BEARER_RE = re.compile(r"^[a-z2-7]{8}\.\S+$")


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture
def config_path(tmp_path):  # type: ignore[no-untyped-def]
    db_path = tmp_path / "store.db"
    cfg = tmp_path / "config.toml"
    cfg.write_text(f'db_path = "{db_path.as_posix()}"\n', encoding="utf-8")
    return cfg


def _db_path_from_config(config_path) -> Any:  # type: ignore[no-untyped-def]
    from akasha.config import load_config

    return load_config(str(config_path)).db_path


@pytest.fixture
def daemon_from_db(config_path) -> Iterator[dict[str, Any]]:  # type: ignore[no-untyped-def]
    """Stand up a live daemon against the DB ``init`` bootstraps into."""
    db_path = _db_path_from_config(config_path)
    conn = store.connect(db_path, check_same_thread=False)

    fastapi_app = create_app(conn=conn)
    port = _free_port()
    uv_config = uvicorn.Config(fastapi_app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(uv_config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10.0
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.01)
    assert server.started, "uvicorn test server failed to start"

    try:
        yield {"base_url": f"http://127.0.0.1:{port}", "conn": conn}
    finally:
        server.should_exit = True
        thread.join(timeout=5)


# --- (a) fresh DB bootstrap + the minted token authenticates a real call ---


def test_init_on_fresh_db_prints_usable_bearer_token(config_path):
    result = runner.invoke(cli_app, ["init", "--config", str(config_path), "--name", "bootstrap"])
    assert result.exit_code == 0, result.output

    lines = [line for line in result.output.splitlines() if line.strip()]
    assert len(lines) >= 1
    bearer = lines[0].strip()
    assert _BEARER_RE.match(bearer), bearer

    # a token row now exists on disk, minted as class "human".
    db_path = _db_path_from_config(config_path)
    conn = store.connect(db_path, check_same_thread=False)
    tokens = store.list_tokens(conn)
    assert len(tokens) == 1
    assert tokens[0]["name"] == "bootstrap"
    assert tokens[0]["class"] == "human"
    conn.close()


def test_init_creates_missing_parent_directory_of_db_path(tmp_path):
    """The real first-run case: ``db_path``'s parent dir does not exist yet
    (e.g. ``~/.config/tm-daemon/`` on a machine that never ran the daemon).
    ``init`` must create it, not fail -- exercises the ``Path(db_path)
    .parent.mkdir(parents=True, exist_ok=True)`` step (spec §4.12 Step 2:
    "safe against a genuinely fresh, schema-less DB file").
    """
    db_path = tmp_path / "never-created" / "nested" / "store.db"
    assert not db_path.parent.exists()
    cfg = tmp_path / "config.toml"
    cfg.write_text(f'db_path = "{db_path.as_posix()}"\n', encoding="utf-8")

    result = runner.invoke(cli_app, ["init", "--config", str(cfg), "--name", "bootstrap"])
    assert result.exit_code == 0, result.output
    assert db_path.exists()

    conn = store.connect(db_path, check_same_thread=False)
    tokens = store.list_tokens(conn)
    assert len(tokens) == 1
    assert tokens[0]["class"] == "human"
    conn.close()


def test_init_minted_token_authenticates_a_live_daemon_call(config_path, daemon_from_db):
    result = runner.invoke(cli_app, ["init", "--config", str(config_path), "--name", "bootstrap"])
    assert result.exit_code == 0, result.output
    bearer = result.output.splitlines()[0].strip()

    resp = httpx.get(
        f"{daemon_from_db['base_url']}/v1/tokens",
        headers={"Authorization": f"Bearer {bearer}"},
    )
    assert resp.status_code == 200, resp.text
    names = [t["name"] for t in resp.json()["tokens"]]
    assert "bootstrap" in names


def test_init_default_name_is_bootstrap(config_path):
    result = runner.invoke(cli_app, ["init", "--config", str(config_path)])
    assert result.exit_code == 0, result.output

    db_path = _db_path_from_config(config_path)
    conn = store.connect(db_path, check_same_thread=False)
    tokens = store.list_tokens(conn)
    assert len(tokens) == 1
    assert tokens[0]["name"] == "bootstrap"
    conn.close()


# --- (b) a second init on an already-bootstrapped DB is a clean no-op-with-error --


def test_init_twice_exits_4_with_clean_message_and_mints_no_second_token(config_path):
    first = runner.invoke(cli_app, ["init", "--config", str(config_path), "--name", "first"])
    assert first.exit_code == 0, first.output

    db_path = _db_path_from_config(config_path)
    conn = store.connect(db_path, check_same_thread=False)
    before = store.list_tokens(conn)
    assert len(before) == 1

    second = runner.invoke(cli_app, ["init", "--config", str(config_path), "--name", "second"])
    assert second.exit_code == 4, second.output
    assert second.exception is None or isinstance(second.exception, SystemExit)
    # no traceback leaked to output.
    assert "Traceback" not in second.output

    after = store.list_tokens(conn)
    assert len(after) == len(before) == 1
    assert after[0]["name"] == "first"
    conn.close()


def test_init_twice_json_output_has_no_second_bearer_token(config_path):
    first = runner.invoke(cli_app, ["init", "--config", str(config_path), "--name", "first"])
    assert first.exit_code == 0, first.output
    first_bearer = first.output.splitlines()[0].strip()

    second = runner.invoke(cli_app, ["init", "--config", str(config_path), "--name", "second"])
    assert second.exit_code == 4, second.output
    assert first_bearer not in second.output
    assert not any(_BEARER_RE.match(line.strip()) for line in second.output.splitlines())
