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
_SPAN_ID_RE = re.compile(r"\{tm-([0-9a-z]{8})\}")
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


def _headers(setup_result: Any) -> dict[str, str]:
    match = re.search(r"export AKASHA_TOKEN=(\S+)", setup_result.output)
    assert match, setup_result.output
    return {"Authorization": f"Bearer {match.group(1)}"}


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
    for note in (inbox, trip, today):  # M20-C: setup, a mint, edits and mirrors add no header
        assert not note.read_text(encoding="utf-8").startswith("---"), note.name
    assert trip.read_text(encoding="utf-8").startswith("# Trip\n")  # its own first line
    assert _ID_RE.findall(trip.read_text()) == [dentist, passport]  # ids untouched by all of it


def test_a_formatter_storm_never_stops_the_file_syncing(env: dict[str, Any]) -> None:
    """M20-G through the real daemon: a formatter strips, rewords and corrupts most of a file's
    ids at once. Every line is resolved on the spot (no pause, no review, no lost text) and the
    file keeps syncing to a mirror afterwards."""
    vault: Path = env["vault"]
    notes, mirror = vault / "notes.md", vault / "mirror.md"
    body = "write the quarterly report section"
    notes.write_text("".join(f"- [ ] {body} {n} ^tm-new\n" for n in range(1, 6)), encoding="utf-8")
    result = runner.invoke(cli_app, ["setup", "--config", env["config"], str(vault)])
    assert result.exit_code == 0, result.output
    match = re.search(r"export AKASHA_TOKEN=(\S+)", result.output)
    assert match
    headers = {"Authorization": f"Bearer {match.group(1)}"}
    ids = _ID_RE.findall(notes.read_text(encoding="utf-8"))
    assert len(ids) == 5
    line5 = notes.read_text(encoding="utf-8").splitlines()[4]
    mirror.write_text(line5 + "\n", encoding="utf-8")  # section 5 is transcluded

    def tracked() -> int:
        status = httpx.get(f"{env['url']}/v1/sync/status", headers=headers).json()
        return len(status["sync_roots"][0]["files"])

    assert _wait_until(lambda: tracked() == 2)

    notes.write_text(
        f"- [ ] {body} 1\n"  # anchor stripped, text identical: the anchor is restored
        f"- [ ] {body}s 2\n"  # anchor stripped and text slightly changed: a new node
        f"- [ ] {body} 3 ^tm-aaaaaaab\n"  # corrupted id: a new node
        f"- [ ] {body} 4 {ids[3]}\n"  # a duplicated id: the second copy is a new node
        f"- [ ] {body} 4 again {ids[3]}\n"
        f"{line5}\n",
        encoding="utf-8",
    )

    def resolved() -> bool:
        text = notes.read_text(encoding="utf-8")
        lines = text.splitlines()
        return (
            "^tm-new" not in text
            and "^tm-aaaaaaab" not in text
            and len(lines) == 6
            and all(_ID_RE.search(line) for line in lines)
        )

    assert _wait_until(resolved), notes.read_text(encoding="utf-8")
    text = notes.read_text(encoding="utf-8")
    for expected in (f"{body} 1", f"{body}s 2", f"{body} 3", f"{body} 4", f"{body} 4 again"):
        assert re.search(rf"- \[ \] {re.escape(expected)} \^tm-[0-9a-z]{{8}}\n", text), expected
    assert text.count(ids[3]) == 1  # exactly one copy kept the duplicated id
    reviews = httpx.get(f"{env['url']}/v1/review?status=open", headers=headers).json()["reviews"]
    assert reviews == []  # no pause, no review: every line resolved on the spot

    # ...and the file still syncs: an edit of the transcluded line reaches the mirror
    notes.write_text(text.replace(f"{body} 5", f"{body} FIVE"), encoding="utf-8")
    assert _wait_until(lambda: "FIVE" in mirror.read_text(encoding="utf-8"))


