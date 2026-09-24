"""One command, then transclusion works: `akasha setup <folder>` and nothing else.

The user-visible claim of milestone M19 combined with M18: after a SINGLE `akasha setup`, editing a
transcluded line in any file changes it in every file that holds it. Everything is real -- a real
detached daemon, a real `watchdog` observer, real files -- and nothing but `setup` is ever run to
start or configure anything. The edits are made the way editors save: in place, by atomic rename,
and in two different files at once (debug-plan D11: files written the instant `setup` returns;
D12: two files edited inside one debounce window).
"""

from __future__ import annotations

import os
import re
import signal
import socket
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from typer.testing import CliRunner

from akasha import daemon as daemon_module
from akasha.cli.main import app as cli_app

runner = CliRunner()
_ID_RE = re.compile(r"\^tm-[0-9a-z]{8}")
_CEILING_SECONDS = 10.0  # the assertion ceiling; measured propagation is about half a second


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, Any]]:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("APPDATA", str(home))
    for var in ("AKASHA_TOKEN", "AKASHA_BASE_URL", "AKASHA_NO_AUTOSTART"):
        monkeypatch.delenv(var, raising=False)
    conf_dir = tmp_path / "conf"
    conf_dir.mkdir()
    port = _free_port()
    config = conf_dir / "config.toml"
    config.write_text(
        f'port = {port}\nbind = "127.0.0.1"\ndb_path = "{(conf_dir / "store.db").as_posix()}"\n',
        encoding="utf-8",
    )
    vault = tmp_path / "vault"
    vault.mkdir()
    info: dict[str, Any] = {
        "config": str(config),
        "url": f"http://127.0.0.1:{port}",
        "vault": vault,
    }
    try:
        yield info
    finally:
        runner.invoke(cli_app, ["down", "--config", info["config"]])
        pid = daemon_module._read_pid(conf_dir)  # pyright: ignore[reportPrivateUsage]
        if pid is not None:
            try:
                os.kill(pid, getattr(signal, "SIGKILL", signal.SIGTERM))
            except ProcessLookupError:
                pass


def _wait_until(predicate: Callable[[], bool], timeout: float = _CEILING_SECONDS) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if predicate():
                return True
        except OSError:
            pass
        time.sleep(0.02)
    return predicate()


def _edit(path: Path, old: str, new: str, *, atomic: bool = False) -> None:
    text = path.read_text(encoding="utf-8")
    assert old in text, (old, text)
    if atomic:  # how vim and many editors save: temp file, then rename over the target
        tmp = path.with_name(f".{path.name}.swp")
        tmp.write_text(text.replace(old, new), encoding="utf-8")
        os.replace(tmp, path)
    else:
        path.write_text(text.replace(old, new), encoding="utf-8")


def test_after_one_setup_editing_any_copy_changes_every_copy(env: dict[str, Any]) -> None:
    vault: Path = env["vault"]
    inbox, prose = vault / "inbox.md", vault / "prose.md"
    trip, today = vault / "projects" / "trip.md", vault / "daily" / "today.md"
    inbox.write_text(
        "# Inbox\n\n- [ ] call the dentist ^tm-new\n- [ ] renew passport ^tm-new\n",
        encoding="utf-8",
    )
    prose.write_text("just words, no tasks\n", encoding="utf-8")
    (vault / "projects").mkdir()
    trip.write_text("# Trip\n\nplain prose\n", encoding="utf-8")

    # ---- the ONE command -----------------------------------------------------------------
    result = runner.invoke(cli_app, ["setup", "--config", env["config"], str(vault)])
    assert result.exit_code == 0, result.output
    match = re.search(r"export AKASHA_TOKEN=(\S+)", result.output)
    assert match, result.output
    headers = {"Authorization": f"Bearer {match.group(1)}"}

    dentist, passport = _ID_RE.findall(inbox.read_text(encoding="utf-8"))
    assert prose.read_text(encoding="utf-8") == "just words, no tasks\n"  # prose is never touched

    # ---- transclude: copy the anchored lines elsewhere, IMMEDIATELY (D11), incl. a new folder ---
    lines = {
        i: next(x for x in inbox.read_text().splitlines() if i in x) for i in (dentist, passport)
    }
    trip.write_text(trip.read_text() + f"\n{lines[dentist]}\n{lines[passport]}\n", encoding="utf-8")
    (vault / "daily").mkdir()
    today.write_text(f"# Today\n\n{lines[dentist]}\n{lines[passport]}\n", encoding="utf-8")

    def tracked() -> int:
        status = httpx.get(f"{env['url']}/v1/sync/status", headers=headers).json()
        return len(status["sync_roots"][0]["files"])

    def open_reviews() -> list[Any]:
        return httpx.get(f"{env['url']}/v1/review?status=open", headers=headers).json()["reviews"]

    assert _wait_until(lambda: tracked() == 3), "the hub never adopted the copies written at once"
    assert open_reviews() == []  # a copy is a mirror, not a violation

    def everywhere(needle: str, *paths: Path) -> bool:
        return all(needle in p.read_text(encoding="utf-8") for p in paths)

    # ---- edit any copy => every copy -------------------------------------------------------
    # the FIRST edit is typed into a copy, not the original (debug-plan D13: the daemon had never
    # seen the original as an owner, so a copy's edit used to reach nothing until the original was
    # edited once -- "syncing directionally")
    _edit(trip, "call the dentist", "call Dr. Rao")
    assert _wait_until(lambda: everywhere("call Dr. Rao", inbox, today)), (
        "trip (a copy) -> original"
    )

    _edit(today, "call Dr. Rao", "call Dr. Rao about the crown", atomic=True)
    assert _wait_until(lambda: everywhere("the crown", inbox, trip)), (
        "today (atomic save) -> others"
    )

    _edit(trip, "- [ ] call Dr. Rao", "- [x] call Dr. Rao")
    assert _wait_until(lambda: everywhere("- [x] call Dr. Rao", inbox, today)), "checkbox"

    # ---- two files, two different lines, milliseconds apart (D12) -----------------------------
    _edit(inbox, "renew passport", "renew passport (urgent)")
    _edit(today, "call Dr. Rao about the crown", "call Dr. Rao about the crown MONDAY")
    both = lambda: all(  # noqa: E731
        everywhere("(urgent)", p) and everywhere("crown MONDAY", p) for p in (inbox, trip, today)
    )
    assert _wait_until(both), {p.name: p.read_text() for p in (inbox, trip, today)}

    assert open_reviews() == []  # nothing was ever queued for a human, nothing was lost
    assert _ID_RE.findall(trip.read_text()) == [dentist, passport]  # ids untouched by all of it