def test_spans_transclude_part_of_a_line_and_several_lines_after_one_setup(
    env: dict[str, Any],
) -> None:
    """M20-A/B/E through the real daemon: `{text}{tm-id}` shares only the braced text, across
    lines too; each file keeps its own surroundings and padding; no header is ever added."""
    vault: Path = env["vault"]
    one, two, three = vault / "one.md", vault / "two.md", vault / "three.md"
    one.write_text(
        "# Plan\n\nThe launch is on {friday the 13th}{tm-new} unless it rains.\n\n"
        "{ Bring the tent\n\nand the stove }{tm-new}\n",
        encoding="utf-8",
    )
    result = runner.invoke(cli_app, ["setup", "--config", env["config"], str(vault)])
    assert result.exit_code == 0, result.output
    ids = _SPAN_ID_RE.findall(one.read_text(encoding="utf-8"))
    assert len(ids) == 2, one.read_text(encoding="utf-8")
    date_id, list_id = ids

    two.write_text(
        f"Reminder: {{friday the 13th}}{{tm-{date_id}}} (do not forget)\n", encoding="utf-8"
    )
    three.write_text(
        f"Checklist\n{{Bring the tent\n\nand the stove}}{{tm-{list_id}}}\nend\n", encoding="utf-8"
    )

    def tracked() -> int:
        return len(
            httpx.get(f"{env['url']}/v1/sync/status", headers=_headers(result)).json()[
                "sync_roots"
            ][0]["files"]
        )

    assert _wait_until(lambda: tracked() == 3)

    _edit(two, "friday the 13th", "monday the 16th")  # a span in the middle of a sentence
    assert _wait_until(lambda: "monday the 16th" in one.read_text(encoding="utf-8"))
    assert one.read_text(encoding="utf-8").startswith(
        "# Plan\n\nThe launch is on {monday the 16th}"
    )
    assert "unless it rains." in one.read_text(encoding="utf-8")  # its own context, untouched

    _edit(three, "Bring the tent", "Bring TWO tents")  # a NON-last line of a multi-line span
    assert _wait_until(lambda: "TWO tents" in one.read_text(encoding="utf-8"))
    assert "{ Bring TWO tents\n\nand the stove }{tm-" in one.read_text(
        encoding="utf-8"
    )  # its padding

    _edit(one, "and the stove", "and the stove\n\nand a map")  # the multi-line text grows
    assert _wait_until(lambda: "and a map" in three.read_text(encoding="utf-8"))
    assert three.read_text(encoding="utf-8").startswith(
        "Checklist\n{Bring TWO tents\n\nand the stove"
    )

    reviews = httpx.get(f"{env['url']}/v1/review?status=open", headers=_headers(result)).json()
    assert reviews["reviews"] == []
    for note in (one, two, three):
        assert not note.read_text(encoding="utf-8").startswith("---"), note.name


def test_a_pasted_copy_edited_in_the_same_save_keeps_the_edit_and_a_stale_paste_loses(
    env: dict[str, Any],
) -> None:
    """M20-D through the real daemon (the sandbox's "if I modify the new version, will the old
    version change?"): a line copied into a new file and edited before the daemon ever saw the
    copy is a NEW change -- it wins and reaches the original. An OLD version pasted back does
    not."""
    vault: Path = env["vault"]
    original, copy_, stale = vault / "orig.md", vault / "copy.md", vault / "stale.md"
    original.write_text("- [ ] plan the trip ^tm-new\n", encoding="utf-8")
    result = runner.invoke(cli_app, ["setup", "--config", env["config"], str(vault)])
    assert result.exit_code == 0, result.output
    [node_id] = _ID_RE.findall(original.read_text(encoding="utf-8"))
    headers = _headers(result)

    def tracked() -> int:
        status = httpx.get(f"{env['url']}/v1/sync/status", headers=headers).json()
        return len(status["sync_roots"][0]["files"])

    # copied AND edited in one save, so the daemon's first sight of the copy already differs
    copy_.write_text(f"- [ ] plan the trip to Lisbon {node_id}\n", encoding="utf-8")
    assert _wait_until(lambda: "Lisbon" in original.read_text(encoding="utf-8"))
    assert "Lisbon" in copy_.read_text(encoding="utf-8")  # the copy kept its edit
    assert _wait_until(lambda: tracked() == 2)
    reviews = httpx.get(f"{env['url']}/v1/review?status=open", headers=headers).json()
    assert reviews["reviews"] == []  # no conflict: nothing was thrown away

    # the very first wording, pasted back into a fresh file: history, not news
    stale.write_text(f"- [ ] plan the trip {node_id}\n", encoding="utf-8")
    assert _wait_until(lambda: "Lisbon" in stale.read_text(encoding="utf-8"))
    assert "Lisbon" in original.read_text(encoding="utf-8")  # the hub's text stayed
    reviews = httpx.get(f"{env['url']}/v1/review?status=open", headers=headers).json()
    assert reviews["reviews"] == []
